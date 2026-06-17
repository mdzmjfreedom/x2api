from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from psycopg import connect
from psycopg.rows import dict_row

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from collector.opensearch_items import fetch_documents  # noqa: E402

try:
    from collector.redis_state import RedisLock, namespaced_key, redis_client, release_lock_safely
except Exception:  # pragma: no cover - Redis is optional for local dry-runs
    RedisLock = None
    namespaced_key = None
    redis_client = None
    release_lock_safely = None


POSITIVE_EVENT_WEIGHTS = {
    "impression": 0.15,
    "play": 1.0,
    "finish": 6.0,
    "like": 9.0,
    "share": 8.0,
}

NEGATIVE_EVENT_WEIGHTS = {
    "skip": -4.0,
    "dislike": -12.0,
}

HALF_LIFE_HOURS = {
    "short_1d": 12.0,
    "short_3d": 36.0,
    "long_30d": 10.0 * 24.0,
    "long_90d": 30.0 * 24.0,
    "negative_30d": 14.0 * 24.0,
}

MAX_FEATURES = {
    "categories": 20,
    "tags": 60,
    "sources": 20,
    "targets": 80,
    "authors": 80,
}

PROFILE_SQL = """
SELECT
  fe.client_id::text AS client_id,
  fe.item_id::text AS item_id,
  fe.event_type,
  fe.watch_ms,
  fe.created_at,
  t.source,
  CASE
    WHEN t.source = 'youtube' THEN 'youtube:' || t.value
    WHEN t.source IN ('heiliao', 'cg91', 'baoliao51', 'douyin', '18mh', 'rou', 'dadaafa', '18j', '1mtif', 'tikporn', '91porna', '91porn', '91rb', 'badnews', 'bdrq', 'avgood', '705hs', 'xxxtik', 'affair', 'attach', 'dirtyship', 'influencersgonewild', 'missav') THEN t.source
    WHEN t.kind = 'keyword' THEN 'search:' || t.value
    ELSE t.value
  END AS target,
  LOWER(COALESCE(tp.category, '')) AS category,
  COALESCE((
    SELECT ARRAY_AGG(DISTINCT LOWER(tag_name) ORDER BY LOWER(tag_name))
    FROM (
      SELECT tag.name AS tag_name
      FROM item_tags it
      INNER JOIN tags tag ON tag.id = it.tag_id
      WHERE it.item_id = i.id
      UNION
      SELECT profile_tag.name AS tag_name
      FROM jsonb_array_elements_text(COALESCE(tp.tags, '[]'::jsonb)) AS profile_tag(name)
    ) tag_values
    WHERE NULLIF(BTRIM(tag_name), '') IS NOT NULL
  ), ARRAY[]::text[]) AS tags
FROM feed_events fe
INNER JOIN items i ON i.id = fe.item_id
INNER JOIN targets t ON t.id = i.target_id
LEFT JOIN target_profiles tp ON tp.target_id = t.id
WHERE fe.created_at >= NOW() - (%s || ' days')::interval
  AND fe.event_type IN ('impression', 'play', 'finish', 'like', 'share', 'skip', 'dislike')
  AND (%s::int <= 1 OR MOD(hashtext(fe.client_id::text)::bigint + 2147483648, %s::int) = %s::int)
ORDER BY fe.client_id, fe.created_at ASC
"""

UPSERT_SQL = """
INSERT INTO user_feed_profiles (
  client_id,
  short_profile,
  long_profile,
  negative_profile,
  source_profile,
  target_profile,
  author_profile,
  explore_ratio,
  confidence,
  event_count,
  last_event_at,
  generated_at
)
VALUES (
  %(client_id)s,
  %(short_profile)s::jsonb,
  %(long_profile)s::jsonb,
  %(negative_profile)s::jsonb,
  %(source_profile)s::jsonb,
  %(target_profile)s::jsonb,
  %(author_profile)s::jsonb,
  %(explore_ratio)s,
  %(confidence)s,
  %(event_count)s,
  %(last_event_at)s,
  NOW()
)
ON CONFLICT (client_id) DO UPDATE SET
  short_profile = EXCLUDED.short_profile,
  long_profile = EXCLUDED.long_profile,
  negative_profile = EXCLUDED.negative_profile,
  source_profile = EXCLUDED.source_profile,
  target_profile = EXCLUDED.target_profile,
  author_profile = EXCLUDED.author_profile,
  explore_ratio = EXCLUDED.explore_ratio,
  confidence = EXCLUDED.confidence,
  event_count = EXCLUDED.event_count,
  last_event_at = EXCLUDED.last_event_at,
  generated_at = NOW(),
  updated_at = NOW()
"""


@dataclass
class ClientProfileState:
    client_id: str
    event_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    last_event_at: datetime | None = None
    short_1d: dict[str, defaultdict[str, float]] | None = None
    short_3d: dict[str, defaultdict[str, float]] | None = None
    long_30d: dict[str, defaultdict[str, float]] | None = None
    long_90d: dict[str, defaultdict[str, float]] | None = None
    negative_30d: dict[str, defaultdict[str, float]] | None = None

    def __post_init__(self) -> None:
        self.short_1d = feature_buckets()
        self.short_3d = feature_buckets()
        self.long_30d = feature_buckets()
        self.long_90d = feature_buckets()
        self.negative_30d = feature_buckets()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build persisted feed profiles from feed_events.")
    parser.add_argument("--lookback-days", type=int, default=90, help="Maximum event history window.")
    parser.add_argument("--limit-clients", type=int, default=None, help="Optional number of clients to update.")
    parser.add_argument("--shard-index", type=int, default=int(os.environ.get("PROFILE_SHARD_INDEX", "0")), help="Client hash shard index.")
    parser.add_argument("--shard-count", type=int, default=int(os.environ.get("PROFILE_SHARD_COUNT", "1")), help="Total client hash shards.")
    parser.add_argument("--lock-ttl-seconds", type=int, default=900, help="Redis lock TTL for one profile update run.")
    parser.add_argument("--lock-bucket-minutes", type=int, default=60, help="Current-time bucket used to derive the Redis lock name.")
    parser.add_argument("--no-redis-lock", action="store_true", help="Skip the optional Redis distributed lock.")
    parser.add_argument("--dry-run", action="store_true", help="Compute profiles but do not write them.")
    return parser.parse_args()


def feature_buckets() -> dict[str, defaultdict[str, float]]:
    return {
        "categories": defaultdict(float),
        "tags": defaultdict(float),
        "sources": defaultdict(float),
        "targets": defaultdict(float),
        "authors": defaultdict(float),
    }


def normalize_value(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def add_feature(bucket: dict[str, defaultdict[str, float]], feature_type: str, value: Any, weight: float) -> None:
    normalized = normalize_value(value)
    if normalized:
        bucket[feature_type][normalized] += weight


def event_weight(row: dict[str, Any]) -> float:
    event_type = row["event_type"]
    base = POSITIVE_EVENT_WEIGHTS.get(event_type, NEGATIVE_EVENT_WEIGHTS.get(event_type, 0.0))
    watch_ms = row.get("watch_ms")
    if event_type in POSITIVE_EVENT_WEIGHTS and isinstance(watch_ms, int) and watch_ms > 0:
        base += min(watch_ms / 30_000.0, 3.0)
    if event_type == "skip" and isinstance(watch_ms, int) and watch_ms > 15_000:
        base *= 0.5
    return base


def decay_factor(created_at: datetime, now: datetime, half_life_hours: float) -> float:
    age_hours = max((now - created_at).total_seconds() / 3600.0, 0.0)
    return math.pow(0.5, age_hours / half_life_hours)


def add_row_to_bucket(bucket: dict[str, defaultdict[str, float]], row: dict[str, Any], weight: float) -> None:
    add_feature(bucket, "categories", row.get("category"), weight)
    for tag in row.get("tags") or []:
        add_feature(bucket, "tags", tag, weight)
    add_feature(bucket, "sources", row.get("source"), weight)
    add_feature(bucket, "targets", row.get("target"), weight)
    add_feature(bucket, "authors", row.get("author") or row.get("fullname"), weight)


def top_features(values: defaultdict[str, float], limit: int) -> list[dict[str, float | str]]:
    ranked = sorted(values.items(), key=lambda item: (-item[1], item[0]))
    return [{"value": value, "weight": round(score, 4)} for value, score in ranked[:limit] if score > 0]


def serialize_bucket(bucket: dict[str, defaultdict[str, float]]) -> dict[str, Any]:
    return {
        key: top_features(values, MAX_FEATURES[key])
        for key, values in bucket.items()
    }


def feature_total(bucket: dict[str, defaultdict[str, float]]) -> float:
    return sum(sum(values.values()) for values in bucket.values())


def confidence_for(state: ClientProfileState) -> float:
    volume = min(math.log1p(state.event_count) / math.log(80), 1.0)
    positive_bias = state.positive_count / max(state.positive_count + state.negative_count, 1)
    return round(max(0.05, min(volume * (0.65 + 0.35 * positive_bias), 1.0)), 3)


def explore_ratio_for(state: ClientProfileState) -> float:
    confidence = confidence_for(state)
    negative_rate = state.negative_count / max(state.positive_count + state.negative_count, 1)
    ratio = 0.28 - (0.12 * confidence) + (0.08 * negative_rate)
    return round(max(0.08, min(ratio, 0.35)), 3)


def profile_payload(state: ClientProfileState) -> dict[str, Any]:
    assert state.short_1d is not None
    assert state.short_3d is not None
    assert state.long_30d is not None
    assert state.long_90d is not None
    assert state.negative_30d is not None

    short_profile = {
        "windows": {
            "1d": serialize_bucket(state.short_1d),
            "3d": serialize_bucket(state.short_3d),
        },
        "totalWeight": round(feature_total(state.short_1d) + feature_total(state.short_3d), 4),
    }
    long_profile = {
        "windows": {
            "30d": serialize_bucket(state.long_30d),
            "90d": serialize_bucket(state.long_90d),
        },
        "totalWeight": round(feature_total(state.long_30d) + feature_total(state.long_90d), 4),
    }
    negative_profile = {
        "windows": {
            "30d": serialize_bucket(state.negative_30d),
        },
        "totalWeight": round(feature_total(state.negative_30d), 4),
    }

    source_profile = {
        "positive": top_features(state.long_90d["sources"], MAX_FEATURES["sources"]),
        "negative": top_features(state.negative_30d["sources"], MAX_FEATURES["sources"]),
    }
    target_profile = {
        "positive": top_features(state.long_90d["targets"], MAX_FEATURES["targets"]),
        "negative": top_features(state.negative_30d["targets"], MAX_FEATURES["targets"]),
    }
    author_profile = {
        "positive": top_features(state.long_90d["authors"], MAX_FEATURES["authors"]),
        "negative": top_features(state.negative_30d["authors"], MAX_FEATURES["authors"]),
    }

    return {
        "client_id": state.client_id,
        "short_profile": json.dumps(short_profile, ensure_ascii=False),
        "long_profile": json.dumps(long_profile, ensure_ascii=False),
        "negative_profile": json.dumps(negative_profile, ensure_ascii=False),
        "source_profile": json.dumps(source_profile, ensure_ascii=False),
        "target_profile": json.dumps(target_profile, ensure_ascii=False),
        "author_profile": json.dumps(author_profile, ensure_ascii=False),
        "explore_ratio": explore_ratio_for(state),
        "confidence": confidence_for(state),
        "event_count": state.event_count,
        "last_event_at": state.last_event_at,
    }


def build_profiles(rows: list[dict[str, Any]], now: datetime) -> dict[str, ClientProfileState]:
    documents = fetch_documents(row["item_id"] for row in rows)
    profiles: dict[str, ClientProfileState] = {}
    for row in rows:
        source = documents.get(str(row["item_id"])) or {}
        materialized_row = {
            **row,
            "author": source.get("author"),
            "fullname": source.get("fullname"),
        }
        client_id = row["client_id"]
        state = profiles.setdefault(client_id, ClientProfileState(client_id=client_id))
        weight = event_weight(materialized_row)
        if weight == 0:
            continue

        created_at = row["created_at"]
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)

        state.event_count += 1
        state.last_event_at = max(state.last_event_at, created_at) if state.last_event_at else created_at

        age_days = (now - created_at).total_seconds() / 86400.0

        if weight > 0:
            state.positive_count += 1
            if age_days <= 1:
                add_row_to_bucket(state.short_1d, materialized_row, weight * decay_factor(created_at, now, HALF_LIFE_HOURS["short_1d"]))
            if age_days <= 3:
                add_row_to_bucket(state.short_3d, materialized_row, weight * decay_factor(created_at, now, HALF_LIFE_HOURS["short_3d"]))
            if age_days <= 30:
                add_row_to_bucket(state.long_30d, materialized_row, weight * decay_factor(created_at, now, HALF_LIFE_HOURS["long_30d"]))
            add_row_to_bucket(state.long_90d, materialized_row, weight * decay_factor(created_at, now, HALF_LIFE_HOURS["long_90d"]))
        else:
            state.negative_count += 1
            if age_days <= 30:
                add_row_to_bucket(state.negative_30d, materialized_row, abs(weight) * decay_factor(created_at, now, HALF_LIFE_HOURS["negative_30d"]))

    return profiles


def profile_lock_name(now: datetime, bucket_minutes: int) -> str:
    bucket_seconds = max(bucket_minutes, 1) * 60
    bucket = int(now.timestamp()) // bucket_seconds
    digest = hashlib.sha256(f"user-feed-profiles:{bucket}".encode("utf-8")).hexdigest()[:12]
    return f"user-feed-profiles-{digest}"


@contextmanager
def profile_update_lock(lock_name: str, ttl_seconds: int):
    if RedisLock is None or namespaced_key is None or redis_client is None or release_lock_safely is None:
        yield {"used_redis_lock": False, "skipped": False}
        return

    client = redis_client()
    if client is None:
        yield {"used_redis_lock": False, "skipped": False}
        return

    max_writers = max(1, int(os.environ.get("DB_LOCK_MAX_WRITERS", "1")))
    slot_lock = None
    source_lock = None
    source_name = lock_name.strip().lower() or "user-feed-profiles"
    ttl = max(ttl_seconds, 60)

    for slot in range(max_writers):
        candidate = RedisLock(client, namespaced_key("db-writer-slot", str(slot)), ttl_seconds=ttl)
        if not candidate.acquire():
            continue

        candidate_source_lock = RedisLock(client, namespaced_key("source-lock", source_name), ttl_seconds=ttl)
        if candidate_source_lock.acquire():
            slot_lock = candidate
            source_lock = candidate_source_lock
            print(f"[redis-lock] acquired source={source_name} slot={slot}")
            break

        release_lock_safely(candidate, source_name=source_name, lock_name=f"slot-{slot}")

    if slot_lock is None or source_lock is None:
        print(f"[redis-lock] skipped source={source_name} reason=locked")
        yield {"used_redis_lock": True, "skipped": True}
        return

    try:
        yield {"used_redis_lock": True, "skipped": False}
    finally:
        release_lock_safely(source_lock, source_name=source_name, lock_name="source")
        release_lock_safely(slot_lock, source_name=source_name, lock_name="slot")


def main() -> int:
    args = parse_args()
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL environment variable.")

    lookback_days = max(1, min(args.lookback_days, 180))
    shard_count = max(args.shard_count, 1)
    shard_index = max(args.shard_index, 0)
    if shard_index >= shard_count:
        raise ValueError("shard-index must be between 0 and shard-count - 1.")

    run_started_at = datetime.now(timezone.utc)
    os.environ.setdefault("DB_LOCK_MAX_WRITERS", "1")
    lock_ttl_seconds = max(args.lock_ttl_seconds, 60)
    os.environ.setdefault("REDIS_LOCK_TTL_SECONDS", str(lock_ttl_seconds))
    os.environ.setdefault("REDIS_LOCK_HEARTBEAT_SECONDS", str(max(5, min(lock_ttl_seconds // 3, 30))))
    lock_name = profile_lock_name(run_started_at, args.lock_bucket_minutes)

    def run_update() -> dict[str, Any]:
        with connect(database_url, row_factory=dict_row, prepare_threshold=None) as conn:
            with conn.cursor() as cur:
                cur.execute(PROFILE_SQL, (lookback_days, shard_count, shard_count, shard_index))
                rows = cur.fetchall()

            now = datetime.now(timezone.utc)
            profiles = build_profiles(rows, now)
            payloads = [profile_payload(state) for state in profiles.values()]
            payloads.sort(key=lambda payload: (-payload["event_count"], payload["client_id"]))
            if args.limit_clients:
                payloads = payloads[: max(args.limit_clients, 1)]

            if not args.dry_run and payloads:
                with conn.cursor() as cur:
                    cur.executemany(UPSERT_SQL, payloads)
                conn.commit()
            else:
                conn.rollback()

        return {
            "dry_run": args.dry_run,
            "events": len(rows),
            "profiles": len(payloads),
            "lookback_days": lookback_days,
            "lock_name": lock_name,
            "shard_index": shard_index,
            "shard_count": shard_count,
        }

    if args.no_redis_lock:
        result = run_update()
    else:
        with profile_update_lock(lock_name, lock_ttl_seconds) as lock_state:
            if lock_state["skipped"]:
                result = {
                    "dry_run": args.dry_run,
                    "skipped": True,
                    "skip_reason": "redis_lock_held",
                    "lookback_days": lookback_days,
                    "lock_name": lock_name,
                    "shard_index": shard_index,
                    "shard_count": shard_count,
                    "used_redis_lock": lock_state["used_redis_lock"],
                }
            else:
                result = {
                    **run_update(),
                    "skipped": False,
                    "used_redis_lock": lock_state["used_redis_lock"],
                }

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
