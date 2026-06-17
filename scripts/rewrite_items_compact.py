from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass

from psycopg import connect
from psycopg.rows import dict_row


DROP_COLUMNS = (
    "raw_content",
    "translated_content",
)


@dataclass(frozen=True)
class RewritePlan:
    source_table: str
    temp_table: str
    source_index: str
    temp_index: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rewrite items into a compact heap that drops historical text payloads.")
    parser.add_argument("--apply", action="store_true", help="Execute the rewrite. Without this flag, only print the plan.")
    parser.add_argument("--drop-columns", action="store_true", help="Drop compacted text columns after the rewrite succeeds.")
    parser.add_argument("--analyze-only", action="store_true", help="Only analyze current items storage and exit.")
    return parser.parse_args()


def require_database_url() -> str:
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL environment variable.")
    return database_url


def fetch_columns(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              column_name,
              data_type,
              udt_name,
              is_nullable,
              column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'items'
            ORDER BY ordinal_position
            """
        )
        return cur.fetchall()


def fetch_items_size(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              COUNT(*) AS rows,
              pg_total_relation_size('public.items') AS total_bytes,
              pg_relation_size('public.items') AS heap_bytes,
              pg_indexes_size('public.items') AS index_bytes,
              COALESCE((
                SELECT pg_total_relation_size(reltoastrelid)
                FROM pg_class
                WHERE oid = 'public.items'::regclass
              ), 0) AS toast_bytes
            FROM items
            """
        )
        return dict(cur.fetchone())


def column_definition(column: dict) -> str:
    data_type = column["data_type"]
    if data_type == "USER-DEFINED":
        sql_type = column["udt_name"]
    else:
        sql_type = data_type

    parts = [f"\"{column['column_name']}\" {sql_type}"]
    if column["column_default"] is not None:
        parts.append(f"DEFAULT {column['column_default']}")
    if column["is_nullable"] == "NO":
        parts.append("NOT NULL")
    return " ".join(parts)


def build_rewrite_plan() -> RewritePlan:
    return RewritePlan(
        source_table="items",
        temp_table="items_compact_tmp",
        source_index="idx_items_target_guid_unique",
        temp_index="idx_items_compact_tmp_target_guid_unique",
    )


def create_temp_table(conn, columns: list[dict], plan: RewritePlan) -> None:
    definitions = ",\n    ".join(column_definition(column) for column in columns)
    with conn.cursor() as cur:
        cur.execute(f'DROP TABLE IF EXISTS public."{plan.temp_table}" CASCADE')
        cur.execute(
            f"""
            CREATE TABLE public."{plan.temp_table}" (
                {definitions},
                CONSTRAINT "{plan.temp_index}" UNIQUE (target_id, guid)
            )
            """
        )


def copy_compact_rows(conn, columns: list[dict], plan: RewritePlan) -> int:
    column_names = [column["column_name"] for column in columns]
    select_exprs: list[str] = []
    for name in column_names:
        if name in DROP_COLUMNS:
            select_exprs.append(f"NULL::{next(column['udt_name'] if column['data_type'] == 'USER-DEFINED' else column['data_type'] for column in columns if column['column_name'] == name)} AS \"{name}\"")
        elif name == "metadata":
            select_exprs.append(
                """
                CASE
                  WHEN metadata = '{}'::jsonb THEN jsonb_build_object('os_compacted', true, 'os_compacted_at', NOW()::text)
                  ELSE metadata || jsonb_build_object('os_compacted', true, 'os_compacted_at', NOW()::text)
                END AS metadata
                """.strip()
            )
        else:
            select_exprs.append(f"\"{name}\"")

    column_list = ", ".join(f"\"{name}\"" for name in column_names)
    select_list = ",\n                ".join(select_exprs)

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO public."{plan.temp_table}" ({column_list})
            SELECT
                {select_list}
            FROM public."{plan.source_table}"
            """
        )
        return cur.rowcount


def recreate_indexes(conn, plan: RewritePlan) -> None:
    with conn.cursor() as cur:
        cur.execute(f'CREATE INDEX "{plan.temp_table}_target_id_stored_at_idx" ON public."{plan.temp_table}" (target_id, stored_at DESC)')
        cur.execute(f'CREATE INDEX "{plan.temp_table}_stored_at_idx" ON public."{plan.temp_table}" (stored_at DESC)')
        cur.execute(f'CREATE INDEX "{plan.temp_table}_updated_at_id_idx" ON public."{plan.temp_table}" (updated_at DESC, id DESC)')
        cur.execute(f'CREATE INDEX "{plan.temp_table}_published_at_idx" ON public."{plan.temp_table}" (published_at DESC)')
        cur.execute(f'CREATE INDEX "{plan.temp_table}_video_feed_idx" ON public."{plan.temp_table}" (stored_at DESC) WHERE video_url IS NOT NULL')
        cur.execute(
            f"""
            CREATE INDEX "{plan.temp_table}_video_feed_sort_time_idx"
            ON public."{plan.temp_table}" ((COALESCE(published_at, stored_at)) DESC, stored_at DESC, id DESC)
            WHERE video_url IS NOT NULL AND video_url <> ''
            """
        )
        cur.execute(
            f"""
            CREATE INDEX "{plan.temp_table}_target_video_feed_sort_time_idx"
            ON public."{plan.temp_table}" (target_id, (COALESCE(published_at, stored_at)) DESC, stored_at DESC, id DESC)
            WHERE video_url IS NOT NULL AND video_url <> ''
            """
        )
        cur.execute(f'CREATE INDEX "{plan.temp_table}_expires_at_idx" ON public."{plan.temp_table}" (expires_at)')
        cur.execute(f'CREATE INDEX "{plan.temp_table}_video_url_expires_at_idx" ON public."{plan.temp_table}" (video_url_expires_at)')


def swap_tables(conn, plan: RewritePlan) -> None:
    with conn.cursor() as cur:
        cur.execute(f'ALTER TABLE public.feed_events DROP CONSTRAINT IF EXISTS feed_events_item_id_fkey')
        cur.execute(f'ALTER TABLE public.item_tags DROP CONSTRAINT IF EXISTS item_tags_item_id_fkey')
        cur.execute(f'ALTER TABLE public.video_stats DROP CONSTRAINT IF EXISTS video_stats_item_id_fkey')
        cur.execute(f'ALTER TABLE public.video_resolution_queue DROP CONSTRAINT IF EXISTS video_resolution_queue_resolved_item_id_fkey')
        cur.execute(f'ALTER TABLE public."{plan.source_table}" RENAME TO items_legacy_full')
        cur.execute(f'ALTER TABLE public."{plan.temp_table}" RENAME TO items')
        cur.execute(
            """
            ALTER TABLE public.feed_events
            ADD CONSTRAINT feed_events_item_id_fkey
            FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE
            """
        )
        cur.execute(
            """
            ALTER TABLE public.item_tags
            ADD CONSTRAINT item_tags_item_id_fkey
            FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE
            """
        )
        cur.execute(
            """
            ALTER TABLE public.video_stats
            ADD CONSTRAINT video_stats_item_id_fkey
            FOREIGN KEY (item_id) REFERENCES public.items(id) ON DELETE CASCADE
            """
        )
        cur.execute(
            """
            ALTER TABLE public.video_resolution_queue
            ADD CONSTRAINT video_resolution_queue_resolved_item_id_fkey
            FOREIGN KEY (resolved_item_id) REFERENCES public.items(id) ON DELETE SET NULL
            """
        )


def optionally_drop_columns(conn) -> list[str]:
    dropped: list[str] = []
    with conn.cursor() as cur:
        for column in DROP_COLUMNS:
            cur.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'items'
                  AND column_name = %s
                """,
                (column,),
            )
            if cur.fetchone():
                cur.execute(f'ALTER TABLE public.items DROP COLUMN "{column}"')
                dropped.append(column)
    return dropped


def main() -> int:
    args = parse_args()
    plan = build_rewrite_plan()

    with connect(require_database_url(), row_factory=dict_row, prepare_threshold=None) as conn:
        before = fetch_items_size(conn)
        columns = fetch_columns(conn)

        if args.analyze_only or not args.apply:
            print(
                json.dumps(
                    {
                        "apply": args.apply,
                        "analyzeOnly": args.analyze_only,
                        "dropColumns": args.drop_columns,
                        "itemsSize": before,
                        "plan": plan.__dict__,
                        "dropColumnsCandidate": list(DROP_COLUMNS),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return 0

        create_temp_table(conn, columns, plan)
        copied_rows = copy_compact_rows(conn, columns, plan)
        recreate_indexes(conn, plan)
        swap_tables(conn, plan)
        dropped_columns = optionally_drop_columns(conn) if args.drop_columns else []
        conn.commit()

        after = fetch_items_size(conn)
        print(
            json.dumps(
                {
                    "apply": True,
                    "copiedRows": copied_rows,
                    "droppedColumns": dropped_columns,
                    "before": before,
                    "after": after,
                    "plan": plan.__dict__,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
