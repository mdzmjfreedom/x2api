from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.opensearch_items import delete_items, delete_items_by_source


def require_database_url() -> str:
    value = os.environ.get("DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    return value


def normalize_db_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme.startswith("postgres") and "sslmode=" not in parsed.query:
      separator = "&" if parsed.query else "?"
      return f"{url}{separator}sslmode=require"
    return url


def cleanup_source(conn: psycopg.Connection, source: str) -> dict[str, int]:
    deleted_item_ids: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.id::text
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s
            """,
            (source,),
        )
        deleted_item_ids = [row[0] for row in cur.fetchall()]

        cur.execute(
            """
            WITH doomed_items AS (
                SELECT i.id
                FROM items i
                INNER JOIN targets t ON t.id = i.target_id
                WHERE t.source = %s
            )
            DELETE FROM feed_events fe
            WHERE fe.item_id IN (SELECT id FROM doomed_items)
            """,
            (source,),
        )
        feed_events_deleted = cur.rowcount

        cur.execute(
            """
            WITH doomed_items AS (
                SELECT i.id
                FROM items i
                INNER JOIN targets t ON t.id = i.target_id
                WHERE t.source = %s
            )
            DELETE FROM video_stats vs
            WHERE vs.item_id IN (SELECT id FROM doomed_items)
            """,
            (source,),
        )
        video_stats_deleted = cur.rowcount

        cur.execute(
            """
            WITH doomed_items AS (
                SELECT i.id
                FROM items i
                INNER JOIN targets t ON t.id = i.target_id
                WHERE t.source = %s
            )
            DELETE FROM item_tags it
            WHERE it.item_id IN (SELECT id FROM doomed_items)
            """,
            (source,),
        )
        item_tags_deleted = cur.rowcount

        cur.execute(
            """
            DELETE FROM items i
            USING targets t
            WHERE t.id = i.target_id
              AND t.source = %s
            """,
            (source,),
        )
        items_deleted = cur.rowcount

        cur.execute(
            """
            DELETE FROM crawl_state cs
            USING targets t
            WHERE t.id = cs.target_id
              AND t.source = %s
            """,
            (source,),
        )
        crawl_state_deleted = cur.rowcount

        cur.execute(
            """
            DELETE FROM video_resolution_queue vrq
            USING targets t
            WHERE t.id = vrq.target_id
              AND t.source = %s
            """,
            (source,),
        )
        resolution_queue_deleted = cur.rowcount

    opensearch_deleted = delete_items(deleted_item_ids)
    opensearch_deleted = max(opensearch_deleted, delete_items_by_source(source))
    return {
        "feed_events_deleted": feed_events_deleted,
        "video_stats_deleted": video_stats_deleted,
        "item_tags_deleted": item_tags_deleted,
        "items_deleted": items_deleted,
        "crawl_state_deleted": crawl_state_deleted,
        "resolution_queue_deleted": resolution_queue_deleted,
        "opensearch_deleted": opensearch_deleted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Delete data related to a specific source.")
    parser.add_argument("--source", required=True, help="target source slug, e.g. caoliu")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    database_url = normalize_db_url(require_database_url())
    with psycopg.connect(database_url) as conn:
        stats = cleanup_source(conn, args.source)
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    print(stats)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
