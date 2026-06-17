from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import update_item_document as update_opensearch_item_document
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import update_item_document as update_opensearch_item_document
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


XXXTIK_SITE_NAME = "xxxtik"
XXXTIK_SOURCE = "xxxtik"
XXXTIK_KIND = "site"
XXXTIK_DEFAULT_BASE_URL = os.environ.get("XXXTIK_BASE_URL", "https://xxxtik.com").strip().rstrip("/") or "https://xxxtik.com"
XXXTIK_RETENTION_HOURS = int(os.environ.get("XXXTIK_RETENTION_HOURS", "168"))
XXXTIK_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("XXXTIK_REQUEST_TIMEOUT_SECONDS", "30"))
XXXTIK_PAGE_LIMIT = int(os.environ.get("XXXTIK_PAGE_LIMIT", "20"))
XXXTIK_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("XXXTIK_MIN_PLAYLIST_DURATION_SECONDS", "5"))
XXXTIK_REFRESH_WINDOW_MINUTES = int(os.environ.get("XXXTIK_REFRESH_WINDOW_MINUTES", "90"))
XXXTIK_CRITICAL_WINDOW_MINUTES = int(os.environ.get("XXXTIK_CRITICAL_WINDOW_MINUTES", "15"))
XXXTIK_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

XXXTIK_API_HOSTS = (
    "https://xxxtik-api-iw98m.ondigitalocean.app",
    "https://xxxtik-apix-s2l6l.ondigitalocean.app",
)
XXXTIK_SPACE_HOSTS = (
    "https://p5rn.com",
    "https://xcdn.tv",
)
XXXTIK_MEDIA_PATH_PREFIX = "/cdn/production/media/0312/"
XXXTIK_MEDIA_BASES = tuple(f"{host}{XXXTIK_MEDIA_PATH_PREFIX}" for host in XXXTIK_SPACE_HOSTS)
XXXTIK_UPLOAD_BASE = "https://upload.xxxtik.com"

AD_HOST_KEYWORDS = (
    "ads",
    "adservice",
    "adsterra",
    "clickadu",
    "doubleclick",
    "exoclick",
    "magsrv",
    "popads",
    "tsyndicate",
    "crme7srv",
)
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire")
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


def parse_api_datetime(value: str | None) -> datetime | None:
    raw = non_empty(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_xxxtik_target_value(raw: str) -> str:
    value = (raw or XXXTIK_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("xxxtik target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_xxxtik_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"xxxtik.com", "www.xxxtik.com"}


def format_target_row(target_row: dict) -> str:
    return f"xxxtik:{target_row['value']}"


def request_origin(referer: str | None) -> str | None:
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def headers(referer: str | None = None, *, accept: str | None = None, range_header: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": accept or "application/json,text/plain,*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
        origin = request_origin(referer)
        if origin:
            result["Origin"] = origin
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
                timeout=XXXTIK_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("xxxtik request failed.")


def api_get(path: str, params: dict | None = None) -> object:
    last_error: Exception | None = None
    query = f"?{urlencode(params)}" if params else ""
    for host in XXXTIK_API_HOSTS:
        try:
            response = request_with_proxy_fallback(
                f"{host.rstrip('/')}/{path.lstrip('/')}{query}",
                referer=XXXTIK_DEFAULT_BASE_URL + "/",
                accept="application/json,text/plain,*/*",
            )
            return response.json()
        except Exception as exc:
            last_error = exc
    raise last_error or ValueError("xxxtik API request failed.")


def fetch_new_posts(cursor: int | None = None, limit: int | None = None) -> list[dict]:
    payload = api_get("/post/new", {"limit": max(1, limit or XXXTIK_PAGE_LIMIT), "cursor": cursor or 0})
    if not isinstance(payload, list):
        raise ValueError("xxxtik new posts response is not a list.")
    return [post for post in payload if isinstance(post, dict)]


def fetch_post(uuid: str) -> dict:
    payload = api_get(f"/post/{uuid}")
    if not isinstance(payload, dict) or payload.get("response", {}).get("statusCode") == 404:
        raise ValueError("xxxtik post was not found.")
    return payload


def post_url(post: dict) -> str:
    uuid = non_empty(post.get("uuid")) or str(post.get("id"))
    return f"{normalize_xxxtik_target_value(XXXTIK_DEFAULT_BASE_URL)}/post/{uuid}"


def media_url(path: str) -> str:
    return f"{XXXTIK_MEDIA_BASES[0].rstrip('/')}/{path.lstrip('/')}"


def post_tags(post: dict) -> list[str]:
    tags = []
    for tag in post.get("tags") or []:
        if isinstance(tag, dict):
            name = clean_text(tag.get("name"))
            if name and name.lower() not in {existing.lower() for existing in tags}:
                tags.append(name)
    if XXXTIK_SITE_NAME not in tags:
        tags.append(XXXTIK_SITE_NAME)
    return tags[:12]


def post_title(post: dict) -> str:
    description = clean_text(post.get("description"))
    return description or f"{XXXTIK_SITE_NAME} video {post.get('uuid') or post.get('id')}"


def post_author_name(post: dict) -> str:
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    return clean_text(author.get("name")) or XXXTIK_SITE_NAME


def is_visible_video_post(post: dict) -> bool:
    return post.get("visible") is not False and post.get("status") in {None, "approved"}


def hls_candidates(post: dict) -> list[str]:
    uid = non_empty(post.get("uid"))
    if not uid:
        return []
    return [f"{base.rstrip('/')}/{uid}/master.m3u8" for base in XXXTIK_MEDIA_BASES]


def direct_candidates(post: dict) -> list[str]:
    candidates: list[str] = []
    video_name = non_empty(post.get("videoName"))
    count = max(1, int_or_none(post.get("count")) or 1)
    if video_name:
        for base in XXXTIK_MEDIA_BASES:
            for index in range(count):
                candidates.append(f"{base.rstrip('/')}/videos/{video_name}/{video_name}-{index}.mp4")
                candidates.append(f"{base.rstrip('/')}/videos/{video_name}/{video_name}-{index}-480.mp4")
    if post.get("redgifs") and post.get("path"):
        path = non_empty(post.get("path"))
        if path:
            for quality in ("hd", "sd"):
                candidates.append(f"{XXXTIK_API_HOSTS[0]}/util/source?{urlencode({'path': path, 'type': quality})}")
    redgifs_video = non_empty(post.get("redGifsVideoUrl"))
    if redgifs_video:
        candidates.append(redgifs_video)
        if "-mobile" in redgifs_video:
            candidates.append(redgifs_video.replace("-mobile", ""))
    return candidates


def image_candidates(post: dict) -> list[str]:
    candidates: list[str] = []
    uid = non_empty(post.get("uid"))
    if uid:
        candidates.extend(f"{base.rstrip('/')}/{uid}/thumbnail.webp" for base in XXXTIK_MEDIA_BASES)
    if post.get("redgifs") and post.get("path"):
        path = non_empty(post.get("path"))
        if path:
            candidates.append(f"{XXXTIK_API_HOSTS[0]}/util/source?{urlencode({'path': path, 'type': 'thumbnail'})}")
    redgifs_thumb = non_empty(post.get("redGifsThumbnailUrl"))
    if redgifs_thumb:
        candidates.append(redgifs_thumb)
    video_name = non_empty(post.get("videoName"))
    if video_name:
        for host in XXXTIK_SPACE_HOSTS:
            candidates.append(f"{host.rstrip('/')}/cdn/production/videos/{video_name}/{video_name}.png")
    return candidates


def parse_post(post: dict) -> dict | None:
    uuid = non_empty(post.get("uuid"))
    if not uuid or not is_visible_video_post(post):
        return None
    title = post_title(post)
    candidates = hls_candidates(post) + direct_candidates(post)
    if not candidates:
        return None
    images = image_candidates(post)
    published_at = parse_api_datetime(post.get("createdAt")) or now_utc()
    return {
        "guid": f"{XXXTIK_SOURCE}:{uuid}",
        "post_uuid": uuid,
        "post_id": post.get("id"),
        "uid": post.get("uid"),
        "url": post_url(post),
        "title": title,
        "description": title,
        "image": images[0] if images else None,
        "images": images[:3],
        "author_name": post_author_name(post),
        "author_url": normalize_xxxtik_target_value(XXXTIK_DEFAULT_BASE_URL),
        "published_at": published_at,
        "modified_at": parse_api_datetime(post.get("updatedAt")),
        "tags": post_tags(post),
        "width": post.get("width"),
        "height": post.get("height"),
        "players": [
            {
                "guid": f"{XXXTIK_SOURCE}:{uuid}",
                "post_uuid": uuid,
                "post_id": post.get("id"),
                "video_title": title,
                "video_url": candidates[0],
                "video_url_candidates": candidates,
                "video_type": "hls" if urlparse(candidates[0]).path.lower().endswith(".m3u8") else "direct",
                "allowed_media_hosts": {urlparse(candidate).netloc.lower() for candidate in candidates if urlparse(candidate).netloc},
            }
        ],
    }


def parse_list_page(base_url: str, page: int, *, cursor: int | None = None, limit: int | None = None) -> tuple[list[dict], int | None]:
    posts = fetch_new_posts(cursor=cursor, limit=limit)
    items: list[dict] = []
    seen: set[str] = set()
    for post in posts:
        item = parse_post(post)
        if not item or item["post_uuid"] in seen:
            continue
        seen.add(item["post_uuid"])
        items.append(item)
    next_cursor = int_or_none(posts[-1].get("id")) if posts else None
    return items, next_cursor


def parse_query_expiry(video_url: str) -> datetime | None:
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        parsed = parse_epoch_datetime((query.get(key) or [None])[0])
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    auth_key = non_empty((query.get("auth_key") or [None])[0])
    if auth_key:
        parsed = parse_epoch_datetime(auth_key.split("-", 1)[0])
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    return None


def playback_expiry(urls: list[str]) -> datetime | None:
    expiries = [expiry for expiry in (parse_query_expiry(url) for url in urls) if expiry]
    return min(expiries) if expiries else None


def same_or_subdomain(host: str, allowed_host: str) -> bool:
    return host == allowed_host or host.endswith(f".{allowed_host}")


def reject_ad_url(url: str, label: str = "playback", allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"xxxtik {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"xxxtik {label} URL points to an ad host: {host}")
    if allowed_hosts and not any(same_or_subdomain(host, allowed_host) for allowed_host in allowed_hosts):
        raise ValueError(f"xxxtik {label} URL is outside configured media hosts: {host}")


def fetch_media_text(url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> str:
    reject_ad_url(url, allowed_hosts=allowed_hosts)
    response = request_with_proxy_fallback(
        url,
        referer=page_url,
        accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
    )
    return response.content.decode("utf-8-sig", "replace")


def read_media_chunk(
    url: str,
    page_url: str | None,
    size: int,
    allowed_hosts: set[str] | None = None,
) -> tuple[bytes, requests.Response]:
    reject_ad_url(url, allowed_hosts=allowed_hosts)
    response = request_with_proxy_fallback(
        url,
        referer=page_url,
        accept="video/mp4,video/webm,video/mp2t,application/octet-stream,*/*",
        range_header=f"bytes=0-{size - 1}",
        stream=True,
    )
    try:
        return next(response.iter_content(size), b""), response
    except StopIteration:
        return b"", response


def parse_hls_attribute_list(line: str) -> dict[str, str]:
    if ":" in line:
        line = line.split(":", 1)[1]
    attrs: dict[str, str] = {}
    for match in re.finditer(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line):
        value = match.group(2)
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        attrs[match.group(1)] = value
    return attrs


def variant_playlists(video_url: str, playlist: str) -> list[str]:
    variants: list[str] = []
    previous_stream_inf = False
    for line in playlist.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#EXT-X-STREAM-INF"):
            previous_stream_inf = True
            continue
        if value.startswith("#"):
            continue
        if previous_stream_inf:
            variants.append(urljoin(video_url, value))
            previous_stream_inf = False
    return variants


def playlist_segments(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in playlist.splitlines():
        value = line.strip()
        if not value.startswith("#EXT-X-KEY"):
            continue
        attrs = parse_hls_attribute_list(value)
        uri = attrs.get("URI")
        if uri:
            key_url = urljoin(video_url, uri)
            if key_url not in seen:
                urls.append(key_url)
                seen.add(key_url)
    return urls


def looks_like_mpeg_ts(chunk: bytes) -> bool:
    if chunk.startswith(b"ID3"):
        return True
    for offset in range(min(188, len(chunk))):
        if len(chunk) > offset + 188 and chunk[offset] == 0x47 and chunk[offset + 188] == 0x47:
            return True
    return len(chunk) >= 188 and chunk[0] == 0x47


def looks_like_media_segment(chunk: bytes) -> bool:
    if looks_like_mpeg_ts(chunk):
        return True
    prefix = chunk[:128]
    return any(marker in prefix for marker in (b"ftyp", b"moof", b"mdat", b"sidx", b"\x1a\x45\xdf\xa3"))


def expected_duration_floor() -> float:
    return float(XXXTIK_MIN_PLAYLIST_DURATION_SECONDS)


def verify_media_hls_url(video_url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> dict:
    playlist = fetch_media_text(video_url, page_url, allowed_hosts)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("xxxtik HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor():
        raise ValueError("xxxtik HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("xxxtik HLS playlist has no media segments.")
    for key_url in playlist_key_urls(video_url, playlist)[:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
        if len(chunk) != 16:
            raise ValueError("xxxtik HLS AES key is not 16 bytes.")
    segment_error: Exception | None = None
    for segment_url in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment_url, page_url, 4096, allowed_hosts)
            if looks_like_media_segment(chunk):
                expires_at = playback_expiry([video_url, *segments[:3], *playlist_key_urls(video_url, playlist)[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("xxxtik HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "video_url_expires_at": expires_at or XXXTIK_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_duration_seconds": total_duration,
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "media_url_count": len(segments),
                    "key_url_count": len(playlist_key_urls(video_url, playlist)),
                    "encrypted": bool(playlist_key_urls(video_url, playlist)),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"xxxtik HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("xxxtik HLS URL must be an .m3u8 URL.")
    playlist = fetch_media_text(video_url, page_url, allowed_hosts)
    if "#EXTM3U" not in playlist:
        raise ValueError("xxxtik HLS URL is not a playlist.")
    variants = variant_playlists(video_url, playlist)
    if variants:
        last_error: Exception | None = None
        for variant_url in variants:
            try:
                verified = verify_media_hls_url(variant_url, page_url, allowed_hosts)
                expires_at = playback_expiry([video_url, variant_url])
                verified["video_url"] = video_url
                verified["raw_video_url"] = video_url
                verified["variant_url"] = variant_url
                verified["master_playlist_bytes"] = len(playlist.encode("utf-8"))
                if expires_at and expires_at < verified["video_url_expires_at"]:
                    verified["video_url_expires_at"] = expires_at
                    verified["playback_refresh_required"] = True
                return verified
            except Exception as exc:
                last_error = exc
        raise ValueError(f"xxxtik HLS master playlist has no playable variants: {last_error}")
    return verify_media_hls_url(video_url, page_url, allowed_hosts)


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_direct_video_url(video_url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("xxxtik direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("xxxtik direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 4096, allowed_hosts)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type:
        raise ValueError("xxxtik direct video URL did not return media bytes.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("xxxtik direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or XXXTIK_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_player_playback(player: dict, page_url: str | None) -> dict:
    errors = []
    allowed_hosts = player.get("allowed_media_hosts")
    for candidate in player.get("video_url_candidates") or [player["video_url"]]:
        try:
            if urlparse(candidate).path.lower().endswith(".m3u8"):
                return verify_hls_url(candidate, page_url, allowed_hosts)
            return verify_direct_video_url(candidate, page_url, allowed_hosts)
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise ValueError("; ".join(errors) or "xxxtik playback candidates are empty.")


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_xxxtik_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (XXXTIK_SOURCE, XXXTIK_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([XXXTIK_SITE_NAME, "video"]), public_pool),
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
    images = item.get("images") or ([item["image"]] if item.get("image") else [])
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
        "display_author": clean_text(detail.get("author_name")) or XXXTIK_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": XXXTIK_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or XXXTIK_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": XXXTIK_KIND,
        "target_value": target_row["value"],
        "site_name": XXXTIK_SITE_NAME,
        "source_url": detail["url"],
        "xxxtik_post_uuid": detail["post_uuid"],
        "xxxtik_post_id": detail.get("post_id"),
        "xxxtik_uid": detail.get("uid"),
        "player_index": 1,
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "variant_url": verified.get("variant_url"),
        "video_poster_url": detail.get("image"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "tags": detail.get("tags") or [],
        "width": detail.get("width"),
        "height": detail.get("height"),
        "resolver": "xxxtik-api-media",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "playlist_bytes": verified.get("playlist_bytes"),
        "master_playlist_bytes": verified.get("master_playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "encrypted": verified.get("encrypted"),
    }
    author_name = detail.get("author_name") or XXXTIK_SITE_NAME
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
        x_url=detail["url"],
        link=content,
        images=images,
    )
    return inserted


def monitor_site(
    conn,
    *,
    base_url: str,
    max_pages: int,
    retention_hours: int,
    public_pool: bool,
    dry_run: bool = False,
    page_limit: int | None = None,
) -> dict:
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_unverified = skipped_old = pages = text_refreshed = 0
    samples = []
    latest_guid = None
    cursor = 0
    for page in range(1, max_pages + 1):
        pages += 1
        list_items, next_cursor = parse_list_page(base_url, page, cursor=cursor, limit=page_limit or XXXTIK_PAGE_LIMIT)
        page_inserted = page_updated = page_existing = page_text_refreshed = page_old = page_verified = page_unverified = page_parsed_videos = 0
        print(f"[xxxtik] page={page} list_items={len(list_items)} cursor={cursor}")
        if not list_items:
            print(f"[xxxtik] page={page} empty_list stop=true")
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
            page_parsed_videos += len(list_item["players"])
            parsed_videos += len(list_item["players"])
            for player in list_item["players"]:
                try:
                    verified = verify_player_playback(player, list_item["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[xxxtik] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "title": player.get("video_title"),
                            "link": list_item["url"],
                            "video_url": verified["video_url"],
                            "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                            "playback_refresh_required": verified.get("playback_refresh_required"),
                            "media_format": verified.get("media_format"),
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                            "media_url_count": verified.get("media_url_count"),
                            "key_url_count": verified.get("key_url_count"),
                            "encrypted": verified.get("encrypted"),
                        }
                    )
                    continue
                if upsert_video_item(conn, target_row, list_item, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
                    page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[xxxtik] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} text_refreshed={page_text_refreshed} "
            f"old={page_old} unverified={page_unverified}"
        )
        if page_inserted == 0 and page_old == len(list_items):
            break
        if not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor
    return {
        "pages": pages,
        "parsed_videos": parsed_videos,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "text_refreshed": text_refreshed,
        "skipped_existing": skipped_existing,
        "skipped_unverified": skipped_unverified,
        "skipped_old": skipped_old,
        "samples": samples[:10],
    }


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""",
            (XXXTIK_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (XXXTIK_SOURCE, refresh_window_minutes, limit),
        ),
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
            post_uuid = metadata.get("xxxtik_post_uuid") or str(row["guid"]).replace(f"{XXXTIK_SOURCE}:", "", 1)
            try:
                post = fetch_post(post_uuid)
                detail = parse_post(post)
                if not detail:
                    raise ValueError("xxxtik refreshed post has no playable candidates")
                player = detail["players"][0]
                verified = verify_player_playback(player, detail["url"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "xxxtik-api-media",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "xxxtik_post_uuid": detail["post_uuid"],
                    "xxxtik_post_id": detail.get("post_id"),
                    "xxxtik_uid": detail.get("uid"),
                    "raw_video_url": verified.get("raw_video_url"),
                    "variant_url": verified.get("variant_url"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "playlist_bytes": verified.get("playlist_bytes"),
                    "master_playlist_bytes": verified.get("master_playlist_bytes"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "encrypted": verified.get("encrypted"),
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
                print(f"[xxxtik] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
