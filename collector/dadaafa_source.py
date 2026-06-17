from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb


DADAAFA_SITE_NAME = "DadaAFA"
DADAAFA_SOURCE = "dadaafa"
DADAAFA_KIND = "site"
DADAAFA_DEFAULT_BASE_URL = os.environ.get("DADAAFA_BASE_URL", "https://dadaafa.cc").strip().rstrip("/")
DADAAFA_RETENTION_HOURS = int(os.environ.get("DADAAFA_RETENTION_HOURS", "84"))
DADAAFA_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("DADAAFA_REQUEST_TIMEOUT_SECONDS", "30"))
DADAAFA_MIN_VIDEO_DURATION_SECONDS = int(os.environ.get("DADAAFA_MIN_VIDEO_DURATION_SECONDS", "60"))
DADAAFA_REFRESH_WINDOW_MINUTES = int(os.environ.get("DADAAFA_REFRESH_WINDOW_MINUTES", "90"))
DADAAFA_CRITICAL_WINDOW_MINUTES = int(os.environ.get("DADAAFA_CRITICAL_WINDOW_MINUTES", "15"))
DADAAFA_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = ("magsrv", "tsyndicate", "clickadu", "exoclick", "popads", "adsterra")
EXPIRY_QUERY_KEYS = ("exp", "expires", "expire", "e", "t")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def non_empty(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_epoch_datetime(value) -> datetime | None:
    raw = int_or_none(value)
    if raw is None or raw <= 0:
        return None
    if raw > 10_000_000_000:
        raw = raw // 1000
    try:
        return datetime.fromtimestamp(raw, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_dadaafa_target_value(raw: str) -> str:
    value = (raw or DADAAFA_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = DADAAFA_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("DadaAFA target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_dadaafa_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"dadaafa.cc", "www.dadaafa.cc"}


def format_target_row(target_row: dict) -> str:
    return f"dadaafa:{target_row['value']}"


def headers(referer: str | None = None, *, accept: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    return result


def request_with_proxy_fallback(url: str, *, referer: str | None = None, accept: str | None = None, stream: bool = False) -> requests.Response:
    last_error: Exception | None = None
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            response = session.get(
                url,
                headers=headers(referer, accept=accept),
                timeout=DADAAFA_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("DadaAFA request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_hls_text(url: str, referer: str) -> str:
    return request_with_proxy_fallback(url, referer=referer, accept="*/*").text


def read_hls_chunk(url: str, referer: str, size: int) -> bytes:
    with request_with_proxy_fallback(url, referer=referer, accept="*/*", stream=True) as response:
        return next(response.iter_content(size), b"")


def build_list_page_url(base_url: str, page: int) -> str:
    query = {"utm_source": "xx", "tab": "new"}
    if page > 1:
        query["page"] = str(page)
    return f"{normalize_dadaafa_target_value(base_url)}/?{urlencode(query)}"


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/play/([^/]+)/video/?$", urlparse(url).path)
    return match.group(1) if match else None


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_dadaafa_target_value(base_url) + "/", f"play/{video_id}/video?utm_source=xx")


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url + "/", raw)
    parsed = urlparse(normalized)
    if parsed.netloc.lower().endswith("gooiyt.cn") and re.search(r"\.(?:jpe?g|png|webp)$", parsed.path, flags=re.IGNORECASE):
        return urlunparse(parsed._replace(query="", fragment=""))
    return urlunparse(parsed._replace(fragment=""))


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="/play/"][href*="/video"]'):
        href = link.get("href")
        if not href:
            continue
        resolved_url = urlunparse(urlparse(urljoin(page_url, href))._replace(fragment=""))
        video_id = detail_id_from_url(resolved_url)
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        image = link.select_one("img") or (link.parent.select_one("img") if link.parent else None)
        image_url = (image.get("data-src") or image.get("src")) if image else None
        items.append(
            {
                "guid": f"{DADAAFA_SOURCE}:{video_id}",
                "video_id": video_id,
                "url": detail_url(base_url, video_id),
                "title": link.get("title") or link.get_text(" ", strip=True),
                "image": normalize_asset_url(page_url, image_url),
            }
        )
    return items


def resolve_nuxt_value(payload: list, value, seen: set[int] | None = None):
    if seen is None:
        seen = set()
    if type(value) is int and 0 <= value < len(payload):
        if value in seen:
            return None
        return resolve_nuxt_value(payload, payload[value], seen | {value})
    if isinstance(value, dict):
        return {key: resolve_nuxt_value(payload, candidate, seen.copy()) for key, candidate in value.items()}
    if isinstance(value, list):
        return [resolve_nuxt_value(payload, candidate, seen.copy()) for candidate in value]
    return value


def extract_nuxt_payload(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NUXT_DATA__")
    if not script:
        raise ValueError("DadaAFA page is missing __NUXT_DATA__.")
    raw = script.string or script.get_text()
    payload = json.loads(raw)
    if not isinstance(payload, list):
        raise ValueError("DadaAFA __NUXT_DATA__ is not a list.")
    root = resolve_nuxt_value(payload, 1)
    if not isinstance(root, dict):
        raise ValueError("DadaAFA __NUXT_DATA__ root is not an object.")
    return root


def find_video_detail_info(root: dict, video_id: str | None = None) -> dict:
    candidates: list[dict] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            detail = value.get("videoDetailInfo")
            if isinstance(detail, dict):
                candidates.append(detail)
            for candidate in value.values():
                walk(candidate)
        elif isinstance(value, list):
            for candidate in value:
                walk(candidate)

    walk(root)
    if video_id:
        for candidate in candidates:
            if non_empty(candidate.get("id")) == video_id:
                return candidate
    if candidates:
        return candidates[0]
    raise ValueError("DadaAFA detail payload is missing videoDetailInfo.")


def truthy(value) -> bool:
    if value is True:
        return True
    return str(value).strip().lower() in {"1", "true", "y", "yes"}


def is_ad_payload(payload: dict) -> bool:
    for key in ("is_ad", "isAd", "ad", "is_advertisement", "promotion", "is_promotion"):
        if truthy(payload.get(key)):
            return True
    return False


def unique_tags(values: list[object] | None) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        if isinstance(value, dict):
            raw = value.get("name") or value.get("title") or value.get("label") or value.get("id")
        else:
            raw = value
        tag = non_empty(raw)
        key = tag.lower() if tag else ""
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def detail_tags(payload: dict) -> list[str]:
    values: list[object] = []
    for key in ("labels", "topics", "actors"):
        if isinstance(payload.get(key), list):
            values.extend(payload[key])
    return unique_tags(values)


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, (list_item or {}).get("url") or detail_page_url)
    video_id = detail_id_from_url(detail_page_url) or (list_item or {}).get("video_id")
    payload = find_video_detail_info(extract_nuxt_payload(html), video_id)
    if is_ad_payload(payload):
        raise ValueError("DadaAFA detail payload is marked as ad.")
    if payload.get("type") and non_empty(payload.get("type")) != "video":
        raise ValueError("DadaAFA detail payload is not a video.")

    resolved_id = non_empty(payload.get("id")) or video_id
    if not resolved_id:
        raise ValueError("DadaAFA detail payload is missing id.")
    if video_id and resolved_id != video_id:
        raise ValueError("DadaAFA detail payload id does not match URL.")

    base_url = normalize_dadaafa_target_value(detail_page_url)
    video_url = normalize_asset_url(base_url, non_empty(payload.get("url")))
    if not video_url or ".m3u8" not in urlparse(video_url).path.lower():
        raise ValueError("DadaAFA detail payload is missing HLS video URL.")

    duration = int_or_none(payload.get("play_duration")) or 0
    if duration and duration < DADAAFA_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("DadaAFA detail duration is too short for a real video.")

    title = non_empty(payload.get("title")) or (list_item or {}).get("title") or DADAAFA_SITE_NAME
    description = non_empty(payload.get("content")) or title
    source_url = detail_url(base_url, resolved_id)
    image = normalize_asset_url(base_url, non_empty(payload.get("cover"))) or (list_item or {}).get("image")
    preview = normalize_asset_url(base_url, non_empty(payload.get("preview")))
    thumbs_vtt = normalize_asset_url(base_url, non_empty(payload.get("thumbs_vtt")))
    tags = detail_tags(payload)
    player = {
        "guid": f"{DADAAFA_SOURCE}:{resolved_id}",
        "video_id": resolved_id,
        "player_index": 1,
        "video_title": title,
        "video_url": video_url,
        "video_type": "hls",
        "thumb_vtt_url": thumbs_vtt,
        "tags": tags,
    }
    return {
        "url": source_url,
        "video_id": resolved_id,
        "title": title,
        "description": description,
        "image": image,
        "preview": preview,
        "thumb_vtt_url": thumbs_vtt,
        "published_at": parse_epoch_datetime(payload.get("created_time")) or now_utc(),
        "modified_at": None,
        "duration": duration,
        "play_count": int_or_none(payload.get("play_count")),
        "size": int_or_none(payload.get("size")),
        "is_hot_video": truthy(payload.get("is_hot_video")),
        "is_recommend_video": truthy(payload.get("is_recommend_video")),
        "is_vertical": truthy(payload.get("is_vertical")),
        "is_vip_video": truthy(payload.get("is_vip_video")),
        "tags": tags,
        "players": [player],
    }


def reject_ad_url(url: str) -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"DadaAFA playback URL points to an ad host: {host}")


def parse_auth_key_expiry(video_url: str) -> datetime | None:
    auth_key = parse_qs(urlparse(video_url).query).get("auth_key", [None])[0]
    if not auth_key:
        return None
    first_part = auth_key.split("-", 1)[0]
    return parse_epoch_datetime(first_part)


def parse_query_expiry(video_url: str) -> datetime | None:
    auth_expiry = parse_auth_key_expiry(video_url)
    if auth_expiry:
        return auth_expiry
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        value = (query.get(key) or [None])[0]
        parsed = parse_epoch_datetime(value)
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    return None


def playback_expiry(urls: list[str]) -> datetime | None:
    expiries = [expiry for expiry in (parse_query_expiry(url) for url in urls) if expiry]
    return min(expiries) if expiries else None


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-KEY"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def origin_header(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def playback_headers(referer: str) -> dict[str, str]:
    result = {"Referer": referer}
    origin = origin_header(referer)
    if origin:
        result["Origin"] = origin
    return result


def expected_duration_floor(expected_duration: int | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(float(DADAAFA_MIN_VIDEO_DURATION_SECONDS), expected_duration * 0.6)
    return float(DADAAFA_MIN_VIDEO_DURATION_SECONDS)


def verify_hls_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("DadaAFA video URL must be an HLS .m3u8 URL.")

    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("DadaAFA HLS playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("DadaAFA HLS playlist is too short for the video metadata.")

    media_urls = playlist_media_urls(video_url, playlist)
    if not media_urls:
        raise ValueError("DadaAFA HLS playlist has no media segments.")
    key_urls = playlist_key_urls(video_url, playlist)
    expires_at = playback_expiry([video_url, *media_urls[:3], *key_urls[:1]])
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("DadaAFA HLS URL is already expired or too close to expiry.")

    checked_key = False
    for key_url in key_urls[:2]:
        reject_ad_url(key_url)
        chunk = read_hls_chunk(key_url, referer, 16)
        if len(chunk) == 16:
            checked_key = True
            break
    if key_urls and not checked_key:
        raise ValueError("DadaAFA HLS playlist has no readable AES key.")

    checked_segment = False
    segment_error: Exception | None = None
    for media_url in media_urls[:8]:
        reject_ad_url(media_url)
        try:
            chunk = read_hls_chunk(media_url, referer, 376)
            if chunk:
                checked_segment = True
                break
        except Exception as exc:
            segment_error = exc
    if not checked_segment:
        raise ValueError(f"DadaAFA HLS playlist has no readable segment: {segment_error}")

    return {
        "video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or DADAAFA_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "playlist_bytes": len(playlist.encode("utf-8")),
        "playlist_duration_seconds": total_duration,
        "media_url_count": len(media_urls),
        "key_url_count": len(key_urls),
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_dadaafa_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (DADAAFA_SOURCE, DADAAFA_KIND, value, normalize_site_target_key(value)),
        )
        return cur.fetchone()


def ensure_target(conn, base_url: str, *, public_pool: bool = True) -> dict:
    target_row = upsert_target(conn, base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO target_profiles (target_id, scope, tags, category, weight, is_public_pool)
            VALUES (%s, 'system', %s, 'adult', 45, %s)
            ON CONFLICT (target_id) DO UPDATE SET scope = EXCLUDED.scope, tags = EXCLUDED.tags, category = EXCLUDED.category, weight = EXCLUDED.weight, is_public_pool = EXCLUDED.is_public_pool, updated_at = NOW()
            """,
            (target_row["id"], Jsonb([DADAAFA_SITE_NAME, "视频"]), public_pool),
        )
    return target_row


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def upsert_crawl_state(conn, target_id: str, *, last_guid: str | None, last_error: str | None, success: bool) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO crawl_state (target_id, last_guid, last_checked_at, last_success_at, last_error)
            VALUES (%s, %s, NOW(), CASE WHEN %s THEN NOW() ELSE NULL END, %s)
            ON CONFLICT (target_id) DO UPDATE SET
                last_guid = COALESCE(EXCLUDED.last_guid, crawl_state.last_guid),
                last_checked_at = EXCLUDED.last_checked_at,
                last_success_at = CASE WHEN %s THEN EXCLUDED.last_checked_at ELSE crawl_state.last_success_at END,
                last_error = EXCLUDED.last_error,
                updated_at = NOW()
            """,
            (target_id, last_guid, success, last_error, success),
        )


def build_author_presentation(link: str) -> dict[str, str | None]:
    return {
        "display_author": DADAAFA_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": DADAAFA_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": DADAAFA_KIND,
        "target_value": target_row["value"],
        "site_name": DADAAFA_SITE_NAME,
        "source_url": detail["url"],
        "dadaafa_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "video_poster_url": detail.get("image"),
        "preview_url": detail.get("preview"),
        "thumb_vtt_url": player.get("thumb_vtt_url") or detail.get("thumb_vtt_url"),
        "duration": detail.get("duration"),
        "play_count": detail.get("play_count"),
        "size": detail.get("size"),
        "is_hot_video": detail.get("is_hot_video"),
        "is_recommend_video": detail.get("is_recommend_video"),
        "is_vertical": detail.get("is_vertical"),
        "is_vip_video": detail.get("is_vip_video"),
        "tags": detail.get("tags") or player.get("tags") or [],
        "resolver": "dadaafa-nuxt-video-detail",
        "resolved_at": now_iso(),
        "playback_headers": verified.get("playback_headers"),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (
                target_id, guid, author, fullname,
                display_author, display_handle, author_profile_url, author_profile_platform,
                title, content,
                link, x_url, images, video_url, expires_at, video_url_expires_at,
                published_at, stored_at, is_retweet, metadata
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, NOW(), FALSE, %s)
            ON CONFLICT (target_id, guid) DO UPDATE SET
                display_author = EXCLUDED.display_author,
                display_handle = EXCLUDED.display_handle,
                author_profile_url = EXCLUDED.author_profile_url,
                author_profile_platform = EXCLUDED.author_profile_platform,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                images = EXCLUDED.images,
                video_url = EXCLUDED.video_url,
                expires_at = EXCLUDED.expires_at,
                video_url_expires_at = EXCLUDED.video_url_expires_at,
                published_at = COALESCE(items.published_at, EXCLUDED.published_at),
                metadata = items.metadata || EXCLUDED.metadata
            RETURNING (xmax = 0) AS inserted
            """,
            (
                target_row["id"],
                player["guid"],
                DADAAFA_SITE_NAME,
                DADAAFA_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
                detail.get("title"),
                detail["url"],
                Jsonb(images),
                verified["video_url"],
                expires_at,
                verified["video_url_expires_at"],
                published_at,
                Jsonb(metadata),
            ),
        )
        row = cur.fetchone()
    return bool(row and row.get("inserted"))


def monitor_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_dadaafa_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[dadaafa] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[dadaafa] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), list_item["guid"]):
                skipped_existing += 1
                page_existing += 1
                continue
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[dadaafa] skip detail {list_item.get('video_id')}: {exc}")
                continue
            if detail.get("published_at") and detail["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[dadaafa] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "link": detail["url"],
                            "published_at": detail["published_at"].isoformat() if detail.get("published_at") else None,
                            "video_url": verified["video_url"],
                            "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                            "playback_refresh_required": verified.get("playback_refresh_required"),
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                        }
                    )
                    continue
                if upsert_video_item(conn, target_row, detail, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
                    page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[dadaafa] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url LIKE 'https://dadaafa.cc/api/web/video/meta/%%' ORDER BY i.published_at DESC LIMIT %s""", (DADAAFA_SOURCE, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (DADAAFA_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (DADAAFA_SOURCE, refresh_window_minutes, limit)),
    ]
    seen_ids: set[str] = set()
    for sql, params in queries:
        if processed >= limit:
            break
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        for row in rows:
            row_id = str(row["id"])
            if row_id in seen_ids or processed >= limit:
                continue
            seen_ids.add(row_id)
            processed += 1
            metadata = row["metadata"] or {}
            source_url = metadata.get("source_url") or row.get("link")
            video_id = metadata.get("dadaafa_video_id") or str(row["guid"]).replace(f"{DADAAFA_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or DADAAFA_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or dadaafa_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "dadaafa-nuxt-video-detail",
                    "resolved_at": now_iso(),
                    "playback_headers": verified.get("playback_headers"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "preview_url": detail.get("preview") or metadata.get("preview_url"),
                    "thumb_vtt_url": detail.get("thumb_vtt_url") or metadata.get("thumb_vtt_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                }
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[dadaafa] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
