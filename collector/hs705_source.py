from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import update_item_document as update_opensearch_item_document
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import update_item_document as update_opensearch_item_document
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


HS705_SITE_NAME = "992KP"
HS705_SOURCE = "705hs"
HS705_KIND = "site"
HS705_DEFAULT_BASE_URL = os.environ.get("HS705_BASE_URL", "https://705hs.com/Html/60/index-1.html").strip() or "https://705hs.com/Html/60/index-1.html"
HS705_RETENTION_HOURS = int(os.environ.get("HS705_RETENTION_HOURS", "84"))
HS705_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("HS705_REQUEST_TIMEOUT_SECONDS", "30"))
HS705_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("HS705_MIN_PLAYLIST_DURATION_SECONDS", "5"))
HS705_REFRESH_WINDOW_MINUTES = int(os.environ.get("HS705_REFRESH_WINDOW_MINUTES", "90"))
HS705_CRITICAL_WINDOW_MINUTES = int(os.environ.get("HS705_CRITICAL_WINDOW_MINUTES", "15"))
HS705_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

HS705_DEFAULT_HLS_HOSTS = (
    "https://kp-p25277.com",
    "https://kp-p25983.com",
    "https://kp-p25779.com",
    "https://kp-p25767.com",
    "https://kp-p25812.com",
    "https://kp-p25996.com",
    "https://kp-p25358.com",
    "https://kp-p25295.com",
    "https://kp-prush25922.com",
    "https://kp-prush25733.com",
)
HS705_DEFAULT_IMAGE_HOSTS = (
    "https://kp-i25977.com",
    "https://kp-i25176.com",
    "https://kp-i25985.com",
    "https://kp-i25372.com",
)
AD_HOST_KEYWORDS = (
    "ads",
    "alicdn",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "doubleclick",
    "ptggtpym",
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


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_hs705_target_value(raw: str) -> str:
    value = (raw or HS705_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("705hs target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_hs705_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"705hs.com", "www.705hs.com"}


def format_target_row(target_row: dict) -> str:
    return f"705hs:{target_row['value']}"


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
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
                timeout=HS705_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("705hs request failed.")


def response_text(response: requests.Response) -> str:
    return response.content.decode("utf-8-sig", "replace")


def fetch_html(url: str, referer: str | None = None) -> str:
    return response_text(request_with_proxy_fallback(url, referer=referer))


def reject_ad_url(url: str, label: str = "playback", allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"705hs {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"705hs {label} URL points to an ad host: {host}")
    if allowed_hosts and host not in allowed_hosts:
        raise ValueError(f"705hs {label} URL is outside configured media hosts: {host}")


def fetch_media_text(url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> str:
    reject_ad_url(url, allowed_hosts=allowed_hosts)
    return request_with_proxy_fallback(
        url,
        referer=page_url,
        accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
    ).text


def read_media_chunk(url: str, page_url: str | None, size: int, allowed_hosts: set[str] | None = None) -> tuple[bytes, requests.Response]:
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


def target_category_id(raw: str | None) -> str:
    value = (raw or HS705_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    match = re.search(r"/Html/(\d+)(?:/|$)", parsed.path)
    return match.group(1) if match else "60"


def build_list_page_url(base_url: str, page: int) -> str:
    root = normalize_hs705_target_value(base_url)
    category_id = target_category_id(base_url)
    return urljoin(root + "/", f"Html/{category_id}/index-{max(1, page)}.html")


def detail_url(base_url: str, category_id: str, video_id: str) -> str:
    return urljoin(normalize_hs705_target_value(base_url) + "/", f"Html/{category_id}/{video_id}.html")


def play_url(base_url: str, video_id: str, line_index: int = 0) -> str:
    return urljoin(normalize_hs705_target_value(base_url) + "/", f"Html/player/play-{video_id}-{line_index}-1.html")


def detail_id_from_url(url: str) -> tuple[str, str] | None:
    match = re.search(r"/Html/(\d+)/(\d+)\.html$", urlparse(url).path)
    return (match.group(1), match.group(2)) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    raw = html_unescape(raw)
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url.rstrip("/") + "/", raw)
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def normalize_image_url(path: str | None) -> str | None:
    raw = non_empty(path)
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if raw.startswith("//"):
        return f"https:{raw}"
    return urljoin(HS705_DEFAULT_IMAGE_HOSTS[0] + "/", raw.lstrip("/"))


def parse_hs705_datetime(value: str | None) -> datetime | None:
    raw = clean_text(value)
    if not raw:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", raw)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    except ValueError:
        return None


def extract_get_img_url(html: str) -> str | None:
    match = re.search(r"get_img_url\(\s*['\"]([^'\"]+)['\"]\s*\)", html)
    return match.group(1) if match else None


def parse_entry(node, page_url: str, base_url: str) -> dict | None:
    link = node.select_one('a[href*="/Html/"][href$=".html"]')
    href = link.get("href") if link else None
    item_url = normalize_asset_url(page_url, href)
    ids = detail_id_from_url(item_url or "")
    if not item_url or not ids:
        return None
    category_id, video_id = ids
    title_node = link.select_one("p") if link else None
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else None)
    date_node = link.select_one(".timeobxobx") if link else None
    published_at = parse_hs705_datetime(date_node.get_text(" ", strip=True) if date_node else None)
    image = normalize_image_url(extract_get_img_url(str(node)))
    return {
        "guid": f"{HS705_SOURCE}:{video_id}",
        "video_id": video_id,
        "category_id": category_id,
        "url": item_url,
        "title": title or f"{HS705_SITE_NAME} video {video_id}",
        "description": title,
        "image": image,
        "author_name": HS705_SITE_NAME,
        "author_url": normalize_hs705_target_value(base_url),
        "category_name": None,
        "published_at": published_at,
        "modified_at": None,
        "tags": [HS705_SITE_NAME],
    }


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for entry in soup.select("li.list1_obxobx"):
        item = parse_entry(entry, page_url, base_url)
        if not item or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        items.append(item)
    return items


def parse_media_hosts(script_text: str | None = None) -> list[str]:
    hosts = list(HS705_DEFAULT_HLS_HOSTS)
    if script_text:
        for host in re.findall(r"https://(?:kp-p|kp-prush)[A-Za-z0-9.-]+", script_text):
            if host not in hosts:
                hosts.append(host)
    return hosts


def fetch_media_hosts(base_url: str) -> list[str]:
    try:
        script = fetch_html(urljoin(normalize_hs705_target_value(base_url) + "/", "js/u.js"), normalize_hs705_target_value(base_url))
        return parse_media_hosts(script)
    except Exception:
        return list(HS705_DEFAULT_HLS_HOSTS)


def extract_down_url(html: str) -> str | None:
    match = re.search(r"var\s+down_url\s*=\s*['\"]([^'\"]+)['\"]", html)
    return match.group(1) if match else None


def extract_hls_path(play_html: str, down_url: str | None) -> str | None:
    match = re.search(r"mp4\(\s*['\"]([^'\"]+\.m3u8)['\"]\s*\)", play_html)
    if match:
        value = match.group(1)
        return value if value.startswith("/") else f"/{value}"
    match = re.search(r"['\"](/[^'\"]+\.m3u8)['\"]", play_html)
    if match:
        return match.group(1)
    if down_url:
        parsed = urlparse(down_url)
        if parsed.path.lower().endswith(".mp4"):
            return f"{parsed.path}.m3u8"
    return None


def hls_candidates(hls_path: str | None, hosts: list[str]) -> list[str]:
    if not hls_path:
        return []
    if hls_path.startswith("http://") or hls_path.startswith("https://"):
        return [hls_path]
    return [urljoin(host.rstrip("/") + "/", hls_path.lstrip("/")) for host in hosts]


def direct_candidates(down_url: str | None) -> list[str]:
    value = non_empty(down_url)
    if not value:
        return []
    return [value]


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, (list_item or {}).get("url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    ids = detail_id_from_url(detail_page_url)
    category_id = (ids or ((list_item or {}).get("category_id"), (list_item or {}).get("video_id")))[0]
    video_id = (ids or ((list_item or {}).get("category_id"), (list_item or {}).get("video_id")))[1]
    if not category_id or not video_id:
        raise ValueError("705hs detail URL is missing video id.")
    title_node = soup.select_one(".film_title h4")
    meta_description = soup.select_one('meta[name="description"]')
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else None) or clean_text(meta_description.get("content") if meta_description else None) or (list_item or {}).get("title") or f"{HS705_SITE_NAME} video {video_id}"
    image = normalize_image_url(extract_get_img_url(html)) or (list_item or {}).get("image")
    info_text = soup.get_text(" ", strip=True)
    category_match = re.search(r"\u60c5\u8272\u5206\u985e[:\uff1a]\s*([^\s]+)", info_text)
    category_name = clean_text(category_match.group(1) if category_match else None)
    published_at = parse_hs705_datetime(info_text) or (list_item or {}).get("published_at") or now_utc()
    down_url = extract_down_url(html)
    play_page_url = play_url(detail_page_url, video_id, 0)
    play_html = fetch_html(play_page_url, detail_page_url)
    hls_path = extract_hls_path(play_html, down_url)
    media_hosts = fetch_media_hosts(detail_page_url)
    candidates = hls_candidates(hls_path, media_hosts) + direct_candidates(down_url)
    if not candidates:
        raise ValueError("705hs detail page did not expose a playable URL.")
    allowed_hosts = {urlparse(candidate).netloc.lower() for candidate in candidates if urlparse(candidate).netloc}
    tags = [tag for tag in [category_name, HS705_SITE_NAME] if tag]
    return {
        "guid": f"{HS705_SOURCE}:{video_id}",
        "video_id": video_id,
        "category_id": category_id,
        "url": detail_url(detail_page_url, category_id, video_id),
        "play_url": play_page_url,
        "title": title,
        "description": title,
        "image": image,
        "images": [image] if image else [],
        "author_name": category_name or HS705_SITE_NAME,
        "author_url": normalize_hs705_target_value(detail_page_url),
        "category_name": category_name,
        "published_at": published_at,
        "modified_at": None,
        "tags": tags,
        "players": [
            {
                "guid": f"{HS705_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": 1,
                "video_title": title,
                "video_url": candidates[0],
                "video_url_candidates": candidates,
                "video_type": "hls" if urlparse(candidates[0]).path.lower().endswith(".m3u8") else "direct",
                "allowed_media_hosts": allowed_hosts,
            }
        ],
    }


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


def playlist_segments(video_url: str, playlist: str) -> list[dict]:
    media_sequence = 0
    current_key: dict | None = None
    segments = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = int_or_none(value.split(":", 1)[1]) or 0
            continue
        if value.startswith("#EXT-X-KEY"):
            attrs = parse_hls_attribute_list(value)
            if attrs.get("METHOD", "").upper() == "NONE":
                current_key = None
            elif attrs.get("URI"):
                current_key = {"url": urljoin(video_url, attrs["URI"]), "iv": attrs.get("IV")}
            continue
        if value.startswith("#"):
            continue
        segments.append({"url": urljoin(video_url, value), "sequence": media_sequence, "key": dict(current_key) if current_key else None})
        media_sequence += 1
    return segments


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for segment in playlist_segments(video_url, playlist):
        key = segment.get("key")
        key_url = key.get("url") if isinstance(key, dict) else None
        if key_url and key_url not in seen:
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


def aes_iv_for_segment(sequence: int, iv_value: str | None) -> bytes:
    if iv_value:
        raw = iv_value[2:] if iv_value.lower().startswith("0x") else iv_value
        return bytes.fromhex(raw.zfill(32)[-32:])
    return int(sequence).to_bytes(16, "big")


def decrypt_aes128_chunk(chunk: bytes, key: bytes, sequence: int, iv_value: str | None) -> bytes:
    block_length = len(chunk) - (len(chunk) % AES.block_size)
    if block_length <= 0:
        return b""
    return AES.new(key, AES.MODE_CBC, aes_iv_for_segment(sequence, iv_value)).decrypt(chunk[:block_length])


def expected_duration_floor() -> float:
    return float(HS705_MIN_PLAYLIST_DURATION_SECONDS)


def verify_hls_url(video_url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("705hs HLS URL must be an .m3u8 URL.")
    playlist = fetch_media_text(video_url, page_url, allowed_hosts)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("705hs HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor():
        raise ValueError("705hs HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("705hs HLS playlist has no media segments.")
    key_cache: dict[str, bytes] = {}
    for key_url in playlist_key_urls(video_url, playlist)[:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
        if len(chunk) != 16:
            raise ValueError("705hs HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk
    segment_error: Exception | None = None
    for segment in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment["url"], page_url, 4096, allowed_hosts)
            media_chunk = chunk
            key = segment.get("key")
            if key:
                key_url = key["url"]
                key_bytes = key_cache.get(key_url)
                if key_bytes is None:
                    key_chunk, _key_response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
                    if len(key_chunk) != 16:
                        raise ValueError("705hs HLS AES key is not 16 bytes.")
                    key_cache[key_url] = key_chunk
                    key_bytes = key_chunk
                media_chunk = decrypt_aes128_chunk(chunk, key_bytes, int(segment["sequence"]), key.get("iv"))
            if looks_like_media_segment(media_chunk):
                expires_at = playback_expiry([video_url, *(segment["url"] for segment in segments[:3]), *playlist_key_urls(video_url, playlist)[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("705hs HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "video_url_expires_at": expires_at or HS705_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_duration_seconds": total_duration,
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "media_url_count": len(segments),
                    "key_url_count": len(key_cache),
                    "encrypted": bool(key_cache),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"705hs HLS playlist has no readable media segment: {segment_error}")


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
        raise ValueError("705hs direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("705hs direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 2048, allowed_hosts)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type:
        raise ValueError("705hs direct video URL did not return media bytes.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("705hs direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or HS705_STABLE_VIDEO_URL_EXPIRES_AT,
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
    raise ValueError("; ".join(errors) or "705hs playback candidates are empty.")


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_hs705_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (HS705_SOURCE, HS705_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([HS705_SITE_NAME, "video"]), public_pool),
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
        "display_author": clean_text(detail.get("author_name")) or HS705_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": HS705_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or HS705_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": HS705_KIND,
        "target_value": target_row["value"],
        "site_name": HS705_SITE_NAME,
        "source_url": detail["url"],
        "play_url": detail.get("play_url"),
        "hs705_video_id": detail["video_id"],
        "hs705_category_id": detail.get("category_id"),
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "video_poster_url": detail.get("image"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "category_name": detail.get("category_name"),
        "tags": detail.get("tags") or [],
        "resolver": "705hs-u2-hls",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "playlist_bytes": verified.get("playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "encrypted": verified.get("encrypted"),
    }
    author_name = detail.get("author_name") or HS705_SITE_NAME
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
        print(f"[705hs] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[705hs] page={page} empty_list stop=true")
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
                print(f"[705hs] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_player_playback(player, detail.get("play_url") or detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[705hs] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "title": player.get("video_title"),
                            "link": detail["url"],
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
                if upsert_video_item(conn, target_row, detail, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
                    page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[705hs] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""",
            (HS705_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (HS705_SOURCE, refresh_window_minutes, limit),
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
            source_url = metadata.get("source_url")
            video_id = metadata.get("hs705_video_id") or str(row["guid"]).replace(f"{HS705_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    category_id = metadata.get("hs705_category_id") or target_category_id(metadata.get("target_value") or HS705_DEFAULT_BASE_URL)
                    source_url = detail_url(metadata.get("target_value") or HS705_DEFAULT_BASE_URL, category_id, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or hs705_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_player_playback(player, detail.get("play_url") or detail["url"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "705hs-u2-hls",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "play_url": detail.get("play_url"),
                    "hs705_video_id": detail["video_id"],
                    "hs705_category_id": detail.get("category_id"),
                    "raw_video_url": verified.get("raw_video_url"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "playlist_bytes": verified.get("playlist_bytes"),
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
                print(f"[705hs] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
