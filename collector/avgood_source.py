from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from psycopg.types.json import Jsonb


AVGOOD_SITE_NAME = "AvGood"
AVGOOD_SOURCE = "avgood"
AVGOOD_KIND = "site"
AVGOOD_DEFAULT_BASE_URL = os.environ.get("AVGOOD_BASE_URL", "https://avgood.com/c/664/").strip() or "https://avgood.com/c/664/"
AVGOOD_RETENTION_HOURS = int(os.environ.get("AVGOOD_RETENTION_HOURS", "84"))
AVGOOD_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("AVGOOD_REQUEST_TIMEOUT_SECONDS", "30"))
AVGOOD_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("AVGOOD_MIN_PLAYLIST_DURATION_SECONDS", "5"))
AVGOOD_REFRESH_WINDOW_MINUTES = int(os.environ.get("AVGOOD_REFRESH_WINDOW_MINUTES", "90"))
AVGOOD_CRITICAL_WINDOW_MINUTES = int(os.environ.get("AVGOOD_CRITICAL_WINDOW_MINUTES", "15"))
AVGOOD_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "doubleclick",
    "adnxs",
    "ads",
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


def normalize_avgood_target_value(raw: str) -> str:
    value = (raw or AVGOOD_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("AvGood target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_avgood_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"avgood.com", "www.avgood.com"}


def format_target_row(target_row: dict) -> str:
    return f"avgood:{target_row['value']}"


def request_origin(referer: str | None) -> str | None:
    if not referer:
        return None
    parsed = urlparse(referer)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def headers(
    referer: str | None = None,
    *,
    accept: str | None = None,
    range_header: str | None = None,
    ajax: bool = False,
) -> dict[str, str]:
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
    if ajax:
        result["X-Requested-With"] = "XMLHttpRequest"
    return result


def request_with_proxy_fallback(
    url: str,
    *,
    referer: str | None = None,
    accept: str | None = None,
    range_header: str | None = None,
    stream: bool = False,
    ajax: bool = False,
) -> requests.Response:
    last_error: Exception | None = None
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            response = session.get(
                url,
                headers=headers(referer, accept=accept, range_header=range_header, ajax=ajax),
                timeout=AVGOOD_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("AvGood request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_json(url: str, referer: str | None = None) -> dict:
    response = request_with_proxy_fallback(
        url,
        referer=referer,
        accept="application/json,text/javascript,*/*;q=0.01",
        ajax=True,
    )
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("AvGood AJAX response is not a JSON object.")
    return payload


def same_or_subdomain(host: str, allowed_host: str) -> bool:
    return host == allowed_host or host.endswith(f".{allowed_host}")


def reject_ad_url(url: str, label: str = "playback", allowed_hosts: set[str] | None = None) -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"AvGood {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"AvGood {label} URL points to an ad host: {host}")
    if allowed_hosts and not any(same_or_subdomain(host, allowed_host) for allowed_host in allowed_hosts):
        raise ValueError(f"AvGood {label} URL is outside configured media hosts: {host}")


def fetch_media_text(url: str, page_url: str | None, allowed_hosts: set[str] | None = None) -> str:
    reject_ad_url(url, allowed_hosts=allowed_hosts)
    return request_with_proxy_fallback(
        url,
        referer=page_url,
        accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*",
    ).text


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


def target_list_path(raw: str | None) -> str:
    value = (raw or AVGOOD_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    path = parsed.path.rstrip("/")
    match = re.match(r"^(/c/\d+)(?:/\d+)?$", path)
    if match:
        return match.group(1)
    return "/c/664"


def build_list_page_url(base_url: str, page: int) -> str:
    root = normalize_avgood_target_value(base_url)
    path = target_list_path(base_url).rstrip("/")
    page = max(1, page)
    if page > 1:
        path = f"{path}/{page}"
    return urljoin(root + "/", f"{path.lstrip('/')}/")


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_avgood_target_value(base_url) + "/", f"c/{video_id}.html")


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/c/(\d+)\.html$", urlparse(url).path)
    return match.group(1) if match else None


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    raw = html_unescape(raw)
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url.rstrip("/") + "/", raw)
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def parse_avgood_date(value: str | None) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_duration_seconds(value: str | None) -> int | None:
    text = clean_text(value)
    if not text:
        return None
    match = re.search(r"(?:(\d+):)?(\d{1,2}):(\d{2})", text)
    if not match:
        return None
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    return hours * 3600 + minutes * 60 + seconds


def list_image_url(page_url: str, node) -> str | None:
    image = node.select_one(".card-image img")
    if not image:
        return None
    return normalize_asset_url(page_url, image.get("data-original") or image.get("data-src") or image.get("src"))


def parse_entry(node, page_url: str, base_url: str) -> dict | None:
    link = node.select_one('a.card[href^="/c/"][href$=".html"], a.card[href*="/c/"][href$=".html"]')
    href = link.get("href") if link else None
    item_url = normalize_asset_url(page_url, href)
    video_id = detail_id_from_url(item_url or "")
    if not item_url or not video_id:
        return None
    title_node = node.select_one(".card-title")
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else None)
    category_node = node.select_one(".card-tags .tag-category")
    category = clean_text(category_node.get_text(" ", strip=True) if category_node else None)
    image = list_image_url(page_url, node)
    return {
        "guid": f"{AVGOOD_SOURCE}:{video_id}",
        "video_id": video_id,
        "url": item_url,
        "title": title or f"{AVGOOD_SITE_NAME} video {video_id}",
        "description": title,
        "image": image,
        "author_name": category or AVGOOD_SITE_NAME,
        "author_url": normalize_avgood_target_value(base_url),
        "category_name": category,
        "duration": None,
        "published_at": None,
        "modified_at": None,
        "tags": [tag for tag in [category, AVGOOD_SITE_NAME] if tag],
    }


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for entry in soup.select(".list-grid-container .grid-item"):
        item = parse_entry(entry, page_url, base_url)
        if not item or item["video_id"] in seen:
            continue
        seen.add(item["video_id"])
        items.append(item)
    return items


def extract_iframe_url(detail_page_url: str, soup: BeautifulSoup) -> str:
    iframe = soup.select_one("iframe#video-player[src], iframe[src*='/remote_play/video/play/']")
    iframe_url = normalize_asset_url(detail_page_url, iframe.get("src") if iframe else None)
    if not iframe_url:
        raise ValueError("AvGood detail page is missing video iframe.")
    return iframe_url


def extract_ajax_url(iframe_url: str, iframe_html: str) -> tuple[str, str | None]:
    player_id_match = re.search(r"var\s+player_id\s*=\s*['\"]([^'\"]+)['\"]", iframe_html)
    player_id = clean_text(player_id_match.group(1) if player_id_match else None)
    ajax_match = re.search(r"var\s+ajax_url\s*=\s*['\"]([^'\"]+)['\"]", iframe_html)
    ajax_url = normalize_asset_url(iframe_url, ajax_match.group(1) if ajax_match else None)
    if not ajax_url and player_id:
        ajax_url = normalize_asset_url(iframe_url, f"/remote_play/index.php/play/ajax/{player_id}.html")
    if not ajax_url:
        iframe_id_match = re.search(r"/play/(\d+)\.html", urlparse(iframe_url).path)
        if iframe_id_match:
            player_id = player_id or iframe_id_match.group(1)
            ajax_url = normalize_asset_url(iframe_url, f"/remote_play/index.php/play/ajax/{player_id}.html")
    if not ajax_url:
        raise ValueError("AvGood iframe page is missing AJAX URL.")
    return ajax_url, player_id


def strip_cache_only_query(video_url: str) -> str:
    parsed = urlparse(video_url)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key.lower() != "t"]
    return urlunparse(parsed._replace(query=urlencode(pairs, doseq=True)))


def classify_video_type(video_url: str) -> str:
    path = urlparse(video_url).path.lower()
    if path.endswith(".m3u8"):
        return "hls"
    if path.endswith(DIRECT_VIDEO_EXTENSIONS):
        return "direct"
    raise ValueError("AvGood playback URL is not a supported direct video or HLS URL.")


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, (list_item or {}).get("url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    video_id = detail_id_from_url(detail_page_url) or (list_item or {}).get("video_id")
    if not video_id:
        raise ValueError("AvGood detail URL is missing video id.")

    title_node = soup.select_one("h1.content-title")
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else None) or (list_item or {}).get("title") or f"{AVGOOD_SITE_NAME} video {video_id}"
    meta_description = soup.select_one('meta[name="description"]')
    meta_text = meta_description.get("content") if meta_description else None
    published_at = parse_avgood_date(meta_text) or (list_item or {}).get("published_at") or now_utc()

    info_text = " ".join(node.get_text(" ", strip=True) for node in soup.select(".description-section.content-info"))
    category = clean_text((re.search(r"\u7c7b\u522b\uff1a([^,\s]+)", info_text) or re.search(r"\u7c7b\u522b[:\uff1a]\s*([^,\s]+)", info_text) or [None, None])[1]) or (list_item or {}).get("category_name")
    duration = parse_duration_seconds(info_text) or (list_item or {}).get("duration")
    images = []
    for image in soup.select(".description-images img[src], .description-images img[data-original]"):
        image_url = normalize_asset_url(detail_page_url, image.get("data-original") or image.get("data-src") or image.get("src"))
        if image_url and image_url not in images:
            images.append(image_url)
    if (list_item or {}).get("image") and (list_item or {})["image"] not in images:
        images.append((list_item or {})["image"])

    iframe_url = extract_iframe_url(detail_page_url, soup)
    iframe_html = fetch_html(iframe_url, detail_page_url)
    ajax_url, player_id = extract_ajax_url(iframe_url, iframe_html)
    payload = fetch_json(ajax_url, iframe_url)
    if int_or_none(payload.get("zt")) not in (None, 0):
        raise ValueError(f"AvGood AJAX response returned non-playable status: {payload.get('zt')}")
    playlink = non_empty(payload.get("playlink"))
    if not playlink:
        raise ValueError("AvGood AJAX response is missing playlink.")
    video_url = normalize_asset_url(normalize_avgood_target_value(detail_page_url), playlink)
    if not video_url:
        raise ValueError("AvGood AJAX playlink did not resolve to a URL.")
    video_url = strip_cache_only_query(video_url)
    video_type = classify_video_type(video_url)
    poster_url = normalize_asset_url(normalize_avgood_target_value(detail_page_url), non_empty(payload.get("piclink")))
    if poster_url and poster_url not in images:
        images.insert(0, poster_url)

    return {
        "guid": f"{AVGOOD_SOURCE}:{video_id}",
        "video_id": video_id,
        "player_id": player_id,
        "url": detail_url(detail_page_url, video_id),
        "iframe_url": iframe_url,
        "ajax_url": ajax_url,
        "title": title,
        "description": title,
        "image": images[0] if images else None,
        "images": images,
        "author_name": category or AVGOOD_SITE_NAME,
        "author_url": normalize_avgood_target_value(detail_page_url),
        "category_name": category,
        "duration": duration,
        "published_at": published_at,
        "modified_at": None,
        "tags": [tag for tag in [category, AVGOOD_SITE_NAME] if tag],
        "players": [
            {
                "guid": f"{AVGOOD_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_id": player_id,
                "player_index": 1,
                "video_title": title,
                "video_url": video_url,
                "video_type": video_type,
                "referer": iframe_url,
                "allowed_media_hosts": {"avgood.com", "www.avgood.com"},
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


def resolve_hls_uri(media_url: str, uri: str) -> str:
    raw = uri.strip()
    parsed_raw = urlparse(raw)
    if parsed_raw.scheme:
        return raw
    if raw.startswith("//"):
        return f"{urlparse(media_url).scheme}:{raw}"
    if raw.startswith("/"):
        return urljoin(media_url, raw)
    parsed = urlparse(media_url)
    if "%2f" in parsed.path.lower():
        prefix = media_playlist_content_prefix(media_url)
        relative = urlparse(raw)
        return urlunparse((parsed.scheme, parsed.netloc, prefix + relative.path, "", relative.query, relative.fragment))
    return urljoin(media_url, raw)


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
            variants.append({"url": resolve_hls_uri(video_url, value), "stream_inf": stream_inf})
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
            urls.append(resolve_hls_uri(video_url, attrs["URI"]))
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
                current_key = {"url": resolve_hls_uri(video_url, attrs["URI"]), "iv": attrs.get("IV")}
            continue
        if value.startswith("#"):
            continue
        segments.append({"url": resolve_hls_uri(video_url, value), "sequence": media_sequence, "key": dict(current_key) if current_key else None})
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
    if "%2f" in path.lower():
        match = re.search(r"(?i)(.*%2f)[^%/]*\.m3u8$", path)
        if match:
            return match.group(1)
    return path.rsplit("/", 1)[0] + "/"


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


def b64url(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def clean_hls_endpoint_url(media_url: str, referer: str, content_prefix: str, *, rewrite_only: bool) -> str:
    query = urlencode({"u": b64url(media_url), "r": b64url(referer), "p": b64url(content_prefix), "rw": "1" if rewrite_only else "0"})
    return f"/api/hls/clean?{query}"


def expected_duration_floor(expected_duration: int | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(float(AVGOOD_MIN_PLAYLIST_DURATION_SECONDS), expected_duration * 0.4)
    return float(AVGOOD_MIN_PLAYLIST_DURATION_SECONDS)


def verify_media_playlist(
    media_url: str,
    playlist: str,
    page_url: str | None,
    expected_duration: int | None,
    allowed_hosts: set[str] | None,
) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("AvGood HLS media playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("AvGood HLS playlist is too short for a real video.")

    content_prefix = media_playlist_content_prefix(media_url)
    all_segments = playlist_segments(media_url, playlist)
    segments = [segment for segment in all_segments if urlparse(segment["url"]).path.startswith(content_prefix)]
    removed_segments = len(all_segments) - len(segments)
    if not segments:
        raise ValueError("AvGood HLS playlist has no media segments.")

    map_urls = [url for url in playlist_map_urls(media_url, playlist) if urlparse(url).path.startswith(content_prefix)]
    key_cache: dict[str, bytes] = {}
    for key_url in [url for url in playlist_key_urls(media_url, playlist) if urlparse(url).path.startswith(content_prefix)][:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
        if len(chunk) != 16:
            raise ValueError("AvGood HLS AES key is not 16 bytes.")
        key_cache[key_url] = chunk

    checked_init = False
    for map_url in map_urls[:2]:
        chunk, _response = read_media_chunk(map_url, page_url, 512, allowed_hosts)
        if chunk and looks_like_media_segment(chunk):
            checked_init = True
            break
    if map_urls and not checked_init:
        raise ValueError("AvGood HLS playlist has no readable fMP4 init segment.")

    segment_error: Exception | None = None
    for segment in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment["url"], page_url, 4096, allowed_hosts)
            media_chunk = chunk
            key = segment.get("key")
            if key:
                key_url = key["url"]
                if not urlparse(key_url).path.startswith(content_prefix):
                    raise ValueError("AvGood HLS key is outside the content prefix.")
                key_bytes = key_cache.get(key_url)
                if key_bytes is None:
                    key_chunk, _key_response = read_media_chunk(key_url, page_url, 16, allowed_hosts)
                    if len(key_chunk) != 16:
                        raise ValueError("AvGood HLS AES key is not 16 bytes.")
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
                    "content_path_prefix": content_prefix,
                    "removed_ad_segment_count": removed_segments,
                    "removed_ad_duration_seconds": 0,
                    "encrypted": bool(key_cache),
                    "rewrite_only_hls": "%2f" in urlparse(media_url).path.lower(),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"AvGood HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None, expected_duration: int | None = None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("AvGood HLS URL must be an .m3u8 URL.")

    master_playlist = fetch_media_text(video_url, page_url, allowed_hosts)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("AvGood HLS URL is not a playlist.")

    checked_urls = [video_url]
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
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"AvGood HLS master playlist has no playable variant: {media_error}")
    else:
        checked_urls.extend(segment["url"] for segment in playlist_segments(video_url, master_playlist)[:3])
        checked_urls.extend(playlist_map_urls(video_url, master_playlist)[:1])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_playlist(video_url, master_playlist, page_url, expected_duration, allowed_hosts),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
        }

    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("AvGood HLS URL is already expired or too close to expiry.")

    checked_media_url = str(media_result["checked_media_playlist_url"])
    playback_url = video_url
    if bool(media_result.get("rewrite_only_hls")) or int(media_result.get("removed_ad_segment_count") or 0) > 0:
        playback_url = clean_hls_endpoint_url(
            checked_media_url,
            page_url or normalize_avgood_target_value(video_url),
            str(media_result["content_path_prefix"]),
            rewrite_only=bool(media_result.get("rewrite_only_hls")),
        )
    return {
        "video_url": playback_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or AVGOOD_STABLE_VIDEO_URL_EXPIRES_AT,
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


def verify_direct_video_url(video_url: str, page_url: str | None, expected_duration: int | None = None, allowed_hosts: set[str] | None = None) -> dict:
    reject_ad_url(video_url, allowed_hosts=allowed_hosts)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(DIRECT_VIDEO_EXTENSIONS):
        raise ValueError("AvGood direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("AvGood direct video URL is already expired or too close to expiry.")
    if expected_duration and expected_duration < AVGOOD_MIN_PLAYLIST_DURATION_SECONDS:
        raise ValueError("AvGood direct video duration is too short for a real video.")
    chunk, response = read_media_chunk(video_url, page_url, 2048, allowed_hosts)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type:
        raise ValueError("AvGood direct video URL did not return media bytes.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("AvGood direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "video_url_expires_at": expires_at or AVGOOD_STABLE_VIDEO_URL_EXPIRES_AT,
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
    value = normalize_avgood_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (AVGOOD_SOURCE, AVGOOD_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([AVGOOD_SITE_NAME, "video"]), public_pool),
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
        cur.execute(
            """
            UPDATE items
            SET title = %s,
                content = %s,
                images = CASE WHEN jsonb_array_length(%s::jsonb) > 0 THEN %s ELSE images END,
                stored_at = stored_at
            WHERE target_id = %s AND guid = %s
            """,
            (title, title, Jsonb(images), Jsonb(images), target_id, guid),
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


def build_author_presentation(detail: dict) -> dict[str, str | None]:
    return {
        "display_author": clean_text(detail.get("author_name")) or AVGOOD_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": AVGOOD_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or AVGOOD_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": AVGOOD_KIND,
        "target_value": target_row["value"],
        "site_name": AVGOOD_SITE_NAME,
        "source_url": detail["url"],
        "iframe_url": detail.get("iframe_url"),
        "ajax_url": detail.get("ajax_url"),
        "avgood_video_id": detail["video_id"],
        "avgood_player_id": player.get("player_id"),
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "category_name": detail.get("category_name"),
        "tags": detail.get("tags") or [],
        "resolver": "avgood-remote-play-ajax",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "raw_playlist_bytes": verified.get("raw_playlist_bytes"),
        "media_url_count": verified.get("media_url_count"),
        "map_url_count": verified.get("map_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "variant_count": verified.get("variant_count"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
        "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
        "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
        "content_path_prefix": verified.get("content_path_prefix"),
        "rewrite_only_hls": verified.get("rewrite_only_hls"),
        "encrypted": verified.get("encrypted"),
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
                detail.get("author_name") or AVGOOD_SITE_NAME,
                detail.get("author_name") or AVGOOD_SITE_NAME,
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
        page_inserted = page_updated = page_existing = page_text_refreshed = page_old = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[avgood] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[avgood] page={page} empty_list stop=true")
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
                print(f"[avgood] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(
                        player["video_url"],
                        player.get("referer") or detail.get("iframe_url") or detail["url"],
                        player["video_type"],
                        detail.get("duration"),
                        player.get("allowed_media_hosts"),
                    )
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[avgood] skip unverified {player['guid']}: {exc}")
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
                            "media_url_count": verified.get("media_url_count"),
                            "key_url_count": verified.get("key_url_count"),
                            "rewrite_only_hls": verified.get("rewrite_only_hls"),
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
            f"[avgood] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
            (AVGOOD_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (AVGOOD_SOURCE, refresh_window_minutes, limit),
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
            source_url = metadata.get("source_url") or row.get("link")
            video_id = metadata.get("avgood_video_id") or str(row["guid"]).replace(f"{AVGOOD_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or AVGOOD_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or avgood_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(
                    player["video_url"],
                    player.get("referer") or detail.get("iframe_url") or detail["url"],
                    player["video_type"],
                    detail.get("duration"),
                    player.get("allowed_media_hosts"),
                )
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "avgood-remote-play-ajax",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "iframe_url": detail.get("iframe_url"),
                    "ajax_url": detail.get("ajax_url"),
                    "avgood_video_id": detail["video_id"],
                    "avgood_player_id": player.get("player_id"),
                    "raw_video_url": verified.get("raw_video_url"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "duration": detail.get("duration") or metadata.get("duration"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "raw_playlist_bytes": verified.get("raw_playlist_bytes"),
                    "media_url_count": verified.get("media_url_count"),
                    "map_url_count": verified.get("map_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "variant_count": verified.get("variant_count"),
                    "selected_variant_url": verified.get("selected_variant_url"),
                    "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
                    "removed_ad_segment_count": verified.get("removed_ad_segment_count"),
                    "removed_ad_duration_seconds": verified.get("removed_ad_duration_seconds"),
                    "content_path_prefix": verified.get("content_path_prefix"),
                    "rewrite_only_hls": verified.get("rewrite_only_hls"),
                    "encrypted": verified.get("encrypted"),
                }
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""",
                        (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]),
                    )
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[avgood] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
