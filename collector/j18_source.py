from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


J18_SITE_NAME = "18J.TV"
J18_SOURCE = "18j"
J18_KIND = "site"
J18_DEFAULT_BASE_URL = os.environ.get("J18_BASE_URL", "https://18j.tv").strip().rstrip("/")
J18_RETENTION_HOURS = int(os.environ.get("J18_RETENTION_HOURS", "84"))
J18_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("J18_REQUEST_TIMEOUT_SECONDS", "30"))
J18_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("J18_MIN_PLAYLIST_DURATION_SECONDS", "60"))
J18_REFRESH_WINDOW_MINUTES = int(os.environ.get("J18_REFRESH_WINDOW_MINUTES", "90"))
J18_CRITICAL_WINDOW_MINUTES = int(os.environ.get("J18_CRITICAL_WINDOW_MINUTES", "15"))
J18_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "madouui",
    "zz999",
    "artplayer",
    "modelym",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "doubleclick",
)


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


def parse_datetime(value: str | None) -> datetime | None:
    raw = non_empty(value)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_iso_duration_seconds(value: str | None) -> float | None:
    raw = non_empty(value)
    if not raw:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+(?:\.\d+)?)D)?(?:T(?:(?P<hours>\d+(?:\.\d+)?)H)?(?:(?P<minutes>\d+(?:\.\d+)?)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?",
        raw,
    )
    if not match:
        return None
    total = (
        float(match.group("days") or 0) * 86400
        + float(match.group("hours") or 0) * 3600
        + float(match.group("minutes") or 0) * 60
        + float(match.group("seconds") or 0)
    )
    return total if total > 0 else None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_18j_target_value(raw: str) -> str:
    value = (raw or J18_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = J18_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("18j target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_18j_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"18j.tv", "www.18j.tv"}


def format_target_row(target_row: dict) -> str:
    return f"18j:{target_row['value']}"


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
                timeout=J18_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("18j request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_hls_text(url: str, referer: str) -> str:
    return request_with_proxy_fallback(url, referer=referer, accept="*/*").text


def read_hls_chunk(url: str, referer: str, size: int) -> bytes:
    with request_with_proxy_fallback(url, referer=referer, accept="*/*", stream=True) as response:
        return next(response.iter_content(size), b"")


def build_list_page_url(base_url: str, page: int) -> str:
    base = normalize_18j_target_value(base_url)
    if page <= 1:
        return urljoin(base + "/", "show/1/")
    return urljoin(base + "/", f"show/1/page/{page}/")


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_18j_target_value(base_url) + "/", f"v/{video_id}/")


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/v/(\d+)/?$", urlparse(url).path)
    return match.group(1) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    return urlunparse(urlparse(urljoin(base_url + "/", html_unescape(raw)))._replace(fragment=""))


def iter_json_ld(soup: BeautifulSoup) -> list[dict]:
    payloads: list[dict] = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for value in data if isinstance(data, list) else [data]:
            if isinstance(value, dict):
                payloads.append(value)
    return payloads


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="/v/"]'):
        video_id = detail_id_from_url(urljoin(page_url, link.get("href") or ""))
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        href = detail_url(base_url, video_id)
        image = link.select_one("img") or (link.parent.select_one("img") if link.parent else None)
        image_url = (image.get("data-original") or image.get("data-src") or image.get("src")) if image else None
        items.append(
            {
                "guid": f"{J18_SOURCE}:{video_id}",
                "video_id": video_id,
                "url": href,
                "title": link.get("title") or link.get_text(" ", strip=True),
                "image": normalize_asset_url(page_url, image_url),
            }
        )
    return items


def extract_player_hls_url(html: str) -> str:
    match = re.search(r"\bconst\s+source\s*=\s*(['\"])(?P<url>https?://[^'\"]+\.m3u8[^'\"]*)\1", html)
    if not match:
        raise ValueError("18j player script is missing m3u8 source.")
    video_url = html_unescape(match.group("url"))
    reject_ad_url(video_url, "playlist")
    return video_url


def extract_json_ld_detail(soup: BeautifulSoup) -> dict:
    for payload in iter_json_ld(soup):
        if payload.get("@type") == "VideoObject":
            return payload
    return {}


def reject_ad_url(url: str, label: str = "playback") -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"18j {label} URL points to an ad host: {host}")


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def parse_hls_key_lines(video_url: str, playlist: str) -> list[dict[str, str | None]]:
    keys: list[dict[str, str | None]] = []
    for match in re.finditer(r"#EXT-X-KEY:(?P<attrs>[^\n]+)", playlist):
        attrs = match.group("attrs")
        method_match = re.search(r"\bMETHOD=([^,]+)", attrs)
        uri_match = re.search(r"\bURI=\"([^\"]+)\"", attrs)
        iv_match = re.search(r"\bIV=(0x[0-9a-fA-F]+)", attrs)
        keys.append(
            {
                "method": method_match.group(1).strip() if method_match else None,
                "uri": urljoin(video_url, uri_match.group(1)) if uri_match else None,
                "iv": iv_match.group(1) if iv_match else None,
            }
        )
    return keys


def parse_auth_key_expiry(url: str) -> datetime | None:
    auth_key = non_empty((parse_qs(urlparse(url).query).get("auth_key") or [""])[0])
    if not auth_key:
        return None
    epoch = int_or_none(auth_key.split("-", 1)[0])
    if not epoch:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    except (OSError, ValueError):
        return None


def video_url_expires_at(urls: list[str]) -> datetime:
    expiries = [expiry for expiry in (parse_auth_key_expiry(url) for url in urls) if expiry]
    return min(expiries) if expiries else J18_STABLE_VIDEO_URL_EXPIRES_AT


def content_path_prefix(video_url: str) -> str:
    match = re.match(r"(?P<prefix>/videos/\d{6}/\d{2}/[A-Za-z0-9]+)/", urlparse(video_url).path)
    if not match:
        raise ValueError("18j HLS URL does not expose a stable content path prefix.")
    return match.group("prefix") + "/"


def b64url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def clean_hls_endpoint_url(video_url: str, referer: str, prefix: str) -> str:
    query = urlencode({"u": b64url(video_url), "r": b64url(referer), "p": b64url(prefix)})
    return f"/api/hls/clean?{query}"


def rewrite_hls_key_line(line: str, video_url: str, prefix: str) -> str | None:
    uri_match = re.search(r'\bURI="([^"]+)"', line)
    if not uri_match:
        return line
    key_url = urljoin(video_url, uri_match.group(1))
    parsed = urlparse(key_url)
    if not parsed.path.startswith(prefix):
        return None
    reject_ad_url(key_url, "key")
    return line.replace(uri_match.group(1), key_url)


def clean_hls_playlist(video_url: str, playlist: str, prefix: str) -> tuple[str, dict[str, int | float]]:
    output: list[str] = []
    pending: list[str] = []
    kept_segments = removed_segments = 0
    kept_duration = removed_duration = 0.0
    current_duration = 0.0
    seen_segment = False
    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line == "#EXT-X-ENDLIST":
            continue
        if line.startswith("#EXTINF:"):
            match = re.search(r"#EXTINF:([0-9.]+)", line)
            current_duration = float(match.group(1)) if match else 0.0
            pending.append(line)
            seen_segment = True
            continue
        if line.startswith("#"):
            if line == "#EXT-X-DISCONTINUITY":
                continue
            if line.startswith("#EXT-X-KEY"):
                rewritten = rewrite_hls_key_line(line, video_url, prefix)
                if not rewritten:
                    continue
                line = rewritten
            if seen_segment:
                pending.append(line)
            else:
                output.append(line)
            continue

        segment_url = urljoin(video_url, line)
        parsed = urlparse(segment_url)
        if parsed.path.startswith(prefix):
            for pending_line in pending:
                if pending_line.startswith("#EXT-X-KEY"):
                    rewritten = rewrite_hls_key_line(pending_line, video_url, prefix)
                    if rewritten:
                        output.append(rewritten)
                else:
                    output.append(pending_line)
            output.append(segment_url)
            kept_segments += 1
            kept_duration += current_duration
        else:
            reject_ad_url(segment_url, "removed segment")
            removed_segments += 1
            removed_duration += current_duration
        pending = []
        current_duration = 0.0
    output.append("#EXT-X-ENDLIST")
    if kept_segments <= 0:
        raise ValueError("18j cleaned playlist has no main video segments.")
    return "\n".join(output) + "\n", {
        "kept_segments": kept_segments,
        "removed_segments": removed_segments,
        "kept_duration_seconds": kept_duration,
        "removed_duration_seconds": removed_duration,
    }


def fetch_hls_key(key_url: str, referer: str) -> bytes:
    reject_ad_url(key_url, "key")
    key = read_hls_chunk(key_url, referer, 64)
    if len(key) != 16:
        raise ValueError("18j HLS AES key is not 16 bytes.")
    return key


def decrypt_aes128_ts_chunk(chunk: bytes, key: bytes, iv_hex: str | None, sequence: int = 0) -> bytes:
    if len(chunk) < 16:
        return b""
    iv = bytes.fromhex(iv_hex[2:] if iv_hex and iv_hex.lower().startswith("0x") else (iv_hex or "")) if iv_hex else sequence.to_bytes(16, "big")
    usable_size = len(chunk) - (len(chunk) % AES.block_size)
    return AES.new(key, AES.MODE_CBC, iv).decrypt(chunk[:usable_size])


def has_ts_sync(chunk: bytes) -> bool:
    return len(chunk) >= 188 and chunk[0] == 0x47 and (len(chunk) < 376 or chunk[188] == 0x47)


def verify_hls_url(video_url: str, referer: str) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("18j video URL must be an HLS .m3u8 URL.")

    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("18j HLS playlist is not playable media.")

    prefix = content_path_prefix(video_url)
    cleaned_playlist, clean_stats = clean_hls_playlist(video_url, playlist, prefix)
    if clean_stats["kept_duration_seconds"] < J18_MIN_PLAYLIST_DURATION_SECONDS:
        raise ValueError("18j cleaned HLS playlist is too short for a real video.")
    if clean_stats["removed_segments"] <= 0:
        raise ValueError("18j playlist did not expose removable ad segments; refusing unclassified playlist.")

    media_urls = playlist_media_urls(video_url, cleaned_playlist)
    key_lines = parse_hls_key_lines(video_url, cleaned_playlist)
    key_urls = [str(key["uri"]) for key in key_lines if key.get("uri")]
    expires_at = video_url_expires_at([video_url, *key_urls, *media_urls[:6]])
    if expires_at != J18_STABLE_VIDEO_URL_EXPIRES_AT and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("18j HLS URL is already expired or too close to expiry.")

    active_key = key_lines[0] if key_lines else None
    key_bytes = fetch_hls_key(str(active_key["uri"]), referer) if active_key and active_key.get("method") == "AES-128" else None
    checked_segment = False
    for index, media_url in enumerate(media_urls[:6]):
        reject_ad_url(media_url, "segment")
        chunk = read_hls_chunk(media_url, referer, 752)
        decoded = decrypt_aes128_ts_chunk(chunk, key_bytes, str(active_key.get("iv") or ""), sequence=index) if key_bytes else chunk
        if has_ts_sync(decoded):
            checked_segment = True
            break
    if not checked_segment:
        raise ValueError("18j cleaned HLS playlist has no readable MPEG-TS segment.")

    return {
        "video_url": clean_hls_endpoint_url(video_url, referer, prefix),
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at,
        "playback_refresh_required": expires_at != J18_STABLE_VIDEO_URL_EXPIRES_AT,
        "playlist_bytes": len(cleaned_playlist.encode("utf-8")),
        "raw_playlist_bytes": len(playlist.encode("utf-8")),
        "playlist_duration_seconds": clean_stats["kept_duration_seconds"],
        "removed_ad_duration_seconds": clean_stats["removed_duration_seconds"],
        "media_url_count": len(media_urls),
        "removed_ad_segment_count": clean_stats["removed_segments"],
        "key_url_count": len(key_urls),
        "content_path_prefix": prefix,
    }


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, (list_item or {}).get("url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    video_id = detail_id_from_url(detail_page_url) or (list_item or {}).get("video_id")
    if not video_id:
        raise ValueError("18j detail URL is missing video id.")
    payload = extract_json_ld_detail(soup)
    source_url = detail_url(normalize_18j_target_value(detail_page_url), video_id)
    title = non_empty(payload.get("name")) or (list_item or {}).get("title") or J18_SITE_NAME
    description = non_empty(payload.get("description")) or title
    image = normalize_asset_url(source_url, non_empty(payload.get("thumbnailUrl"))) or (list_item or {}).get("image")
    tags = [tag.strip() for tag in re.split(r"[,，]", non_empty(payload.get("keywords")) or "") if tag.strip()]
    video_url = extract_player_hls_url(html)
    player = {
        "guid": f"{J18_SOURCE}:{video_id}",
        "video_id": video_id,
        "player_index": 1,
        "video_title": title,
        "video_url": video_url,
        "video_type": "hls",
        "tags": tags,
    }
    return {
        "url": source_url,
        "video_id": video_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": parse_datetime(non_empty(payload.get("uploadDate"))) or now_utc(),
        "modified_at": parse_datetime(non_empty(payload.get("dateModified"))),
        "duration": parse_iso_duration_seconds(non_empty(payload.get("duration"))),
        "category": non_empty(payload.get("genre")),
        "tags": tags,
        "players": [player],
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_18j_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (J18_SOURCE, J18_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([J18_SITE_NAME, "18J", "视频"]), public_pool),
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
        "display_author": J18_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": J18_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": J18_KIND,
        "target_value": target_row["value"],
        "site_name": J18_SITE_NAME,
        "source_url": detail["url"],
        "j18_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "category": detail.get("category"),
        "tags": detail.get("tags") or player.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "18j-plyr-clean-hls",
        "resolved_at": now_iso(),
        "raw_video_url": verified.get("raw_video_url"),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
        "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "content_path_prefix": verified.get("content_path_prefix"),
    }
    author_name = J18_SITE_NAME
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
    base_url = normalize_18j_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[18j] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[18j] page={page} empty_list stop=true")
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
                print(f"[18j] skip detail {list_item.get('video_id')}: {exc}")
                continue
            if detail.get("published_at") and detail["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_hls_url(player["video_url"], detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[18j] skip unverified {player['guid']}: {exc}")
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
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                            "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
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
            f"[18j] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (J18_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (J18_SOURCE, refresh_window_minutes, limit)),
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
            video_id = metadata.get("j18_video_id") or str(row["guid"]).replace(f"{J18_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or J18_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or j18_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_hls_url(player["video_url"], detail["url"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "18j-plyr-clean-hls",
                    "resolved_at": now_iso(),
                    "raw_video_url": verified.get("raw_video_url"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
                    "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "content_path_prefix": verified.get("content_path_prefix"),
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
                print(f"[18j] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
