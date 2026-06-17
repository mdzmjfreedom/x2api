from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
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


MTIF_SITE_NAME = "蜜桃视频"
MTIF_SOURCE = "1mtif"
MTIF_KIND = "site"
MTIF_DEFAULT_BASE_URL = os.environ.get("MTIF_BASE_URL", "https://1mtif.sbs").strip().rstrip("/")
MTIF_DEFAULT_LIST_PATH = os.environ.get("MTIF_LIST_PATH", "/type/2").strip() or "/type/2"
MTIF_RETENTION_HOURS = int(os.environ.get("MTIF_RETENTION_HOURS", "84"))
MTIF_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MTIF_REQUEST_TIMEOUT_SECONDS", "30"))
MTIF_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("MTIF_MIN_PLAYLIST_DURATION_SECONDS", "5"))
MTIF_REFRESH_WINDOW_MINUTES = int(os.environ.get("MTIF_REFRESH_WINDOW_MINUTES", "90"))
MTIF_CRITICAL_WINDOW_MINUTES = int(os.environ.get("MTIF_CRITICAL_WINDOW_MINUTES", "15"))
MTIF_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "adhh",
    "yaknd",
    "33667610",
    "18696701",
    "18798058",
    "3635c39",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "adnxs",
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


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_mtif_target_value(raw: str) -> str:
    value = (raw or MTIF_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = MTIF_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("1mtif target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_mtif_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    default_host = urlparse(MTIF_DEFAULT_BASE_URL).netloc.lower()
    return host in {"1mtif.sbs", "www.1mtif.sbs"} or bool(default_host and host == default_host)


def format_target_row(target_row: dict) -> str:
    return f"1mtif:{target_row['value']}"


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
                timeout=MTIF_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("1mtif request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_json(url: str, referer: str | None = None) -> dict:
    response = request_with_proxy_fallback(url, referer=referer, accept="application/json,*/*")
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("1mtif config response is not a JSON object.")
    return payload


def same_or_subdomain(host: str, allowed_host: str) -> bool:
    return host == allowed_host or host.endswith(f".{allowed_host}")


def reject_ad_url(url: str, label: str = "playback", allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"1mtif {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"1mtif {label} URL points to an ad host: {host}")
    if allowed_hosts and not any(same_or_subdomain(host, allowed_host) for allowed_host in allowed_hosts):
        raise ValueError(f"1mtif {label} URL is outside configured media hosts: {host}")


def fetch_media_text(url: str, page_url: str | None, allowed_hosts: set[str] | None) -> str:
    reject_ad_url(url, allowed_hosts=allowed_hosts)
    return request_with_proxy_fallback(
        url,
        referer=page_url,
        accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
    ).text


def read_media_chunk(url: str, page_url: str | None, size: int, allowed_hosts: set[str] | None) -> tuple[bytes, requests.Response]:
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


def target_list_path(raw: str | None) -> tuple[str, str]:
    value = (raw or MTIF_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path.rstrip("/")
    query = parsed.query
    if not path or path == "/" or path.startswith("/play/"):
        path = MTIF_DEFAULT_LIST_PATH.rstrip("/") or "/type/2"
        query = ""
    return path, query


def build_list_page_url(base_url: str, page: int) -> str:
    page = max(1, page)
    root = normalize_mtif_target_value(base_url)
    path, query = target_list_path(base_url)
    if page > 1:
        path = f"{path.rstrip('/')}/{page}"
    return urlunparse((*urlparse(root)[:2], path, "", query, ""))


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_mtif_target_value(base_url) + "/", f"play/{video_id}")


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/play/([^/?#]+)/?$", urlparse(url).path)
    return match.group(1) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url.rstrip("/") + "/", html_unescape(raw).lstrip("/"))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def extract_json_assignment(html: str, variable_name: str) -> object:
    marker = f"window.{variable_name}"
    start = html.find(marker)
    if start < 0:
        raise ValueError(f"1mtif page is missing window.{variable_name}.")
    equals = html.find("=", start)
    if equals < 0:
        raise ValueError(f"1mtif page has malformed window.{variable_name}.")
    index = equals + 1
    while index < len(html) and html[index].isspace():
        index += 1
    if index >= len(html) or html[index] not in "[{":
        raise ValueError(f"1mtif window.{variable_name} is not JSON.")

    stack = [html[index]]
    in_string = False
    escaped = False
    end = index + 1
    while end < len(html) and stack:
        char = html[end]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        else:
            if char == '"':
                in_string = True
            elif char in "[{":
                stack.append(char)
            elif char in "]}":
                opener = stack.pop()
                if (opener, char) not in {("[", "]"), ("{", "}")}:
                    raise ValueError(f"1mtif window.{variable_name} has invalid JSON nesting.")
        end += 1
    if stack:
        raise ValueError(f"1mtif window.{variable_name} JSON is incomplete.")
    return json.loads(html[index:end])


def parse_app_data(html: str) -> object:
    return extract_json_assignment(html, "APP_DATA")


def iter_video_cards(value) -> list[dict]:
    cards: list[dict] = []
    if isinstance(value, dict):
        if non_empty(value.get("short_id")) and non_empty(value.get("title")):
            cards.append(value)
        for child in value.values():
            cards.extend(iter_video_cards(child))
    elif isinstance(value, list):
        for child in value:
            cards.extend(iter_video_cards(child))
    return cards


def normalize_list_item(base_url: str, card: dict) -> dict | None:
    video_id = clean_text(card.get("short_id"))
    if not video_id:
        return None
    title = clean_text(card.get("title")) or f"{MTIF_SITE_NAME} video {video_id}"
    return {
        "guid": f"{MTIF_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": detail_url(base_url, video_id),
        "title": title,
        "description": title,
        "duration": int_or_none(card.get("duration")),
        "image": None,
        "published_at": now_utc(),
        "tags": [],
    }


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    html = fetch_html(page_url, build_list_page_url(base_url, 1))
    data = parse_app_data(html)
    items: list[dict] = []
    seen: set[str] = set()
    for card in iter_video_cards(data):
        item = normalize_list_item(base_url, card)
        if not item or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        items.append(item)
    return items


def parse_m3u8_host_config(raw_value) -> dict[str, str]:
    if isinstance(raw_value, dict):
        raw_hosts = raw_value
    elif isinstance(raw_value, str) and raw_value.strip():
        raw_hosts = json.loads(raw_value)
    else:
        raw_hosts = {}
    if not isinstance(raw_hosts, dict):
        raise ValueError("1mtif m3u8_host config is not a JSON object.")
    hosts: dict[str, str] = {}
    for key, value in raw_hosts.items():
        host = non_empty(value)
        if not host:
            continue
        if host.startswith("//"):
            host = f"https:{host}"
        parsed = urlparse(host if "://" in host else f"https://{host}")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") + "/", "", "", ""))
        reject_ad_url(normalized, "media host")
        hosts[str(key)] = normalized
    return hosts


def fetch_config(base_url: str) -> dict:
    root = normalize_mtif_target_value(base_url)
    payload = fetch_json(urljoin(root + "/", "api/cfg"), root)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        raise ValueError("1mtif config is missing data.")
    m3u8_hosts = parse_m3u8_host_config(data.get("m3u8_host"))
    pic_host = normalize_asset_url(root, non_empty(data.get("pic_host")))
    if pic_host and not pic_host.endswith("/"):
        pic_host = f"{pic_host}/"
    if pic_host:
        reject_ad_url(pic_host, "picture host")
    if not m3u8_hosts and pic_host:
        m3u8_hosts["default"] = pic_host
    if not m3u8_hosts:
        raise ValueError("1mtif config did not expose a media host.")
    return {"site_name": clean_text(data.get("name")) or MTIF_SITE_NAME, "m3u8_hosts": m3u8_hosts, "pic_host": pic_host}


def configured_media_host(config: dict, server: str | None = None) -> str:
    hosts = config.get("m3u8_hosts") or {}
    if server and hosts.get(server):
        return hosts[server]
    if hosts.get("default"):
        return hosts["default"]
    return next(iter(hosts.values()))


def allowed_media_hosts(config: dict) -> set[str]:
    hosts = set()
    for host_url in (config.get("m3u8_hosts") or {}).values():
        host = urlparse(host_url).netloc.lower()
        if host:
            hosts.add(host)
    return hosts


def parse_detail_payload(data: object, expected_video_id: str | None = None) -> dict:
    candidates = iter_video_cards(data)
    if expected_video_id:
        for candidate in candidates:
            if clean_text(candidate.get("short_id")) == expected_video_id and non_empty(candidate.get("m3u8")):
                return candidate
    for candidate in candidates:
        if non_empty(candidate.get("m3u8")):
            return candidate
    raise ValueError("1mtif detail page is missing playable video data.")


def normalize_tags(tags) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags if isinstance(tags, list) else []:
        if isinstance(tag, dict):
            value = clean_text(tag.get("Name") or tag.get("name") or tag.get("title"))
        else:
            value = clean_text(tag)
        key = (value or "").lower()
        if value and key not in seen:
            result.append(value)
            seen.add(key)
    return result


def classify_video_type(video_url: str) -> str:
    path = urlparse(video_url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(DIRECT_VIDEO_EXTENSIONS):
        return "direct"
    raise ValueError("1mtif playback URL is not a supported direct video or HLS URL.")


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    parsed = urlparse(detail_page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    expected_video_id = detail_id_from_url(detail_page_url) or (list_item or {}).get("video_id")
    config = fetch_config(base_url)
    html = fetch_html(detail_page_url, base_url)
    payload = parse_detail_payload(parse_app_data(html), expected_video_id)

    video_id = clean_text(payload.get("short_id")) or expected_video_id
    if not video_id:
        raise ValueError("1mtif detail page is missing video id.")
    title = clean_text(payload.get("title")) or (list_item or {}).get("title") or f"{MTIF_SITE_NAME} video {video_id}"
    media_host = configured_media_host(config, clean_text(payload.get("server")))
    video_url = normalize_asset_url(media_host, non_empty(payload.get("m3u8")))
    if not video_url:
        raise ValueError("1mtif detail page is missing m3u8 URL.")
    video_type = classify_video_type(video_url)
    image = normalize_asset_url(config.get("pic_host") or media_host, non_empty(payload.get("thumbnail"))) or (list_item or {}).get("image")
    tags = normalize_tags(payload.get("tags"))
    duration = int_or_none(payload.get("duration")) or (list_item or {}).get("duration")

    return {
        "guid": f"{MTIF_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": detail_url(base_url, video_id),
        "title": title,
        "description": title,
        "image": image,
        "author_name": config.get("site_name") or MTIF_SITE_NAME,
        "author_url": base_url,
        "duration": duration,
        "published_at": (list_item or {}).get("published_at") or now_utc(),
        "modified_at": None,
        "tags": tags,
        "players": [
            {
                "guid": f"{MTIF_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": 1,
                "video_title": title,
                "video_url": video_url,
                "video_type": video_type,
                "allowed_media_hosts": allowed_media_hosts(config),
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
        if not line.startswith("#EXT-X-MAP"):
            continue
        attrs = parse_hls_attribute_list(line)
        if attrs.get("URI"):
            urls.append(urljoin(video_url, attrs["URI"]))
    return urls


def playlist_media_group_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-MEDIA"):
            continue
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


def expected_duration_floor(expected_duration: int | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(float(MTIF_MIN_PLAYLIST_DURATION_SECONDS), expected_duration * 0.5)
    return float(MTIF_MIN_PLAYLIST_DURATION_SECONDS)


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
    cipher = AES.new(key, AES.MODE_CBC, aes_iv_for_segment(sequence, iv_value))
    return cipher.decrypt(chunk[:block_length])


def verify_media_playlist(media_url: str, playlist: str, page_url: str | None, expected_duration: int | None, allowed_hosts: set[str] | None) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("1mtif HLS media playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("1mtif HLS playlist is too short for the video metadata.")

    segments = playlist_segments(media_url, playlist)
    if not segments:
        raise ValueError("1mtif HLS playlist has no media segments.")

    map_urls = playlist_map_urls(media_url, playlist)
    key_cache: dict[str, bytes] = {}
    for key_url in playlist_key_urls(media_url, playlist)[:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
        if len(chunk) != 16:
            raise ValueError("1mtif HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk

    checked_init = False
    for map_url in map_urls[:2]:
        chunk, _response = read_media_chunk(map_url, page_url, 512, allowed_hosts)
        if chunk and looks_like_media_segment(chunk):
            checked_init = True
            break
    if map_urls and not checked_init:
        raise ValueError("1mtif HLS playlist has no readable fMP4 init segment.")

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
                        raise ValueError("1mtif HLS AES key is not 16 bytes.")
                    key_cache[key_url] = key_chunk
                    key_bytes = key_chunk
                media_chunk = decrypt_aes128_chunk(chunk, key_bytes, int(segment["sequence"]), key.get("iv"))
            if looks_like_media_segment(media_chunk):
                return {
                    "playlist_duration_seconds": total_duration,
                    "media_url_count": len(segments),
                    "map_url_count": len(map_urls),
                    "key_url_count": len(key_cache),
                    "checked_media_playlist_url": media_url,
                    "encrypted": bool(key_cache),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"1mtif HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None, expected_duration: int | None = None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("1mtif HLS URL must be an .m3u8 URL.")

    master_playlist = fetch_media_text(video_url, page_url, allowed_hosts)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("1mtif HLS URL is not a playlist.")

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
                reject_ad_url(variant_url, "variant", allowed_hosts)
                media_playlist = fetch_media_text(variant_url, page_url, allowed_hosts)
                checked_urls.append(variant_url)
                checked_urls.extend(segment["url"] for segment in playlist_segments(variant_url, media_playlist)[:3])
                checked_urls.extend(playlist_map_urls(variant_url, media_playlist)[:1])
                checked_urls.extend(playlist_key_urls(variant_url, media_playlist)[:1])
                media_result = {
                    **verify_media_playlist(variant_url, media_playlist, page_url, expected_duration, allowed_hosts),
                    "variant_count": len(variants),
                    "selected_variant_url": variant_url,
                    "selected_variant_stream_inf": variant.get("stream_inf"),
                    "media_group_url_count": len(media_group_urls),
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"1mtif HLS master playlist has no playable variant: {media_error}")
    else:
        checked_urls.extend(segment["url"] for segment in playlist_segments(video_url, master_playlist)[:3])
        checked_urls.extend(playlist_map_urls(video_url, master_playlist)[:1])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_playlist(video_url, master_playlist, page_url, expected_duration, allowed_hosts),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
            "media_group_url_count": len(media_group_urls),
        }

    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("1mtif HLS URL is already expired or too close to expiry.")

    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or MTIF_STABLE_VIDEO_URL_EXPIRES_AT,
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


def verify_direct_video_url(video_url: str, page_url: str | None, expected_duration: int | None = None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("1mtif direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("1mtif direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 2048, allowed_hosts)
    if not chunk:
        raise ValueError("1mtif direct video URL returned an empty media chunk.")
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206}:
        raise ValueError(f"1mtif direct video URL returned unexpected status {response.status_code}.")
    if "text/html" in content_type:
        raise ValueError("1mtif direct video URL returned HTML instead of media.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("1mtif direct video URL did not return recognizable media bytes.")
    if expected_duration and expected_duration < MTIF_MIN_PLAYLIST_DURATION_SECONDS:
        raise ValueError("1mtif direct video duration is too short for a real video.")
    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or MTIF_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(
    video_url: str,
    page_url: str | None,
    video_type: str,
    expected_duration: int | None = None,
    allowed_hosts: set[str] | None = None,
) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, page_url, expected_duration, allowed_hosts)
    return verify_direct_video_url(video_url, page_url, expected_duration, allowed_hosts)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_mtif_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (MTIF_SOURCE, MTIF_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([MTIF_SITE_NAME, "video"]), public_pool),
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
        "display_author": clean_text(detail.get("author_name")) or MTIF_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": MTIF_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or MTIF_SITE_NAME
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": MTIF_KIND,
        "target_value": target_row["value"],
        "site_name": MTIF_SITE_NAME,
        "source_url": detail["url"],
        "mtif_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "tags": detail.get("tags") or [],
        "resolver": "1mtif-app-data-hls",
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
        "encrypted": verified.get("encrypted"),
    }
    author_name = detail.get("author_name") or MTIF_SITE_NAME
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
        print(f"[1mtif] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[1mtif] page={page} empty_list stop=true")
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
                print(f"[1mtif] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(
                        player["video_url"],
                        detail["url"],
                        player["video_type"],
                        detail.get("duration"),
                        player.get("allowed_media_hosts"),
                    )
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[1mtif] skip unverified {player['guid']}: {exc}")
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
            f"[1mtif] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
            (MTIF_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (MTIF_SOURCE, refresh_window_minutes, limit),
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
            video_id = metadata.get("mtif_video_id") or str(row["guid"]).replace(f"{MTIF_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or MTIF_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or mtif_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(
                    player["video_url"],
                    detail["url"],
                    player["video_type"],
                    detail.get("duration"),
                    player.get("allowed_media_hosts"),
                )
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "1mtif-app-data-hls",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "mtif_video_id": detail["video_id"],
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "duration": detail.get("duration") or metadata.get("duration"),
                    "author_name": detail.get("author_name") or metadata.get("author_name"),
                    "author_url": detail.get("author_url") or metadata.get("author_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "map_url_count": verified.get("map_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "variant_count": verified.get("variant_count"),
                    "selected_variant_url": verified.get("selected_variant_url"),
                    "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
                    "media_group_url_count": verified.get("media_group_url_count"),
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
                print(f"[1mtif] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
