from __future__ import annotations

import json
import os
import re
import time
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from psycopg.types.json import Jsonb


ATTACH_SITE_NAME = "黑料吃瓜网"
ATTACH_SOURCE = "attach"
ATTACH_KIND = "site"
ATTACH_DEFAULT_BASE_URL = os.environ.get("ATTACH_BASE_URL", "https://attach.bslqmdvk.cc/category/zxcg/").strip() or "https://attach.bslqmdvk.cc/category/zxcg/"
ATTACH_RETENTION_HOURS = int(os.environ.get("ATTACH_RETENTION_HOURS", "84"))
ATTACH_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("ATTACH_REQUEST_TIMEOUT_SECONDS", "20"))
ATTACH_REFRESH_WINDOW_MINUTES = int(os.environ.get("ATTACH_REFRESH_WINDOW_MINUTES", "12"))
ATTACH_CRITICAL_WINDOW_MINUTES = int(os.environ.get("ATTACH_CRITICAL_WINDOW_MINUTES", "5"))
ATTACH_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire")
AD_HOST_KEYWORDS = (
    "adnxs",
    "adservice",
    "adsterra",
    "adtng",
    "clickadu",
    "doubleclick",
    "exoclick",
    "magsrv",
    "popads",
    "realsrv",
    "tsyndicate",
)
EXPECTED_PLAYBACK_HOSTS = (
    "hls.chxgdn.cn",
    "dx.oviluf.cn",
)
EXPECTED_PLAYBACK_HOST_SUFFIXES = (
    ".chxgdn.cn",
    ".oviluf.cn",
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


def parse_datetime(value: str | None) -> datetime | None:
    raw = non_empty(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def normalize_attach_target_value(raw: str) -> str:
    value = (raw or ATTACH_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("Attach target must be a URL or host.")
    path = parsed.path or "/category/zxcg/"
    if path == "/":
        path = "/category/zxcg/"
    path = "/" + path.strip("/") + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", "", ""))


def is_attach_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    host = parsed.netloc.lower()
    return host in {"attach.bslqmdvk.cc", "hlcgw.com", "www.hlcgw.com"}


def format_target_row(target_row: dict) -> str:
    return f"attach:{target_row['value']}"


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
                timeout=ATTACH_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("Attach request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    response = request_with_proxy_fallback(url, referer=referer)
    return response.content.decode(response.encoding or "utf-8", "replace")


def build_list_page_url(base_url: str, page: int) -> str:
    root = normalize_attach_target_value(base_url)
    if page <= 1:
        return root
    return urljoin(root, f"{page}/")


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/archives/(\d+)/?$", urlparse(url).path)
    return match.group(1) if match else None


def extract_banner_image(node, page_url: str) -> str | None:
    for image in node.select("img"):
        for attr in ("data-xkrkllgl", "data-src", "data-lazy-src", "src"):
            value = normalize_asset_url(page_url, image.get(attr))
            if value and not value.endswith("/usr/plugins/tbxw/zw.png"):
                return value
    for script in node.select("script"):
        text = script.get_text(" ", strip=True)
        match = re.search(r"loadBannerDirect\(\s*['\"]([^'\"]+)['\"]", text)
        if match:
            return normalize_asset_url(page_url, match.group(1))
    return None


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    html = fetch_html(page_url, build_list_page_url(base_url, 1))
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        link = article.select_one('a[href*="/archives/"]')
        heading = article.select_one(".post-card-title, h2, h1, h3")
        if not link or not heading:
            continue
        rel = " ".join(link.get("rel") or []).lower()
        if "sponsored" in rel or article.select_one(".post-card-ads"):
            continue
        detail_url = normalize_asset_url(page_url, link.get("href"))
        detail_id = detail_id_from_url(detail_url or "")
        if not detail_url or not detail_id or detail_id in seen:
            continue
        seen.add(detail_id)
        published_el = article.select_one("[itemprop='datePublished'][content], time[datetime]")
        categories = []
        info = article.select_one(".post-card-info")
        if info:
            parts = [clean_text(part) for part in info.get_text(" ", strip=True).split("•")]
            category_text = parts[-1] if parts else None
            categories = [part for part in category_text.split(",") if part.strip()] if category_text else []
        items.append(
            {
                "guid": f"{ATTACH_SOURCE}:{detail_id}",
                "detail_id": detail_id,
                "url": detail_url,
                "title": clean_text(heading.get_text(" ", strip=True)) or f"{ATTACH_SITE_NAME} {detail_id}",
                "image": extract_banner_image(article, page_url),
                "published_at": parse_datetime(published_el.get("content") or published_el.get("datetime") if published_el else None),
                "tags": [clean_text(tag) for tag in categories if clean_text(tag)][:12],
            }
        )
    return items


def extract_json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text("", strip=True))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("@type") in {"Article", "NewsArticle", "BlogPosting"}:
            return payload
        entity = payload.get("mainEntity") if isinstance(payload, dict) else None
        if isinstance(entity, dict):
            return entity
    return {}


def first_json_text(value) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                return item
    if isinstance(value, dict):
        for key in ("url", "contentUrl"):
            if isinstance(value.get(key), str):
                return value[key]
    return None


def decode_player_config(raw: str | None) -> dict | None:
    value = clean_text(raw)
    if not value:
        return None
    try:
        if value.startswith("{"):
            payload = json.loads(value)
        else:
            payload = json.loads(b64decode(value + "===").decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def player_config_has_media(payload: dict | None) -> bool:
    video = payload.get("video") if isinstance(payload, dict) else None
    return isinstance(video, dict) and bool(non_empty(video.get("url")))


def base36_word(value: int) -> str:
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyz"
    if value <= 0:
        return "0"
    result = ""
    while value:
        value, remainder = divmod(value, 36)
        result = alphabet[remainder] + result
    return result


def unpack_packer_script(script_text: str) -> str | None:
    packed_match = re.search(
        r"\}\('(?P<p>.*?)',(?P<a>\d+),(?P<c>\d+),'(?P<k>.*?)'\.split\('\|'\)",
        script_text,
        re.S,
    )
    if not packed_match:
        return None
    payload = packed_match.group("p")
    keys = packed_match.group("k").split("|")
    count = int_or_none(packed_match.group("c")) or len(keys)
    for index in range(min(count, len(keys)) - 1, -1, -1):
        replacement = keys[index]
        if not replacement:
            continue
        payload = re.sub(r"\b" + re.escape(base36_word(index)) + r"\b", lambda _match, value=replacement: value, payload)
    return payload


def unpacked_player_script_parts(script_text: str) -> tuple[str, str] | None:
    if "/3/d/4/" not in script_text and "/player/" not in script_text:
        return None
    unpacked = unpack_packer_script(script_text)
    searchable = f"{script_text}\n{unpacked or ''}"
    key_match = re.search(r"player-box-([0-9a-f]{6,64})", searchable, re.I)
    if not key_match:
        return None
    token = max(re.findall(r"[0-9a-f]{64,}", searchable, re.I), key=len, default=None)
    if not token:
        return None
    return token, key_match.group(1)


def player_script_slot_candidates() -> list[int]:
    current = int(time.time() / 2000)
    return [current, current - 1, current + 1]


def player_script_urls(detail_url: str, script_text: str) -> list[str]:
    parts = unpacked_player_script_parts(script_text)
    if not parts:
        return []
    token, player_key = parts
    return [urljoin(detail_url, f"/player/{token}/{player_key}/{slot}.js") for slot in player_script_slot_candidates()]


def extract_player_config_from_js(js_text: str) -> dict | None:
    for match in re.finditer(r"eyJ[A-Za-z0-9+/=]{100,}", js_text):
        payload = decode_player_config(match.group(0))
        if player_config_has_media(payload):
            return payload
    return None


def fetch_player_script_config(detail_url: str, script_url: str) -> dict | None:
    try:
        response = request_with_proxy_fallback(script_url, referer=detail_url, accept="application/javascript,text/javascript,*/*")
    except Exception:
        return None
    return extract_player_config_from_js(response.content.decode(response.encoding or "utf-8", "replace"))


def extract_player_configs(soup: BeautifulSoup, detail_url: str) -> list[dict]:
    configs: list[dict] = []
    seen_urls: set[str] = set()

    def add_config(payload: dict | None, player_key: str | None = None) -> None:
        if not player_config_has_media(payload):
            return
        video = payload.get("video") or {}
        video_url = normalize_asset_url(detail_url, video.get("url"))
        if not video_url or video_url in seen_urls:
            return
        seen_urls.add(video_url)
        next_payload = dict(payload)
        next_payload["_attach_player_key"] = player_key
        configs.append(next_payload)

    for node in soup.select("div.dplayer[data-config]"):
        add_config(decode_player_config(node.get("data-config")), node.get("id"))

    for script in soup.select("script"):
        script_text = script.get_text("", strip=False)
        parts = unpacked_player_script_parts(script_text)
        if not parts:
            continue
        _token, player_key = parts
        for script_url in player_script_urls(detail_url, script_text):
            payload = fetch_player_script_config(detail_url, script_url)
            if payload:
                add_config(payload, player_key)
                break

    return configs


def parse_detail_page(detail_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_url, (list_item or {}).get("url") or ATTACH_DEFAULT_BASE_URL)
    soup = BeautifulSoup(html, "html.parser")
    entity = extract_json_ld(soup)
    title_el = soup.select_one("h1.post-title, h1")
    title = clean_text(entity.get("headline") or entity.get("name")) or clean_text(title_el.get_text(" ", strip=True) if title_el else None) or (list_item or {}).get("title") or ATTACH_SITE_NAME
    description = clean_text(entity.get("description")) or title
    published_at = parse_datetime(entity.get("datePublished")) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_datetime(entity.get("dateModified"))
    image = normalize_asset_url(detail_url, first_json_text(entity.get("image") or entity.get("thumbnailUrl"))) or extract_banner_image(soup, detail_url) or (list_item or {}).get("image")
    tags = []
    keywords = entity.get("keywords")
    if isinstance(keywords, list):
        tags.extend(clean_text(tag) for tag in keywords if clean_text(tag))
    elif isinstance(keywords, str):
        tags.extend(clean_text(tag) for tag in re.split(r"[,，#\s]+", keywords) if clean_text(tag))
    for tag in (list_item or {}).get("tags") or []:
        if tag and tag not in tags:
            tags.append(tag)
    if ATTACH_SITE_NAME not in tags:
        tags.append(ATTACH_SITE_NAME)
    detail_id = (list_item or {}).get("detail_id") or detail_id_from_url(detail_url) or re.sub(r"\W+", "-", detail_url).strip("-").lower()
    players = []
    for index, config in enumerate(extract_player_configs(soup, detail_url), start=1):
        video_config = config.get("video") if isinstance(config, dict) else None
        video_url = normalize_asset_url(detail_url, video_config.get("url") if isinstance(video_config, dict) else None)
        video_type = clean_text(video_config.get("type") if isinstance(video_config, dict) else None) or ""
        if not video_url:
            continue
        if video_url in {
            normalize_asset_url(detail_url, config.get("video_ads_url")),
            normalize_asset_url(detail_url, config.get("video_ads_url_h")),
            normalize_asset_url(detail_url, config.get("backend_video_ads_url")),
            normalize_asset_url(detail_url, config.get("backend_video_ads_url_h")),
        }:
            continue
        player_key = clean_text(config.get("_attach_player_key"))
        player_video_id = f"{detail_id}{index:03d}"
        player_title = title
        players.append(
            {
                "guid": f"{ATTACH_SOURCE}:{detail_id}:{player_video_id}",
                "detail_id": detail_id,
                "player_index": index,
                "video_id": player_video_id,
                "video_title": player_title,
                "video_url": video_url,
                "video_type": "hls" if video_type.lower() == "hls" or urlparse(video_url).path.lower().endswith(".m3u8") else "direct",
                "referer": detail_url,
                "tags": [],
                "player_key": player_key,
            }
        )
    return {
        "guid": f"{ATTACH_SOURCE}:{detail_id}",
        "detail_id": detail_id,
        "url": detail_url,
        "title": title,
        "description": description,
        "image": image,
        "images": [image] if image else [],
        "published_at": published_at,
        "modified_at": modified_at,
        "tags": tags[:12],
        "players": players,
    }


def parse_query_expiry(video_url: str) -> datetime | None:
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        parsed = parse_epoch_datetime((query.get(key) or [None])[0])
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    # This site's auth_key prefix mirrors the page/playlist generation time and
    # remains playable after that timestamp, so it is not a reliable expiry.
    return None


def playback_expiry(urls: list[str]) -> datetime | None:
    expiries = [expiry for expiry in (parse_query_expiry(url) for url in urls) if expiry]
    return min(expiries) if expiries else None


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"Attach {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"Attach {label} URL points to an ad host: {host}")
    if (
        label == "playback"
        and host not in EXPECTED_PLAYBACK_HOSTS
        and not any(host.endswith(suffix) for suffix in EXPECTED_PLAYBACK_HOST_SUFFIXES)
        and not host.endswith(".bslqmdvk.cc")
    ):
        raise ValueError(f"Attach playback URL is outside expected media hosts: {host}")


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
    return next(response.iter_content(size), b""), response


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


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for line in playlist.splitlines():
        value = line.strip()
        if not value.startswith("#EXT-X-KEY"):
            continue
        uri = parse_hls_attrs(value).get("URI")
        if uri:
            key_url = urljoin(video_url, uri)
            if key_url not in seen:
                urls.append(key_url)
                seen.add(key_url)
    return urls


def playlist_segments(video_url: str, playlist: str) -> list[dict]:
    segments: list[dict] = []
    current_key: dict[str, str] | None = None
    media_sequence = 0
    for line in playlist.splitlines():
        value = line.strip()
        if value.startswith("#EXT-X-MEDIA-SEQUENCE"):
            media_sequence = int_or_none(value.split(":", 1)[1] if ":" in value else None) or 0
            continue
        if value.startswith("#EXT-X-KEY"):
            current_key = parse_hls_attrs(value)
            continue
        if not value or value.startswith("#"):
            continue
        segments.append({"url": urljoin(video_url, value), "key": current_key, "sequence": media_sequence + len(segments)})
    return segments


def playlist_variant_urls(video_url: str, playlist: str) -> list[str]:
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
        return bytes.fromhex(raw.zfill(32))
    return sequence.to_bytes(16, "big")


def decrypt_aes128_chunk(chunk: bytes, key: bytes, key_attrs: dict[str, str] | None, sequence: int) -> bytes:
    usable = len(chunk) - (len(chunk) % 16)
    if usable <= 0:
        return b""
    return AES.new(key, AES.MODE_CBC, aes_iv_for_segment(key_attrs, sequence)).decrypt(chunk[:usable])


def verify_media_hls_url(video_url: str, referer: str | None) -> dict:
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("Attach HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < 5:
        raise ValueError("Attach HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("Attach HLS playlist has no media segments.")
    key_cache: dict[str, bytes] = {}
    key_urls = playlist_key_urls(video_url, playlist)
    for key_url in key_urls[:3]:
        chunk, _response = read_media_chunk(key_url, referer, 16)
        if len(chunk) != 16:
            raise ValueError("Attach HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk
    segment_error: Exception | None = None
    for segment in segments[:8]:
        try:
            segment_url = segment["url"]
            chunk, _response = read_media_chunk(segment_url, referer, 4096)
            probe = chunk
            key_attrs = segment.get("key")
            if key_attrs and key_attrs.get("METHOD", "").upper() == "AES-128":
                key_url = urljoin(video_url, key_attrs.get("URI", ""))
                key = key_cache.get(key_url)
                if not key:
                    key, _key_response = read_media_chunk(key_url, referer, 16)
                probe = decrypt_aes128_chunk(chunk, key, key_attrs, int(segment.get("sequence") or 0))
            if looks_like_media_chunk(probe):
                expires_at = playback_expiry([video_url, *(item["url"] for item in segments[:3]), *key_urls[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("Attach HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "playback_headers": playback_headers(referer),
                    "video_url_expires_at": expires_at or ATTACH_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_duration_seconds": total_duration,
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "media_url_count": len(segments),
                    "key_url_count": len(key_urls),
                    "encrypted": bool(key_urls),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"Attach HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, referer: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("Attach HLS URL must be an .m3u8 URL.")
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist:
        raise ValueError("Attach HLS URL is not a playlist.")
    variants = playlist_variant_urls(video_url, playlist)
    if variants:
        last_error: Exception | None = None
        for variant_url in variants:
            try:
                verified = verify_media_hls_url(variant_url, referer)
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
        raise ValueError(f"Attach HLS master playlist has no playable variants: {last_error}")
    return verify_media_hls_url(video_url, referer)


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
        raise ValueError("Attach direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("Attach direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, referer, 4096)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type or "image/" in content_type:
        raise ValueError("Attach direct video URL did not return media bytes.")
    if not looks_like_media_chunk(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("Attach direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or ATTACH_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, referer: str | None, video_type: str | None = None) -> dict:
    if video_type == "hls" or urlparse(video_url).path.lower().endswith(".m3u8"):
        return verify_hls_url(video_url, referer)
    return verify_direct_video_url(video_url, referer)


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_attach_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (ATTACH_SOURCE, ATTACH_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([ATTACH_SITE_NAME, "video"]), public_pool),
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
        "display_author": ATTACH_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": ATTACH_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or ATTACH_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": ATTACH_KIND,
        "target_value": target_row["value"],
        "site_name": ATTACH_SITE_NAME,
        "source_url": detail["url"],
        "attach_detail_id": detail["detail_id"],
        "attach_video_id": player["video_id"],
        "attach_player_key": player.get("player_key"),
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "variant_url": verified.get("variant_url"),
        "video_poster_url": detail.get("image"),
        "tags": list(dict.fromkeys([*(detail.get("tags") or []), *(player.get("tags") or [])]))[:20],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "attach-dplayer-video-url",
        "resolved_at": now_iso(),
        "playback_headers": verified.get("playback_headers"),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "playlist_bytes": verified.get("playlist_bytes"),
        "master_playlist_bytes": verified.get("master_playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "encrypted": verified.get("encrypted"),
        "content_type": verified.get("content_type"),
        "content_length": verified.get("content_length"),
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
                ATTACH_SITE_NAME,
                ATTACH_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
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
    base_url = normalize_attach_target_value(base_url)
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
        print(f"[attach] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[attach] page={page} empty_list stop=true")
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
                print(f"[attach] skip detail {list_item.get('url')}: {exc}")
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
                    print(f"[attach] skip unverified {player['guid']}: {exc}")
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
            f"[attach] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
