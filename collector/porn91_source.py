from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb


PORN91_SITE_NAME = "91porn"
PORN91_SOURCE = "91porn"
PORN91_KIND = "site"
PORN91_DEFAULT_BASE_URL = os.environ.get("PORN91_BASE_URL", "https://91porn.com/v.php?next=watch&page=1").strip()
PORN91_RETENTION_HOURS = int(os.environ.get("PORN91_RETENTION_HOURS", "84"))
PORN91_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("PORN91_REQUEST_TIMEOUT_SECONDS", "30"))
PORN91_MIN_VIDEO_DURATION_SECONDS = int(os.environ.get("PORN91_MIN_VIDEO_DURATION_SECONDS", "5"))
PORN91_REFRESH_WINDOW_MINUTES = int(os.environ.get("PORN91_REFRESH_WINDOW_MINUTES", "90"))
PORN91_CRITICAL_WINDOW_MINUTES = int(os.environ.get("PORN91_CRITICAL_WINDOW_MINUTES", "15"))
PORN91_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "kwai.net",
    "91selfie",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "doubleclick",
    "adnxs",
)
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "t")


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


def parse_date(value: str | None) -> datetime | None:
    raw = non_empty(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def parse_duration_seconds(value: str | None) -> int | None:
    raw = non_empty(value)
    if not raw:
        return None
    parts = raw.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    values = [int(part) for part in parts]
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    if len(values) == 3:
        hours, minutes, seconds = values
        return hours * 3600 + minutes * 60 + seconds
    return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_91porn_target_value(raw: str) -> str:
    value = (raw or PORN91_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("91porn target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_91porn_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"91porn.com", "www.91porn.com"}


def format_target_row(target_row: dict) -> str:
    return f"91porn:{target_row['value']}"


def headers(referer: str | None = None, *, accept: str | None = None, range_header: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    if range_header:
        result["Range"] = range_header
    return result


def request_with_proxy_fallback(
    url: str,
    *,
    referer: str | None = None,
    accept: str | None = None,
    range_header: str | None = None,
    stream: bool = False,
) -> requests.Response:
    last_error: Exception | None = None
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            response = session.get(
                url,
                headers=headers(referer, accept=accept, range_header=range_header),
                timeout=PORN91_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("91porn request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_text(url: str, referer: str) -> str:
    return request_with_proxy_fallback(url, referer=referer, accept="*/*").text


def read_media_chunk(url: str, referer: str, size: int = 2048) -> tuple[bytes, requests.Response]:
    response = request_with_proxy_fallback(
        url,
        referer=referer,
        accept="video/mp4,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        range_header=f"bytes=0-{size - 1}",
        stream=True,
    )
    return next(response.iter_content(size), b""), response


def list_query_from_target(raw: str | None) -> dict[str, str]:
    value = (raw or PORN91_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    query = {key: value for key, value in parse_qsl(parsed.query, keep_blank_values=True)}
    if not query:
        query["next"] = "watch"
    return query


def build_list_page_url(base_url: str, page: int) -> str:
    target = normalize_91porn_target_value(base_url)
    query = list_query_from_target(base_url)
    query["page"] = str(page)
    return f"{target}/v.php?{urlencode(query)}"


def detail_id_from_url(url: str) -> str | None:
    return non_empty((parse_qs(urlparse(url).query).get("viewkey") or [""])[0])


def detail_url(base_url: str, video_id: str) -> str:
    return f"{normalize_91porn_target_value(base_url)}/view_video.php?{urlencode({'viewkey': video_id})}"


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url + "/", html_unescape(raw))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="view_video.php"][href*="viewkey="]'):
        href = link.get("href")
        source_url = normalize_asset_url(page_url, href)
        video_id = detail_id_from_url(source_url or "")
        if not source_url or not video_id or video_id in seen:
            continue
        seen.add(video_id)
        image = link.select_one("img")
        image_url = image.get("data-src") or image.get("src") if image else None
        duration_text = link.select_one(".duration")
        title = link.select_one(".video-title")
        items.append(
            {
                "guid": f"{PORN91_SOURCE}:{video_id}",
                "video_id": video_id,
                "url": detail_url(base_url, video_id),
                "source_url": source_url,
                "title": html_unescape(title.get_text(" ", strip=True)) if title else PORN91_SITE_NAME,
                "image": normalize_asset_url(page_url, image_url),
                "duration": parse_duration_seconds(duration_text.get_text(" ", strip=True) if duration_text else None),
                "published_at": now_utc(),
            }
        )
    return items


def source_tags(soup: BeautifulSoup) -> list[dict[str, str | None]]:
    tags: list[dict[str, str | None]] = []
    for match in re.finditer(r"document\.write\(\s*strencode2\(\s*(?P<quote>['\"])(?P<payload>.*?)(?P=quote)\s*\)\s*\)", str(soup), flags=re.DOTALL):
        decoded = unquote(match.group("payload"))
        decoded_soup = BeautifulSoup(decoded, "html.parser")
        for source in decoded_soup.find_all("source"):
            tags.append({"src": non_empty(source.get("src")), "type": non_empty(source.get("type"))})
    for source in soup.select("video source[src]"):
        tags.append({"src": non_empty(source.get("src")), "type": non_empty(source.get("type"))})
    return [tag for tag in tags if tag.get("src")]


def extract_video_source(soup: BeautifulSoup, detail_page_url: str) -> dict[str, str]:
    candidates = []
    for tag in source_tags(soup):
        src = normalize_asset_url(detail_page_url, tag.get("src"))
        if not src:
            continue
        parsed = urlparse(src)
        path = parsed.path.lower()
        if path.endswith(".mp4"):
            candidates.append({"video_url": src, "video_type": "mp4"})
        elif path.endswith(".m3u8"):
            candidates.append({"video_url": src, "video_type": "hls"})
    if not candidates:
        raise ValueError("91porn detail page is missing a playable mp4/m3u8 source.")
    return candidates[0]


def detail_title(soup: BeautifulSoup, fallback: str | None = None) -> str:
    heading = soup.select_one(".video-border h4.login_register_header")
    if heading:
        for tag in heading.find_all(["img", "br"]):
            tag.decompose()
        title = non_empty(heading.get_text(" ", strip=True))
        if title:
            return html_unescape(title)
    raw_title = non_empty(soup.title.string if soup.title else None)
    if raw_title:
        return html_unescape(re.sub(r"\s*-\s*91porn\s*$", "", raw_title).strip())
    return fallback or PORN91_SITE_NAME


def first_info_value(soup: BeautifulSoup, label: str) -> str | None:
    for info in soup.select("span.info"):
        if label.lower() not in info.get_text(" ", strip=True).lower():
            continue
        sibling = info.find_next_sibling("span")
        if sibling:
            return non_empty(sibling.get_text(" ", strip=True))
    return None


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    parsed = urlparse(detail_page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = fetch_html(detail_page_url, (list_item or {}).get("source_url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    video_id = detail_id_from_url(detail_page_url) or non_empty((list_item or {}).get("video_id"))
    if not video_id:
        raise ValueError("91porn detail page is missing viewkey.")
    source = extract_video_source(soup, detail_page_url)

    title = detail_title(soup, non_empty((list_item or {}).get("title")))
    description = title
    poster = soup.select_one("video[poster]")
    image = normalize_asset_url(detail_page_url, poster.get("poster") if poster else None) or (list_item or {}).get("image")
    runtime = parse_duration_seconds(first_info_value(soup, "Runtime")) or (list_item or {}).get("duration")
    if runtime and runtime < PORN91_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("91porn detail duration is too short for a real video.")
    author_link = soup.select_one('a[href*="uprofile.php?UID="]')
    author_name = non_empty(author_link.get_text(" ", strip=True)) if author_link else None
    author_url = normalize_asset_url(base_url, author_link.get("href")) if author_link else None
    published_at = parse_date(first_info_value(soup, "Added")) or (list_item or {}).get("published_at") or now_utc()
    player = {
        "guid": f"{PORN91_SOURCE}:{video_id}",
        "video_id": video_id,
        "player_index": 1,
        "video_title": title,
        "video_url": source["video_url"],
        "video_type": source["video_type"],
    }
    return {
        "url": detail_url(base_url, video_id),
        "video_id": video_id,
        "title": title,
        "description": html_unescape(description),
        "image": image,
        "author_name": author_name,
        "author_url": author_url,
        "duration": runtime,
        "published_at": published_at,
        "modified_at": None,
        "players": [player],
    }


def reject_ad_url(url: str, label: str = "playback") -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"91porn {label} URL points to an ad host: {host}")


def parse_query_expiry(video_url: str) -> datetime | None:
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        parsed = parse_epoch_datetime((query.get(key) or [None])[0])
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


def verify_hls_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("91porn HLS URL must be an .m3u8 URL.")
    playlist = fetch_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("91porn HLS playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if expected_duration and total_duration < max(PORN91_MIN_VIDEO_DURATION_SECONDS, expected_duration * 0.5):
        raise ValueError("91porn HLS playlist is too short for the video metadata.")
    media_urls = playlist_media_urls(video_url, playlist)
    if not media_urls:
        raise ValueError("91porn HLS playlist has no media segments.")
    key_urls = playlist_key_urls(video_url, playlist)
    expires_at = playback_expiry([video_url, *media_urls[:3], *key_urls[:1]])
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("91porn HLS URL is already expired or too close to expiry.")
    segment_error: Exception | None = None
    for media_url in media_urls[:6]:
        reject_ad_url(media_url, "segment")
        try:
            chunk, _response = read_media_chunk(media_url, referer, 512)
            if chunk:
                return {
                    "video_url": video_url,
                    "video_url_expires_at": expires_at or PORN91_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "playlist_duration_seconds": total_duration,
                    "media_url_count": len(media_urls),
                    "key_url_count": len(key_urls),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"91porn HLS playlist has no readable segment: {segment_error}")


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_mp4_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".mp4"):
        raise ValueError("91porn MP4 URL must be an .mp4 URL.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("91porn MP4 URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, referer, 2048)
    if not chunk:
        raise ValueError("91porn MP4 URL returned an empty media chunk.")
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206}:
        raise ValueError(f"91porn MP4 URL returned unexpected status {response.status_code}.")
    if "text/html" in content_type:
        raise ValueError("91porn MP4 URL returned HTML instead of media.")
    if expected_duration and expected_duration < PORN91_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("91porn MP4 duration is too short for a real video.")
    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or PORN91_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "mp4",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, referer: str, video_type: str, expected_duration: int | None = None) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, referer, expected_duration)
    return verify_mp4_url(video_url, referer, expected_duration)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_91porn_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (PORN91_SOURCE, PORN91_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([PORN91_SITE_NAME, "视频"]), public_pool),
        )
    return target_row


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def update_existing_item_text(conn, target_id: str, guid: str, title: str | None) -> bool:
    text = non_empty(title)
    if not text:
        return False
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE items
            SET title = %s,
                content = %s,
                stored_at = stored_at
            WHERE target_id = %s AND guid = %s
            """,
            (text, text, target_id, guid),
        )
        return cur.rowcount > 0


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
        "display_author": PORN91_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": PORN91_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("title") or player.get("video_title") or PORN91_SITE_NAME
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": PORN91_KIND,
        "target_value": target_row["value"],
        "site_name": PORN91_SITE_NAME,
        "source_url": detail["url"],
        "porn91_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "resolver": "91porn-video-source",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "content_type": verified.get("content_type"),
        "content_length": verified.get("content_length"),
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
                PORN91_SITE_NAME,
                PORN91_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
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
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = text_refreshed = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = page_text_refreshed = 0
        print(f"[91porn] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[91porn] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), list_item["guid"]):
                if update_existing_item_text(conn, str(target_row["id"]), list_item["guid"], list_item.get("title")):
                    text_refreshed += 1
                    page_text_refreshed += 1
                skipped_existing += 1
                page_existing += 1
                continue
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[91porn] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(player["video_url"], detail["url"], player["video_type"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[91porn] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "title": player.get("video_title"),
                            "link": detail["url"],
                            "published_at": detail["published_at"].isoformat() if detail.get("published_at") else None,
                            "video_url": verified["video_url"],
                            "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                            "playback_refresh_required": verified.get("playback_refresh_required"),
                            "media_format": verified.get("media_format"),
                            "content_type": verified.get("content_type"),
                            "content_length": verified.get("content_length"),
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
            f"[91porn] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} text_refreshed={page_text_refreshed} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and page_old == len(list_items):
            break
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "text_refreshed": text_refreshed, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (PORN91_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (PORN91_SOURCE, refresh_window_minutes, limit)),
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
            video_id = metadata.get("porn91_video_id") or str(row["guid"]).replace(f"{PORN91_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or PORN91_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or porn91_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(player["video_url"], detail["url"], player["video_type"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "91porn-video-source",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "porn91_video_id": detail["video_id"],
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "duration": detail.get("duration") or metadata.get("duration"),
                    "author_name": detail.get("author_name") or metadata.get("author_name"),
                    "author_url": detail.get("author_url") or metadata.get("author_url"),
                    "content_type": verified.get("content_type"),
                    "content_length": verified.get("content_length"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                }
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[91porn] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
