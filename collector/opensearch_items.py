from __future__ import annotations

import json
import os
import sys
from typing import Iterable
from urllib.parse import urlparse

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

try:
    import psycopg
except ModuleNotFoundError:  # pragma: no cover
    import psycopg  # type: ignore

_CLIENT = None
_INDEX = None
_TARGET_CONTEXT_CACHE: dict[str, dict] = {}
_UNSET = object()
X2_ITEMS_INDEX = "x2_items"


def is_opensearch_write_enabled() -> bool:
    return bool(os.environ.get("OPENSEARCH_URL", "").strip())


def get_items_index() -> str:
    global _INDEX
    if _INDEX is None:
        _INDEX = os.environ.get("OPENSEARCH_ITEMS_INDEX", "").strip() or X2_ITEMS_INDEX
    return _INDEX


def create_os_client(opensearch_url: str):
    from opensearchpy import OpenSearch  # imported lazily

    parsed = urlparse(opensearch_url)
    host = parsed.hostname
    port = parsed.port or 9200
    scheme = parsed.scheme or "https"
    username = parsed.username
    password = parsed.password

    return OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=(username, password) if username else None,
        use_ssl=(scheme == "https"),
        verify_certs=False,
        ssl_show_warn=False,
        timeout=180,
    )


def get_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT

    opensearch_url = os.environ.get("OPENSEARCH_URL", "").strip()
    if not opensearch_url:
        return None

    _CLIENT = create_os_client(opensearch_url)
    return _CLIENT


def _to_iso(value):
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def _normalize_tags(values):
    if not isinstance(values, list):
        return []
    normalized = []
    seen = set()
    for value in values:
        if not isinstance(value, str):
            continue
        tag = value.strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def _compute_target_display(source: str | None, kind: str | None, value: str | None):
    if source == "youtube":
        return f"youtube:{value}"
    if source in {
        "heiliao", "cg91", "baoliao51", "douyin", "18mh", "rou", "dadaafa",
        "18j", "1mtif", "tikporn", "91porna", "91porn", "91rb", "badnews",
        "bdrq", "avgood", "705hs", "xxxtik", "affair", "attach", "dirtyship",
        "influencersgonewild", "missav",
    }:
        return source
    if kind == "keyword" and value:
        return f"search:{value}"
    return value


def _compute_target_link(source: str | None, kind: str | None, value: str | None):
    if not value:
        return None
    if source in {
        "heiliao", "cg91", "baoliao51", "douyin", "18mh", "rou", "dadaafa",
        "18j", "1mtif", "tikporn", "91porna", "91porn", "91rb", "badnews",
        "bdrq", "avgood", "705hs", "xxxtik", "affair", "attach", "dirtyship",
        "influencersgonewild", "missav",
    }:
        return value
    if source == "youtube":
        return f"https://www.youtube.com/channel/{value}"
    if source == "twitter" and kind == "user":
        return f"https://x.com/{value}"
    return None


def load_target_context(conn: psycopg.Connection, target_id: str) -> dict | None:
    cache_key = str(target_id)
    if cache_key in _TARGET_CONTEXT_CACHE:
        return _TARGET_CONTEXT_CACHE[cache_key]

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT
              t.id::text AS target_id,
              t.source,
              t.kind,
              t.value,
              tp.category,
              COALESCE(tp.is_public_pool, FALSE) AS is_public_pool,
              COALESCE(tp.tags, '[]'::jsonb) AS profile_tags,
              COALESCE(cat.is_sensitive, FALSE) AS is_sensitive
            FROM targets t
            LEFT JOIN target_profiles tp ON tp.target_id = t.id
            LEFT JOIN categories cat ON cat.slug = tp.category
            WHERE t.id = %s
            LIMIT 1
            """,
            (target_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    _TARGET_CONTEXT_CACHE[cache_key] = row
    return row


def index_item_document(
    conn: psycopg.Connection,
    *,
    item_id: str,
    target_id: str,
    guid: str,
    video_url: str | None,
    playback_headers: dict | None,
    cover_url: str | None,
    title: str | None,
    caption: str | None,
    content: str | None,
    author: str | None,
    fullname: str | None,
    display_author: str | None,
    display_handle: str | None,
    author_profile_url: str | None,
    author_profile_platform: str | None,
    x_url: str | None,
    link: str | None,
    images: list[str] | None,
    published_at,
    stored_at,
    updated_at,
    expires_at,
    video_url_expires_at,
    is_retweet: bool,
) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    target_context = load_target_context(conn, target_id)
    if not target_context:
        return False

    profile_tags = target_context.get("profile_tags")
    if isinstance(profile_tags, str):
        try:
            profile_tags = json.loads(profile_tags)
        except Exception:
            profile_tags = []

    category = target_context.get("category")
    category_value = category.strip().lower() if isinstance(category, str) and category.strip() else None
    target = _compute_target_display(target_context.get("source"), target_context.get("kind"), target_context.get("value"))
    doc = {
        "id": str(item_id),
        "target_id": str(target_id),
        "guid": guid,
        "video_url": video_url,
        "video_key": video_url,
        "playback_headers": playback_headers or None,
        "cover_url": cover_url,
        "title": title,
        "caption": caption,
        "content": content,
        "raw_content": None,
        "translated_content": None,
        "author": author,
        "fullname": fullname,
        "display_author": display_author,
        "display_handle": display_handle,
        "author_profile_url": author_profile_url,
        "author_profile_platform": author_profile_platform,
        "x_url": x_url,
        "link": link,
        "published_at": _to_iso(published_at),
        "stored_at": _to_iso(stored_at),
        "updated_at": _to_iso(updated_at or stored_at),
        "sort_at": _to_iso(published_at or stored_at),
        "source": target_context.get("source"),
        "target": target,
        "target_link": _compute_target_link(target_context.get("source"), target_context.get("kind"), target_context.get("value")),
        "kind": target_context.get("kind"),
        "category": category_value,
        "tags": _normalize_tags(profile_tags if isinstance(profile_tags, list) else []),
        "expires_at": _to_iso(expires_at),
        "video_url_expires_at": _to_iso(video_url_expires_at),
        "is_public_pool": bool(target_context.get("is_public_pool")),
        "is_retweet": bool(is_retweet),
        "is_sensitive": bool(target_context.get("is_sensitive")),
        "has_video": bool(video_url),
        "score": 0.0,
        "quality_score": 0.0,
        "impressions": 0,
        "plays": 0,
        "finishes": 0,
        "likes": 0,
        "dislikes": 0,
        "skips": 0,
        "shares": 0,
        "images": [image for image in (images or []) if isinstance(image, str) and image],
    }

    try:
        client.index(index=get_items_index(), id=str(item_id), body=doc, refresh=False)
        return True
    except Exception as exc:
        print(f"[opensearch] direct index failed for {item_id}: {exc}", file=sys.stderr)
        return False


def update_item_playback(
    item_id: str,
    *,
    video_url: str | None,
    video_url_expires_at,
    playback_headers: dict | None = None,
    cover_url: str | None = None,
) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    payload = {
        "video_url": video_url,
        "video_key": video_url,
        "video_url_expires_at": _to_iso(video_url_expires_at),
        "has_video": bool(video_url),
    }
    if playback_headers is not None:
        payload["playback_headers"] = playback_headers
    if cover_url is not None:
        payload["cover_url"] = cover_url

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
        print(f"[opensearch] playback update failed for {item_id}: {exc}", file=sys.stderr)
        return False


def update_item_document(
    item_id: str,
    *,
    title=_UNSET,
    caption=_UNSET,
    content=_UNSET,
    category=_UNSET,
    tags=_UNSET,
    author=_UNSET,
    fullname=_UNSET,
    display_author=_UNSET,
    display_handle=_UNSET,
    author_profile_url=_UNSET,
    author_profile_platform=_UNSET,
    x_url=_UNSET,
    link=_UNSET,
    images=_UNSET,
    cover_url=_UNSET,
    published_at=_UNSET,
    stored_at=_UNSET,
    updated_at=_UNSET,
    expires_at=_UNSET,
    video_url_expires_at=_UNSET,
) -> bool:
    if not is_opensearch_write_enabled():
        return False

    client = get_client()
    if client is None:
        return False

    payload: dict[str, object] = {}
    field_map = {
        "title": title,
        "caption": caption,
        "content": content,
        "category": category,
        "author": author,
        "fullname": fullname,
        "display_author": display_author,
        "display_handle": display_handle,
        "author_profile_url": author_profile_url,
        "author_profile_platform": author_profile_platform,
        "x_url": x_url,
        "link": link,
        "cover_url": cover_url,
        "published_at": _to_iso(published_at) if published_at is not _UNSET else _UNSET,
        "stored_at": _to_iso(stored_at) if stored_at is not _UNSET else _UNSET,
        "updated_at": _to_iso(updated_at) if updated_at is not _UNSET else _UNSET,
        "expires_at": _to_iso(expires_at) if expires_at is not _UNSET else _UNSET,
        "video_url_expires_at": _to_iso(video_url_expires_at) if video_url_expires_at is not _UNSET else _UNSET,
    }
    for key, value in field_map.items():
        if value is not _UNSET:
            payload[key] = value

    if images is not _UNSET:
        payload["images"] = [image for image in (images or []) if isinstance(image, str) and image]
    if tags is not _UNSET:
        payload["tags"] = _normalize_tags(tags if isinstance(tags, list) else [])

    if not payload:
        return True

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
        print(f"[opensearch] document update failed for {item_id}: {exc}", file=sys.stderr)
        return False


def upsert_item_record(
    conn: psycopg.Connection,
    *,
    target_id: str,
    guid: str,
    display_author: str | None,
    display_handle: str | None,
    author_profile_url: str | None,
    author_profile_platform: str | None,
    video_url: str | None,
    expires_at,
    video_url_expires_at,
    published_at,
    stored_at,
    is_retweet: bool,
    metadata: dict | None,
    cover_url: str | None,
    title: str | None,
    caption: str | None,
    content: str | None,
    author: str | None,
    fullname: str | None,
    x_url: str | None,
    link: str | None,
    images: list[str] | None,
    playback_headers: dict | None = None,
) -> tuple[str | None, bool]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid,
                video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                video_url = EXCLUDED.video_url,
                expires_at = EXCLUDED.expires_at,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                published_at = COALESCE(items.published_at, EXCLUDED.published_at),
                metadata = COALESCE(items.metadata, '{}'::jsonb) || EXCLUDED.metadata
            RETURNING id::text AS id, (xmax = 0) AS inserted
            """,
            (
                target_id,
                guid,
                video_url,
                expires_at,
                video_url_expires_at,
                published_at,
                stored_at,
                is_retweet,
                Jsonb(metadata or {}),
            ),
        )
        row = cur.fetchone()

    if not row or not row.get("id"):
        return None, False

    item_id = str(row["id"])
    index_item_document(
        conn,
        item_id=item_id,
        target_id=str(target_id),
        guid=guid,
        video_url=video_url,
        playback_headers=playback_headers,
        cover_url=cover_url,
        title=title,
        caption=caption,
        content=content,
        author=author,
        fullname=fullname,
        display_author=display_author,
        display_handle=display_handle,
        author_profile_url=author_profile_url,
        author_profile_platform=author_profile_platform,
        x_url=x_url,
        link=link,
        images=images,
        published_at=published_at,
        stored_at=stored_at,
        updated_at=stored_at,
        expires_at=expires_at,
        video_url_expires_at=video_url_expires_at,
        is_retweet=is_retweet,
    )
    return item_id, bool(row.get("inserted"))


def refresh_item_playback(
    conn: psycopg.Connection,
    *,
    item_id: str,
    video_url: str | None,
    video_url_expires_at,
    metadata: dict | None,
    playback_headers: dict | None = None,
    cover_url: str | None = None,
) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET video_url = %s,
                video_url_expires_at = %s,
                metadata = %s,
                stored_at = stored_at
            WHERE id = %s
            """,
            (video_url, video_url_expires_at, Jsonb(metadata or {}), item_id),
        )

    return update_item_playback(
        item_id,
        video_url=video_url,
        video_url_expires_at=video_url_expires_at,
        playback_headers=playback_headers,
        cover_url=cover_url,
    )


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


def delete_items(item_ids: Iterable[str]) -> int:
    ids = [str(item_id) for item_id in dict.fromkeys(item_id for item_id in item_ids if item_id)]
    if not ids or not is_opensearch_write_enabled():
        return 0

    client = get_client()
    if client is None:
        return 0

    deleted = 0
    for item_id in ids:
        if delete_item(item_id):
            deleted += 1
    return deleted


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


def fetch_documents(item_ids: Iterable[str]) -> dict[str, dict]:
    ids = [str(item_id) for item_id in dict.fromkeys(item_id for item_id in item_ids if item_id)]
    if not ids:
        return {}

    client = get_client()
    if client is None:
        return {}

    try:
        response = client.mget(index=get_items_index(), body={"ids": ids})
    except Exception as exc:
        print(f"[opensearch] mget failed: {exc}", file=sys.stderr)
        return {}

    docs = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(docs, list):
        return {}

    result: dict[str, dict] = {}
    for doc in docs:
        if not isinstance(doc, dict) or not doc.get("found"):
            continue
        doc_id = str(doc.get("_id") or "")
        source = doc.get("_source")
        if doc_id and isinstance(source, dict):
            result[doc_id] = source
    return result


def fetch_document(item_id: str) -> dict:
    return fetch_documents([item_id]).get(str(item_id), {})


def compact_item(conn: psycopg.Connection, item_id: str) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET
                metadata = CASE
                    WHEN metadata = '{}'::jsonb THEN '{}'::jsonb
                    ELSE (
                        metadata
                        || jsonb_build_object('os_compacted', true, 'os_compacted_at', NOW()::text)
                    )
                END,
                updated_at = NOW()
            WHERE id = %s
              AND COALESCE(metadata->>'os_compacted', 'false') <> 'true'
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
    return str(metadata.get("os_compacted")).lower() == "true"


def sync_item(conn: psycopg.Connection, item_id: str) -> bool:
    raise RuntimeError("sync_item has been retired after the OpenSearch hard cutover.")


def sync_items(conn: psycopg.Connection, item_ids: Iterable[str]) -> int:
    raise RuntimeError("sync_items has been retired after the OpenSearch hard cutover.")
