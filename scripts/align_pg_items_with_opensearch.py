from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collector.sync_to_opensearch import create_os_client, ensure_indices, reset_sync_checkpoint, sync_items


TARGET_COLUMNS = ("raw_content", "translated_content")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Align PostgreSQL items with OpenSearch, then drop targeted heavy PG columns."
    )
    parser.add_argument("--drop-columns", action="store_true", help="Drop raw_content / translated_content after sync succeeds.")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Reset item sync checkpoint before syncing.")
    parser.add_argument("--limit", type=int, default=None, help="Optional item sync limit for testing.")
    parser.add_argument("--retries", type=int, default=5, help="Retry count for flaky PostgreSQL/OpenSearch alignment steps.")
    parser.add_argument("--retry-delay-seconds", type=float, default=5.0, help="Base delay between retries.")
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


def run_with_retry(label: str, retries: int, retry_delay_seconds: float, fn):
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except Exception:
            if attempt >= retries:
                raise
            delay = retry_delay_seconds * attempt
            print(f"{label} failed on attempt {attempt}/{retries}; retrying in {delay:.1f}s...", file=sys.stderr)
            time.sleep(delay)


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

    def ensure_pg_updated_at():
        with psycopg.connect(db_url, row_factory=dict_row, prepare_threshold=None) as conn:
            ensure_updated_at(conn)
            conn.commit()

    run_with_retry("ensure_updated_at", args.retries, args.retry_delay_seconds, ensure_pg_updated_at)

    run_with_retry(
        "sync_items",
        args.retries,
        args.retry_delay_seconds,
        lambda: sync_items(client, db_url, full=True, limit=args.limit, shard_index=0, shard_count=1),
    )

    if not args.drop_columns:
        print("Alignment completed. Target columns were not dropped.")
        return 0

    def drop_columns():
        with psycopg.connect(db_url, row_factory=dict_row, prepare_threshold=None) as conn:
            dropped = drop_target_columns(conn)
            conn.commit()
            return dropped

    dropped = run_with_retry("drop_target_columns", args.retries, args.retry_delay_seconds, drop_columns)
    print(f"Dropped columns: {', '.join(dropped) if dropped else '(none)'}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
