from __future__ import annotations

import hashlib
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

try:
    import redis
except ImportError:  # pragma: no cover - optional dependency fallback
    redis = None

DEFAULT_LOCK_TTL_SECONDS = 3600
DEFAULT_WAIT_TIMEOUT_SECONDS = 1800
DEFAULT_POLL_SECONDS = 5
DEFAULT_MAX_WRITERS = 4
DEFAULT_COOLDOWN_SECONDS = 3600
DEFAULT_ITEM_SEEN_TTL_SECONDS = 604800
DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS = 30
DEFAULT_REDIS_CONNECT_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class RedisConfig:
    url: str
    namespace: str


def redis_config() -> RedisConfig | None:
    url = os.environ.get("REDIS_URL", "").strip()
    if not url or redis is None:
        return None
    namespace = os.environ.get("REDIS_NAMESPACE", "x2api").strip() or "x2api"
    return RedisConfig(url=url, namespace=namespace)


def redis_client():
    config = redis_config()
    if not config:
        return None
    socket_timeout = max(1, int(os.environ.get("REDIS_SOCKET_TIMEOUT_SECONDS", str(DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS))))
    connect_timeout = max(1, int(os.environ.get("REDIS_CONNECT_TIMEOUT_SECONDS", str(DEFAULT_REDIS_CONNECT_TIMEOUT_SECONDS))))
    return redis.Redis.from_url(
        config.url,
        decode_responses=True,
        socket_timeout=socket_timeout,
        socket_connect_timeout=connect_timeout,
        socket_keepalive=True,
    )


def namespaced_key(kind: str, *parts: str) -> str:
    namespace = os.environ.get("REDIS_NAMESPACE", "x2api").strip() or "x2api"
    clean_parts = [str(part).strip().replace(" ", "_") for part in parts if str(part).strip()]
    return ":".join([namespace, kind, *clean_parts])


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""


class RedisLock:
    def __init__(self, client, key: str, *, ttl_seconds: int):
        self.client = client
        self.key = key
        self.ttl_seconds = ttl_seconds
        self.token = secrets.token_hex(16)

    def acquire(self) -> bool:
        return bool(self.client.set(self.key, self.token, nx=True, ex=self.ttl_seconds))

    def release(self) -> None:
        self.client.eval(_RELEASE_SCRIPT, 1, self.key, self.token)


def release_lock_safely(lock: RedisLock | None, *, source_name: str, lock_name: str) -> bool:
    if lock is None:
        return True
    try:
        lock.release()
        return True
    except Exception as exc:
        print(f"[redis-lock] release warning source={source_name} lock={lock_name}: {type(exc).__name__}: {exc}")
        return False


@contextmanager
def acquire_writer_locks(source: str) -> Iterator[bool]:
    client = redis_client()
    if client is None:
        yield False
        return

    max_writers = max(1, int(os.environ.get("DB_LOCK_MAX_WRITERS", str(DEFAULT_MAX_WRITERS))))
    wait_timeout = int(os.environ.get("REDIS_LOCK_WAIT_TIMEOUT_SECONDS", os.environ.get("DB_LOCK_WAIT_TIMEOUT_SECONDS", str(DEFAULT_WAIT_TIMEOUT_SECONDS))))
    poll_seconds = max(1, int(os.environ.get("REDIS_LOCK_POLL_SECONDS", os.environ.get("DB_LOCK_POLL_SECONDS", str(DEFAULT_POLL_SECONDS)))))
    ttl_seconds = max(60, int(os.environ.get("REDIS_LOCK_TTL_SECONDS", str(DEFAULT_LOCK_TTL_SECONDS))))
    deadline = time.monotonic() + wait_timeout
    source_name = source.strip().lower() or "other"
    slot_lock: RedisLock | None = None
    source_lock: RedisLock | None = None
    attempt = 0

    while True:
        attempt += 1
        for slot in range(max_writers):
            candidate = RedisLock(client, namespaced_key("db-writer-slot", str(slot)), ttl_seconds=ttl_seconds)
            if not candidate.acquire():
                continue
            locked_source = RedisLock(client, namespaced_key("source-lock", source_name), ttl_seconds=ttl_seconds)
            if locked_source.acquire():
                slot_lock = candidate
                source_lock = locked_source
                print(f"[redis-lock] acquired source={source_name} slot={slot}")
                break
            release_lock_safely(candidate, source_name=source_name, lock_name=f"slot-{slot}")
        if slot_lock and source_lock:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Timed out waiting for Redis DB writer lock: {source_name}")
        if attempt == 1 or attempt % 12 == 0:
            print(f"[redis-lock] waiting source={source_name} attempts={attempt} max_writers={max_writers}")
        time.sleep(poll_seconds)

    try:
        yield True
    finally:
        source_released = release_lock_safely(source_lock, source_name=source_name, lock_name="source")
        slot_released = release_lock_safely(slot_lock, source_name=source_name, lock_name="slot")
        if source_released and slot_released:
            print(f"[redis-lock] released source={source_name}")
        else:
            print(f"[redis-lock] release incomplete source={source_name}")


def cooldown_key(source: str) -> str:
    return namespaced_key("cooldown", source.strip().lower() or "other")


def is_in_cooldown(source: str) -> str | None:
    client = redis_client()
    if client is None:
        return None
    return client.get(cooldown_key(source))


def set_cooldown(source: str, reason: str, *, ttl_seconds: int | None = None) -> None:
    client = redis_client()
    if client is None:
        return
    ttl = ttl_seconds or int(os.environ.get("REDIS_SOURCE_COOLDOWN_SECONDS", str(DEFAULT_COOLDOWN_SECONDS)))
    client.set(cooldown_key(source), reason[:500], ex=max(60, ttl))


def mark_item_seen(source: str, guid: str, *, ttl_seconds: int | None = None) -> bool:
    client = redis_client()
    if client is None or not guid:
        return False
    ttl = ttl_seconds or int(os.environ.get("REDIS_ITEM_SEEN_TTL_SECONDS", str(DEFAULT_ITEM_SEEN_TTL_SECONDS)))
    key = namespaced_key("item-seen", source.strip().lower() or "other", stable_hash(guid))
    return bool(client.set(key, "1", nx=True, ex=max(60, ttl)))
