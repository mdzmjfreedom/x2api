from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb


BAOLIAO51_SITE_NAME = "51爆料网"
BAOLIAO51_SOURCE = "baoliao51"
BAOLIAO51_KIND = "site"
BAOLIAO51_DEFAULT_BASE_URL = os.environ.get("BAOLIAO51_BASE_URL", "https://www.51baoliao01.com/category/jrbl/").strip() or "https://www.51baoliao01.com/category/jrbl/"
BAOLIAO51_RETENTION_HOURS = int(os.environ.get("BAOLIAO51_RETENTION_HOURS", "84"))
BAOLIAO51_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("BAOLIAO51_REQUEST_TIMEOUT_SECONDS", "20"))
BAOLIAO51_REFRESH_WINDOW_MINUTES = int(os.environ.get("BAOLIAO51_REFRESH_WINDOW_MINUTES", "90"))
BAOLIAO51_CRITICAL_WINDOW_MINUTES = int(os.environ.get("BAOLIAO51_CRITICAL_WINDOW_MINUTES", "15"))
BAOLIAO51_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire")
EXPECTED_PLAYBACK_HOSTS = {
    "hls.chxgdn.cn",
    "tts.doudou520.online",
    "dx.oviluf.cn",
    "ts.syjiaotong.mobi",
    "ts.liheiat.xyz",
}
EXPECTED_PLAYBACK_HOST_SUFFIXES = (
    ".chxgdn.cn",
    ".doudou520.online",
    ".oviluf.cn",
    ".syjiaotong.mobi",
    ".liheiat.xyz",
)
AD_HOST_KEYWORDS = (
    "adnxs",
    "adservice",
    "adsterra",
    "adtng",
    "clickadu",
    "doubleclick",
    "exoclick",
    "magsrv",
    "myedua.cn",
    "popads",
    "realsrv",
    "tsyndicate",
)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def clean_text(value: str | None, *, reject_seo: bool = True) -> str:
    text = html_unescape(str(value or "")).strip()
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if reject_seo:
        for marker in ("官方交流群", "点击加入", "获取最新网址", "最新地址", "永久地址"):
            index = text.find(marker)
            if index >= 0:
                text = text[:index].strip()
    return text


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
    raw = clean_text(value, reject_seo=False)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_chinese_date(value: str | None, default_time: str = "00:00:00") -> datetime | None:
    if not value:
        return None
    match = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", value)
    if not match:
        return parse_datetime(value)
    year, month, day = (int(part) for part in match.groups())
    hour, minute, second = (int(part) for part in default_time.split(":"))
    return datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = clean_text(value, reject_seo=False)
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url, raw.replace("\\/", "/"))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_baoliao51_target_value(raw: str) -> str:
    value = (raw or BAOLIAO51_DEFAULT_BASE_URL).strip()
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("51baoliao target must be a URL or host.")
    path = parsed.path or "/category/jrbl/"
    if path == "/":
        path = "/category/jrbl/"
    path = "/" + path.strip("/") + "/"
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), path, "", "", ""))


def is_baoliao51_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"51baoliao01.com", "www.51baoliao01.com"}


def format_target_row(target_row: dict) -> str:
    return f"baoliao51:{target_row['value']}"


def origin_header(url: str | None) -> str | None:
    raw = clean_text(url, reject_seo=False)
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def playback_headers(referer: str | None) -> dict[str, str]:
    raw_referer = clean_text(referer, reject_seo=False)
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
                timeout=BAOLIAO51_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("51baoliao request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    response = request_with_proxy_fallback(url, referer=referer)
    return response.content.decode(response.encoding or "utf-8", "replace")


def extract_json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.get_text("", strip=True))
        except json.JSONDecodeError:
            continue
        candidates = []
        if isinstance(payload, dict):
            graph = payload.get("@graph")
            candidates = graph if isinstance(graph, list) else [payload]
        elif isinstance(payload, list):
            candidates = payload
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") in {"BlogPosting", "Article", "NewsArticle"}:
                return candidate
    return {}


def extract_page_id(url: str) -> str:
    match = re.search(r"/archives/(\d+)/?", urlparse(url).path)
    return match.group(1) if match else hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def extract_meta_description(soup: BeautifulSoup, entity: dict) -> str:
    for selector in ('meta[name="description"]', 'meta[property="og:description"]'):
        meta = soup.select_one(selector)
        content = meta.get("content") if meta else None
        if isinstance(content, str) and content.strip():
            return clean_text(content)
    description = entity.get("description") if isinstance(entity.get("description"), str) else ""
    return clean_text(description)


def extract_post_body(soup: BeautifulSoup) -> str:
    content_scope = soup.select_one(".post-content") or soup.select_one("article.post")
    if not content_scope:
        return ""
    for removable in content_scope.select("script,style,iframe,ins,div.dplayer"):
        removable.decompose()
    return clean_text(content_scope.get_text(" ", strip=True))


def extract_image(soup: BeautifulSoup, detail_url: str, entity: dict) -> str | None:
    image_value = entity.get("image")
    if isinstance(image_value, dict):
        image_value = image_value.get("url")
    if isinstance(image_value, list):
        image_value = next((item.get("url") if isinstance(item, dict) else item for item in image_value if item), None)
    image = normalize_asset_url(detail_url, image_value if isinstance(image_value, str) else None)
    if image:
        return image
    meta = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
    return normalize_asset_url(detail_url, meta.get("content") if meta else None)


def parse_list_page(base_url: str, page_url: str) -> tuple[list[dict], str | None]:
    html = fetch_html(page_url, base_url)
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        link = article.select_one('a[href*="/archives/"]')
        heading = article.select_one(".post-card-title, h2, h1, h3")
        if not link or not link.get("href") or not heading:
            continue
        rel = " ".join(link.get("rel") or []).lower()
        if "sponsored" in rel or article.select_one(".post-card-ads"):
            continue
        detail_url = normalize_asset_url(page_url, link["href"])
        page_id = extract_page_id(detail_url or "")
        if not detail_url or not re.search(r"/archives/\d+/?$", urlparse(detail_url).path) or page_id in seen:
            continue
        seen.add(page_id)
        text = article.get_text(" ", strip=True)
        items.append(
            {
                "url": detail_url,
                "page_id": page_id,
                "title": clean_text(heading.get_text(" ", strip=True)),
                "published_at": parse_chinese_date(text),
                "raw_meta": text[:500],
            }
        )
    next_url = None
    for selector in ('link[rel="next"]', 'a[rel="next"]', 'a.next', '.page-navigator a.next'):
        next_link = soup.select_one(selector)
        href = next_link.get("href") if next_link else None
        if href:
            next_url = urljoin(page_url, href)
            break
    if not next_url:
        for link in soup.find_all("a", href=True):
            label = link.get_text(" ", strip=True).lower()
            if label in {"下一页", "next", "›", "»"} or "下一页" in label:
                next_url = urljoin(page_url, link["href"])
                break
    if next_url:
        parsed_base = urlparse(base_url)
        parsed_next = urlparse(next_url)
        if parsed_next.netloc and parsed_next.netloc.lower() != parsed_base.netloc.lower():
            next_url = None
    return items, next_url


def player_ad_urls(config: dict, detail_url: str) -> set[str]:
    urls: set[str] = set()
    for key in ("video_ads_url", "video_ads_url_h", "backend_video_ads_url", "backend_video_ads_url_h"):
        value = normalize_asset_url(detail_url, config.get(key))
        if value:
            urls.add(value)
    ads = config.get("video_player_ads")
    if isinstance(ads, list):
        for ad in ads:
            if isinstance(ad, dict):
                value = normalize_asset_url(detail_url, ad.get("src"))
                if value:
                    urls.add(value)
    return urls


def parse_detail_page(detail_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_url, (list_item or {}).get("url") or BAOLIAO51_DEFAULT_BASE_URL)
    soup = BeautifulSoup(html, "html.parser")
    entity = extract_json_ld(soup)
    title_el = soup.select_one("h1.post-title") or soup.find("h1")
    title = clean_text(title_el.get_text(" ", strip=True) if title_el else None) or (list_item or {}).get("title") or "51爆料视频"
    published_at = parse_datetime(entity.get("datePublished")) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_datetime(entity.get("dateModified"))
    description = extract_meta_description(soup, entity) or extract_post_body(soup)
    image = extract_image(soup, detail_url, entity)
    page_id = (list_item or {}).get("page_id") or extract_page_id(detail_url)
    content_scope = soup.select_one("article.post") or soup
    players = []
    seen_urls: set[str] = set()
    for index, player in enumerate(content_scope.select("div.dplayer[data-config]"), start=1):
        try:
            config = json.loads(player["data-config"])
        except (KeyError, json.JSONDecodeError):
            continue
        video_config = config.get("video") if isinstance(config, dict) else None
        video_url = normalize_asset_url(detail_url, video_config.get("url") if isinstance(video_config, dict) else None)
        video_type = clean_text(video_config.get("type") if isinstance(video_config, dict) else None, reject_seo=False).lower()
        if not video_url or video_url in seen_urls or video_url in player_ad_urls(config, detail_url):
            continue
        path = urlparse(video_url).path.lower()
        if video_type == "hls" or path.endswith(".m3u8"):
            normalized_type = "hls"
        elif path.endswith(DIRECT_VIDEO_EXTENSIONS):
            normalized_type = "direct"
        else:
            continue
        seen_urls.add(video_url)
        video_id = (player.get("data-video_id") or f"{page_id}{index:03d}").strip()
        video_title = (player.get("data-video_title") or f"{title}{index:03d}").strip()
        tags = [tag.strip() for tag in (player.get("data-video_tag_name") or "").split(",") if tag.strip()]
        players.append(
            {
                "guid": f"{BAOLIAO51_SOURCE}:{page_id}:{video_id}",
                "page_id": page_id,
                "player_index": index,
                "video_id": video_id,
                "video_title": video_title,
                "video_url": video_url,
                "video_type": normalized_type,
                "referer": detail_url,
                "tags": tags,
            }
        )
    return {
        "url": detail_url,
        "page_id": page_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": published_at,
        "modified_at": modified_at,
        "players": players,
    }


def parse_query_expiry(video_url: str) -> datetime | None:
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        parsed = parse_epoch_datetime((query.get(key) or [None])[0])
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    # The site's auth_key prefix mirrors a generation/token value that can be
    # already in the past while the playlist and segments still play, so it is
    # not reliable enough to schedule refreshes.
    return None


def playback_expiry(urls: list[str]) -> datetime | None:
    expiries = [expiry for expiry in (parse_query_expiry(url) for url in urls) if expiry]
    return min(expiries) if expiries else None


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"51baoliao {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"51baoliao {label} URL points to an ad host: {host}")
    if label == "playback" and host not in EXPECTED_PLAYBACK_HOSTS and not any(host.endswith(suffix) for suffix in EXPECTED_PLAYBACK_HOST_SUFFIXES):
        raise ValueError(f"51baoliao playback URL is outside expected media hosts: {host}")


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


def playlist_segments(video_url: str, playlist: str) -> list[str]:
    segments: list[str] = []
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
            previous_stream_inf = False
            continue
        segments.append(urljoin(video_url, value))
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


def verify_media_hls_url(video_url: str, referer: str | None) -> dict:
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("51baoliao HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < 5:
        raise ValueError("51baoliao HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("51baoliao HLS playlist has no media segments.")
    key_urls = playlist_key_urls(video_url, playlist)
    for key_url in key_urls[:3]:
        chunk, _response = read_media_chunk(key_url, referer, 16)
        if len(chunk) != 16:
            raise ValueError("51baoliao HLS AES key is not 16 bytes.")
    segment_error: Exception | None = None
    for segment_url in segments[:8]:
        try:
            chunk, response = read_media_chunk(segment_url, referer, 4096)
            content_type = (response.headers.get("Content-Type") or "").lower()
            if chunk and "text/html" not in content_type and (key_urls or looks_like_media_chunk(chunk) or "video/" in content_type or "octet-stream" in content_type):
                expires_at = playback_expiry([video_url, *segments[:3], *key_urls[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("51baoliao HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "playback_headers": playback_headers(referer),
                    "video_url_expires_at": expires_at or BAOLIAO51_STABLE_VIDEO_URL_EXPIRES_AT,
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
    raise ValueError(f"51baoliao HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, referer: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("51baoliao HLS URL must be an .m3u8 URL.")
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist:
        raise ValueError("51baoliao HLS URL is not a playlist.")
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
        raise ValueError(f"51baoliao HLS master playlist has no playable variants: {last_error}")
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
        raise ValueError("51baoliao direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("51baoliao direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, referer, 4096)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type or "image/" in content_type:
        raise ValueError("51baoliao direct video URL did not return media bytes.")
    if not looks_like_media_chunk(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("51baoliao direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or BAOLIAO51_STABLE_VIDEO_URL_EXPIRES_AT,
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
    value = normalize_baoliao51_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (BAOLIAO51_SOURCE, BAOLIAO51_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([BAOLIAO51_SITE_NAME, "爆料", "视频"]), public_pool),
        )
    return target_row


def item_exists_for_guid(conn, target_id: str, guid: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone() is not None


def existing_item_for_guid(conn, target_id: str, guid: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute("SELECT id, video_url_expires_at, metadata FROM items WHERE target_id = %s AND guid = %s LIMIT 1", (target_id, guid))
        return cur.fetchone()


def existing_item_needs_playback_update(row: dict | None) -> bool:
    if not row:
        return False
    metadata = row.get("metadata") or {}
    if not isinstance(metadata.get("playback_headers"), dict) or not metadata.get("playback_headers"):
        return True
    if metadata.get("playback_refresh_required") is not False:
        return True
    expires_at = row.get("video_url_expires_at")
    if isinstance(expires_at, datetime):
        expires_at = expires_at.astimezone(timezone.utc) if expires_at.tzinfo else expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= now_utc() + timedelta(minutes=10)
    return True


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
        "display_author": BAOLIAO51_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": "51爆料",
    }


def upsert_baoliao51_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": BAOLIAO51_KIND,
        "target_value": target_row["value"],
        "site_name": BAOLIAO51_SITE_NAME,
        "source_url": detail["url"],
        "page_id": detail["page_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "baoliao51_video_id": player["video_id"],
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "variant_url": verified.get("variant_url"),
        "tags": player.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "baoliao51-dplayer",
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
                BAOLIAO51_SITE_NAME,
                BAOLIAO51_SITE_NAME,
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                player.get("video_title") or detail.get("title"),
                content,
                detail.get("title"),
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
    base_url = normalize_baoliao51_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    page_url = base_url
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for _ in range(max_pages):
        pages += 1
        try:
            list_items, next_url = parse_list_page(base_url, page_url)
        except Exception as exc:
            if target_row:
                upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=str(exc), success=False)
            raise
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[51baoliao] page={pages} list_items={len(list_items)} url={page_url}")
        if not list_items:
            print(f"[51baoliao] page={pages} empty_list stop=true")
            break
        for list_item in list_items:
            if list_item.get("published_at") and list_item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[51baoliao] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                latest_guid = latest_guid or player["guid"]
                existing_item = existing_item_for_guid(conn, str(target_row["id"]), player["guid"]) if target_row else None
                if existing_item and not existing_item_needs_playback_update(existing_item):
                    skipped_existing += 1
                    page_existing += 1
                    continue
                try:
                    verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[51baoliao] skip unverified {player['guid']}: {exc}")
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
                            "playback_headers": verified.get("playback_headers"),
                            "media_format": verified.get("media_format"),
                        }
                    )
                    continue
                if upsert_baoliao51_video_item(conn, target_row, detail, player, verified, retention_hours):
                    inserted += 1
                    page_inserted += 1
                else:
                    updated += 1
                    page_updated += 1
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[51baoliao] page={pages} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if not next_url:
            break
        page_url = next_url
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
