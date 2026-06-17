from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import update_item_document as update_opensearch_item_document
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import update_item_document as update_opensearch_item_document
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


BADNEWS_SITE_NAME = "Bad.news"
BADNEWS_SOURCE = "badnews"
BADNEWS_KIND = "site"
BADNEWS_DEFAULT_BASE_URL = os.environ.get("BADNEWS_BASE_URL", "https://bad.news").strip().rstrip("/")
BADNEWS_RETENTION_HOURS = int(os.environ.get("BADNEWS_RETENTION_HOURS", "84"))
BADNEWS_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("BADNEWS_REQUEST_TIMEOUT_SECONDS", "30"))
BADNEWS_MIN_VIDEO_DURATION_SECONDS = int(os.environ.get("BADNEWS_MIN_VIDEO_DURATION_SECONDS", "3"))
BADNEWS_REFRESH_WINDOW_MINUTES = int(os.environ.get("BADNEWS_REFRESH_WINDOW_MINUTES", "90"))
BADNEWS_CRITICAL_WINDOW_MINUTES = int(os.environ.get("BADNEWS_CRITICAL_WINDOW_MINUTES", "15"))
BADNEWS_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
BADNEWS_LOCAL_TIMEZONE = timezone(timedelta(hours=8))

AD_HOST_KEYWORDS = (
    "statcounter",
    "trafficstars",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "adnxs",
    "doubleclick",
)
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "t")
DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def non_empty(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def clean_text(value: str | None) -> str | None:
    text = non_empty(value)
    if not text:
        return None
    return re.sub(r"\s+", " ", html_unescape(text)).strip() or None


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


def parse_badnews_datetime(value: str | None) -> datetime | None:
    raw = clean_text(value)
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y/%m/%d %H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt)
            return parsed.replace(tzinfo=BADNEWS_LOCAL_TIMEZONE).astimezone(timezone.utc)
        except ValueError:
            pass
    return None


def parse_duration_seconds(value: str | None) -> int | None:
    raw = clean_text(value)
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


def normalize_badnews_target_value(raw: str) -> str:
    value = (raw or BADNEWS_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("Bad.news target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_badnews_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"bad.news", "www.bad.news"}


def format_target_row(target_row: dict) -> str:
    return f"badnews:{target_row['value']}"


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
                timeout=BADNEWS_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("Bad.news request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def media_referer_for_url(url: str, page_url: str | None) -> str | None:
    host = urlparse(url).netloc.lower()
    if host == "video.twimg.com" or host.endswith(".video.twimg.com") or host == "pbs.twimg.com" or host.endswith(".pbs.twimg.com"):
        return None
    return page_url


def fetch_media_text(url: str, page_url: str | None) -> str:
    reject_ad_url(url)
    return request_with_proxy_fallback(url, referer=media_referer_for_url(url, page_url), accept="*/*").text


def read_media_chunk(url: str, page_url: str | None, size: int = 2048) -> tuple[bytes, requests.Response]:
    reject_ad_url(url)
    response = request_with_proxy_fallback(
        url,
        referer=media_referer_for_url(url, page_url),
        accept="video/mp4,video/webm,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        range_header=f"bytes=0-{size - 1}",
        stream=True,
    )
    return next(response.iter_content(size), b""), response


def build_list_page_url(base_url: str, page: int) -> str:
    page = max(1, page)
    return f"{normalize_badnews_target_value(base_url)}/sort-new/page-{page}"


def detail_url(base_url: str, video_id: str) -> str:
    return f"{normalize_badnews_target_value(base_url)}/t/{video_id}"


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/t/(\d+)/?$", urlparse(url).path)
    return match.group(1) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url + "/", html_unescape(raw))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def classify_video_type(video_url: str, data_type: str | None = None) -> str:
    kind = (data_type or "").strip().lower()
    path = urlparse(video_url).path.lower()
    if kind in {"m3u8", "hls", "application/vnd.apple.mpegurl", "application/x-mpegurl"} or path.endswith(".m3u8"):
        return "hls"
    if kind in {"mp4", "video/mp4"} or path.endswith(DIRECT_VIDEO_EXTENSIONS):
        return "direct"
    raise ValueError("Bad.news video source is not a supported direct video or HLS URL.")


def normalize_title(title: str | None, author: str | None, video_id: str) -> str:
    value = clean_text(title)
    if value and value.lower() not in {"watch video", "video"}:
        return value
    author_value = clean_text(author)
    if author_value:
        return f"{author_value} video"
    return f"{BADNEWS_SITE_NAME} video {video_id}"


def parse_entry(entry, page_url: str, base_url: str) -> dict | None:
    video = entry.select_one("video.my-videos[data-source]") or entry.select_one("video[data-source]")
    if not video:
        return None
    video_id = clean_text(video.get("data-id"))
    detail_link = entry.select_one('a.dateline[href^="/t/"], a[href^="/t/"]')
    resolved_detail_url = normalize_asset_url(page_url, detail_link.get("href") if detail_link else None)
    video_id = video_id or detail_id_from_url(resolved_detail_url or "")
    if not video_id:
        return None

    author_link = entry.select_one("a.author")
    author_name = clean_text(author_link.get_text(" ", strip=True) if author_link else None)
    author_url = normalize_asset_url(page_url, author_link.get("href") if author_link else None)
    title_node = entry.select_one("a.title")
    title = normalize_title(title_node.get_text(" ", strip=True) if title_node else None, author_name, video_id)
    time_node = entry.select_one("time[datetime]")
    published_at = parse_badnews_datetime(time_node.get("datetime") if time_node else None) or now_utc()
    duration_node = entry.select_one(".ct-time span")
    source_url = normalize_asset_url(page_url, video.get("data-source"))
    if not source_url:
        return None
    video_type = classify_video_type(source_url, video.get("data-type"))
    tag_nodes = entry.select("h4.label, a[href^='/tag/']")
    tags = []
    seen_tags = set()
    for tag_node in tag_nodes:
        tag = clean_text(tag_node.get_text(" ", strip=True))
        if tag and tag.lower() not in seen_tags:
            tags.append(tag)
            seen_tags.add(tag.lower())

    return {
        "guid": f"{BADNEWS_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": resolved_detail_url or detail_url(base_url, video_id),
        "title": title,
        "description": title,
        "image": normalize_asset_url(page_url, video.get("poster")),
        "author_name": author_name,
        "author_url": author_url,
        "duration": parse_duration_seconds(duration_node.get_text(" ", strip=True) if duration_node else None),
        "published_at": published_at,
        "modified_at": None,
        "tags": tags,
        "players": [
            {
                "guid": f"{BADNEWS_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": 1,
                "video_title": title,
                "video_url": source_url,
                "video_type": video_type,
            }
        ],
    }


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for entry in soup.select("div.entry"):
        item = parse_entry(entry, page_url, base_url)
        if not item or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        items.append(item)
    return items


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    if list_item and list_item.get("players"):
        return list_item
    parsed = urlparse(detail_page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = fetch_html(detail_page_url, detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    expected_video_id = detail_id_from_url(detail_page_url)
    candidates = []
    for entry in soup.select("div.entry"):
        item = parse_entry(entry, detail_page_url, base_url)
        if item:
            candidates.append(item)
    if expected_video_id:
        for candidate in candidates:
            if candidate.get("video_id") == expected_video_id:
                return candidate
    if candidates:
        return candidates[0]
    raise ValueError("Bad.news detail page is missing a playable video entry.")


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"Bad.news {label} URL points to an ad host: {host}")
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Bad.news {label} URL must be http(s).")


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


def playlist_stream_variants(video_url: str, playlist: str) -> list[dict]:
    variants = []
    stream_inf = None
    for line in playlist.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#EXT-X-STREAM-INF"):
            stream_inf = value
            continue
        if stream_inf and not value.startswith("#"):
            variants.append({"url": urljoin(video_url, value), "stream_inf": stream_inf})
            stream_inf = None
    return variants


def playlist_media_group_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-MEDIA"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def variant_bandwidth(stream_inf: str) -> int:
    match = re.search(r"BANDWIDTH=(\d+)", stream_inf)
    return int(match.group(1)) if match else 0


def sort_variants_for_probe(variants: list[dict]) -> list[dict]:
    return sorted(variants, key=lambda variant: variant_bandwidth(str(variant.get("stream_inf") or "")))


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_map_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-MAP"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-KEY"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def expected_duration_floor(expected_duration: int | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(float(BADNEWS_MIN_VIDEO_DURATION_SECONDS), expected_duration * 0.5)
    return float(BADNEWS_MIN_VIDEO_DURATION_SECONDS)


def looks_like_media_segment(chunk: bytes) -> bool:
    if len(chunk) >= 188 and chunk[0] == 0x47:
        return True
    prefix = chunk[:128]
    return any(marker in prefix for marker in (b"ftyp", b"moof", b"mdat", b"sidx", b"\x1a\x45\xdf\xa3"))


def verify_media_playlist(media_url: str, playlist: str, page_url: str | None, expected_duration: int | None) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("Bad.news HLS media playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("Bad.news HLS playlist is too short for the video metadata.")

    media_urls = playlist_media_urls(media_url, playlist)
    if not media_urls:
        raise ValueError("Bad.news HLS playlist has no media segments.")

    map_urls = playlist_map_urls(media_url, playlist)
    key_urls = playlist_key_urls(media_url, playlist)

    for key_url in key_urls[:2]:
        chunk, _response = read_media_chunk(key_url, page_url, 16)
        if len(chunk) != 16:
            raise ValueError("Bad.news HLS playlist has an unreadable AES key.")

    checked_init = False
    for map_url in map_urls[:2]:
        chunk, _response = read_media_chunk(map_url, page_url, 256)
        if chunk and looks_like_media_segment(chunk):
            checked_init = True
            break
    if map_urls and not checked_init:
        raise ValueError("Bad.news HLS playlist has no readable fMP4 init segment.")

    segment_error: Exception | None = None
    for media_url_candidate in media_urls[:8]:
        try:
            chunk, _response = read_media_chunk(media_url_candidate, page_url, 512)
            if looks_like_media_segment(chunk):
                return {
                    "playlist_duration_seconds": total_duration,
                    "media_url_count": len(media_urls),
                    "map_url_count": len(map_urls),
                    "key_url_count": len(key_urls),
                    "checked_media_playlist_url": media_url,
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"Bad.news HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("Bad.news HLS URL must be an .m3u8 URL.")

    master_playlist = fetch_media_text(video_url, page_url)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("Bad.news HLS URL is not a playlist.")

    checked_urls = [video_url]
    media_group_urls = playlist_media_group_urls(video_url, master_playlist)
    checked_urls.extend(media_group_urls[:2])
    variants = playlist_stream_variants(video_url, master_playlist)
    media_result = None
    media_error: Exception | None = None
    if variants:
        for variant in sort_variants_for_probe(variants):
            variant_url = variant["url"]
            try:
                reject_ad_url(variant_url, "variant")
                media_playlist = fetch_media_text(variant_url, page_url)
                checked_urls.append(variant_url)
                checked_urls.extend(playlist_media_urls(variant_url, media_playlist)[:3])
                checked_urls.extend(playlist_map_urls(variant_url, media_playlist)[:1])
                checked_urls.extend(playlist_key_urls(variant_url, media_playlist)[:1])
                media_result = {
                    **verify_media_playlist(variant_url, media_playlist, page_url, expected_duration),
                    "variant_count": len(variants),
                    "selected_variant_url": variant_url,
                    "selected_variant_stream_inf": variant.get("stream_inf"),
                    "media_group_url_count": len(media_group_urls),
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"Bad.news HLS master playlist has no playable variant: {media_error}")
    else:
        checked_urls.extend(playlist_media_urls(video_url, master_playlist)[:3])
        checked_urls.extend(playlist_map_urls(video_url, master_playlist)[:1])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_playlist(video_url, master_playlist, page_url, expected_duration),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
            "media_group_url_count": len(media_group_urls),
        }

    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("Bad.news HLS URL is already expired or too close to expiry.")

    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or BADNEWS_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "hls",
        "playlist_bytes": len(master_playlist.encode("utf-8")),
        **media_result,
    }


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_direct_video_url(video_url: str, page_url: str | None, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("Bad.news direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("Bad.news direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 2048)
    if not chunk:
        raise ValueError("Bad.news direct video URL returned an empty media chunk.")
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206}:
        raise ValueError(f"Bad.news direct video URL returned unexpected status {response.status_code}.")
    if "text/html" in content_type:
        raise ValueError("Bad.news direct video URL returned HTML instead of media.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("Bad.news direct video URL did not return recognizable media bytes.")
    if expected_duration and expected_duration < BADNEWS_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("Bad.news direct video duration is too short for a real video.")
    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or BADNEWS_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, page_url: str | None, video_type: str, expected_duration: int | None = None) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, page_url, expected_duration)
    return verify_direct_video_url(video_url, page_url, expected_duration)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_badnews_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (BADNEWS_SOURCE, BADNEWS_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([BADNEWS_SITE_NAME, "video"]), public_pool),
        )
    return target_row


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def update_existing_item_text(conn, target_id: str, guid: str, item: dict) -> bool:
    title = clean_text(item.get("title"))
    if not title:
        return False
    images = [item["image"]] if item.get("image") else []
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        row = cur.fetchone()
    if row and row.get("id"):
        update_opensearch_item_document(
            str(row["id"]),
            title=title,
            caption=title,
            content=title,
            images=images if images else None,
        )
    return bool(row)


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


def build_author_presentation(detail: dict) -> dict[str, str | None]:
    return {
        "display_author": clean_text(detail.get("author_name")) or BADNEWS_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("author_url") or detail.get("url"),
        "author_profile_platform": BADNEWS_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or BADNEWS_SITE_NAME
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": BADNEWS_KIND,
        "target_value": target_row["value"],
        "site_name": BADNEWS_SITE_NAME,
        "source_url": detail["url"],
        "badnews_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "tags": detail.get("tags") or [],
        "resolver": "badnews-video-source",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "content_type": verified.get("content_type"),
        "content_length": verified.get("content_length"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "media_url_count": verified.get("media_url_count"),
        "map_url_count": verified.get("map_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "variant_count": verified.get("variant_count"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
        "media_group_url_count": verified.get("media_group_url_count"),
    }
    author_name = detail.get("author_name") or BADNEWS_SITE_NAME
    _item_id, inserted = upsert_item_record_with_opensearch(
        conn,
        target_id=str(target_row["id"]),
        guid=player["guid"],
        display_author=presentation["display_author"],
        display_handle=presentation["display_handle"],
        author_profile_url=presentation["author_profile_url"],
        author_profile_platform=presentation["author_profile_platform"],
        video_url=verified["video_url"],
        expires_at=expires_at,
        video_url_expires_at=verified["video_url_expires_at"],
        published_at=published_at,
        stored_at=now_utc(),
        is_retweet=False,
        metadata=metadata,
        cover_url=detail.get("image"),
        title=player.get("video_title") or detail.get("title"),
        caption=content,
        content=content,
        author=author_name,
        fullname=author_name,
        x_url=None,
        link=detail["url"],
        images=images,
    )
    return inserted


def monitor_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = text_refreshed = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page)
        page_inserted = page_updated = page_existing = page_text_refreshed = page_old = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[badnews] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[badnews] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), list_item["guid"]):
                if update_existing_item_text(conn, str(target_row["id"]), list_item["guid"], list_item):
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
                print(f"[badnews] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(player["video_url"], detail["url"], player["video_type"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[badnews] skip unverified {player['guid']}: {exc}")
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
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
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
            f"[badnews] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} text_refreshed={page_text_refreshed} "
            f"old={page_old} detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and page_old == len(list_items):
            break
    return {
        "pages": pages,
        "parsed_videos": parsed_videos,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "text_refreshed": text_refreshed,
        "skipped_existing": skipped_existing,
        "skipped_detail_errors": skipped_detail_errors,
        "skipped_unverified": skipped_unverified,
        "skipped_old": skipped_old,
        "samples": samples[:10],
    }


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (BADNEWS_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (BADNEWS_SOURCE, refresh_window_minutes, limit)),
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
            source_url = metadata.get("source_url")
            video_id = metadata.get("badnews_video_id") or str(row["guid"]).replace(f"{BADNEWS_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or BADNEWS_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or badnews_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(player["video_url"], detail["url"], player["video_type"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "badnews-video-source",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "badnews_video_id": detail["video_id"],
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
                    "map_url_count": verified.get("map_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "variant_count": verified.get("variant_count"),
                    "selected_variant_url": verified.get("selected_variant_url"),
                    "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
                    "media_group_url_count": verified.get("media_group_url_count"),
                }
                refresh_item_playback_in_opensearch(
                    conn,
                    item_id=row_id,
                    video_url=verified["video_url"],
                    video_url_expires_at=verified["video_url_expires_at"],
                    metadata=next_metadata,
                    cover_url=detail.get("image") or metadata.get("video_poster_url"),
                )
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[badnews] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
