#!/usr/bin/env python3
"""
Retired legacy PG->OpenSearch sync module.

The runtime architecture has been hard-cut to:
- PostgreSQL `items`: lightweight relation/control fields
- OpenSearch `x2_items`: heavy content/read model

This file remains only for historical tests and reference helpers.
"""

import argparse
import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
from psycopg.rows import dict_row
from opensearchpy import OpenSearch, helpers


# ---------------------------------------------------------------------------
# Index definitions
# ---------------------------------------------------------------------------

X2_ITEMS_INDEX = "x2_items"
X2_SYNC_META_INDEX = "x2_sync_meta"
ITEMS_SYNC_META_KEY = "last_sync_v2"
STATS_SYNC_META_KEY = "last_stats_sync_v2"

X2_ITEMS_MAPPING = {
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        },
        "analysis": {
            "normalizer": {
                "lowercase_keyword": {
                    "type": "custom",
                    "filter": ["lowercase"],
                }
            }
        },
    },
    "mappings": {
        "properties": {
            "id": {"type": "keyword"},
            "target_id": {"type": "keyword"},
            "guid": {"type": "keyword"},
            "video_url": {"type": "keyword", "index": False, "doc_values": False},
            "video_key": {"type": "keyword"},
            "playback_headers": {"type": "object", "enabled": False},
            "cover_url": {"type": "keyword", "index": False, "doc_values": False},
            "title": {"type": "text", "analyzer": "standard"},
            "caption": {"type": "text", "analyzer": "standard"},
            "content": {"type": "text", "analyzer": "standard"},
            "raw_content": {"type": "text", "analyzer": "standard"},
            "translated_content": {"type": "text", "analyzer": "standard"},
            "author": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "fullname": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
            "display_author": {"type": "keyword", "index": False, "doc_values": False},
            "display_handle": {"type": "keyword", "index": False, "doc_values": False},
            "author_profile_url": {"type": "keyword", "index": False, "doc_values": False},
            "author_profile_platform": {"type": "keyword", "index": False, "doc_values": False},
            "x_url": {"type": "keyword", "index": False, "doc_values": False},
            "link": {"type": "keyword", "index": False, "doc_values": False},
            "published_at": {"type": "date"},
            "stored_at": {"type": "date"},
            "updated_at": {"type": "date"},
            "sort_at": {"type": "date"},
            "source": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "target": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "target_link": {"type": "keyword", "index": False, "doc_values": False},
            "kind": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "category": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "tags": {"type": "keyword", "normalizer": "lowercase_keyword"},
            "expires_at": {"type": "date"},
            "video_url_expires_at": {"type": "date"},
            "is_public_pool": {"type": "boolean"},
            "is_retweet": {"type": "boolean"},
            "is_sensitive": {"type": "boolean"},
            "has_video": {"type": "boolean"},
            "score": {"type": "float"},
            "quality_score": {"type": "float"},
            "impressions": {"type": "integer"},
            "plays": {"type": "integer"},
            "finishes": {"type": "integer"},
            "likes": {"type": "integer"},
            "dislikes": {"type": "integer"},
            "skips": {"type": "integer"},
            "shares": {"type": "integer"},
            "images": {"type": "keyword", "index": False, "doc_values": False},
        }
    },
}

X2_SYNC_META_MAPPING = {
    "settings": {
        "index": {
            "number_of_shards": 1,
            "number_of_replicas": 0,
        }
    },
    "mappings": {
        "properties": {
            "timestamp": {"type": "date"},
            "item_id": {"type": "keyword"},
            "synced_at": {"type": "date"},
        }
    },
}


# ---------------------------------------------------------------------------
# Video-source constants (shared across helpers)
# ---------------------------------------------------------------------------

VIDEO_SOURCES = [
    "heiliao", "cg91", "baoliao51", "douyin", "18mh", "rou", "dadaafa",
    "18j", "1mtif", "tikporn", "91porna", "91porn", "91rb", "badnews",
    "bdrq", "avgood", "705hs", "xxxtik", "affair", "attach", "dirtyship",
    "influencersgonewild", "missav",
]


# ---------------------------------------------------------------------------
# Helper: compute video_key
# ---------------------------------------------------------------------------

def compute_video_key(row):
    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    guid = row["guid"] or ""
    video_url = row["video_url"] or ""

    video_id_fields = [
        ("youtube_video_id", "youtube"),
        ("heiliao_video_id", "heiliao"),
        ("cg91_video_id", "cg91"),
        ("baoliao51_video_id", "baoliao51"),
        ("douyin_video_id", "douyin"),
        ("mh18_video_id", "18mh"),
        ("rou_video_id", "rou"),
        ("dadaafa_video_id", "dadaafa"),
        ("j18_video_id", "18j"),
        ("mtif_video_id", "1mtif"),
        ("tikporn_video_id", "tikporn"),
        ("porna91_video_id", "91porna"),
        ("porn91_video_id", "91porn"),
        ("rb91_video_id", "91rb"),
        ("badnews_video_id", "badnews"),
        ("bdrq_video_id", "bdrq"),
        ("avgood_video_id", "avgood"),
        ("hs705_video_id", "705hs"),
        ("xxxtik_post_uuid", "xxxtik"),
        ("affair_video_id", "affair"),
        ("attach_detail_id", "attach"),
        ("dirtyship_detail_id", "dirtyship"),
        ("influencersgonewild_detail_id", "influencersgonewild"),
        ("missav_video_id", "missav"),
    ]

    for field, prefix in video_id_fields:
        vid = metadata.get(field)
        if vid:
            return f"{prefix}:{vid}"

    guid_prefixes = [
        "heiliao:", "cg91:", "baoliao51:", "douyin:", "18mh:", "rou:",
        "dadaafa:", "18j:", "1mtif:", "tikporn:", "91porna:", "91porn:",
        "91rb:", "badnews:", "bdrq:", "avgood:", "705hs:", "xxxtik:",
        "affair:", "attach:", "dirtyship:", "influencersgonewild:", "missav:",
    ]
    for prefix in guid_prefixes:
        if guid.startswith(prefix):
            return guid

    if guid.startswith("yt:video:"):
        return "youtube:" + guid.replace("yt:video:", "")

    if video_url.startswith("https://video.twimg.com/"):
        return video_url.split("?")[0]

    return video_url or None


# ---------------------------------------------------------------------------
# Helper: compute target display name
# ---------------------------------------------------------------------------

def compute_target_display(source, kind, value):
    if source == "youtube":
        return f"youtube:{value}"
    elif source in VIDEO_SOURCES:
        return source
    elif kind == "keyword":
        return f"search:{value}"
    else:
        return value


# ---------------------------------------------------------------------------
# Helper: compute target link
# ---------------------------------------------------------------------------

def compute_target_link(source, kind, value):
    if source in VIDEO_SOURCES:
        return value
    elif source == "youtube":
        return f"https://www.youtube.com/channel/{value}"
    elif source == "twitter" and kind == "user":
        return f"https://x.com/{value}"
    return None


# ---------------------------------------------------------------------------
# Helper: datetime → ISO string (or None)
# ---------------------------------------------------------------------------

def dt_to_iso(val):
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def shard_sql_filter(column_name: str) -> str:
    return f"MOD(hashtext({column_name}::text)::bigint + 2147483648, %s::int) = %s::int"


def validate_shard_args(shard_index: int, shard_count: int) -> None:
    if shard_count <= 0:
        raise ValueError("shard_count must be a positive integer.")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard_index must be between 0 and shard_count - 1.")


def sync_scope_suffix(shard_index: int, shard_count: int) -> str:
    return f"shard_{shard_index}_of_{shard_count}"


def sync_meta_key(base_key: str, shard_index: int, shard_count: int) -> str:
    return f"{base_key}_{sync_scope_suffix(shard_index, shard_count)}"


def stable_shard(value: str, shard_count: int) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) % shard_count


def advisory_lock_key(lock_name: str) -> int:
    digest = hashlib.sha256(lock_name.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False) & 0x7FFFFFFFFFFFFFFF


def try_acquire_db_lock(conn: psycopg.Connection, lock_name: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (advisory_lock_key(lock_name),))
        row = cur.fetchone()
    return bool(row and row["locked"])


def release_db_lock(conn: psycopg.Connection, lock_name: str) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", (advisory_lock_key(lock_name),))


@contextmanager
def sync_run_lock(db_url: str, lock_name: str):
    conn = psycopg.connect(db_url, row_factory=dict_row)
    acquired = False
    try:
        acquired = try_acquire_db_lock(conn, lock_name)
        if acquired:
            print(f"Acquired sync lock: {lock_name}")
            yield True
        else:
            print(f"Another sync run already holds lock '{lock_name}', skipping.")
            yield False
    finally:
        if acquired:
            try:
                release_db_lock(conn, lock_name)
                print(f"Released sync lock: {lock_name}")
            except Exception as exc:
                print(f"WARNING: Failed to release sync lock '{lock_name}': {exc}", file=sys.stderr)
        conn.close()


# ---------------------------------------------------------------------------
# Helper: merge tags from item_tags_array + profile_tags
# ---------------------------------------------------------------------------

def merge_tags(item_tags_array, profile_tags_jsonb):
    tags = set()
    if item_tags_array:
        for t in item_tags_array:
            if t:
                tags.add(t)
    if profile_tags_jsonb:
        pt = profile_tags_jsonb
        if isinstance(pt, str):
            pt = json.loads(pt)
        if isinstance(pt, list):
            for t in pt:
                if t:
                    tags.add(str(t))
    return sorted(tags) if tags else []


def metadata_text(metadata, key: str, fallback):
    value = metadata.get(key)
    if isinstance(value, str):
        value = value.strip()
    return value if value not in (None, "") else fallback


def metadata_array(metadata, key: str, fallback):
    value = metadata.get(key)
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return parsed
    return fallback


# ---------------------------------------------------------------------------
# Helper: build OpenSearch document from a PG row
# ---------------------------------------------------------------------------

def build_document(row):
    metadata = row["metadata"] or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)

    video_url = row["video_url"] or ""
    images_raw = row["images"]
    if images_raw is None:
        images_list = []
    elif isinstance(images_raw, str):
        images_list = json.loads(images_raw)
    elif isinstance(images_raw, list):
        images_list = images_raw
    else:
        images_list = list(images_raw)

    playback_headers = row["playback_headers"]
    if isinstance(playback_headers, str):
        playback_headers = json.loads(playback_headers)

    source = row["source"]
    kind = row["kind"]
    target_value = row["target_value"]

    tags = [tag.strip().lower() for tag in merge_tags(row["item_tags_array"], row["profile_tags"]) if tag.strip()]
    category = row["category"].strip().lower() if row["category"] else None
    score = float(row["score"])

    doc = {
        "id": str(row["id"]),
        "target_id": str(row["target_id"]) if row["target_id"] else None,
        "guid": row["guid"],
        "video_url": row["video_url"],
        "video_key": compute_video_key(row),
        "playback_headers": playback_headers,
        "cover_url": row["cover_url"],
        "title": metadata_text(metadata, "item_title", row["title"]),
        "caption": metadata_text(metadata, "item_content", row["caption"]),
        "content": metadata_text(metadata, "item_content", row["content"]),
        "raw_content": row.get("raw_content"),
        "translated_content": row.get("translated_content"),
        "author": metadata_text(metadata, "item_author", row["author"]),
        "fullname": metadata_text(metadata, "item_fullname", row["fullname"]),
        "display_author": metadata_text(metadata, "item_display_author", row["display_author"]),
        "display_handle": metadata_text(metadata, "item_display_handle", row["display_handle"]),
        "author_profile_url": metadata_text(metadata, "item_author_profile_url", row["author_profile_url"]),
        "author_profile_platform": metadata_text(metadata, "item_author_profile_platform", row["author_profile_platform"]),
        "x_url": metadata_text(metadata, "item_x_url", row["x_url"]),
        "link": metadata_text(metadata, "item_link", row["link"]),
        "published_at": dt_to_iso(row["published_at"]),
        "stored_at": dt_to_iso(row["stored_at"]),
        "updated_at": dt_to_iso(row["updated_at"]),
        "sort_at": dt_to_iso(row["published_at"] or row["stored_at"]),
        "source": source,
        "target": compute_target_display(source, kind, target_value),
        "target_link": compute_target_link(source, kind, target_value),
        "kind": kind,
        "category": category,
        "tags": tags,
        "expires_at": dt_to_iso(row["expires_at"]),
        "video_url_expires_at": dt_to_iso(row["video_url_expires_at"]),
        "is_public_pool": bool(row["is_public_pool"]),
        "is_retweet": bool(row["is_retweet"]) if row["is_retweet"] is not None else False,
        "is_sensitive": bool(row["is_sensitive"]),
        "has_video": bool(video_url),
        "score": score,
        "quality_score": max(score, 0.0),
        "impressions": int(row["impressions"]),
        "plays": int(row["plays"]),
        "finishes": int(row["finishes"]),
        "likes": int(row["likes"]),
        "dislikes": int(row["dislikes"]),
        "skips": int(row["skips"]),
        "shares": int(row["shares"]),
        "images": metadata_array(metadata, "item_images", images_list),
    }
    return doc


# ---------------------------------------------------------------------------
# OpenSearch client factory
# ---------------------------------------------------------------------------

def create_os_client(opensearch_url: str) -> OpenSearch:
    parsed = urlparse(opensearch_url)
    host = parsed.hostname
    port = parsed.port or 9200
    scheme = parsed.scheme or "https"
    username = parsed.username
    password = parsed.password

    client = OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=(username, password) if username else None,
        use_ssl=(scheme == "https"),
        verify_certs=False,
        ssl_show_warn=False,
        timeout=180,
    )
    return client


def bulk_with_retry(client: OpenSearch, actions: list[dict], *, retries: int = 3, retry_delay_seconds: float = 2.0):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return helpers.bulk(client, actions, raise_on_error=False)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                raise
            delay = retry_delay_seconds * attempt
            print(f"  Bulk request failed on attempt {attempt}/{retries}: {exc}. Retrying in {delay:.1f}s...")
            time.sleep(delay)
    raise last_error  # pragma: no cover


# ---------------------------------------------------------------------------
# Ensure indices exist
# ---------------------------------------------------------------------------

def ensure_indices(client: OpenSearch):
    if not client.indices.exists(index=X2_ITEMS_INDEX):
        print(f"Creating index '{X2_ITEMS_INDEX}'...")
        client.indices.create(index=X2_ITEMS_INDEX, body=X2_ITEMS_MAPPING)
        print(f"Index '{X2_ITEMS_INDEX}' created.")
    else:
        print(f"Index '{X2_ITEMS_INDEX}' already exists.")

    if not client.indices.exists(index=X2_SYNC_META_INDEX):
        print(f"Creating index '{X2_SYNC_META_INDEX}'...")
        client.indices.create(index=X2_SYNC_META_INDEX, body=X2_SYNC_META_MAPPING)
        print(f"Index '{X2_SYNC_META_INDEX}' created.")
    else:
        print(f"Index '{X2_SYNC_META_INDEX}' already exists.")

    # Existing indices keep their original mappings, so these are best-effort
    # additions for deployments created before the feed engine fields existed.
    try:
        client.indices.put_mapping(
            index=X2_ITEMS_INDEX,
            body={
                "properties": {
                    "sort_at": {"type": "date"},
                    "quality_score": {"type": "float"},
                }
            },
        )
    except Exception as exc:
        print(f"WARNING: Could not update '{X2_ITEMS_INDEX}' mapping: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Last-sync timestamp helpers
# ---------------------------------------------------------------------------

def get_last_sync_timestamp(client: OpenSearch, meta_key: str = "last_sync"):
    checkpoint = get_last_sync_checkpoint(client, meta_key)
    return checkpoint[0]


def get_last_sync_checkpoint(client: OpenSearch, meta_key: str = "last_sync"):
    try:
        resp = client.get(index=X2_SYNC_META_INDEX, id=meta_key)
        source = resp["_source"]
        return source.get("timestamp"), source.get("item_id")
    except Exception:
        return None, None


def set_last_sync_timestamp(client: OpenSearch, timestamp_iso: str, meta_key: str = "last_sync", item_id: str | None = None):
    now_iso = datetime.now(timezone.utc).isoformat()
    body = {"timestamp": timestamp_iso, "synced_at": now_iso}
    if item_id:
        body["item_id"] = item_id
    client.index(
        index=X2_SYNC_META_INDEX,
        id=meta_key,
        body=body,
    )


def reset_sync_checkpoint(client: OpenSearch, meta_key: str):
    try:
        client.delete(index=X2_SYNC_META_INDEX, id=meta_key)
        print(f"Deleted checkpoint: {meta_key}")
    except Exception:
        print(f"Checkpoint not present: {meta_key}")


# ---------------------------------------------------------------------------
# Main data sync SQL
# ---------------------------------------------------------------------------

BASE_SYNC_SQL = """\
SELECT
  i.id,
  i.target_id,
  i.guid,
  i.video_url,
  i.metadata,
  CASE WHEN jsonb_typeof(i.metadata->'playback_headers') = 'object' THEN i.metadata->'playback_headers' ELSE NULL END as playback_headers,
  COALESCE(i.metadata->>'video_poster_url', i.metadata->'item_images'->>0, i.images->>0) as cover_url,
  COALESCE(NULLIF(i.metadata->>'item_title', ''), i.title) as title,
  COALESCE(NULLIF(i.metadata->>'item_content', ''), i.content, NULLIF(i.metadata->>'item_title', ''), i.title) as caption,
  COALESCE(NULLIF(i.metadata->>'item_content', ''), i.content) as content,
  NULL::text as raw_content,
  NULL::text as translated_content,
  COALESCE(NULLIF(i.metadata->>'item_author', ''), i.author) as author,
  COALESCE(NULLIF(i.metadata->>'item_fullname', ''), i.fullname) as fullname,
  COALESCE(NULLIF(i.metadata->>'item_display_author', ''), i.display_author) as display_author,
  COALESCE(NULLIF(i.metadata->>'item_display_handle', ''), i.display_handle) as display_handle,
  COALESCE(NULLIF(i.metadata->>'item_author_profile_url', ''), i.author_profile_url) as author_profile_url,
  COALESCE(NULLIF(i.metadata->>'item_author_profile_platform', ''), i.author_profile_platform) as author_profile_platform,
  COALESCE(NULLIF(i.metadata->>'item_x_url', ''), i.x_url) as x_url,
  COALESCE(NULLIF(i.metadata->>'item_link', ''), i.link) as link,
  i.published_at,
  i.stored_at,
  i.updated_at,
  t.source,
  t.kind,
  t.value as target_value,
  tp.category,
  COALESCE(tp.is_public_pool, FALSE) as is_public_pool,
  COALESCE(cat.is_sensitive, FALSE) as is_sensitive,
  COALESCE(i.metadata->'item_images', i.images) as images,
  i.expires_at,
  i.video_url_expires_at,
  i.is_retweet,
  COALESCE(vs.score, 0) as score,
  COALESCE(vs.impressions, 0) as impressions,
  COALESCE(vs.plays, 0) as plays,
  COALESCE(vs.finishes, 0) as finishes,
  COALESCE(vs.likes, 0) as likes,
  COALESCE(vs.dislikes, 0) as dislikes,
  COALESCE(vs.skips, 0) as skips,
  COALESCE(vs.shares, 0) as shares,
  COALESCE(
    (SELECT ARRAY_AGG(DISTINCT tag.name ORDER BY tag.name)
     FROM item_tags it
     JOIN tags tag ON tag.id = it.tag_id
     WHERE it.item_id = i.id
    ), ARRAY[]::text[]
  ) as item_tags_array,
  COALESCE(tp.tags, '[]'::jsonb) as profile_tags
FROM items i
JOIN targets t ON t.id = i.target_id
LEFT JOIN target_profiles tp ON tp.target_id = t.id
LEFT JOIN categories cat ON cat.slug = tp.category
LEFT JOIN video_stats vs ON vs.item_id = i.id
WHERE i.expires_at > NOW()
  AND (
    t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
    OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
  )"""

STATS_SQL = """\
SELECT i.id, vs.updated_at, vs.score, vs.impressions, vs.plays, vs.finishes,
       vs.likes, vs.dislikes, vs.skips, vs.shares
FROM items i
JOIN targets t ON t.id = i.target_id
JOIN video_stats vs ON vs.item_id = i.id
WHERE (vs.updated_at > %s OR (vs.updated_at = %s AND i.id::text > %s))
  AND i.video_url IS NOT NULL
  AND i.video_url <> ''
  AND i.expires_at > NOW()
  AND (
    t.source NOT IN ('youtube', 'heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav')
    OR i.video_url_expires_at > NOW() + INTERVAL '10 minutes'
  )"""


# ---------------------------------------------------------------------------
# Sync: items (full or incremental)
# ---------------------------------------------------------------------------

def sync_items(os_client: OpenSearch, db_url: str, full: bool, limit: int | None, shard_index: int, shard_count: int):
    validate_shard_args(shard_index, shard_count)
    meta_key = sync_meta_key(ITEMS_SYNC_META_KEY, shard_index, shard_count)
    last_sync_ts = None
    last_sync_item_id = None
    if not full:
        last_sync_ts, last_sync_item_id = get_last_sync_checkpoint(os_client, meta_key=meta_key)
        if last_sync_ts:
            print(f"Incremental sync from: {last_sync_ts}")
        else:
            print("No previous sync timestamp found – performing full sync.")

    sql = BASE_SYNC_SQL
    params: list = []

    if not full and last_sync_ts:
        sql += "\n  AND (i.updated_at > %s OR (i.updated_at = %s AND i.id::text > %s))"
        params.extend([last_sync_ts, last_sync_ts, last_sync_item_id or ""])

    sql += f"\n  AND ({shard_sql_filter('i.id')})"
    params.extend([shard_count, shard_index])

    sql += "\nORDER BY i.updated_at ASC, i.id ASC"

    if limit:
        sql += "\nLIMIT %s"
        params.append(limit)

    print("Querying PostgreSQL...")
    t0 = time.time()

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row)
    except Exception as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)

    # Stream rows from PostgreSQL in server-side batches so full syncs do not
    # materialize the entire active items set in memory.
    total_synced = 0
    total_errors = 0
    last_updated_at = None
    last_item_id = None
    batch_size = 200
    t_sync = time.time()
    total_rows = 0
    batch_number = 0

    with conn:
        with conn.cursor(name=f"os_sync_items_{shard_index}_{shard_count}", row_factory=dict_row) as cur:
            cur.itersize = batch_size
            cur.execute(sql, params or None)

            while True:
                batch = cur.fetchmany(batch_size)
                if not batch:
                    break

                if total_rows == 0:
                    query_time = time.time() - t0
                    print(f"Fetched first batch from PG in {query_time:.1f}s")

                total_rows += len(batch)
                batch_number += 1
                actions = []
                for row in batch:
                    doc = build_document(row)
                    actions.append({
                        "_op_type": "index",
                        "_index": X2_ITEMS_INDEX,
                        "_id": str(row["id"]),
                        "_source": doc,
                    })
                    if row["updated_at"]:
                        last_updated_at = row["updated_at"]
                        last_item_id = str(row["id"])

                success, errors = bulk_with_retry(os_client, actions)
                total_synced += success
                total_errors += len(errors)
                if errors:
                    print(f"  Batch {batch_number}: {success} ok, {len(errors)} errors")
                    for err in errors[:5]:
                        print(f"    {err}")
                else:
                    print(f"  Batch {batch_number}: {success} indexed")

    total_fetch_time = time.time() - t0
    print(f"Scanned {total_rows} rows from PG in {total_fetch_time:.1f}s")

    if total_rows == 0:
        print("Nothing to sync.")
        return

    sync_time = time.time() - t_sync

    # Persist last sync timestamp
    if total_errors == 0 and last_updated_at:
        ts_iso = dt_to_iso(last_updated_at)
        set_last_sync_timestamp(os_client, ts_iso, meta_key=meta_key, item_id=last_item_id)
        print(f"Last sync timestamp saved: {ts_iso}")
    elif total_errors:
        print("Item sync checkpoint not advanced because some OpenSearch writes failed.")

    total_time = time.time() - t0
    print(f"\nSync complete: {total_synced} items indexed in {sync_time:.1f}s (total {total_time:.1f}s)")


# ---------------------------------------------------------------------------
# Sync: stats only
# ---------------------------------------------------------------------------

def sync_stats(os_client: OpenSearch, db_url: str, limit: int | None, shard_index: int, shard_count: int):
    validate_shard_args(shard_index, shard_count)
    meta_key = sync_meta_key(STATS_SYNC_META_KEY, shard_index, shard_count)
    last_sync_ts, last_sync_item_id = get_last_sync_checkpoint(os_client, meta_key=meta_key)
    if last_sync_ts:
        print(f"Stats incremental from: {last_sync_ts}")
    else:
        print("No previous stats sync timestamp – syncing all stats.")
        last_sync_ts = "1970-01-01T00:00:00+00:00"

    sql = STATS_SQL
    params: list = [last_sync_ts, last_sync_ts, last_sync_item_id or ""]
    sql += f"\n  AND ({shard_sql_filter('i.id')})"
    params.extend([shard_count, shard_index])
    sql += "\nORDER BY vs.updated_at ASC, i.id ASC"
    if limit:
        sql += "\nLIMIT %s"
        params.append(limit)

    print("Querying PostgreSQL for video_stats...")
    t0 = time.time()

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row)
    except Exception as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    query_time = time.time() - t0
    print(f"Fetched {len(rows)} stats rows from PG in {query_time:.1f}s")

    if not rows:
        print("No stats to update.")
        return

    total_updated = 0
    total_errors = 0
    batch_size = 200
    t_sync = time.time()
    last_updated_at = None
    last_item_id = None

    for batch_start in range(0, len(rows), batch_size):
        batch = rows[batch_start : batch_start + batch_size]
        actions = []
        for row in batch:
            actions.append({
                "_op_type": "update",
                "_index": X2_ITEMS_INDEX,
                "_id": str(row["id"]),
                "doc": {
                    "score": float(row["score"]) if row["score"] is not None else 0.0,
                    "impressions": int(row["impressions"]) if row["impressions"] is not None else 0,
                    "plays": int(row["plays"]) if row["plays"] is not None else 0,
                    "finishes": int(row["finishes"]) if row["finishes"] is not None else 0,
                    "likes": int(row["likes"]) if row["likes"] is not None else 0,
                    "dislikes": int(row["dislikes"]) if row["dislikes"] is not None else 0,
                    "skips": int(row["skips"]) if row["skips"] is not None else 0,
                    "shares": int(row["shares"]) if row["shares"] is not None else 0,
                    "quality_score": max(float(row["score"]) if row["score"] is not None else 0.0, 0.0),
                },
            })
            if row["updated_at"]:
                last_updated_at = row["updated_at"]
                last_item_id = str(row["id"])

        success, errors = bulk_with_retry(os_client, actions)
        total_updated += success
        total_errors += len(errors)
        if errors:
            print(f"  Batch {batch_start // batch_size + 1}: {success} ok, {len(errors)} errors")
            for err in errors[:5]:
                print(f"    {err}")
        else:
            print(f"  Batch {batch_start // batch_size + 1}: {success} updated")

    sync_time = time.time() - t_sync

    if total_errors == 0 and last_updated_at:
        ts_iso = dt_to_iso(last_updated_at)
        set_last_sync_timestamp(os_client, ts_iso, meta_key=meta_key, item_id=last_item_id)
        print(f"Stats sync timestamp saved: {ts_iso}")
    elif total_errors:
        print("Stats sync checkpoint not advanced because some OpenSearch documents were missing.")

    total_time = time.time() - t0
    print(f"\nStats sync complete: {total_updated} docs updated in {sync_time:.1f}s (total {total_time:.1f}s)")


# ---------------------------------------------------------------------------
# Sync: prune deleted PostgreSQL rows from OpenSearch
# ---------------------------------------------------------------------------

def prune_deleted(os_client: OpenSearch, db_url: str, limit: int | None, shard_index: int, shard_count: int):
    validate_shard_args(shard_index, shard_count)
    print("Scanning OpenSearch docs for deleted PostgreSQL items...")
    t0 = time.time()

    try:
        conn = psycopg.connect(db_url, row_factory=dict_row)
    except Exception as exc:
        print(f"ERROR: Cannot connect to PostgreSQL: {exc}", file=sys.stderr)
        sys.exit(1)

    scanned = 0
    deleted = 0
    batch_size = 500
    pending_ids: list[str] = []

    def flush(ids: list[str]) -> int:
        if not ids:
            return 0
        with conn.cursor() as cur:
            cur.execute("SELECT id::text AS id FROM items WHERE id = ANY(%s::uuid[])", (ids,))
            existing = {row["id"] for row in cur.fetchall()}

        missing = [item_id for item_id in ids if item_id not in existing]
        if not missing:
            return 0

        actions = [
            {
                "_op_type": "delete",
                "_index": X2_ITEMS_INDEX,
                "_id": item_id,
            }
            for item_id in missing
        ]
        success, errors = bulk_with_retry(os_client, actions)
        if errors:
            print(f"  Prune batch: {success} deleted, {len(errors)} errors")
            for err in errors[:5]:
                print(f"    {err}")
        return success

    with conn:
        for hit in helpers.scan(
            os_client,
            index=X2_ITEMS_INDEX,
            query={"query": {"match_all": {}}, "_source": False},
            size=batch_size,
            preserve_order=False,
        ):
            item_id = str(hit["_id"])
            if stable_shard(item_id, shard_count) != shard_index:
                continue
            pending_ids.append(item_id)
            scanned += 1
            if len(pending_ids) >= batch_size:
                deleted += flush(pending_ids)
                pending_ids = []
            if limit and scanned >= limit:
                break

        deleted += flush(pending_ids)

    total_time = time.time() - t0
    print(f"\nPrune complete: scanned {scanned} docs, deleted {deleted} stale docs in {total_time:.1f}s")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync PostgreSQL items to OpenSearch")
    parser.add_argument("--full", action="store_true", help="Full re-sync (ignore last sync timestamp)")
    parser.add_argument("--stats-only", action="store_true", help="Only update video_stats scores")
    parser.add_argument("--prune-deleted", action="store_true", help="Delete OpenSearch docs whose PostgreSQL item no longer exists")
    parser.add_argument("--reset-checkpoint", action="store_true", help="Reset the item sync checkpoint before syncing")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of items to sync")
    parser.add_argument("--shard-index", type=int, default=int(os.environ.get("OPENSEARCH_SYNC_SHARD_INDEX", "0")), help="Shard index for parallel sync workers")
    parser.add_argument("--shard-count", type=int, default=int(os.environ.get("OPENSEARCH_SYNC_SHARD_COUNT", "1")), help="Total number of sync shards")
    args = parser.parse_args()

    db_url = os.environ.get("DATABASE_URL")
    opensearch_url = os.environ.get("OPENSEARCH_URL")

    if not db_url:
        print("ERROR: DATABASE_URL environment variable is required.", file=sys.stderr)
        sys.exit(1)
    if not opensearch_url:
        print("ERROR: OPENSEARCH_URL environment variable is required.", file=sys.stderr)
        sys.exit(1)
    try:
        validate_shard_args(args.shard_index, args.shard_count)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    # Create OpenSearch client
    os_client = create_os_client(opensearch_url)

    # Test OpenSearch connectivity
    try:
        info = os_client.info()
        print(f"Connected to OpenSearch: {info['version']['distribution']} {info['version']['number']}")
    except Exception as exc:
        print(f"ERROR: Cannot connect to OpenSearch: {exc}", file=sys.stderr)
        sys.exit(1)

    # Ensure indices
    ensure_indices(os_client)

    if args.reset_checkpoint:
        reset_sync_checkpoint(os_client, sync_meta_key(ITEMS_SYNC_META_KEY, args.shard_index, args.shard_count))
        if args.stats_only:
            reset_sync_checkpoint(os_client, sync_meta_key(STATS_SYNC_META_KEY, args.shard_index, args.shard_count))

    if args.prune_deleted:
        lock_name = f"os-prune-{sync_scope_suffix(args.shard_index, args.shard_count)}"
        with sync_run_lock(db_url, lock_name) as acquired:
            if acquired:
                prune_deleted(os_client, db_url, args.limit, args.shard_index, args.shard_count)
    elif args.stats_only:
        lock_name = f"os-stats-{sync_scope_suffix(args.shard_index, args.shard_count)}"
        with sync_run_lock(db_url, lock_name) as acquired:
            if acquired:
                sync_stats(os_client, db_url, args.limit, args.shard_index, args.shard_count)
    else:
        lock_name = f"os-items-{sync_scope_suffix(args.shard_index, args.shard_count)}"
        with sync_run_lock(db_url, lock_name) as acquired:
            if acquired:
                sync_items(os_client, db_url, args.full, args.limit, args.shard_index, args.shard_count)


if __name__ == "__main__":
    main()
