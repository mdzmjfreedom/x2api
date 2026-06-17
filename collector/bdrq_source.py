from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, unquote, urlencode, urljoin, urlparse, urlunparse

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


BDRQ_SITE_NAME = "背德人妻"
BDRQ_SOURCE = "bdrq"
BDRQ_KIND = "site"
BDRQ_DEFAULT_BASE_URL = os.environ.get("BDRQ_BASE_URL", "https://g3h4i5j6.bdrq45.cc").strip().rstrip("/")
BDRQ_DEFAULT_LIST_PATHS = (
    "/vodshow/181-----------.html",
    "/vodtype/4.html",
)
BDRQ_RETENTION_HOURS = int(os.environ.get("BDRQ_RETENTION_HOURS", "84"))
BDRQ_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("BDRQ_REQUEST_TIMEOUT_SECONDS", "30"))
BDRQ_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("BDRQ_MIN_PLAYLIST_DURATION_SECONDS", "10"))
BDRQ_REFRESH_WINDOW_MINUTES = int(os.environ.get("BDRQ_REFRESH_WINDOW_MINUTES", "90"))
BDRQ_CRITICAL_WINDOW_MINUTES = int(os.environ.get("BDRQ_CRITICAL_WINDOW_MINUTES", "15"))
BDRQ_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "ah7907",
    "imso1044",
    "lm9093",
    "jklhgfg",
    "panduov",
    "sqdm-888",
    "ttyiwu",
    "47131268",
    "25130233",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "doubleclick",
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


def parse_bdrq_datetime(value: str | None) -> datetime | None:
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


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_bdrq_target_value(raw: str) -> str:
    value = (raw or BDRQ_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = BDRQ_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("BDRQ target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_bdrq_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    default_host = urlparse(BDRQ_DEFAULT_BASE_URL).netloc.lower()
    return host == default_host or host.endswith(".bdrq45.cc") or host in {"bdrq45.cc", "www.bdrq45.cc", "bdrq12.cc", "www.bdrq12.cc"}


def format_target_row(target_row: dict) -> str:
    return f"bdrq:{target_row['value']}"


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
                timeout=BDRQ_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("BDRQ request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"BDRQ {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"BDRQ {label} URL points to an ad host: {host}")


def fetch_media_text(url: str, page_url: str | None) -> str:
    reject_ad_url(url)
    return request_with_proxy_fallback(url, referer=page_url, accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*").text


def read_media_chunk(url: str, page_url: str | None, size: int) -> tuple[bytes, requests.Response]:
    reject_ad_url(url)
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


def list_paths_from_target(raw: str | None) -> list[str]:
    value = (raw or BDRQ_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path.rstrip("/")
    if path.startswith("/vodshow/") or path.startswith("/vodtype/"):
        return [path + (".html" if not path.endswith(".html") else "")]
    return list(BDRQ_DEFAULT_LIST_PATHS)


def build_list_page_url(base_url: str, list_path: str, page: int) -> str:
    root = normalize_bdrq_target_value(base_url)
    page = max(1, page)
    path = list_path
    if page > 1:
        vodshow_match = re.match(r"^/vodshow/(\d+)", list_path)
        vodtype_match = re.match(r"^/vodtype/(\d+)", list_path)
        if vodshow_match:
            path = f"/vodshow/{vodshow_match.group(1)}--------{page}---.html"
        elif vodtype_match:
            path = f"/vodtype/{vodtype_match.group(1)}-{page}.html"
    return urljoin(root + "/", path.lstrip("/"))


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_bdrq_target_value(base_url) + "/", f"voddetail/{video_id}.html")


def play_url(base_url: str, video_id: str, sid: int = 1, nid: int = 1) -> str:
    return urljoin(normalize_bdrq_target_value(base_url) + "/", f"vodplay/{video_id}-{sid}-{nid}.html")


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/voddetail/(\d+)\.html", urlparse(url).path)
    return match.group(1) if match else None


def play_id_from_url(url: str) -> str | None:
    match = re.search(r"/vodplay/(\d+)-(\d+)-(\d+)\.html", urlparse(url).path)
    return match.group(1) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url.rstrip("/") + "/", html_unescape(raw))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def parse_entry(entry, page_url: str, base_url: str, list_path: str) -> dict | None:
    thumb = entry.select_one('a.stui-vodlist__thumb[href*="/voddetail/"]')
    if not thumb:
        return None
    source_url = normalize_asset_url(page_url, thumb.get("href"))
    video_id = detail_id_from_url(source_url or "")
    if not source_url or not video_id:
        return None
    title_node = entry.select_one("h4.title a[title], h4.title a")
    title = clean_text(thumb.get("title")) or clean_text(title_node.get("title") if title_node else None) or clean_text(title_node.get_text(" ", strip=True) if title_node else None)
    title = title or f"{BDRQ_SITE_NAME} video {video_id}"
    category = clean_text(entry.select_one(".pic-text1").get_text(" ", strip=True) if entry.select_one(".pic-text1") else None)
    info_text = clean_text(entry.select_one(".stui-vodlist__detail p").get_text(" ", strip=True) if entry.select_one(".stui-vodlist__detail p") else None)
    published_at = parse_bdrq_datetime(info_text) or now_utc()
    image = normalize_asset_url(page_url, thumb.get("data-original") or thumb.get("data-src") or thumb.get("src"))
    return {
        "guid": f"{BDRQ_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": source_url,
        "title": title,
        "description": title,
        "image": image,
        "author_name": category or BDRQ_SITE_NAME,
        "author_url": normalize_bdrq_target_value(base_url),
        "category_name": category,
        "published_at": published_at,
        "modified_at": None,
        "list_path": list_path,
        "tags": [tag for tag in [category, BDRQ_SITE_NAME] if tag],
    }


def parse_list_page(base_url: str, list_path: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, list_path, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, list_path, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for entry in soup.select("ul.stui-vodlist li"):
        item = parse_entry(entry, page_url, base_url, list_path)
        if not item or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        items.append(item)
    return items


def extract_player_json(html: str) -> dict:
    match = re.search(r"var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not match:
        raise ValueError("BDRQ play page is missing player_aaaa.")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("BDRQ player payload is not an object.")
    return payload


def decode_player_url(payload: dict, play_page_url: str) -> str:
    raw = non_empty(payload.get("url"))
    if not raw:
        raise ValueError("BDRQ player payload is missing URL.")
    encrypt = int_or_none(payload.get("encrypt")) or 0
    if encrypt == 1:
        raw = unquote(raw)
    elif encrypt == 2:
        raw = base64.b64decode(raw).decode("utf-8")
    return normalize_asset_url(play_page_url, raw) or raw


def classify_video_type(video_url: str) -> str:
    path = urlparse(video_url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(DIRECT_VIDEO_EXTENSIONS):
        return "direct"
    raise ValueError("BDRQ playback URL is not a supported direct video or HLS URL.")


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, (list_item or {}).get("url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    video_id = detail_id_from_url(detail_page_url) or (list_item or {}).get("video_id")
    if not video_id:
        raise ValueError("BDRQ detail URL is missing video id.")
    title = clean_text(soup.select_one("h1.title").get_text(" ", strip=True) if soup.select_one("h1.title") else None) or (list_item or {}).get("title") or f"{BDRQ_SITE_NAME} video {video_id}"
    image = normalize_asset_url(detail_page_url, soup.select_one(".stui-content__thumb img").get("data-original") if soup.select_one(".stui-content__thumb img") else None) or (list_item or {}).get("image")
    play_link = soup.select_one(f'a[href^="/vodplay/{video_id}-"], a[href*="/vodplay/{video_id}-"]')
    resolved_play_url = normalize_asset_url(detail_page_url, play_link.get("href") if play_link else None) or play_url(detail_page_url, video_id)
    play_html = fetch_html(resolved_play_url, detail_page_url)
    payload = extract_player_json(play_html)
    video_url = decode_player_url(payload, resolved_play_url)
    video_type = classify_video_type(video_url)
    category = (list_item or {}).get("category_name")
    if not category:
        category_node = soup.select_one(".stui-content__detail p.left")
        category = clean_text(category_node.get_text(" ", strip=True).replace("类型：", "") if category_node else None)
    return {
        "guid": f"{BDRQ_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": detail_url(detail_page_url, video_id),
        "play_url": resolved_play_url,
        "title": title,
        "description": title,
        "image": image,
        "author_name": category or BDRQ_SITE_NAME,
        "author_url": normalize_bdrq_target_value(detail_page_url),
        "category_name": category,
        "published_at": (list_item or {}).get("published_at") or now_utc(),
        "modified_at": None,
        "tags": [tag for tag in [category, BDRQ_SITE_NAME] if tag],
        "players": [
            {
                "guid": f"{BDRQ_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": int_or_none(payload.get("nid")) or 1,
                "video_title": title,
                "video_url": video_url,
                "video_type": video_type,
                "player_from": payload.get("from"),
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


def variant_bandwidth(stream_inf: str) -> int:
    match = re.search(r"BANDWIDTH=(\d+)", stream_inf)
    return int(match.group(1)) if match else 0


def sort_variants_for_probe(variants: list[dict]) -> list[dict]:
    return sorted(variants, key=lambda variant: variant_bandwidth(str(variant.get("stream_inf") or "")))


def playlist_map_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MAP"):
            attrs = parse_hls_attribute_list(line)
            if attrs.get("URI"):
                urls.append(urljoin(video_url, attrs["URI"]))
    return urls


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
                current_key = {"url": urljoin(video_url, attrs["URI"]), "method": attrs.get("METHOD"), "iv": attrs.get("IV")}
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


def media_playlist_content_prefix(media_url: str) -> str:
    path = urlparse(media_url).path
    return path.rsplit("/", 1)[0] + "/"


def rewrite_hls_key_line(line: str, media_url: str, content_prefix: str) -> str | None:
    attrs = parse_hls_attribute_list(line)
    uri = attrs.get("URI")
    if not uri:
        return line
    key_url = urljoin(media_url, uri)
    reject_ad_url(key_url, "key")
    if not urlparse(key_url).path.startswith(content_prefix):
        return None
    return line.replace(uri, key_url)


def clean_hls_playlist(media_url: str, playlist: str, content_prefix: str) -> tuple[str, dict[str, int | float]]:
    output: list[str] = []
    pending: list[str] = []
    kept_segments = removed_segments = 0
    kept_duration = removed_duration = 0.0
    current_duration = 0.0
    seen_segment = False
    for raw_line in playlist.splitlines():
        line = raw_line.strip()
        if not line or line == "#EXT-X-ENDLIST":
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
                rewritten = rewrite_hls_key_line(line, media_url, content_prefix)
                if not rewritten:
                    continue
                line = rewritten
            if seen_segment:
                pending.append(line)
            else:
                output.append(line)
            continue
        segment_url = urljoin(media_url, line)
        reject_ad_url(segment_url, "segment")
        if urlparse(segment_url).path.startswith(content_prefix):
            output.extend(pending)
            output.append(segment_url)
            kept_segments += 1
            kept_duration += current_duration
        else:
            removed_segments += 1
            removed_duration += current_duration
        pending = []
        current_duration = 0.0
    output.append("#EXT-X-ENDLIST")
    if kept_segments <= 0:
        raise ValueError("BDRQ cleaned playlist has no main video segments.")
    return "\n".join(output) + "\n", {
        "kept_segments": kept_segments,
        "removed_segments": removed_segments,
        "kept_duration_seconds": kept_duration,
        "removed_duration_seconds": removed_duration,
    }


def expected_duration_floor() -> float:
    return float(BDRQ_MIN_PLAYLIST_DURATION_SECONDS)


def looks_like_mpeg_ts(chunk: bytes) -> bool:
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


def b64url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def clean_hls_endpoint_url(media_url: str, referer: str, content_prefix: str) -> str:
    query = urlencode({"u": b64url(media_url), "r": b64url(referer), "p": b64url(content_prefix)})
    return f"/api/hls/clean?{query}"


def verify_media_playlist(media_url: str, playlist: str, page_url: str | None) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("BDRQ HLS media playlist is not playable media.")
    content_prefix = media_playlist_content_prefix(media_url)
    cleaned_playlist, clean_stats = clean_hls_playlist(media_url, playlist, content_prefix)
    if clean_stats["kept_duration_seconds"] < expected_duration_floor():
        raise ValueError("BDRQ HLS playlist is too short for a real video.")

    segments = playlist_segments(media_url, cleaned_playlist)
    if not segments:
        raise ValueError("BDRQ HLS playlist has no media segments.")
    key_cache: dict[str, bytes] = {}
    for key_url in playlist_key_urls(media_url, cleaned_playlist)[:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16)
        if len(chunk) != 16:
            raise ValueError("BDRQ HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk

    segment_error: Exception | None = None
    for segment in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment["url"], page_url, 1024)
            media_chunk = chunk
            key = segment.get("key")
            if key:
                key_url = key["url"]
                key_bytes = key_cache.get(key_url)
                if key_bytes is None:
                    key_chunk, _key_response = read_media_chunk(key_url, page_url, 16)
                    if len(key_chunk) != 16:
                        raise ValueError("BDRQ HLS AES key is not 16 bytes.")
                    key_cache[key_url] = key_chunk
                    key_bytes = key_chunk
                media_chunk = decrypt_aes128_chunk(chunk, key_bytes, int(segment["sequence"]), key.get("iv"))
            if looks_like_media_segment(media_chunk):
                return {
                    "playlist_duration_seconds": clean_stats["kept_duration_seconds"],
                    "removed_ad_duration_seconds": clean_stats["removed_duration_seconds"],
                    "media_url_count": len(segments),
                    "removed_ad_segment_count": clean_stats["removed_segments"],
                    "key_url_count": len(key_cache),
                    "checked_media_playlist_url": media_url,
                    "content_path_prefix": content_prefix,
                    "cleaned_playlist_bytes": len(cleaned_playlist.encode("utf-8")),
                    "encrypted": bool(key_cache),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"BDRQ HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("BDRQ HLS URL must be an .m3u8 URL.")
    master_playlist = fetch_media_text(video_url, page_url)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("BDRQ HLS URL is not a playlist.")
    checked_urls = [video_url]
    variants = playlist_stream_variants(video_url, master_playlist)
    if variants:
        media_result = None
        media_error: Exception | None = None
        for variant in sort_variants_for_probe(variants):
            variant_url = variant["url"]
            try:
                media_playlist = fetch_media_text(variant_url, page_url)
                checked_urls.append(variant_url)
                checked_urls.extend(segment["url"] for segment in playlist_segments(variant_url, media_playlist)[:3])
                checked_urls.extend(playlist_key_urls(variant_url, media_playlist)[:1])
                media_result = {
                    **verify_media_playlist(variant_url, media_playlist, page_url),
                    "variant_count": len(variants),
                    "selected_variant_url": variant_url,
                    "selected_variant_stream_inf": variant.get("stream_inf"),
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"BDRQ HLS master playlist has no playable variant: {media_error}")
    else:
        checked_urls.extend(segment["url"] for segment in playlist_segments(video_url, master_playlist)[:3])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_playlist(video_url, master_playlist, page_url),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
        }
    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("BDRQ HLS URL is already expired or too close to expiry.")
    playback_url = video_url
    if int(media_result.get("removed_ad_segment_count") or 0) > 0:
        playback_url = clean_hls_endpoint_url(str(media_result["checked_media_playlist_url"]), page_url or "", str(media_result["content_path_prefix"]))
    return {
        "video_url": playback_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or BDRQ_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "hls",
        "raw_playlist_bytes": len(master_playlist.encode("utf-8")),
        **media_result,
    }


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_direct_video_url(video_url: str, page_url: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("BDRQ direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("BDRQ direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 2048)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type:
        raise ValueError("BDRQ direct video URL did not return media bytes.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("BDRQ direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or BDRQ_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, page_url: str | None, video_type: str) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, page_url)
    return verify_direct_video_url(video_url, page_url)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_bdrq_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (BDRQ_SOURCE, BDRQ_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([BDRQ_SITE_NAME, "video"]), public_pool),
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
        "display_author": clean_text(detail.get("author_name")) or BDRQ_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": BDRQ_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or BDRQ_SITE_NAME
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": BDRQ_KIND,
        "target_value": target_row["value"],
        "site_name": BDRQ_SITE_NAME,
        "source_url": detail["url"],
        "play_url": detail.get("play_url"),
        "bdrq_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "player_from": player.get("player_from"),
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "video_poster_url": detail.get("image"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "category_name": detail.get("category_name"),
        "tags": detail.get("tags") or [],
        "resolver": "bdrq-player-aaaa",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "raw_playlist_bytes": verified.get("raw_playlist_bytes"),
        "cleaned_playlist_bytes": verified.get("cleaned_playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "variant_count": verified.get("variant_count"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
        "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
        "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
        "content_path_prefix": verified.get("content_path_prefix"),
        "encrypted": verified.get("encrypted"),
    }
    author_name = detail.get("author_name") or BDRQ_SITE_NAME
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
    seen_guids: set[str] = set()
    for list_path in list_paths_from_target(base_url):
        for page in range(1, max_pages + 1):
            pages += 1
            list_items = parse_list_page(base_url, list_path, page)
            page_inserted = page_updated = page_existing = page_text_refreshed = page_old = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
            print(f"[bdrq] list_path={list_path} page={page} list_items={len(list_items)} url={build_list_page_url(base_url, list_path, page)}")
            if not list_items:
                print(f"[bdrq] list_path={list_path} page={page} empty_list stop=true")
                break
            for list_item in list_items:
                if list_item["guid"] in seen_guids:
                    continue
                seen_guids.add(list_item["guid"])
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
                    print(f"[bdrq] skip detail {list_item.get('url')}: {exc}")
                    continue
                page_parsed_videos += len(detail["players"])
                parsed_videos += len(detail["players"])
                for player in detail["players"]:
                    try:
                        verified = verify_playback_url(player["video_url"], detail.get("play_url") or detail["url"], player["video_type"])
                    except Exception as exc:
                        skipped_unverified += 1
                        page_unverified += 1
                        print(f"[bdrq] skip unverified {player['guid']}: {exc}")
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
                                "raw_video_url": verified.get("raw_video_url"),
                                "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                                "playback_refresh_required": verified.get("playback_refresh_required"),
                                "media_format": verified.get("media_format"),
                                "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                                "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
                                "media_url_count": verified.get("media_url_count"),
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
                f"[bdrq] list_path={list_path} page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
            (BDRQ_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (BDRQ_SOURCE, refresh_window_minutes, limit),
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
            video_id = metadata.get("bdrq_video_id") or str(row["guid"]).replace(f"{BDRQ_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or BDRQ_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or bdrq_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(player["video_url"], detail.get("play_url") or detail["url"], player["video_type"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "bdrq-player-aaaa",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "play_url": detail.get("play_url"),
                    "bdrq_video_id": detail["video_id"],
                    "raw_video_url": verified.get("raw_video_url"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "raw_playlist_bytes": verified.get("raw_playlist_bytes"),
                    "cleaned_playlist_bytes": verified.get("cleaned_playlist_bytes"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "variant_count": verified.get("variant_count"),
                    "selected_variant_url": verified.get("selected_variant_url"),
                    "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
                    "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
                    "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
                    "content_path_prefix": verified.get("content_path_prefix"),
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
                print(f"[bdrq] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
