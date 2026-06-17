from __future__ import annotations

import json
import os
import sys
from typing import Iterable

from psycopg.rows import dict_row

try:
    import psycopg
    from opensearchpy import helpers
except ModuleNotFoundError:  # pragma: no cover
    import psycopg  # type: ignore
    from opensearchpy import helpers  # type: ignore

try:
    from collector.sync_to_opensearch import BASE_SYNC_SQL, X2_ITEMS_INDEX, build_document, create_os_client
except ModuleNotFoundError:  # pragma: no cover
    from sync_to_opensearch import BASE_SYNC_SQL, X2_ITEMS_INDEX, build_document, create_os_client

_CLIENT = None
_INDEX = None


def is_opensearch_write_enabled() -> bool:
    return bool(os.environ.get("OPENSEARCH_URL", "").strip())


def get_items_index() -> str:
    global _INDEX
    if _INDEX is None:
        _INDEX = os.environ.get("OPENSEARCH_ITEMS_INDEX", "").strip() or X2_ITEMS_INDEX
    return _INDEX


def get_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    opensearch_url = os.environ.get("OPENSEARCH_URL", "").strip()
    if not opensearch_url:
        return None

    _CLIENT = create_os_client(opensearch_url)
    return _CLIENT


def fetch_item_row(conn: psycopg.Connection, item_id: str):
    sql = BASE_SYNC_SQL + "\n  AND i.id = %s\nLIMIT 1"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (item_id,))
        return cur.fetchone()


def fetch_item_rows(conn: psycopg.Connection, item_ids: Iterable[str]):
    ids = list(dict.fromkeys(item_id for item_id in item_ids if item_id))
    if not ids:
        return []

    sql = BASE_SYNC_SQL + "\n  AND i.id = ANY(%s::uuid[])"
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, (ids,))
        return cur.fetchall()


def delete_item(item_id: str) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    try:
        client.delete(index=get_items_index(), id=str(item_id), ignore=[404], refresh=False)
        return True
    except Exception as exc:
        print(f"[opensearch] delete failed for {item_id}: {exc}", file=sys.stderr)
        return False


def update_item_stats(item_id: str, stats: dict[str, int | float]) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    score = float(stats.get("score") or 0.0)
    payload = {
        "score": score,
        "quality_score": max(score, 0.0),
        "impressions": int(stats.get("impressions") or 0),
        "plays": int(stats.get("plays") or 0),
        "finishes": int(stats.get("finishes") or 0),
        "likes": int(stats.get("likes") or 0),
        "dislikes": int(stats.get("dislikes") or 0),
        "skips": int(stats.get("skips") or 0),
        "shares": int(stats.get("shares") or 0),
    }
    try:
        client.update(
            index=get_items_index(),
            id=str(item_id),
            body={"doc": payload},
            refresh=False,
            retry_on_conflict=3,
        )
        return True
    except Exception as exc:
        print(f"[opensearch] stats update failed for {item_id}: {exc}", file=sys.stderr)
        return False


def compact_item(conn: psycopg.Connection, item_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET
                title = NULL,
                content = NULL,
                metadata = CASE
                    WHEN metadata = '{}'::jsonb THEN '{}'::jsonb
                    ELSE (
                        metadata
                        || jsonb_build_object('os_compacted', true, 'os_compacted_at', NOW()::text)
                    )
                END,
                updated_at = NOW()
            WHERE id = %s
              AND (
                title IS NOT NULL
                OR content IS NOT NULL
                OR COALESCE(metadata->>'os_compacted', 'false') <> 'true'
              )
            """,
            (item_id,),
        )
        return cur.rowcount > 0


def is_item_compacted(row: dict) -> bool:
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    return (
        row.get("title") is None
        and row.get("content") is None
        and str(metadata.get("os_compacted")).lower() == "true"
    )


def sync_item(conn: psycopg.Connection, item_id: str) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    row = fetch_item_row(conn, item_id)
    if not row:
        return False

    doc = build_document(row)
    client.index(index=get_items_index(), id=str(row["id"]), body=doc, refresh=False)
    return True


def sync_items(conn: psycopg.Connection, item_ids: Iterable[str]) -> int:
    if not is_opensearch_write_enabled():
        return 0

    client = get_client()
    if client is None:
        return 0

    rows = fetch_item_rows(conn, item_ids)
    if not rows:
        return 0

    actions = [
        {
            "_op_type": "index",
            "_index": get_items_index(),
            "_id": str(row["id"]),
            "_source": build_document(row),
        }
        for row in rows
    ]
    success, errors = helpers.bulk(client, actions, raise_on_error=False)
    if errors:
        print(f"[opensearch] bulk sync had {len(errors)} errors", file=sys.stderr)
        for error in errors[:3]:
            print(f"[opensearch] {error}", file=sys.stderr)
    return success
