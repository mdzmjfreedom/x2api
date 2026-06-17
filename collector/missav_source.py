from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse, urlunparse

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


MISSAV_SITE_NAME = "MISSAV"
MISSAV_SOURCE = "missav"
MISSAV_KIND = "site"
MISSAV_DEFAULT_BASE_URL = os.environ.get("MISSAV_BASE_URL", "https://missav.app/vodtype/20/").strip() or "https://missav.app/vodtype/20/"
MISSAV_RETENTION_HOURS = int(os.environ.get("MISSAV_RETENTION_HOURS", "168"))
MISSAV_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MISSAV_REQUEST_TIMEOUT_SECONDS", "20"))
MISSAV_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("MISSAV_MIN_PLAYLIST_DURATION_SECONDS", "10"))
MISSAV_REFRESH_WINDOW_MINUTES = int(os.environ.get("MISSAV_REFRESH_WINDOW_MINUTES", "90"))
MISSAV_CRITICAL_WINDOW_MINUTES = int(os.environ.get("MISSAV_CRITICAL_WINDOW_MINUTES", "15"))
MISSAV_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire")
AD_HOST_KEYWORDS = (
    "adnxs",
    "adservice",
    "adsterra",
    "adtng",
    "clickadu",
    "doubleclick",
    "driverhugoverblown",
    "exoclick",
    "googletagmanager",
    "magsrv",
    "popads",
    "realsrv",
    "rmhfrtnd",
    "rmishe",
    "smartpop",
    "tsyndicate",
)


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


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url, html_unescape(raw).replace("\\/", "/"))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    path = re.sub(r"/+$", "", parsed.path or "")
    return f"{parsed.netloc.lower()}{path}".strip("/") or value.lower().rstrip("/")


def normalize_missav_target_value(raw: str) -> str:
    value = (raw or MISSAV_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("MISSAV target must be a URL or host.")
    path = parsed.path or "/vodtype/20/"
    if path == "/":
        path = "/vodtype/20/"
    path = "/" + path.strip("/") + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", "", ""))


def is_missav_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"missav.app", "www.missav.app"}


def format_target_row(target_row: dict) -> str:
    return f"missav:{target_row['value']}"


def origin_header(url: str | None) -> str | None:
    raw = non_empty(url)
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def playback_headers(referer: str | None) -> dict[str, str]:
    raw_referer = non_empty(referer)
    if not raw_referer:
        return {}
    result = {"Referer": raw_referer}
    origin = origin_header(raw_referer)
    if origin:
        result["Origin"] = origin
    return result


def headers(referer: str | None = None, *, accept: str | None = None, range_header: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": accept or "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    result.update(playback_headers(referer))
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
                timeout=MISSAV_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("MISSAV request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    response = request_with_proxy_fallback(url, referer=referer)
    return response.content.decode(response.encoding or "utf-8", "replace")


def build_list_page_url(base_url: str, page: int) -> str:
    root = normalize_missav_target_value(base_url)
    if page <= 1:
        return root
    return urljoin(root, f"page/{page}/")


def play_id_from_url(url: str) -> str | None:
    match = re.search(r"/vodplay/(\d+)-(\d+)-(\d+)/?$", urlparse(url).path)
    return match.group(1) if match else None


def extract_card_image(node, page_url: str) -> str | None:
    image = node.select_one("img") if hasattr(node, "select_one") else None
    if not image:
        return None
    for attr in ("data-src", "data-original", "data-lazy-src", "src"):
        value = normalize_asset_url(page_url, image.get(attr))
        if value and not value.endswith("/static/images/lazyload.gif"):
            return value
    srcset = non_empty(image.get("data-srcset") or image.get("srcset"))
    if srcset:
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        return normalize_asset_url(page_url, first)
    return None


def extract_card_title(node, fallback: str | None = None) -> str | None:
    image = node.select_one("img") if hasattr(node, "select_one") else None
    title = clean_text(image.get("alt") if image else None) or clean_text(image.get("title") if image else None)
    if title:
        return title
    for selector in (".video-title", ".entry-title", "h1", "h2", "h3", "a"):
        el = node.select_one(selector) if hasattr(node, "select_one") else None
        title = clean_text(el.get_text(" ", strip=True) if el else None)
        if title:
            return title
    return clean_text(fallback)


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    html = fetch_html(page_url, build_list_page_url(base_url, 1))
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('a[href*="/vodplay/"]'):
        play_url = normalize_asset_url(page_url, link.get("href"))
        video_id = play_id_from_url(play_url or "")
        if not play_url or not video_id or video_id in seen:
            continue
        if urlparse(play_url).netloc.lower() not in {"missav.app", "www.missav.app"}:
            continue
        card = link.find_parent(class_=re.compile(r"\bthumbnail\b")) or link.parent or link
        seen.add(video_id)
        title = extract_card_title(card, f"{MISSAV_SITE_NAME} {video_id}") or f"{MISSAV_SITE_NAME} {video_id}"
        items.append(
            {
                "guid": f"{MISSAV_SOURCE}:{video_id}",
                "video_id": video_id,
                "url": play_url,
                "title": title,
                "image": extract_card_image(card, page_url),
                "published_at": now_utc(),
                "tags": [MISSAV_SITE_NAME],
            }
        )
    return items


def extract_player_json(html: str) -> dict:
    match = re.search(r"var\s+player_aaaa\s*=\s*(\{.*?\})\s*</script>", html, re.S)
    if not match:
        raise ValueError("MISSAV play page is missing player_aaaa.")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("MISSAV player payload is not an object.")
    return payload


def decode_player_url(payload: dict, play_page_url: str) -> str:
    raw = non_empty(payload.get("url"))
    if not raw:
        raise ValueError("MISSAV player payload is missing URL.")
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
    raise ValueError("MISSAV playback URL is not a supported direct video or HLS URL.")


def parse_detail_page(play_page_url: str, list_item: dict | None = None) -> dict:
    play_url = normalize_asset_url(MISSAV_DEFAULT_BASE_URL, play_page_url) or play_page_url
    html = fetch_html(play_url, (list_item or {}).get("url") or MISSAV_DEFAULT_BASE_URL)
    soup = BeautifulSoup(html, "html.parser")
    payload = extract_player_json(html)
    video_url = decode_player_url(payload, play_url)
    video_type = classify_video_type(video_url)
    video_id = str(payload.get("id") or play_id_from_url(play_url) or (list_item or {}).get("video_id") or "")
    if not video_id:
        raise ValueError("MISSAV play page is missing video id.")
    vod_data = payload.get("vod_data") if isinstance(payload.get("vod_data"), dict) else {}
    title = (
        clean_text(vod_data.get("vod_name"))
        or clean_text(soup.select_one("h1").get_text(" ", strip=True) if soup.select_one("h1") else None)
        or (list_item or {}).get("title")
        or f"{MISSAV_SITE_NAME} {video_id}"
    )
    category = clean_text(vod_data.get("vod_class"))
    image = (
        normalize_asset_url(play_url, soup.select_one('meta[property="og:image"]').get("content") if soup.select_one('meta[property="og:image"]') else None)
        or extract_card_image(soup, play_url)
        or (list_item or {}).get("image")
    )
    tags = [tag for tag in [category, MISSAV_SITE_NAME, *((list_item or {}).get("tags") or [])] if tag]
    return {
        "guid": f"{MISSAV_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": play_url,
        "title": title,
        "description": title,
        "image": image,
        "images": [image] if image else [],
        "author_name": category or MISSAV_SITE_NAME,
        "author_url": normalize_missav_target_value(play_url),
        "category_name": category,
        "published_at": (list_item or {}).get("published_at") or now_utc(),
        "modified_at": None,
        "tags": list(dict.fromkeys(tags))[:12],
        "players": [
            {
                "guid": f"{MISSAV_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": int_or_none(payload.get("nid")) or 1,
                "video_title": title,
                "video_url": video_url,
                "video_type": video_type,
                "player_from": payload.get("from"),
                "referer": play_url,
            }
        ],
    }


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


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"MISSAV {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"MISSAV {label} URL points to an ad host: {host}")


def fetch_hls_text(url: str, referer: str | None) -> str:
    reject_ad_url(url)
    response = request_with_proxy_fallback(url, referer=referer, accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*")
    return response.content.decode("utf-8-sig", "replace")


def read_media_chunk(url: str, referer: str | None, size: int) -> tuple[bytes, requests.Response]:
    reject_ad_url(url)
    response = request_with_proxy_fallback(
        url,
        referer=referer,
        accept="video/mp4,video/webm,video/mp2t,application/octet-stream,*/*",
        range_header=f"bytes=0-{size - 1}",
        stream=True,
    )
    try:
        return next(response.iter_content(size), b""), response
    except StopIteration:
        return b"", response


def parse_hls_attrs(line: str) -> dict[str, str]:
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


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in playlist.splitlines():
        value = line.strip()
        if not value.startswith("#EXT-X-KEY"):
            continue
        attrs = parse_hls_attrs(value)
        uri = attrs.get("URI")
        if uri:
            key_url = urljoin(video_url, uri)
            if key_url not in seen:
                urls.append(key_url)
                seen.add(key_url)
    return urls


def playlist_segments(video_url: str, playlist: str) -> list[dict]:
    segments: list[dict] = []
    media_sequence = 0
    current_key: dict[str, str] | None = None
    for line in playlist.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = int_or_none(value.split(":", 1)[1]) or 0
            continue
        if value.startswith("#EXT-X-KEY"):
            attrs = parse_hls_attrs(value)
            if attrs.get("METHOD", "").upper() == "NONE":
                current_key = None
            elif attrs.get("URI"):
                current_key = attrs
            continue
        if value.startswith("#"):
            continue
        segments.append({"url": urljoin(video_url, value), "sequence": media_sequence, "key": dict(current_key) if current_key else None})
        media_sequence += 1
    return segments


def playlist_map_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if line.startswith("#EXT-X-MAP"):
            uri = parse_hls_attrs(line).get("URI")
            if uri:
                urls.append(urljoin(video_url, uri))
    return urls


def looks_like_mpeg_ts(chunk: bytes) -> bool:
    if chunk.startswith(b"ID3"):
        return True
    for offset in range(min(188, len(chunk))):
        if len(chunk) > offset + 188 and chunk[offset] == 0x47 and chunk[offset + 188] == 0x47:
            return True
    return len(chunk) >= 188 and chunk[0] == 0x47


def looks_like_media_chunk(chunk: bytes) -> bool:
    if looks_like_mpeg_ts(chunk):
        return True
    prefix = chunk[:128]
    return any(marker in prefix for marker in (b"ftyp", b"moof", b"mdat", b"sidx", b"\x1a\x45\xdf\xa3"))


def aes_iv_for_segment(key_attrs: dict[str, str] | None, sequence: int) -> bytes:
    if key_attrs and key_attrs.get("IV"):
        raw = key_attrs["IV"]
        if raw.lower().startswith("0x"):
            raw = raw[2:]
        return bytes.fromhex(raw.zfill(32)[-32:])
    return int(sequence).to_bytes(16, "big")


def decrypt_aes128_chunk(chunk: bytes, key: bytes, key_attrs: dict[str, str] | None, sequence: int) -> bytes:
    usable = len(chunk) - (len(chunk) % AES.block_size)
    if usable <= 0:
        return b""
    return AES.new(key, AES.MODE_CBC, aes_iv_for_segment(key_attrs, sequence)).decrypt(chunk[:usable])


def verify_media_hls_url(media_url: str, playlist: str, referer: str | None) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("MISSAV HLS media playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < MISSAV_MIN_PLAYLIST_DURATION_SECONDS:
        raise ValueError("MISSAV HLS playlist is too short for a real video.")
    segments = playlist_segments(media_url, playlist)
    map_urls = playlist_map_urls(media_url, playlist)
    if not segments and not map_urls:
        raise ValueError("MISSAV HLS playlist has no media segments.")
    key_cache: dict[str, bytes] = {}
    key_urls = playlist_key_urls(media_url, playlist)
    for key_url in key_urls[:3]:
        chunk, _response = read_media_chunk(key_url, referer, 16)
        if len(chunk) != 16:
            raise ValueError("MISSAV HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk
    probe_urls = [{"url": url, "sequence": 0, "key": None} for url in map_urls[:1]] + segments[:8]
    segment_error: Exception | None = None
    for segment in probe_urls:
        try:
            chunk, _response = read_media_chunk(segment["url"], referer, 4096)
            probe = chunk
            key_attrs = segment.get("key")
            if key_attrs and key_attrs.get("METHOD", "").upper() == "AES-128":
                key_url = urljoin(media_url, key_attrs.get("URI", ""))
                key = key_cache.get(key_url)
                if key is None:
                    key, _key_response = read_media_chunk(key_url, referer, 16)
                probe = decrypt_aes128_chunk(chunk, key, key_attrs, int(segment.get("sequence") or 0))
            if looks_like_media_chunk(probe):
                return {
                    "playlist_duration_seconds": total_duration,
                    "media_url_count": len(segments),
                    "map_url_count": len(map_urls),
                    "key_url_count": len(key_urls),
                    "checked_media_playlist_url": media_url,
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "encrypted": bool(key_urls),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"MISSAV HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, referer: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("MISSAV HLS URL must be an .m3u8 URL.")
    master_playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("MISSAV HLS URL is not a playlist.")
    checked_urls = [video_url]
    variants = playlist_stream_variants(video_url, master_playlist)
    media_result = None
    media_error: Exception | None = None
    if variants:
        for variant in sort_variants_for_probe(variants):
            variant_url = variant["url"]
            try:
                media_playlist = fetch_hls_text(variant_url, referer)
                checked_urls.append(variant_url)
                checked_urls.extend(segment["url"] for segment in playlist_segments(variant_url, media_playlist)[:3])
                checked_urls.extend(playlist_key_urls(variant_url, media_playlist)[:1])
                media_result = {
                    **verify_media_hls_url(variant_url, media_playlist, referer),
                    "variant_count": len(variants),
                    "selected_variant_url": variant_url,
                    "selected_variant_stream_inf": variant.get("stream_inf"),
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"MISSAV HLS master playlist has no playable variants: {media_error}")
    else:
        checked_urls.extend(segment["url"] for segment in playlist_segments(video_url, master_playlist)[:3])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_hls_url(video_url, master_playlist, referer),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
        }
    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("MISSAV HLS URL is already expired or too close to expiry.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or MISSAV_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "hls",
        "master_playlist_bytes": len(master_playlist.encode("utf-8")),
        **media_result,
    }


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_direct_video_url(video_url: str, referer: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("MISSAV direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("MISSAV direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, referer, 4096)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type or "image/" in content_type:
        raise ValueError("MISSAV direct video URL did not return media bytes.")
    if not looks_like_media_chunk(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("MISSAV direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or MISSAV_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, referer: str | None, video_type: str) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, referer)
    return verify_direct_video_url(video_url, referer)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_missav_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (MISSAV_SOURCE, MISSAV_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([MISSAV_SITE_NAME, "video"]), public_pool),
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


def build_author_presentation(detail: dict) -> dict[str, str | None]:
    return {
        "display_author": detail.get("author_name") or MISSAV_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": MISSAV_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or MISSAV_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": MISSAV_KIND,
        "target_value": target_row["value"],
        "site_name": MISSAV_SITE_NAME,
        "source_url": detail["url"],
        "missav_video_id": player["video_id"],
        "player_index": player["player_index"],
        "player_from": player.get("player_from"),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
        "checked_media_playlist_url": verified.get("checked_media_playlist_url"),
        "video_poster_url": detail.get("image"),
        "tags": list(dict.fromkeys(detail.get("tags") or []))[:20],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "missav-player-aaaa-url",
        "resolved_at": now_iso(),
        "playback_headers": verified.get("playback_headers"),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "master_playlist_bytes": verified.get("master_playlist_bytes"),
        "playlist_bytes": verified.get("playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "map_url_count": verified.get("map_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "variant_count": verified.get("variant_count"),
        "encrypted": verified.get("encrypted"),
        "content_type": verified.get("content_type"),
        "content_length": verified.get("content_length"),
    }
    author_name = detail.get("author_name") or MISSAV_SITE_NAME
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
        playback_headers=verified.get("playback_headers"),
    )
    return inserted


def monitor_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_missav_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        try:
            list_items = parse_list_page(base_url, page)
        except Exception as exc:
            if target_row:
                upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=str(exc), success=False)
            raise
        page_inserted = page_updated = page_existing = page_old = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[missav] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[missav] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[missav] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                latest_guid = latest_guid or player["guid"]
                if target_row and item_exists_for_guid(conn, str(target_row["id"]), player["guid"]):
                    skipped_existing += 1
                    page_existing += 1
                    continue
                try:
                    verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[missav] skip unverified {player['guid']}: {exc}")
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
                            "playback_headers": verified.get("playback_headers"),
                            "media_format": verified.get("media_format"),
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
            f"[missav] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_verified == 0 and page_existing == 0 and page_old == len(list_items):
            break
    return {
        "pages": pages,
        "parsed_videos": parsed_videos,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_detail_errors": skipped_detail_errors,
        "skipped_unverified": skipped_unverified,
        "skipped_old": skipped_old,
        "samples": samples[:10],
    }
