from __future__ import annotations

import argparse
import os
import sys

import psycopg
from psycopg.rows import dict_row

from collector.sync_to_opensearch import create_os_client, ensure_indices, reset_sync_checkpoint, sync_items


TARGET_COLUMNS = ("raw_content", "translated_content")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align PostgreSQL items with OpenSearch, then drop targeted heavy PG columns."
    )
    parser.add_argument("--drop-columns", action="store_true", help="Drop raw_content / translated_content after sync succeeds.")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Reset item sync checkpoint before syncing.")
    parser.add_argument("--limit", type=int, default=None, help="Optional item sync limit for testing.")
    return parser.parse_args()


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def existing_target_columns(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'items'
              AND column_name = ANY(%s::text[])
            ORDER BY column_name
            """,
            (list(TARGET_COLUMNS),),
        )
        return [row["column_name"] for row in cur.fetchall()]


def ensure_updated_at(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ")
        cur.execute(
            """
            UPDATE items
            SET updated_at = COALESCE(updated_at, stored_at, NOW())
            WHERE updated_at IS NULL
            """
        )
        cur.execute("ALTER TABLE items ALTER COLUMN updated_at SET DEFAULT NOW()")
        cur.execute("ALTER TABLE items ALTER COLUMN updated_at SET NOT NULL")


def drop_target_columns(conn: psycopg.Connection) -> list[str]:
    dropped: list[str] = []
    for column in existing_target_columns(conn):
        with conn.cursor() as cur:
            cur.execute(f'ALTER TABLE items DROP COLUMN "{column}"')
        dropped.append(column)
    return dropped


def main() -> int:
    args = parse_args()
    db_url = require_env("DATABASE_URL")
    os_url = require_env("OPENSEARCH_URL")

    client = create_os_client(os_url)
    ensure_indices(client)
    if args.reset_checkpoint:
        reset_sync_checkpoint(client, "last_sync_v2")

    with psycopg.connect(db_url, row_factory=dict_row, prepare_threshold=None) as conn:
        ensure_updated_at(conn)
        conn.commit()

    sync_items(client, db_url, full=True, limit=args.limit, shard_index=0, shard_count=1)

    if not args.drop_columns:
        print("Alignment completed. Target columns were not dropped.")
        return 0

    with psycopg.connect(db_url, row_factory=dict_row, prepare_threshold=None) as conn:
        dropped = drop_target_columns(conn)
        conn.commit()
        print(f"Dropped columns: {', '.join(dropped) if dropped else '(none)'}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
