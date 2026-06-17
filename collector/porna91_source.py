from __future__ import annotations

import ast
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, parse_qsl, unquote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from Crypto.Cipher import AES
from psycopg.types.json import Jsonb


PORNA91_SITE_NAME = "91porna"
PORNA91_SOURCE = "91porna"
PORNA91_KIND = "site"
PORNA91_DEFAULT_BASE_URL = os.environ.get("PORNA91_BASE_URL", "https://91porna.com").strip().rstrip("/")
PORNA91_DEFAULT_CATEGORY = os.environ.get("PORNA91_CATEGORY", "new_update").strip() or "new_update"
PORNA91_RETENTION_HOURS = int(os.environ.get("PORNA91_RETENTION_HOURS", "84"))
PORNA91_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("PORNA91_REQUEST_TIMEOUT_SECONDS", "30"))
PORNA91_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("PORNA91_MIN_PLAYLIST_DURATION_SECONDS", "15"))
PORNA91_REFRESH_WINDOW_MINUTES = int(os.environ.get("PORNA91_REFRESH_WINDOW_MINUTES", "90"))
PORNA91_CRITICAL_WINDOW_MINUTES = int(os.environ.get("PORNA91_CRITICAL_WINDOW_MINUTES", "15"))

PORNA91_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
AD_HOST_KEYWORDS = (
    "jyreuyjr",
    "mqoeomads",
    "qhzxnjnt",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "adnxs",
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
    days = float(match.group("days") or 0)
    hours = float(match.group("hours") or 0)
    minutes = float(match.group("minutes") or 0)
    seconds = float(match.group("seconds") or 0)
    total = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return total if total > 0 else None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_porna91_target_value(raw: str) -> str:
    value = (raw or PORNA91_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = PORNA91_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("91porna target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_porna91_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"91porna.com", "www.91porna.com"}


def category_from_target(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return PORNA91_DEFAULT_CATEGORY
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return PORNA91_DEFAULT_CATEGORY
    category = non_empty((parse_qs(parsed.query).get("category") or [""])[0])
    return category or PORNA91_DEFAULT_CATEGORY


def format_target_row(target_row: dict) -> str:
    return f"91porna:{target_row['value']}"


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
                timeout=PORNA91_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("91porna request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_hls_text(url: str, referer: str) -> str:
    return request_with_proxy_fallback(url, referer=referer, accept="*/*").text


def read_hls_chunk(url: str, referer: str, size: int) -> bytes:
    with request_with_proxy_fallback(url, referer=referer, accept="*/*", stream=True) as response:
        return next(response.iter_content(size), b"")


def build_list_page_url(base_url: str, page: int, category: str | None = None) -> str:
    query = {"category": category or PORNA91_DEFAULT_CATEGORY}
    if page > 1:
        query["page"] = str(page)
    return f"{normalize_porna91_target_value(base_url)}/comic/index/video?{urlencode(query)}"


def detail_url(base_url: str, video_id: str) -> str:
    return f"{normalize_porna91_target_value(base_url)}/comic/index/detail?{urlencode({'video_key': video_id})}"


def detail_id_from_url(url: str) -> str | None:
    return non_empty((parse_qs(urlparse(url).query).get("video_key") or [""])[0])


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url + "/", html_unescape(raw))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


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
        values = data if isinstance(data, list) else [data]
        for value in values:
            if not isinstance(value, dict):
                continue
            payloads.append(value)
            graph = value.get("@graph")
            if isinstance(graph, list):
                payloads.extend(node for node in graph if isinstance(node, dict))
    return payloads


def find_json_ld(payloads: list[dict], ld_type: str) -> dict | None:
    for payload in payloads:
        payload_type = payload.get("@type")
        if payload_type == ld_type or (isinstance(payload_type, list) and ld_type in payload_type):
            return payload
    return None


def parse_list_page(base_url: str, page: int, category: str | None = None) -> list[dict]:
    page_url = build_list_page_url(base_url, page, category)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1, category)), "html.parser")
    item_list = find_json_ld(iter_json_ld(soup), "ItemList")
    raw_items = item_list.get("itemListElement") if isinstance(item_list, dict) else []
    items: list[dict] = []
    seen: set[str] = set()
    for raw_item in raw_items if isinstance(raw_items, list) else []:
        item = raw_item.get("item") if isinstance(raw_item, dict) and isinstance(raw_item.get("item"), dict) else raw_item
        if not isinstance(item, dict):
            continue
        source_url = normalize_asset_url(base_url, non_empty(item.get("url")))
        video_id = detail_id_from_url(source_url or "")
        if not source_url or not video_id or video_id in seen:
            continue
        seen.add(video_id)
        image_payload = item.get("primaryImageOfPage") if isinstance(item.get("primaryImageOfPage"), dict) else {}
        image = normalize_asset_url(source_url, non_empty(image_payload.get("url")) or non_empty(item.get("thumbnailUrl")))
        published_at = parse_datetime(non_empty(item.get("datePublished"))) or now_utc()
        items.append(
            {
                "guid": f"{PORNA91_SOURCE}:{video_id}",
                "video_id": video_id,
                "url": detail_url(base_url, video_id),
                "source_url": source_url,
                "title": non_empty(item.get("name")) or PORNA91_SITE_NAME,
                "image": image,
                "published_at": published_at,
            }
        )
    return items


def js_string_value(raw: str) -> str:
    try:
        return ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        quote = raw[:1]
        body = raw[1:-1] if quote in {"'", '"'} else raw
        return bytes(body.replace("\\/", "/"), "utf-8").decode("unicode_escape")


def base_decode(value: str, base: int) -> int:
    result = 0
    for char in value:
        code = ord(char)
        if 48 <= code <= 57:
            digit = code - 48
        elif 97 <= code <= 122:
            digit = code - 87
        elif 65 <= code <= 90:
            digit = code - 29
        else:
            digit = base
        if digit >= base:
            raise ValueError("invalid packed-js digit.")
        result = result * base + digit
    return result


PACKED_RE = re.compile(
    r"eval\(function\(p,a,c,k,e,[rd]\).*?\(\s*"
    r"(?P<payload>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")\s*,\s*"
    r"(?P<radix>\d+)\s*,\s*(?P<count>\d+)\s*,\s*"
    r"(?P<words>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")\.split\('\|'\)",
    flags=re.DOTALL,
)


def unpack_packed_js(script: str) -> str:
    match = PACKED_RE.search(script)
    if not match:
        raise ValueError("91porna page is missing packed player script.")
    payload = js_string_value(match.group("payload"))
    radix = int(match.group("radix"))
    count = int(match.group("count"))
    words = js_string_value(match.group("words")).split("|")
    for index in range(count - 1, -1, -1):
        word = words[index] if index < len(words) else ""
        if not word:
            continue
        token = ""
        number = index
        while True:
            digit = number % radix
            if digit > 35:
                token = chr(digit + 29) + token
            else:
                token = "0123456789abcdefghijklmnopqrstuvwxyz"[digit] + token
            number //= radix
            if number == 0:
                break
        payload = re.sub(rf"\b{re.escape(token)}\b", word, payload)
    return payload


def first_packed_script(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script"):
        text = script.string or script.get_text()
        if "eval(function(p,a,c,k,e," in text:
            return text
    raise ValueError("91porna detail page is missing packed script.")


def extract_detail_play_params(unpacked: str) -> dict[str, str]:
    u_match = re.search(r'encodeURIComponent\((?P<u>"(?:\\.|[^"])*"|\'(?:\\.|[^\'])*\')\)', unpacked)
    u_value = js_string_value(u_match.group("u")) if u_match else ""
    src_match = re.search(r'document\.write\("(?P<script><script\s+src=\\?"(?P<src>[^"]+?)\\?")', unpacked, flags=re.DOTALL)
    if src_match:
        src = html_unescape(src_match.group("src").replace('\\"', '"'))
        parsed_src = urlparse(src)
        query = parse_qs(parsed_src.query)
        params = {
            "img": non_empty((query.get("img") or [""])[0]) or "",
            "ads": non_empty((query.get("ads") or [""])[0]) or "",
            "u": u_value or non_empty((query.get("u") or [""])[0]) or "",
        }
        if params["img"] and params["u"]:
            return params

    img_match = re.search(r"detail_play\?img=(?P<img>[^&\"']+)", unpacked)
    ads_match = re.search(r"(?:&amp;|&)ads=(?P<ads>[^&\"']+)", unpacked)
    return {
        "img": unquote(html_unescape(img_match.group("img"))) if img_match else "",
        "ads": unquote(html_unescape(ads_match.group("ads"))) if ads_match else "",
        "u": u_value,
    }


def build_detail_play_url(base_url: str, detail_html: str) -> str:
    unpacked = unpack_packed_js(first_packed_script(detail_html))
    params = extract_detail_play_params(unpacked)
    img = non_empty(params.get("img"))
    u_value = non_empty(params.get("u"))
    if not img or not u_value:
        raise ValueError("91porna packed detail script is missing detail_play params.")
    query = {
        "img": img,
        "ads": params.get("ads") or "",
        "u": u_value,
        "t": str(int(time.time() / 1800)),
    }
    return f"{normalize_porna91_target_value(base_url)}/index/detail_play?{urlencode(query)}"


def extract_player_hls_url(unpacked: str) -> str:
    create_player_index = unpacked.find("create_player")
    search_area = unpacked[create_player_index:] if create_player_index >= 0 else unpacked
    match = re.search(r"\burl\s*:\s*(?P<url>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")", search_area)
    if not match:
        raise ValueError("91porna player script is missing m3u8 URL.")
    video_url = js_string_value(match.group("url"))
    if ".m3u8" not in urlparse(video_url).path.lower():
        raise ValueError("91porna player URL is not an HLS playlist.")
    return normalize_player_hls_url(video_url)


def normalize_player_hls_url(video_url: str) -> str:
    parsed = urlparse(video_url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    has_version = False
    normalized_query: list[tuple[str, str]] = []
    for key, value in query_items:
        if key == "v":
            has_version = True
            normalized_query.append((key, "3" if value in {"", "undefined", "null"} else value))
        else:
            normalized_query.append((key, value))
    if not has_version:
        normalized_query.append(("v", "3"))
    return urlunparse(parsed._replace(query=urlencode(normalized_query)))


def reject_ad_url(url: str, label: str = "playback") -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"91porna {label} URL points to an ad host: {host}")


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls: list[str] = []
    for match in re.finditer(r"#EXT-X-KEY:[^\n]*\bURI=\"(?P<uri>[^\"]+)\"", playlist):
        urls.append(urljoin(video_url, match.group("uri")))
    return urls


def parse_hls_key_lines(video_url: str, playlist: str) -> list[dict[str, str | None]]:
    keys: list[dict[str, str | None]] = []
    for match in re.finditer(r"#EXT-X-KEY:(?P<attrs>[^\n]+)", playlist):
        attrs = match.group("attrs")
        method_match = re.search(r"\bMETHOD=([^,]+)", attrs)
        uri_match = re.search(r"\bURI=\"([^\"]+)\"", attrs)
        iv_match = re.search(r"\bIV=(0x[0-9a-fA-F]+)", attrs)
        if uri_match:
            keys.append(
                {
                    "method": method_match.group(1).strip() if method_match else None,
                    "uri": urljoin(video_url, uri_match.group(1)),
                    "iv": iv_match.group(1) if iv_match else None,
                }
            )
    return keys


def parse_auth_key_parts(url: str) -> tuple[int, list[str]] | None:
    auth_key = non_empty((parse_qs(urlparse(url).query).get("auth_key") or [""])[0])
    if not auth_key:
        return None
    parts = auth_key.split("-")
    epoch = int_or_none(parts[0])
    if not epoch:
        return None
    return epoch, parts


def parse_auth_key_ttl_expiry(url: str) -> datetime | None:
    parsed = parse_auth_key_parts(url)
    if not parsed:
        return None
    epoch, parts = parsed
    ttl_minutes = int_or_none(parts[1]) if len(parts) > 1 else None
    if ttl_minutes is None or ttl_minutes <= 0 or ttl_minutes > 24 * 60:
        return None
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc) + timedelta(minutes=ttl_minutes)
    except (OSError, ValueError):
        return None


def video_url_expires_at(urls: list[str]) -> datetime:
    ttl_expiries = [expiry for expiry in (parse_auth_key_ttl_expiry(url) for url in urls) if expiry]
    if ttl_expiries:
        return min(ttl_expiries)
    timestamp_expiries = []
    for url in urls:
        parsed = parse_auth_key_parts(url)
        if not parsed:
            continue
        epoch, _parts = parsed
        try:
            timestamp_expiries.append(datetime.fromtimestamp(epoch, tz=timezone.utc))
        except (OSError, ValueError):
            continue
    return min(timestamp_expiries) if timestamp_expiries else PORNA91_STABLE_VIDEO_URL_EXPIRES_AT


def expected_duration_floor(expected_duration: float | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(15.0, expected_duration * 0.6)
    return float(PORNA91_MIN_PLAYLIST_DURATION_SECONDS)


def fetch_hls_key(key_url: str, referer: str) -> bytes:
    reject_ad_url(key_url, "key")
    key = read_hls_chunk(key_url, referer, 64)
    if len(key) != 16:
        raise ValueError("91porna HLS AES key is not 16 bytes.")
    return key


def decrypt_aes128_ts_chunk(chunk: bytes, key: bytes, iv_hex: str | None, sequence: int = 0) -> bytes:
    if len(chunk) < 16:
        return b""
    if iv_hex:
        iv = bytes.fromhex(iv_hex[2:] if iv_hex.lower().startswith("0x") else iv_hex)
    else:
        iv = sequence.to_bytes(16, "big")
    usable_size = len(chunk) - (len(chunk) % AES.block_size)
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.decrypt(chunk[:usable_size])


def has_ts_sync(chunk: bytes) -> bool:
    if len(chunk) < 188:
        return False
    if chunk[0] != 0x47:
        return False
    return len(chunk) < 376 or chunk[188] == 0x47


def verify_hls_url(video_url: str, referer: str, expected_duration: float | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("91porna video URL must be an HLS .m3u8 URL.")

    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("91porna HLS playlist is not playable media.")

    media_urls = playlist_media_urls(video_url, playlist)
    if not media_urls:
        raise ValueError("91porna HLS playlist has no media segments.")
    key_lines = parse_hls_key_lines(video_url, playlist)
    key_urls = [str(key["uri"]) for key in key_lines if key.get("uri")]

    expiry_urls = [video_url, *key_urls, *media_urls[:6]]
    expires_at = video_url_expires_at(expiry_urls)
    if expires_at != PORNA91_STABLE_VIDEO_URL_EXPIRES_AT and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("91porna HLS URL is already expired or too close to expiry.")

    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("91porna HLS playlist is too short for the video metadata.")

    checked_segment = False
    active_key = key_lines[0] if key_lines else None
    key_bytes = fetch_hls_key(str(active_key["uri"]), referer) if active_key and active_key.get("method") == "AES-128" else None
    for index, media_url in enumerate(media_urls[:6]):
        reject_ad_url(media_url, "segment")
        chunk = read_hls_chunk(media_url, referer, 752)
        if key_bytes:
            decoded = decrypt_aes128_ts_chunk(chunk, key_bytes, str(active_key.get("iv") or ""), sequence=index)
            if has_ts_sync(decoded):
                checked_segment = True
                break
        elif has_ts_sync(chunk):
            checked_segment = True
            break
    if not checked_segment:
        raise ValueError("91porna HLS playlist has no readable MPEG-TS segment.")

    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at,
        "playback_refresh_required": expires_at != PORNA91_STABLE_VIDEO_URL_EXPIRES_AT,
        "playlist_bytes": len(playlist.encode("utf-8")),
        "playlist_duration_seconds": total_duration,
        "media_url_count": len(media_urls),
        "key_url_count": len(key_urls),
    }


def resolve_detail_player(base_url: str, detail_page_url: str, detail_html: str) -> str:
    detail_play_url = build_detail_play_url(base_url, detail_html)
    player_script = fetch_html(detail_play_url, detail_page_url)
    unpacked_player = unpack_packed_js(player_script)
    return extract_player_hls_url(unpacked_player)


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    parsed = urlparse(detail_page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = fetch_html(detail_page_url, (list_item or {}).get("source_url") or detail_page_url)
    soup = BeautifulSoup(html, "html.parser")
    video_object = find_json_ld(iter_json_ld(soup), "VideoObject") or {}
    video_id = detail_id_from_url(detail_page_url) or non_empty((list_item or {}).get("video_id"))
    if not video_id:
        raise ValueError("91porna detail page is missing video_key.")

    video_url = resolve_detail_player(base_url, detail_page_url, html)
    title = non_empty(video_object.get("name")) or non_empty((list_item or {}).get("title")) or PORNA91_SITE_NAME
    description = non_empty(video_object.get("description")) or title
    thumbnail = video_object.get("thumbnailUrl")
    if isinstance(thumbnail, list):
        thumbnail = next((non_empty(value) for value in thumbnail), None)
    image = normalize_asset_url(detail_page_url, non_empty(thumbnail) or non_empty((list_item or {}).get("image")))
    author = video_object.get("author") if isinstance(video_object.get("author"), dict) else {}
    keywords = video_object.get("keywords")
    tags = []
    if isinstance(keywords, list):
        tags = [tag for tag in (non_empty(value) for value in keywords) if tag]
    elif isinstance(keywords, str):
        tags = [tag.strip() for tag in re.split(r"[,，]", keywords) if tag.strip()]
    published_at = (
        parse_datetime(non_empty(video_object.get("datePublished")))
        or parse_datetime(non_empty(video_object.get("uploadDate")))
        or (list_item or {}).get("published_at")
        or now_utc()
    )
    player = {
        "guid": f"{PORNA91_SOURCE}:{video_id}",
        "video_id": video_id,
        "player_index": 1,
        "video_title": title,
        "video_url": video_url,
        "video_type": "hls",
    }
    return {
        "url": detail_url(base_url, video_id),
        "video_id": video_id,
        "title": title,
        "description": description,
        "image": image,
        "tags": tags,
        "author_name": non_empty(author.get("name")) if isinstance(author, dict) else None,
        "author_url": normalize_asset_url(base_url, non_empty(author.get("url"))) if isinstance(author, dict) else None,
        "duration": parse_iso_duration_seconds(non_empty(video_object.get("duration"))),
        "published_at": published_at,
        "modified_at": parse_datetime(non_empty(video_object.get("dateModified"))),
        "players": [player],
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_porna91_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (PORNA91_SOURCE, PORNA91_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([PORNA91_SITE_NAME, "漫画", "视频"]), public_pool),
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
        "display_author": PORNA91_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": PORNA91_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": PORNA91_KIND,
        "target_value": target_row["value"],
        "site_name": PORNA91_SITE_NAME,
        "source_url": detail["url"],
        "porna91_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "author_name": detail.get("author_name"),
        "author_url": detail.get("author_url"),
        "tags": detail.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "91porna-packed-detail-play",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
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
                PORNA91_SITE_NAME,
                PORNA91_SITE_NAME,
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
    category = category_from_target(base_url)
    base_url = normalize_porna91_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page, category)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[91porna] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page, category)}")
        if not list_items:
            print(f"[91porna] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), list_item["guid"]):
                skipped_existing += 1
                page_existing += 1
                continue
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[91porna] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[91porna] skip unverified {player['guid']}: {exc}")
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
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                            "media_url_count": verified.get("media_url_count"),
                            "key_url_count": verified.get("key_url_count"),
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
            f"[91porna] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (PORNA91_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (PORNA91_SOURCE, refresh_window_minutes, limit)),
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
            video_id = metadata.get("porna91_video_id") or str(row["guid"]).replace(f"{PORNA91_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or PORNA91_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or porna91_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "91porna-packed-detail-play",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "porna91_video_id": detail["video_id"],
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "duration": detail.get("duration") or metadata.get("duration"),
                    "author_name": detail.get("author_name") or metadata.get("author_name"),
                    "author_url": detail.get("author_url") or metadata.get("author_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                }
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[91porna] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
