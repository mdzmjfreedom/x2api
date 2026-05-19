from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import random
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, unquote, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg import connect
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
DATA_DIR = PROJECT_ROOT / "data"
QUERY_RESULTS_DIR = DATA_DIR / "query_results"
INSTANCES_FILE = PROJECT_ROOT / "instances.json"

DEFAULT_RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))
DEFAULT_MAX_RECORDS = int(os.environ.get("MAX_RECORDS", "2000"))
AUTO_TRANSLATE = os.environ.get("TRANSLATE_CONTENT", "false").lower() == "true"
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "").strip()

DATABASE_URL = os.environ.get("DATABASE_URL", "")

NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.privacyredirect.com",
    "https://nitter.poast.org",
    "https://nitter.hu",
    "https://nitter.moomoo.me",
    "https://nitter.net",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ensure_data_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    QUERY_RESULTS_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        with path.open("r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:
        print(f"[系统] 读取 {path.name} 失败: {exc}")
        return default


def save_json(path: Path, payload) -> None:
    ensure_data_dirs()
    with path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def require_database_url() -> str:
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL environment variable.")
    return DATABASE_URL


def get_db_connection():
    # Supabase transaction pooler is the safest fit for short-lived jobs such
    # as GitHub Actions, but it doesn't support prepared statements.
    return connect(require_database_url(), row_factory=dict_row, prepare_threshold=None)


def normalize_target(target: str) -> str:
    return target.strip()


def parse_targets(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        parts = raw
    else:
        parts = re.split(r"[\n,]+", raw)
    targets = []
    seen = set()
    for part in parts:
        target = normalize_target(part)
        if not target or target in seen:
            continue
        seen.add(target)
        targets.append(target)
    return targets


def parse_target_value(target: str) -> dict[str, str]:
    normalized = normalize_target(target)
    if normalized.startswith("search:"):
        keyword = normalized[7:].strip()
        if not keyword:
            raise ValueError("Keyword target cannot be empty.")
        return {
            "kind": "keyword",
            "value": keyword,
            "normalized_value": keyword.lower(),
        }

    if not normalized:
        raise ValueError("Target cannot be empty.")

    return {
        "kind": "user",
        "value": normalized,
        "normalized_value": normalized.lower(),
    }


def format_target(kind: str, value: str) -> str:
    return f"search:{value}" if kind == "keyword" else value


def create_opaque_token(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def load_instances():
    if INSTANCES_FILE.exists():
        try:
            with INSTANCES_FILE.open("r", encoding="utf-8") as fh:
                instances = json.load(fh)
            if instances and isinstance(instances, list):
                print(f"[系统] 成功从本地缓存加载 {len(instances)} 个实例")
                return instances
        except Exception as exc:
            print(f"[系统] 加载实例缓存失败: {exc}")

    print("[系统] 缓存不存在或损坏，采用内置兜底实例列表")
    return NITTER_INSTANCES


def normalize_instance_config(instances: list[object]) -> list[dict[str, object]]:
    normalized_instances: list[dict[str, object]] = []
    for item in instances:
        if isinstance(item, str):
            url = item.rstrip("/")
            if not url:
                continue
            normalized_instances.append({"url": url, "priority": 0})
            continue

        if isinstance(item, dict):
            raw_url = item.get("url")
            if not isinstance(raw_url, str):
                continue
            url = raw_url.rstrip("/")
            if not url:
                continue

            raw_priority = item.get("priority", 0)
            try:
                priority = int(raw_priority)
            except (TypeError, ValueError):
                priority = 0

            normalized_instances.append({"url": url, "priority": priority})

    return normalized_instances


def order_instances_for_attempts(
    instances: list[object],
    runtime_penalties: dict[str, int] | None = None,
) -> list[str]:
    runtime_penalties = runtime_penalties or {}
    normalized_instances = normalize_instance_config(instances)

    # Lower score wins: explicit priority first, then runtime 403 penalties.
    for item in normalized_instances:
        url = str(item["url"])
        item["sort_score"] = int(item["priority"]) + runtime_penalties.get(url, 0)
        item["shuffle_key"] = random.random()

    normalized_instances.sort(key=lambda item: (int(item["sort_score"]), float(item["shuffle_key"])))
    return [str(item["url"]) for item in normalized_instances]


def select_targets_for_shard(target_rows: list[dict], shard_index: int, shard_count: int) -> list[dict]:
    if shard_count <= 1:
        return target_rows

    selected_targets: list[dict] = []
    for target_row in target_rows:
        target_key = format_target(target_row["kind"], target_row["value"]).lower()
        digest = hashlib.sha256(target_key.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % shard_count
        if bucket == shard_index:
            selected_targets.append(target_row)

    return selected_targets


def get_random_user_agent():
    ua_list = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/121.0.0.0",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
    ]
    return random.choice(ua_list)


def get_original_image_url(nitter_url: str) -> str:
    try:
        if "pbs.twimg.com" in nitter_url:
            return nitter_url

        if "/pic/enc/" in nitter_url:
            enc_part = nitter_url.split("/pic/enc/")[-1].split("?")[0]
            try:
                decoded = bytes.fromhex(enc_part).decode("utf-8")
                if "pbs.twimg.com" in decoded:
                    return decoded
            except Exception:
                pass

        path = unquote(nitter_url)
        if "/media/" in path:
            media_part = path.split("/media/")[-1].split("?")[0]
            if "." in media_part:
                media_id, ext = media_part.rsplit(".", 1)
                ext = ext.split("&")[0].split("?")[0]
                return f"https://pbs.twimg.com/media/{media_id}?format={ext}&name=large"

        match = re.search(r"(pbs\.twimg\.com/media/[^?&]+)", path)
        if match:
            return "https://" + match.group(1)
    except Exception as exc:
        print(f"[图片解析] 还原 URL 失败 {nitter_url}: {exc}")

    return nitter_url


def upload_to_imgbb(image_url: str) -> str | None:
    if not IMGBB_API_KEY:
        return None

    try:
        print(f"[图床] 正在从 {image_url} 下载图片...")
        img_response = requests.get(
            image_url,
            timeout=30,
            headers={
                "User-Agent": get_random_user_agent(),
                "Referer": "https://twitter.com/",
            },
        )
        img_response.raise_for_status()

        image_base64 = base64.b64encode(img_response.content).decode("utf-8")
        print("[图床] 正在上传到 ImgBB...")
        upload_response = requests.post(
            "https://api.imgbb.com/1/upload",
            data={
                "key": IMGBB_API_KEY,
                "image": image_base64,
            },
            timeout=30,
        )
        upload_response.raise_for_status()
        payload = upload_response.json()
        if payload.get("success") and payload.get("data", {}).get("url"):
            uploaded_url = payload["data"]["url"]
            print(f"[图床] ImgBB 上传成功: {uploaded_url}")
            return uploaded_url

        print(f"[图床] ImgBB 上传失败: {payload}")
    except Exception as exc:
        print(f"[图床] ImgBB 上传异常: {exc}")

    return None


def rewrite_images_with_imgbb(tweets: list[dict]) -> None:
    if not IMGBB_API_KEY:
        return

    uploaded_cache: dict[str, str] = {}
    for tweet in tweets:
        images = tweet.get("images") or []
        if not images:
            continue

        rewritten_images: list[str] = []
        for image_url in images:
            cached = uploaded_cache.get(image_url)
            if cached:
                rewritten_images.append(cached)
                continue

            uploaded = upload_to_imgbb(image_url)
            final_url = uploaded or image_url
            uploaded_cache[image_url] = final_url
            rewritten_images.append(final_url)

        tweet["images"] = rewritten_images


def translate_text(text: str, target_lang: str = "zh-CN") -> str | None:
    if not text or not text.strip():
        return None

    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={
                "client": "gtx",
                "sl": "auto",
                "tl": target_lang,
                "dt": "t",
                "q": text,
            },
            headers={"User-Agent": get_random_user_agent()},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and data[0]:
            return "".join(part[0] for part in data[0] if part and part[0])
    except Exception as exc:
        print(f"[翻译] 失败: {exc}")
    return None


def nitter_to_x_url(nitter_url: str) -> str:
    if not nitter_url:
        return ""
    parsed = urlparse(nitter_url)
    return urlunparse(("https", "x.com", parsed.path, "", parsed.query, ""))


def scrape_nitter_with_playwright(
    target: str,
    dynamic_instances: list[str] | None = None,
    runtime_penalties: dict[str, int] | None = None,
) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import stealth_sync
    except ModuleNotFoundError as exc:
        print(f"[{target}] 缺少抓取依赖: {exc}")
        return []

    is_search = target.startswith("search:")
    keyword = target[7:] if is_search else target

    instances = list(dynamic_instances or order_instances_for_attempts(list(dynamic_instances or NITTER_INSTANCES)))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for instance in instances:
            context = None
            try:
                context = browser.new_context(
                    user_agent=get_random_user_agent(),
                    viewport={"width": 1280, "height": 720},
                )
                page = context.new_page()
                stealth_sync(page)

                if is_search:
                    url = f"{instance.rstrip('/')}/search?f=tweets&q={quote(keyword)}"
                else:
                    url = f"{instance.rstrip('/')}/{keyword}"

                print(f"[{target}] 正在加载: {url}")
                try:
                    response = page.goto(url, wait_until="networkidle", timeout=45000)
                    if response and response.status == 403:
                        print(f"[{target}] 访问 {instance} 被拒 (403 Forbidden)")
                        if runtime_penalties is not None:
                            runtime_penalties[instance] = runtime_penalties.get(instance, 0) + 100
                        context.close()
                        context = None
                        continue
                except Exception as exc:
                    print(f"[{target}] 加载 {instance} 超时或失败: {exc}")
                    context.close()
                    context = None
                    continue

                challenge_keywords = ["Verifying your browser", "Just a moment", "Checking your browser"]
                for _ in range(5):
                    content = page.content()
                    if any(keyword in content for keyword in challenge_keywords):
                        page.wait_for_timeout(5000)
                    else:
                        break

                soup = BeautifulSoup(page.content(), "html.parser")
                items = soup.select(".timeline-item")
                if not items:
                    print(f"[{target}] 在实例 {instance} 上未发现推文内容")
                    context.close()
                    context = None
                    continue

                valid_tweets = []
                for item in items[:20]:
                    if item.select_one(".pinned") is not None:
                        print(f"[{target}] 发现置顶推文，跳过")
                        continue

                    is_retweet = item.select_one(".retweet-header") is not None
                    images = []
                    for img in item.select(".attachment.image img, .tweet-image img, .still-image img, .attachments img"):
                        if any(cls in str(img.parent.get("class", [])) for cls in ["avatar", "profile"]):
                            continue
                        src = img.get("src", "")
                        if not src or "emoji" in src.lower() or "hashtag_click" in src:
                            continue

                        if src.startswith("//"):
                            full_src = "https:" + src
                        elif src.startswith("/"):
                            full_src = instance.rstrip("/") + src
                        else:
                            full_src = src
                        images.append(get_original_image_url(full_src))

                    video_url = None
                    try:
                        video_el = item.select_one("video source") or item.select_one("video")
                        if video_el:
                            poster_el = item.select_one("video")
                            if poster_el:
                                poster = poster_el.get("poster", "")
                                if poster:
                                    if poster.startswith("//"):
                                        full_poster = "https:" + poster
                                    elif poster.startswith("/"):
                                        full_poster = instance.rstrip("/") + poster
                                    else:
                                        full_poster = poster
                                    full_poster = get_original_image_url(full_poster)
                                    if full_poster not in images:
                                        images.append(full_poster)

                            v_src = video_el.get("src", "")
                            if v_src:
                                if v_src.startswith("//"):
                                    video_url = "https:" + v_src
                                elif v_src.startswith("/"):
                                    video_url = instance.rstrip("/") + v_src
                                else:
                                    video_url = v_src
                    except Exception as exc:
                        print(f"[{target}] 视频提取异常: {exc}")

                    content_el = item.select_one(".tweet-content")
                    link_el = item.select_one(".tweet-link")
                    date_el = item.select_one(".tweet-date a")
                    author_el = item.select_one(".username")
                    fullname_el = item.select_one(".fullname")
                    if not content_el or not link_el:
                        continue

                    link_href = link_el.get("href", "")
                    tweet_id = link_href.split("/status/")[-1].split("#")[0] if "/status/" in link_href else link_href
                    nitter_link = instance.rstrip("/") + link_href
                    raw_content = content_el.get_text(strip=True)
                    clean_content = raw_content.replace("€∋", "").strip()
                    published = date_el.get("title", "") if date_el else ""

                    tweet = {
                        "target": target,
                        "target_type": "keyword" if is_search else "user",
                        "target_value": keyword,
                        "content": clean_content,
                        "raw_content": raw_content,
                        "translated_content": translate_text(clean_content) if AUTO_TRANSLATE else None,
                        "link": nitter_link,
                        "x_url": nitter_to_x_url(nitter_link),
                        "published": published,
                        "author": author_el.get_text(strip=True) if author_el else keyword,
                        "fullname": fullname_el.get_text(" ", strip=True) if fullname_el else None,
                        "guid": tweet_id,
                        "is_retweet": is_retweet,
                        "images": images,
                        "video_url": video_url,
                        "stored_at": now_iso(),
                        "source_instance": instance,
                    }
                    valid_tweets.append(tweet)

                if valid_tweets:
                    newest_id = valid_tweets[0]["guid"]
                    print(f"[{target}] 成功从 {instance} 抓取 {len(valid_tweets)} 条候选推文，最新 ID: {newest_id}")
                    context.close()
                    browser.close()
                    return valid_tweets

                print(f"[{target}] {instance} 页面上未找到符合条件的非置顶推文")
                context.close()
                context = None
            except Exception as exc:
                print(f"[{target}] 访问 {instance} 出错: {exc}")
            finally:
                if context is not None:
                    try:
                        context.close()
                    except Exception:
                        pass

        browser.close()
    return []


def upsert_target(conn, target: str) -> dict:
    parsed = parse_target_value(target)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (kind, value, normalized_value)
            VALUES (%s, %s, %s)
            ON CONFLICT (kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, kind, value, normalized_value
            """,
            (parsed["kind"], parsed["value"], parsed["normalized_value"]),
        )
        return cur.fetchone()


def load_active_targets(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.kind,
                t.value,
                t.normalized_value,
                cs.last_guid
            FROM targets t
            LEFT JOIN crawl_state cs ON cs.target_id = t.id
            WHERE EXISTS (
                SELECT 1
                FROM subscriptions s
                INNER JOIN clients c ON c.id = s.client_id
                WHERE s.target_id = t.id
                  AND c.status = 'active'
            )
            ORDER BY t.kind, LOWER(t.value)
            """
        )
        return cur.fetchall()


def resolve_client(conn, api_key: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, api_key, feed_token, label, status
            FROM clients
            WHERE api_key = %s
            LIMIT 1
            """,
            (api_key,),
        )
        return cur.fetchone()


def list_subscriptions(conn, client_id: str) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.kind, t.value
            FROM subscriptions s
            INNER JOIN targets t ON t.id = s.target_id
            WHERE s.client_id = %s
            ORDER BY t.kind, LOWER(t.value)
            """,
            (client_id,),
        )
        rows = cur.fetchall()
    return [format_target(row["kind"], row["value"]) for row in rows]


def replace_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM subscriptions WHERE client_id = %s", (client_id,))
        for target in targets:
            target_row = upsert_target(conn, target)
            cur.execute(
                """
                INSERT INTO subscriptions (client_id, target_id)
                VALUES (%s, %s)
                ON CONFLICT (client_id, target_id) DO NOTHING
                """,
                (client_id, target_row["id"]),
            )


def add_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        for target in targets:
            target_row = upsert_target(conn, target)
            cur.execute(
                """
                INSERT INTO subscriptions (client_id, target_id)
                VALUES (%s, %s)
                ON CONFLICT (client_id, target_id) DO NOTHING
                """,
                (client_id, target_row["id"]),
            )


def remove_subscriptions(conn, client_id: str, targets: list[str]) -> None:
    with conn.cursor() as cur:
        for target in targets:
            parsed = parse_target_value(target)
            cur.execute(
                """
                DELETE FROM subscriptions
                WHERE client_id = %s
                  AND target_id IN (
                    SELECT id
                    FROM targets
                    WHERE kind = %s AND normalized_value = %s
                  )
                """,
                (client_id, parsed["kind"], parsed["normalized_value"]),
            )


def register_client(conn, label: str | None) -> dict:
    api_key = create_opaque_token("x2d")
    feed_token = create_opaque_token("feed")
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO clients (api_key, feed_token, label)
            VALUES (%s, %s, %s)
            RETURNING id, api_key, feed_token, label, created_at
            """,
            (api_key, feed_token, label),
        )
        return cur.fetchone()


def upsert_crawl_state(conn, target_id: str, *, last_guid: str | None, last_error: str | None, success: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crawl_state (target_id, last_guid, last_checked_at, last_success_at, last_error)
            VALUES (%s, %s, NOW(), CASE WHEN %s THEN NOW() ELSE NULL END, %s)
            ON CONFLICT (target_id)
            DO UPDATE SET
                last_guid = COALESCE(EXCLUDED.last_guid, crawl_state.last_guid),
                last_checked_at = NOW(),
                last_success_at = CASE WHEN %s THEN NOW() ELSE crawl_state.last_success_at END,
                last_error = %s,
                updated_at = NOW()
            """,
            (target_id, last_guid, success, last_error, success, last_error),
        )


def insert_items(conn, target_row: dict, tweets: list[dict], previous_id: str | None) -> int:
    pending_records = []
    for tweet in tweets:
        if previous_id and tweet["guid"] == previous_id:
            break
        pending_records.append(tweet)

    if pending_records:
        rewrite_images_with_imgbb(pending_records)

    inserted = 0
    with conn.cursor() as cur:
        for tweet in reversed(pending_records):
            published_at = parse_datetime(tweet.get("published"))
            title = tweet.get("content", "").strip()
            if len(title) > 140:
                title = title[:137] + "..."

            metadata = {
                "target": tweet.get("target"),
                "target_type": tweet.get("target_type"),
                "target_value": tweet.get("target_value"),
                "published_raw": tweet.get("published"),
                "source_instance": tweet.get("source_instance"),
            }

            cur.execute(
                """
                INSERT INTO items (
                    target_id,
                    guid,
                    author,
                    fullname,
                    title,
                    content,
                    raw_content,
                    translated_content,
                    link,
                    x_url,
                    images,
                    video_url,
                    published_at,
                    stored_at,
                    is_retweet,
                    metadata
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                ON CONFLICT (target_id, guid) DO NOTHING
                """,
                (
                    target_row["id"],
                    tweet.get("guid"),
                    tweet.get("author"),
                    tweet.get("fullname"),
                    title or None,
                    tweet.get("content"),
                    tweet.get("raw_content"),
                    tweet.get("translated_content"),
                    tweet.get("link"),
                    tweet.get("x_url"),
                    Jsonb(tweet.get("images", [])),
                    tweet.get("video_url"),
                    published_at,
                    parse_datetime(tweet.get("stored_at")) or now_utc(),
                    bool(tweet.get("is_retweet")),
                    Jsonb(metadata),
                ),
            )
            inserted += cur.rowcount

    return inserted


def cleanup_records(conn, retention_days: int, max_records: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM items")
        before_count = cur.fetchone()["count"]

        threshold = now_utc() - timedelta(days=retention_days)
        cur.execute("DELETE FROM items WHERE stored_at < %s", (threshold,))

        if max_records > 0:
            cur.execute(
                """
                WITH doomed AS (
                    SELECT id
                    FROM items
                    ORDER BY stored_at DESC
                    OFFSET %s
                )
                DELETE FROM items
                WHERE id IN (SELECT id FROM doomed)
                """,
                (max_records,),
            )

        cur.execute(
            """
            DELETE FROM targets t
            WHERE NOT EXISTS (
                SELECT 1 FROM subscriptions s WHERE s.target_id = t.id
            )
              AND NOT EXISTS (
                SELECT 1 FROM items i WHERE i.target_id = t.id
            )
            """
        )

        cur.execute("SELECT COUNT(*) AS count FROM items")
        after_count = cur.fetchone()["count"]

    return {"before": before_count, "after": after_count, "deleted": before_count - after_count}


def query_records(
    conn,
    *,
    limit: int,
    target: str | None,
    keyword: str | None,
    since: str | None,
    until: str | None,
    api_key: str | None,
) -> list[dict]:
    like_keyword = f"%{keyword.lower()}%" if keyword else None
    normalized_target = target.lower() if target else None
    since_dt = parse_datetime(since)
    until_dt = parse_datetime(until)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                i.guid,
                i.author,
                i.fullname,
                i.title,
                i.content,
                i.raw_content,
                i.translated_content,
                i.link,
                i.x_url,
                i.images,
                i.video_url,
                i.published_at,
                i.stored_at,
                i.is_retweet,
                t.kind,
                t.value
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE (
                %s IS NULL
                OR EXISTS (
                    SELECT 1
                    FROM subscriptions s
                    INNER JOIN clients c ON c.id = s.client_id
                    WHERE s.target_id = i.target_id
                      AND c.api_key = %s
                      AND c.status = 'active'
                )
            )
              AND (
                %s IS NULL
                OR LOWER(CASE WHEN t.kind = 'keyword' THEN 'search:' || t.value ELSE t.value END) = %s
              )
              AND (
                %s IS NULL
                OR LOWER(COALESCE(i.content, '')) LIKE %s
                OR LOWER(COALESCE(i.raw_content, '')) LIKE %s
                OR LOWER(COALESCE(i.translated_content, '')) LIKE %s
                OR LOWER(COALESCE(i.author, '')) LIKE %s
              )
              AND (%s IS NULL OR i.stored_at >= %s)
              AND (%s IS NULL OR i.stored_at <= %s)
            ORDER BY COALESCE(i.published_at, i.stored_at) DESC, i.stored_at DESC
            LIMIT %s
            """,
            (
                api_key,
                api_key,
                normalized_target,
                normalized_target,
                like_keyword,
                like_keyword,
                like_keyword,
                like_keyword,
                like_keyword,
                since_dt,
                since_dt,
                until_dt,
                until_dt,
                limit,
            ),
        )
        rows = cur.fetchall()

    records = []
    for row in rows:
        records.append(
            {
                "target": format_target(row["kind"], row["value"]),
                "author": row["author"],
                "fullname": row["fullname"],
                "guid": row["guid"],
                "title": row["title"],
                "content": row["content"],
                "raw_content": row["raw_content"],
                "translated_content": row["translated_content"],
                "link": row["link"],
                "x_url": row["x_url"],
                "images": row["images"] or [],
                "video_url": row["video_url"],
                "published_at": row["published_at"].isoformat() if row["published_at"] else None,
                "stored_at": row["stored_at"].isoformat() if row["stored_at"] else None,
                "is_retweet": row["is_retweet"],
            }
        )
    return records


def print_record(record: dict, index: int | None = None) -> None:
    prefix = f"{index}. " if index is not None else ""
    print(f"{prefix}[{record.get('stored_at', '-')}] {record.get('target', '-')}")
    print(f"   作者: {record.get('author', '-')}")
    if record.get("fullname"):
        print(f"   昵称: {record['fullname']}")
    print(f"   ID: {record.get('guid', '-')}")
    print(f"   内容: {record.get('content', '').strip()}")
    if record.get("translated_content"):
        print(f"   翻译: {record['translated_content']}")
    print(f"   Nitter: {record.get('link', '-')}")
    if record.get("x_url"):
        print(f"   X: {record['x_url']}")
    if record.get("images"):
        print(f"   图片数: {len(record['images'])}")
    if record.get("video_url"):
        print(f"   视频: {record['video_url']}")
    print("")


def command_register_client(args) -> int:
    with get_db_connection() as conn:
        with conn.transaction():
            client = register_client(conn, args.label)
        payload = {
            "id": str(client["id"]),
            "label": client["label"],
            "apiKey": client["api_key"],
            "feedToken": client["feed_token"],
            "feedUrlPath": f"/rss/{client['feed_token']}.xml",
            "createdAt": client["created_at"].isoformat(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_monitor(args) -> int:
    instances = load_instances()
    runtime_penalties: dict[str, int] = {}
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    shard_index = args.shard_index if args.shard_index is not None else 0
    shard_count = args.shard_count if args.shard_count is not None else 1

    if shard_count <= 0:
        raise ValueError("shard-count must be greater than 0.")
    if shard_index < 0 or shard_index >= shard_count:
        raise ValueError("shard-index must be between 0 and shard-count - 1.")

    with get_db_connection() as conn:
        if args.targets:
            target_rows = [upsert_target(conn, target) for target in parse_targets(args.targets)]
        else:
            target_rows = load_active_targets(conn)

        target_rows = select_targets_for_shard(target_rows, shard_index, shard_count)

        if not target_rows:
            print(
                f"[系统] 当前分片没有活跃订阅目标，跳过本轮监控 "
                f"(shard_index={shard_index}, shard_count={shard_count})"
            )
            return 0

        print(
            f"[{datetime.now()}] 开始监控，共 {len(target_rows)} 个目标 "
            f"(shard_index={shard_index}, shard_count={shard_count})"
        )
        new_records = 0
        for target_row in target_rows:
            target = format_target(target_row["kind"], target_row["value"])
            previous_id = target_row.get("last_guid")
            try:
                ordered_instances = order_instances_for_attempts(instances, runtime_penalties)
                tweets = scrape_nitter_with_playwright(target, ordered_instances, runtime_penalties)
                if not tweets:
                    upsert_crawl_state(conn, target_row["id"], last_guid=previous_id, last_error="No tweets returned.", success=False)
                    conn.commit()
                    continue

                current_id = tweets[0]["guid"]
                if previous_id == current_id:
                    print(f"[{target}] 无更新")
                    upsert_crawl_state(conn, target_row["id"], last_guid=current_id, last_error=None, success=True)
                    conn.commit()
                    continue

                inserted = insert_items(conn, target_row, tweets, previous_id)
                upsert_crawl_state(conn, target_row["id"], last_guid=current_id, last_error=None, success=True)
                conn.commit()
                new_records += inserted
                print(f"[{target}] 已保存 {inserted} 条新记录到数据库")
            except Exception as exc:
                conn.rollback()
                upsert_crawl_state(conn, target_row["id"], last_guid=previous_id, last_error=str(exc)[:500], success=False)
                conn.commit()
                print(f"[{target}] 处理异常: {exc}")

        print(f"[系统] 本轮新增 {new_records} 条记录")

        if not args.skip_cleanup:
            stats = cleanup_records(conn, retention_days, max_records)
            conn.commit()
            print(
                f"[系统] 清理完成: 保留 {stats['after']} 条，删除 {stats['deleted']} 条 "
                f"(retention_days={retention_days}, max_records={max_records})"
            )
    return 0


def command_subscribe(args) -> int:
    with get_db_connection() as conn:
        client = resolve_client(conn, args.api_key)
        if not client or client["status"] != "active":
            print("[系统] 无效的 API key")
            return 1

        if args.action == "list":
            subscriptions = list_subscriptions(conn, client["id"])
            if not subscriptions:
                print("[系统] 当前没有订阅目标")
                return 0
            print("[系统] 当前订阅列表:")
            for idx, target in enumerate(subscriptions, start=1):
                print(f"{idx}. {target}")
            return 0

        raw_targets = args.targets
        targets = parse_targets(raw_targets)
        if args.action not in {"list", "set"} and not targets:
            print("[系统] 请通过 --targets 提供目标")
            return 1
        if args.action == "set" and raw_targets is None:
            print("[系统] set 动作需要显式提供 --targets，可传空字符串清空订阅")
            return 1

        with conn.transaction():
            if args.action == "add":
                add_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已新增 {len(targets)} 个订阅目标")
            elif args.action == "remove":
                remove_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已移除 {len(targets)} 个订阅目标")
            elif args.action == "set":
                replace_subscriptions(conn, client["id"], targets)
                print(f"[系统] 已重置订阅列表，共 {len(targets)} 个目标")
            else:
                print(f"[系统] 未知订阅动作: {args.action}")
                return 1

        current = list_subscriptions(conn, client["id"])
        if current:
            print("[系统] 当前订阅:")
            for idx, target in enumerate(current, start=1):
                print(f"{idx}. {target}")
    return 0


def command_query(args) -> int:
    limit = args.limit if args.limit > 0 else 20
    with get_db_connection() as conn:
        records = query_records(
            conn,
            limit=limit,
            target=args.target,
            keyword=args.keyword,
            since=args.since,
            until=args.until,
            api_key=args.api_key,
        )

    print(f"[系统] 查询结果 {len(records)} 条")
    for idx, record in enumerate(records, start=1):
        print_record(record, idx)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        print(f"[系统] 查询结果已写入 {output_path}")

    return 0


def command_cleanup(args) -> int:
    retention_days = args.retention_days if args.retention_days is not None else DEFAULT_RETENTION_DAYS
    max_records = args.max_records if args.max_records is not None else DEFAULT_MAX_RECORDS
    with get_db_connection() as conn:
        stats = cleanup_records(conn, retention_days, max_records)
        conn.commit()
    print(
        f"[系统] 清理完成: 处理前 {stats['before']} 条，处理后 {stats['after']} 条，"
        f"删除 {stats['deleted']} 条"
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Twitter/X 监控与 PostgreSQL 存储工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    register_parser = subparsers.add_parser("register-client", help="生成客户端 API key 与 feed token")
    register_parser.add_argument("--label", help="客户端标签")
    register_parser.set_defaults(func=command_register_client)

    monitor_parser = subparsers.add_parser("monitor", help="抓取订阅目标并保存最新推文")
    monitor_parser.add_argument("--targets", help="覆盖订阅列表，逗号或换行分隔")
    monitor_parser.add_argument("--retention-days", type=int, default=None, help="保留天数")
    monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    monitor_parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号，从 0 开始")
    monitor_parser.add_argument("--shard-count", type=int, default=1, help="总分片数")
    monitor_parser.set_defaults(func=command_monitor)

    subscribe_parser = subparsers.add_parser("subscribe", help="用 API key 管理订阅列表")
    subscribe_parser.add_argument("action", choices=["add", "remove", "set", "list"], help="订阅动作")
    subscribe_parser.add_argument("--api-key", required=True, help="客户端 API key")
    subscribe_parser.add_argument("--targets", help="目标列表，逗号或换行分隔")
    subscribe_parser.set_defaults(func=command_subscribe)

    query_parser = subparsers.add_parser("query", help="查询历史保存结果")
    query_parser.add_argument("--api-key", help="仅查询某个客户端可见的数据")
    query_parser.add_argument("--target", help="按订阅目标精确过滤")
    query_parser.add_argument("--keyword", help="按内容关键字过滤")
    query_parser.add_argument("--since", help="起始时间，ISO 8601，例如 2026-05-01T00:00:00+00:00")
    query_parser.add_argument("--until", help="结束时间，ISO 8601，例如 2026-05-31T23:59:59+00:00")
    query_parser.add_argument("--limit", type=int, default=20, help="最大返回条数")
    query_parser.add_argument("--output", help="将查询结果写入 JSON 文件")
    query_parser.set_defaults(func=command_query)

    cleanup_parser = subparsers.add_parser("cleanup", help="清理历史记录")
    cleanup_parser.add_argument("--retention-days", type=int, default=None, help="保留天数")
    cleanup_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    cleanup_parser.set_defaults(func=command_cleanup)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
