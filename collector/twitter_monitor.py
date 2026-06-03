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
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse, urlunparse

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
DEFAULT_MAX_RECORDS = int(os.environ.get("MAX_RECORDS", "100000"))
AUTO_TRANSLATE = os.environ.get("TRANSLATE_CONTENT", "false").lower() == "true"
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "").strip()
YOUTUBE_RETENTION_HOURS = 72
YOUTUBE_RSS_TIMEOUT_SECONDS = 20
YOUTUBE_PLAYBACK_RESOLVER_TIMEOUT_SECONDS = 30

DATABASE_URL = os.environ.get("DATABASE_URL", "")
VIDEO_THUMB_PREFIXES = (
    "amplify_video_thumb",
    "ext_tw_video_thumb",
    "tweet_video_thumb",
)

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
    normalized = value.strip()

    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        pass

    for fmt in (
        "%b %d, %Y · %I:%M %p UTC",
        "%b %d, %Y %I:%M %p UTC",
    ):
        try:
            return datetime.strptime(normalized, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

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
    if normalized.lower().startswith("youtube:"):
        channel_id = normalize_youtube_channel_id(normalized[8:].strip())
        return {
            "source": "youtube",
            "kind": "channel",
            "value": channel_id,
            "normalized_value": channel_id.lower(),
        }

    if normalized.startswith("search:"):
        keyword = normalized[7:].strip()
        if not keyword:
            raise ValueError("Keyword target cannot be empty.")
        return {
            "source": "twitter",
            "kind": "keyword",
            "value": keyword,
            "normalized_value": keyword.lower(),
        }

    if not normalized:
        raise ValueError("Target cannot be empty.")

    return {
        "source": "twitter",
        "kind": "user",
        "value": normalized,
        "normalized_value": normalized.lower(),
    }


def format_target(kind: str, value: str) -> str:
    return f"search:{value}" if kind == "keyword" else value


def format_target_row(target_row: dict) -> str:
    if target_row.get("source") == "youtube":
        return f"youtube:{target_row['value']}"
    return format_target(target_row["kind"], target_row["value"])


def normalize_youtube_channel_id(raw: str) -> str:
    value = raw.strip()
    if not value:
        raise ValueError("YouTube channel target cannot be empty.")
    channel_id = value
    parsed = urlparse(value)
    if parsed.netloc.lower() in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        query = parse_qs(parsed.query)
        feed_channel_id = (query.get("channel_id") or [""])[0].strip()
        if feed_channel_id:
            channel_id = feed_channel_id
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0].lower() == "channel":
                channel_id = parts[1]
    elif value.lower().startswith("/channel/"):
        parts = [part for part in value.split("/") if part]
        if len(parts) >= 2:
            channel_id = parts[1]
    if not re.fullmatch(r"UC[A-Za-z0-9_-]{20,}", channel_id):
        raise ValueError("YouTube channel target must be a channel ID, /channel/UC... URL, or feeds/videos.xml?channel_id=UC... URL.")
    return channel_id


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
        target_key = format_target_row(target_row).lower()
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

        for prefix in VIDEO_THUMB_PREFIXES:
            match = re.search(rf"(?:/pic/)?({prefix}/[^#]+)", path)
            if match:
                return f"https://pbs.twimg.com/{match.group(1)}"

        match = re.search(r"(pbs\.twimg\.com/media/[^?&]+)", path)
        if match:
            return "https://" + match.group(1)
    except Exception as exc:
        print(f"[图片解析] 还原 URL 失败 {nitter_url}: {exc}")

    return nitter_url


def get_original_video_url(video_url: str, instance: str) -> str:
    if not video_url:
        return ""

    try:
        if video_url.startswith("//"):
            absolute_url = "https:" + video_url
        elif video_url.startswith("/"):
            absolute_url = instance.rstrip("/") + video_url
        else:
            absolute_url = video_url

        parsed = urlparse(absolute_url)
        decoded_path = unquote(parsed.path)

        if decoded_path.startswith("/video/"):
            parts = decoded_path.split("/", 3)
            if len(parts) == 4:
                decoded_target = unquote(parts[3])
                if decoded_target.startswith("http://") or decoded_target.startswith("https://"):
                    return decoded_target
                if decoded_target.startswith("//"):
                    return "https:" + decoded_target
                if decoded_target.startswith("video.twimg.com/"):
                    return "https://" + decoded_target

        if decoded_path.startswith("/pic/video.twimg.com/"):
            suffix = decoded_path[len("/pic/") :]
            return "https://" + suffix

        if "video.twimg.com" in decoded_path:
            match = re.search(r"(video\.twimg\.com/[^?#\"'<>\\s]+(?:\\?[^#\"'<>\\s]+)?)", decoded_path)
            if match:
                return "https://" + match.group(1)

        return absolute_url
    except Exception as exc:
        print(f"[视频解析] 还原 URL 失败 {video_url}: {exc}")
        return video_url


def upload_to_imgbb(image_url: str) -> str | None:
    if not IMGBB_API_KEY:
        return None

    original_url = image_url
    image_url = get_original_image_url(image_url)
    if image_url != original_url:
        print(f"[图床] 已还原图片地址: {image_url}")

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
        content_type = img_response.headers.get("Content-Type", "")
        if not content_type.lower().startswith("image/"):
            print(f"[图床] 下载结果不是图片，跳过上传: {content_type or 'unknown'}")
            return None

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
                    video_poster_url = None
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
                                    video_poster_url = get_original_image_url(full_poster)
                                    if video_poster_url not in images:
                                        images.append(video_poster_url)

                            v_src = (
                                video_el.get("src", "")
                                or video_el.get("data-url", "")
                                or video_el.get("data-src", "")
                            )
                            if v_src:
                                video_url = get_original_video_url(v_src, instance)
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
                        "video_poster_url": video_poster_url,
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
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (parsed["source"], parsed["kind"], parsed["value"], parsed["normalized_value"]),
        )
        return cur.fetchone()


def load_active_targets(conn, source: str = "twitter") -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.source,
                t.kind,
                t.value,
                t.normalized_value,
                cs.last_guid
            FROM targets t
            LEFT JOIN crawl_state cs ON cs.target_id = t.id
            WHERE t.source = %s
              AND EXISTS (
                SELECT 1
                FROM subscriptions s
                INNER JOIN clients c ON c.id = s.client_id
                WHERE s.target_id = t.id
                  AND c.status = 'active'
            )
            ORDER BY t.source, t.kind, LOWER(t.value)
            """,
            (source,),
        )
        return cur.fetchall()


def load_youtube_targets(conn) -> list[dict]:
    return load_active_targets(conn, "youtube")


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
            SELECT t.source, t.kind, t.value
            FROM subscriptions s
            INNER JOIN targets t ON t.id = s.target_id
            WHERE s.client_id = %s
            ORDER BY t.source, t.kind, LOWER(t.value)
            """,
            (client_id,),
        )
        rows = cur.fetchall()
    return [format_target_row(row) for row in rows]


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
                    WHERE source = %s AND kind = %s AND normalized_value = %s
                  )
                """,
                (client_id, parsed["source"], parsed["kind"], parsed["normalized_value"]),
            )


DEFAULT_SYSTEM_TARGETS = [
    {"target": "search:AI video", "category": "科技", "tags": ["AI", "科技"], "weight": 10},
    {"target": "search:robot demo", "category": "科技", "tags": ["机器人", "科技"], "weight": 8},
    {"target": "search:funny video", "category": "搞笑", "tags": ["搞笑"], "weight": 8},
    {"target": "search:cat video", "category": "宠物", "tags": ["猫", "宠物"], "weight": 7},
    {"target": "search:dog video", "category": "宠物", "tags": ["狗", "宠物"], "weight": 7},
    {"target": "search:NBA highlights", "category": "体育", "tags": ["NBA", "篮球", "体育"], "weight": 8},
    {"target": "search:football highlights", "category": "体育", "tags": ["足球", "体育"], "weight": 7},
    {"target": "search:movie trailer", "category": "影视", "tags": ["电影", "预告片", "影视"], "weight": 6},
    {"target": "search:game trailer", "category": "游戏", "tags": ["游戏", "预告片"], "weight": 6},
    {"target": "search:music video", "category": "音乐", "tags": ["音乐"], "weight": 6},
]


def parse_system_targets_file(path: str | None) -> list[dict]:
    if not path:
        return DEFAULT_SYSTEM_TARGETS

    with open(path, "r", encoding="utf-8") as file:
        payload = json.load(file)

    if not isinstance(payload, list):
        raise ValueError("System targets file must contain a JSON array.")

    targets = []
    for item in payload:
        if isinstance(item, str):
            targets.append({"target": item, "category": None, "tags": [], "weight": 0})
            continue
        if not isinstance(item, dict) or not str(item.get("target") or "").strip():
            raise ValueError("Each system target must be a string or object with target.")
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            raise ValueError("System target tags must be a list.")
        targets.append(
            {
                "target": str(item["target"]).strip(),
                "category": str(item.get("category") or "").strip() or None,
                "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
                "weight": int(item.get("weight") or 0),
            }
        )
    return targets


def seed_system_targets(conn, target_configs: list[dict]) -> dict[str, int]:
    upserted = 0
    with conn.cursor() as cur:
        for config in target_configs:
            target_row = upsert_target(conn, config["target"])
            cur.execute(
                """
                INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
                VALUES (%s, 'system', %s, %s, %s, TRUE)
                ON CONFLICT (target_id) DO UPDATE SET
                    scope = 'system',
                    tags = EXCLUDED.tags,
                    category = EXCLUDED.category,
                    weight = EXCLUDED.weight,
                    is_public_pool = TRUE,
                    updated_at = NOW()
                """,
                (target_row["id"], Jsonb(config.get("tags") or []), config.get("category"), int(config.get("weight") or 0)),
            )
            upserted += 1
    return {"upserted": upserted}


def load_system_targets(conn) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                t.id,
                t.source,
                t.kind,
                t.value,
                t.normalized_value,
                cs.last_guid
            FROM targets t
            INNER JOIN target_profiles tp ON tp.target_id = t.id
            LEFT JOIN crawl_state cs ON cs.target_id = t.id
            WHERE t.source = 'twitter'
              AND tp.scope = 'system'
              AND tp.is_public_pool = TRUE
            ORDER BY tp.weight DESC, t.source, t.kind, LOWER(t.value)
            """
        )
        return cur.fetchall()


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
                "video_poster_url": tweet.get("video_poster_url"),
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


def youtube_entry_value(entry: dict, *keys: str) -> str | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_youtube_video_id(entry: dict) -> str | None:
    direct = youtube_entry_value(entry, "yt_videoid", "yt:videoId", "videoId")
    if direct:
        return direct
    entry_id = youtube_entry_value(entry, "id", "guid")
    if entry_id and entry_id.startswith("yt:video:"):
        return entry_id.removeprefix("yt:video:")
    link = youtube_entry_value(entry, "link")
    if link:
        parsed = urlparse(link)
        query_video_id = parse_qs(parsed.query).get("v", [None])[0]
        if query_video_id:
            return query_video_id
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2 and parts[0] == "shorts":
            return parts[1]
    media_content = entry.get("media_content") or []
    if isinstance(media_content, list):
        for media in media_content:
            if isinstance(media, dict):
                url = media.get("url")
                if isinstance(url, str):
                    parsed = urlparse(url)
                    query_video_id = parse_qs(parsed.query).get("v", [None])[0]
                    if query_video_id:
                        return query_video_id
    return None


def extract_youtube_thumbnail(entry: dict, video_id: str) -> str:
    thumbnails = entry.get("media_thumbnail") or []
    if isinstance(thumbnails, list):
        for thumbnail in thumbnails:
            if isinstance(thumbnail, dict):
                url = thumbnail.get("url")
                if isinstance(url, str) and url.strip():
                    return url.strip()
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"


def make_youtube_queue_payload(target_row: dict, entry: dict, fetched_at: datetime) -> dict | None:
    video_id = extract_youtube_video_id(entry)
    if not video_id:
        return None
    published_at = parse_datetime(youtube_entry_value(entry, "published", "updated")) or fetched_at
    if published_at < fetched_at - timedelta(hours=YOUTUBE_RETENTION_HOURS):
        return None
    guid = f"yt:video:{video_id}"
    link = youtube_entry_value(entry, "link") or f"https://www.youtube.com/watch?v={video_id}"
    title = youtube_entry_value(entry, "title", "media_title") or "YouTube video"
    description = youtube_entry_value(entry, "media_description", "summary", "description") or ""
    author = youtube_entry_value(entry, "author", "name") or target_row["value"]
    thumbnail = extract_youtube_thumbnail(entry, video_id)
    expires_at = published_at + timedelta(hours=YOUTUBE_RETENTION_HOURS)
    return {
        "source": "youtube",
        "target_id": target_row["id"],
        "channel_id": target_row["value"],
        "guid": guid,
        "provider_video_id": video_id,
        "title": title,
        "content": description,
        "raw_content": description,
        "author": author,
        "fullname": author,
        "link": link,
        "x_url": None,
        "images": [thumbnail],
        "video_poster_url": thumbnail,
        "published_at": published_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }


def enqueue_youtube_payload(conn, payload: dict) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO video_resolution_queue (
                source, target_id, guid, provider_video_id, payload, status, attempts, next_attempt_at, expires_at
            )
            VALUES ('youtube', %s, %s, %s, %s, 'pending', 0, NOW(), %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                payload = EXCLUDED.payload,
                next_attempt_at = LEAST(video_resolution_queue.next_attempt_at, NOW()),
                expires_at = EXCLUDED.expires_at,
                updated_at = NOW()
            RETURNING (xmax = 0) AS inserted
            """,
            (payload["target_id"], payload["guid"], payload["provider_video_id"], Jsonb(payload), parse_datetime(payload["expires_at"])),
        )
        row = cur.fetchone()
        return bool(row and row.get("inserted"))


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def collect_media_candidates(value) -> list[dict]:
    candidates: list[dict] = []
    if isinstance(value, dict):
        if isinstance(value.get("url"), str):
            candidates.append(value)
        for child in value.values():
            candidates.extend(collect_media_candidates(child))
    elif isinstance(value, list):
        for child in value:
            candidates.extend(collect_media_candidates(child))
    return candidates


def parse_url_expire(video_url: str) -> datetime | None:
    expire_value = parse_qs(urlparse(video_url).query).get("expire", [None])[0]
    if not expire_value:
        return None
    try:
        return datetime.fromtimestamp(int(expire_value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def youtube_resolver_url_variants(watch_url: str) -> list[str]:
    variants = [watch_url]
    parsed = urlparse(watch_url)
    video_id = parse_qs(parsed.query).get("v", [None])[0]
    parts = [part for part in parsed.path.split("/") if part]
    if not video_id and len(parts) >= 2 and parts[0] in {"shorts", "v"}:
        video_id = parts[1]
    if not video_id and parsed.netloc.lower() == "youtu.be" and parts:
        video_id = parts[0]
    if video_id:
        variants.extend(
            [
                f"https://www.youtube.com/v/{video_id}?version=3",
                f"https://www.youtube.com/watch?v={video_id}",
                f"https://youtu.be/{video_id}",
            ]
        )
    seen: set[str] = set()
    return [url for url in variants if url and not (url in seen or seen.add(url))]


def fetch_youtube_resolver_payload(resolver_url: str) -> dict:
    response = requests.post(
        "https://www.clipto.com/api/youtube",
        headers={
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://www.clipto.com",
            "Referer": "https://www.clipto.com/zh-TW/media-downloader/youtube-downloader?via=ytb",
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.6 Safari/605.1.15",
        },
        json={"url": resolver_url},
        timeout=YOUTUBE_PLAYBACK_RESOLVER_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.json()


def resolve_youtube_playback_url(watch_url: str) -> dict:
    errors: list[str] = []
    payload = None
    candidates: list[dict] = []
    for resolver_url in youtube_resolver_url_variants(watch_url):
        try:
            payload = fetch_youtube_resolver_payload(resolver_url)
            candidates = collect_media_candidates(payload)
            if candidates:
                break
            errors.append(f"{resolver_url}: empty media list")
        except Exception as exc:
            errors.append(f"{resolver_url}: {exc}")
    if payload is None:
        raise RuntimeError("; ".join(errors) or "YouTube resolver returned no payload.")
    progressive = []
    for candidate in candidates:
        url = candidate.get("url")
        if not isinstance(url, str) or not url.startswith("http"):
            continue
        format_id = str(candidate.get("formatId") or candidate.get("format_id") or candidate.get("itag") or "")
        mime = str(candidate.get("mimeType") or candidate.get("mime") or candidate.get("type") or "").lower()
        quality = str(candidate.get("quality") or candidate.get("qualityLabel") or "")
        has_audio = candidate.get("hasAudio") is True or candidate.get("audio") is not False or "audio" in mime
        video_only = candidate.get("hasAudio") is False or "video/webm" in mime and "audio" not in mime
        if format_id == "18" or (("mp4" in mime or ".mp4" in url) and has_audio and not video_only):
            progressive.append((0 if format_id == "18" else 1, quality, candidate))
    if not progressive:
        raise RuntimeError("No progressive MP4 candidate returned by YouTube resolver. " + "; ".join(errors))
    progressive.sort(key=lambda item: (item[0], item[1]))
    selected = progressive[0][2]
    video_url = selected["url"]
    expires_at = parse_url_expire(video_url)
    if not expires_at:
        raise RuntimeError("Resolved YouTube playback URL does not include an expire query parameter.")
    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at,
        "format_id": str(selected.get("formatId") or selected.get("format_id") or selected.get("itag") or ""),
        "duration_seconds": selected.get("duration") or selected.get("durationSeconds"),
        "raw": payload,
    }


def upsert_resolved_youtube_item(conn, queue_row: dict, resolved: dict) -> str:
    payload = queue_row["payload"] or {}
    metadata = {
        "target": f"youtube:{payload['channel_id']}",
        "target_type": "channel",
        "target_value": payload["channel_id"],
        "youtube_video_id": payload["provider_video_id"],
        "youtube_channel_id": payload["channel_id"],
        "watch_url": payload["link"],
        "resolver": "clipto",
        "resolved_at": now_iso(),
        "format_id": resolved.get("format_id"),
        "duration_seconds": resolved.get("duration_seconds"),
        "video_poster_url": payload.get("video_poster_url"),
        "video_url_expires_at": resolved["video_url_expires_at"].isoformat(),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname, title, content, raw_content, translated_content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                video_url = EXCLUDED.video_url,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                expires_at = EXCLUDED.expires_at,
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING id
            """,
            (
                queue_row["target_id"],
                payload["guid"],
                payload.get("author"),
                payload.get("fullname"),
                payload.get("title"),
                payload.get("content"),
                payload.get("raw_content"),
                payload.get("link"),
                Jsonb(payload.get("images") or []),
                resolved["video_url"],
                parse_datetime(payload["expires_at"]),
                resolved["video_url_expires_at"],
                parse_datetime(payload["published_at"]),
                Jsonb(metadata),
            ),
        )
        return str(cur.fetchone()["id"])


def resolve_youtube_queue_row(conn, queue_row: dict) -> bool:
    payload = queue_row["payload"] or {}
    try:
        resolved = resolve_youtube_playback_url(payload["link"])
        item_id = upsert_resolved_youtube_item(conn, queue_row, resolved)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_resolution_queue
                SET status = 'resolved', attempts = attempts + 1, last_error = NULL,
                    resolved_item_id = %s, updated_at = NOW()
                WHERE id = %s
                """,
                (item_id, queue_row["id"]),
            )
        return True
    except Exception as exc:
        attempts = int(queue_row.get("attempts") or 0) + 1
        delay_minutes = min(180, 5 * (2 ** min(attempts, 5)))
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE video_resolution_queue
                SET status = 'failed', attempts = attempts + 1, last_error = %s,
                    next_attempt_at = NOW() + (%s || ' minutes')::interval,
                    updated_at = NOW()
                WHERE id = %s
                """,
                (str(exc)[:500], delay_minutes, queue_row["id"]),
            )
        print(f"[YouTube] resolve failed for {payload.get('guid')}: {exc}")
        return False


def fetch_youtube_rss_entries(channel_id: str) -> list[dict]:
    import feedparser

    url = "https://www.youtube.com/feeds/videos.xml?" + urlencode({"channel_id": channel_id})
    response = requests.get(
        url,
        headers={"User-Agent": "x2api-youtube-rss/1.0"},
        timeout=YOUTUBE_RSS_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    parsed = feedparser.parse(response.content)
    if parsed.bozo:
        raise RuntimeError(f"YouTube RSS parse failed: {parsed.bozo_exception}")
    return [dict(entry) for entry in parsed.entries]


def monitor_youtube_target(conn, target_row: dict) -> int:
    fetched_at = now_utc()
    entries = fetch_youtube_rss_entries(target_row["value"])
    if not entries:
        upsert_crawl_state(conn, target_row["id"], last_guid=target_row.get("last_guid"), last_error="No YouTube RSS entries returned.", success=False)
        return 0
    latest_guid: str | None = None
    queued = 0
    for entry in entries:
        payload = make_youtube_queue_payload(target_row, entry, fetched_at)
        if payload is None:
            continue
        latest_guid = latest_guid or payload["guid"]
        if target_row.get("last_guid") == payload["guid"]:
            break
        if item_exists_for_guid(conn, target_row["id"], payload["guid"]):
            continue
        if enqueue_youtube_payload(conn, payload):
            queued += 1
    conn.commit()

    resolved_count = 0
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT *
            FROM video_resolution_queue
            WHERE target_id = %s
              AND status IN ('pending', 'failed')
              AND next_attempt_at <= NOW()
              AND expires_at > NOW()
            ORDER BY attempts ASC, payload->>'published_at' DESC
            LIMIT 5
            """,
            (target_row["id"],),
        )
        rows = cur.fetchall()
    for row in rows:
        if resolve_youtube_queue_row(conn, row):
            resolved_count += 1
        conn.commit()

    upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid or target_row.get("last_guid"), last_error=None, success=True)
    return resolved_count


def refresh_youtube_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = 0
    resolved = 0
    refreshed = 0

    def remaining() -> int:
        return max(0, limit - processed)

    def fetch_queue_rows(where_sql: str, params: tuple = ()) -> list[dict]:
        if remaining() <= 0:
            return []
        with conn.cursor() as cur:
            cur.execute(where_sql, (*params, remaining()))
            return cur.fetchall()

    def refresh_item(row: dict) -> bool:
        payload = {
            "guid": row["guid"],
            "provider_video_id": row["metadata"].get("youtube_video_id"),
            "channel_id": row["metadata"].get("youtube_channel_id"),
            "link": row["metadata"].get("watch_url") or row["link"],
            "expires_at": row["expires_at"].isoformat(),
            "published_at": row["published_at"].isoformat() if row.get("published_at") else row["stored_at"].isoformat(),
            "title": row.get("title"),
            "content": row.get("content"),
            "raw_content": row.get("raw_content"),
            "author": row.get("author"),
            "fullname": row.get("fullname"),
            "images": row.get("images") or [],
            "video_poster_url": row["metadata"].get("video_poster_url"),
        }
        try:
            resolved_payload = resolve_youtube_playback_url(payload["link"])
            metadata = row["metadata"] | {
                "resolver": "clipto",
                "resolved_at": now_iso(),
                "format_id": resolved_payload.get("format_id"),
                "duration_seconds": resolved_payload.get("duration_seconds"),
                "video_url_expires_at": resolved_payload["video_url_expires_at"].isoformat(),
            }
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE items
                    SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at
                    WHERE id = %s
                    """,
                    (resolved_payload["video_url"], resolved_payload["video_url_expires_at"], Jsonb(metadata), row["id"]),
                )
            return True
        except Exception as exc:
            print(f"[YouTube] refresh failed for {row['guid']}: {exc}")
            return False

    item_queries = [
        (
            """
            SELECT i.*, t.value AS channel_id
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = 'youtube' AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (critical_window_minutes,),
        ),
        (
            """
            SELECT i.*, t.value AS channel_id
            FROM items i INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = 'youtube' AND i.expires_at > NOW()
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (refresh_window_minutes,),
        ),
    ]
    queue_queries = [
        (
            """
            SELECT * FROM video_resolution_queue
            WHERE source = 'youtube' AND status = 'pending' AND expires_at > NOW()
            ORDER BY payload->>'published_at' DESC
            LIMIT %s
            """,
            (),
        ),
        (
            """
            SELECT * FROM video_resolution_queue
            WHERE source = 'youtube' AND status = 'failed' AND next_attempt_at <= NOW() AND expires_at > NOW()
            ORDER BY attempts ASC, payload->>'published_at' DESC
            LIMIT %s
            """,
            (),
        ),
    ]

    for query, params in [item_queries[0], queue_queries[0], item_queries[1], queue_queries[1]]:
        rows = fetch_queue_rows(query, params)
        for row in rows:
            processed += 1
            if "payload" in row:
                if resolve_youtube_queue_row(conn, row):
                    resolved += 1
            elif refresh_item(row):
                refreshed += 1
            conn.commit()
            if remaining() <= 0:
                break
    return {"processed": processed, "resolved": resolved, "refreshed": refreshed}


def cleanup_records(conn, retention_days: int, max_records: int) -> dict[str, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM items")
        before_count = cur.fetchone()["count"]

        threshold = now_utc() - timedelta(days=retention_days)
        cur.execute("DELETE FROM video_resolution_queue WHERE expires_at <= NOW()")
        cur.execute(
            """
            DELETE FROM items i
            USING targets t
            WHERE t.id = i.target_id
              AND t.source = 'youtube'
              AND i.expires_at <= NOW()
            """
        )
        cur.execute(
            """
            DELETE FROM items i
            WHERE i.stored_at < %s
              AND (
                i.video_url IS NULL
                OR NOT EXISTS (
                  SELECT 1
                  FROM video_stats vs
                  WHERE vs.item_id = i.id
                    AND vs.score >= 20
                )
              )
              AND NOT EXISTS (
                SELECT 1
                FROM target_profiles tp
                WHERE tp.target_id = i.target_id
                  AND tp.is_public_pool = TRUE
              )
            """,
            (threshold,),
        )

        if max_records > 0:
            cur.execute(
                """
                WITH ranked_items AS (
                    SELECT
                        i.id,
                        ROW_NUMBER() OVER (
                            ORDER BY
                                CASE
                                  WHEN i.video_url IS NOT NULL AND COALESCE(vs.score, 0) >= 20 THEN 1
                                  WHEN i.video_url IS NOT NULL AND COALESCE(tp.is_public_pool, FALSE) THEN 2
                                  WHEN i.video_url IS NOT NULL THEN 3
                                  ELSE 4
                                END,
                                i.stored_at DESC
                        ) AS keep_rank
                    FROM items i
                    LEFT JOIN video_stats vs ON vs.item_id = i.id
                    LEFT JOIN target_profiles tp ON tp.target_id = i.target_id
                ),
                doomed AS (
                    SELECT id
                    FROM ranked_items
                    WHERE keep_rank > %s
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
              AND NOT EXISTS (
                SELECT 1 FROM video_resolution_queue vrq WHERE vrq.target_id = t.id
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
                i.expires_at,
                i.video_url_expires_at,
                i.published_at,
                i.stored_at,
                i.is_retweet,
                t.source,
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
                OR LOWER(CASE
                    WHEN t.source = 'youtube' THEN 'youtube:' || t.value
                    WHEN t.kind = 'keyword' THEN 'search:' || t.value
                    ELSE t.value
                END) = %s
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
                "target": format_target_row(row),
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
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "video_url_expires_at": row["video_url_expires_at"].isoformat() if row["video_url_expires_at"] else None,
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
            target_rows = [row for row in (upsert_target(conn, target) for target in parse_targets(args.targets)) if row.get("source") == "twitter"]
        elif args.system_only:
            target_rows = load_system_targets(conn)
        elif args.include_system:
            target_map = {row["id"]: row for row in load_active_targets(conn)}
            target_map.update({row["id"]: row for row in load_system_targets(conn)})
            target_rows = list(target_map.values())
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
            target = format_target_row(target_row)
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


def command_monitor_youtube(args) -> int:
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
            target_rows = [row for row in (upsert_target(conn, target) for target in parse_targets(args.targets)) if row.get("source") == "youtube"]
        else:
            target_rows = load_youtube_targets(conn)

        target_rows = select_targets_for_shard(target_rows, shard_index, shard_count)
        if not target_rows:
            print(
                f"[YouTube] 当前分片没有活跃 YouTube 订阅目标，跳过 "
                f"(shard_index={shard_index}, shard_count={shard_count})"
            )
            return 0

        resolved_records = 0
        for target_row in target_rows:
            target = format_target_row(target_row)
            try:
                resolved = monitor_youtube_target(conn, target_row)
                conn.commit()
                resolved_records += resolved
                print(f"[{target}] 已解析 {resolved} 条 YouTube 视频")
            except Exception as exc:
                conn.rollback()
                upsert_crawl_state(conn, target_row["id"], last_guid=target_row.get("last_guid"), last_error=str(exc)[:500], success=False)
                conn.commit()
                print(f"[{target}] YouTube 处理异常: {exc}")

        print(f"[YouTube] 本轮解析 {resolved_records} 条视频")
        if not args.skip_cleanup:
            stats = cleanup_records(conn, retention_days, max_records)
            conn.commit()
            print(
                f"[YouTube] 清理完成: 保留 {stats['after']} 条，删除 {stats['deleted']} 条 "
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


def command_refresh_youtube_playback_urls(args) -> int:
    with get_db_connection() as conn:
        stats = refresh_youtube_playback_urls(
            conn,
            limit=max(1, args.limit),
            refresh_window_minutes=max(1, args.refresh_window_minutes),
            critical_window_minutes=max(1, args.critical_window_minutes),
        )
        conn.commit()
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


def command_seed_system_targets(args) -> int:
    target_configs = parse_system_targets_file(args.file)
    with get_db_connection() as conn:
        stats = seed_system_targets(conn, target_configs)
        conn.commit()
    print(json.dumps({**stats, "targets": [config["target"] for config in target_configs]}, ensure_ascii=False, indent=2))
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
    monitor_parser.add_argument("--include-system", action="store_true", help="同时抓取系统公共视频池目标")
    monitor_parser.add_argument("--system-only", action="store_true", help="只抓取系统公共视频池目标")
    monitor_parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号，从 0 开始")
    monitor_parser.add_argument("--shard-count", type=int, default=1, help="总分片数")
    monitor_parser.set_defaults(func=command_monitor)

    youtube_monitor_parser = subparsers.add_parser("monitor-youtube", help="单独抓取 YouTube RSS 并解析播放 URL")
    youtube_monitor_parser.add_argument("--targets", help="覆盖 YouTube 订阅目标，逗号或换行分隔，格式 youtube:UC...")
    youtube_monitor_parser.add_argument("--retention-days", type=int, default=None, help="Twitter 保留天数参数；YouTube 固定按 expires_at 清理")
    youtube_monitor_parser.add_argument("--max-records", type=int, default=None, help="最大保留记录数")
    youtube_monitor_parser.add_argument("--skip-cleanup", action="store_true", help="本轮监控后不执行清理")
    youtube_monitor_parser.add_argument("--shard-index", type=int, default=0, help="当前分片编号，从 0 开始")
    youtube_monitor_parser.add_argument("--shard-count", type=int, default=1, help="总分片数")
    youtube_monitor_parser.set_defaults(func=command_monitor_youtube)

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

    refresh_youtube_parser = subparsers.add_parser("refresh-youtube-playback-urls", help="刷新 YouTube 播放 URL 并处理解析队列")
    refresh_youtube_parser.add_argument("--limit", type=int, default=30, help="单次最多处理条数")
    refresh_youtube_parser.add_argument("--refresh-window-minutes", type=int, default=90, help="普通刷新窗口")
    refresh_youtube_parser.add_argument("--critical-window-minutes", type=int, default=15, help="临界过期窗口")
    refresh_youtube_parser.set_defaults(func=command_refresh_youtube_playback_urls)

    seed_system_parser = subparsers.add_parser("seed-system-targets", help="初始化系统公共视频池目标")
    seed_system_parser.add_argument("--file", help="系统目标 JSON 文件；默认使用内置目标")
    seed_system_parser.set_defaults(func=command_seed_system_targets)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
