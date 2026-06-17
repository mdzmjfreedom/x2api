from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

try:
    from collector.redis_state import is_in_cooldown, mark_item_seen, set_cooldown
except ModuleNotFoundError:
    from redis_state import is_in_cooldown, mark_item_seen, set_cooldown


DIRTYSHIP_SITE_NAME = "DirtyShip"
DIRTYSHIP_SOURCE = "dirtyship"
DIRTYSHIP_KIND = "site"
DIRTYSHIP_DEFAULT_BASE_URL = os.environ.get("DIRTYSHIP_BASE_URL", "https://dirtyship.com").strip().rstrip("/") or "https://dirtyship.com"
DIRTYSHIP_RETENTION_HOURS = int(os.environ.get("DIRTYSHIP_RETENTION_HOURS", "168"))
DIRTYSHIP_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("DIRTYSHIP_REQUEST_TIMEOUT_SECONDS", "20"))
DIRTYSHIP_REFRESH_WINDOW_MINUTES = int(os.environ.get("DIRTYSHIP_REFRESH_WINDOW_MINUTES", "90"))
DIRTYSHIP_CRITICAL_WINDOW_MINUTES = int(os.environ.get("DIRTYSHIP_CRITICAL_WINDOW_MINUTES", "15"))
DIRTYSHIP_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire", "t")
AD_HOST_KEYWORDS = (
    "adnxs",
    "adservice",
    "adsterra",
    "adtng",
    "clickadu",
    "doubleclick",
    "eunow4u",
    "exacdn",
    "exoclick",
    "histats",
    "magsrv",
    "popads",
    "realsrv",
    "tsyndicate",
)


class TemporarySourceAccessError(RuntimeError):
    pass


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


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_dirtyship_target_value(raw: str) -> str:
    value = (raw or DIRTYSHIP_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("DirtyShip target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_dirtyship_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"dirtyship.com", "www.dirtyship.com"}


def format_target_row(target_row: dict) -> str:
    return f"dirtyship:{target_row['value']}"


def origin_header(url: str | None) -> str | None:
    raw = non_empty(url)
    if not raw:
        return None
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


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
                timeout=DIRTYSHIP_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            if response.status_code in {403, 429}:
                raise TemporarySourceAccessError(f"DirtyShip returned HTTP {response.status_code} for {url}")
            response.raise_for_status()
            return response
        except TemporarySourceAccessError:
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("DirtyShip request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    response = request_with_proxy_fallback(url, referer=referer)
    return response.content.decode(response.encoding or "utf-8", "replace")


def build_list_page_url(base_url: str, page: int) -> str:
    value = normalize_dirtyship_target_value(base_url)
    if page <= 1:
        return value + "/"
    return urljoin(value + "/", f"page/{page}/")


def detail_id_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""
    return slug or re.sub(r"\W+", "-", url).strip("-").lower()


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    return urlunparse(urlparse(urljoin(base_url, html_unescape(raw)))._replace(fragment=""))


def list_item_image(item, page_url: str) -> str | None:
    image = item.select_one("img")
    if not image:
        return None
    for attr in ("data-src", "data-lazy-src", "src"):
        value = normalize_asset_url(page_url, image.get(attr))
        if value:
            return value
    srcset = non_empty(image.get("data-srcset") or image.get("srcset"))
    if srcset:
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        return normalize_asset_url(page_url, first)
    return None


def is_detail_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() not in {"dirtyship.com", "www.dirtyship.com"}:
        return False
    path = parsed.path.strip("/")
    if not path or "/" in path:
        return False
    blocked_prefixes = ("tag", "category", "page", "author", "categories", "actors", "privacy", "dmca", "contact")
    return not any(path.startswith(prefix) for prefix in blocked_prefixes)


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    html = fetch_html(page_url, build_list_page_url(base_url, 1))
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    containers = soup.select("ul.Thumbnail_List li.thumi")
    if not containers:
        containers = soup.select("a#preview_image[href], a[href]")
    for container in containers:
        link_el = container.select_one("a#preview_image[href], a[href]") if hasattr(container, "select_one") else container
        if not link_el:
            continue
        detail_url = normalize_asset_url(page_url, link_el.get("href"))
        if not detail_url or not is_detail_url(detail_url):
            continue
        detail_id = detail_id_from_url(detail_url)
        if not detail_id or detail_id in seen:
            continue
        seen.add(detail_id)
        image = list_item_image(container, page_url) if hasattr(container, "select_one") else None
        title = (
            clean_text(link_el.get("title"))
            or clean_text((container.select_one(".title, h2, h3") if hasattr(container, "select_one") else None).get_text(" ", strip=True) if hasattr(container, "select_one") and container.select_one(".title, h2, h3") else None)
            or clean_text((container.select_one("img") if hasattr(container, "select_one") else None).get("alt") if hasattr(container, "select_one") and container.select_one("img") else None)
            or detail_id.replace("-", " ")
        )
        items.append(
            {
                "guid": f"{DIRTYSHIP_SOURCE}:{detail_id}",
                "detail_id": detail_id,
                "url": detail_url,
                "title": title,
                "image": image,
                "published_at": None,
                "tags": [],
            }
        )
    return items


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


def source_candidates_from_html(html: str, detail_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(raw_url: str | None, label: str | None = None, source: str = "html") -> None:
        video_url = normalize_asset_url(detail_url, raw_url)
        if not video_url or video_url in seen:
            return
        parsed = urlparse(video_url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"}:
            return
        if path.endswith(".m3u8"):
            video_type = "hls"
        elif path.endswith(DIRECT_VIDEO_EXTENSIONS):
            video_type = "direct"
        else:
            return
        seen.add(video_url)
        candidates.append({"video_url": video_url, "video_type": video_type, "label": label, "source": source, "referer": detail_url})

    for source in soup.select("video[src], video source[src], source[src]"):
        add(source.get("src"), clean_text(source.get("title") or source.get("label") or source.get("res")), "video-tag")
    for match in re.findall(r"https?://[^\"'<>\s]+\.(?:m3u8|mp4|webm|mov|m4v)(?:\?[^\"'<>\s]*)?", html, flags=re.I):
        add(match, None, "html-regex")
    for match in re.findall(r"(?:file|source|src)\s*[:=]\s*['\"]([^'\"]+\.(?:m3u8|mp4|webm|mov|m4v)(?:\?[^'\"]*)?)['\"]", html, flags=re.I):
        add(match, None, "player-config")
    return candidates


def tags_from_article(article: dict, soup: BeautifulSoup) -> list[str]:
    tags: list[str] = []
    keywords = article.get("keywords")
    if isinstance(keywords, list):
        tags.extend(cleaned for cleaned in (clean_text(value) for value in keywords) if cleaned)
    elif isinstance(keywords, str):
        tags.extend(tag for tag in (clean_text(value) for value in re.split(r"[,，]", keywords)) if tag)
    section = article.get("articleSection")
    if isinstance(section, list):
        tags.extend(cleaned for cleaned in (clean_text(value) for value in section) if cleaned)
    elif isinstance(section, str):
        tags.append(section)
    for link in soup.select('a[rel="tag"], .tags a, .post-tags a'):
        value = clean_text(link.get_text(" ", strip=True))
        if value:
            tags.append(value)
    seen: set[str] = set()
    result = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= 20:
            break
    return result


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_page_url, build_list_page_url(DIRTYSHIP_DEFAULT_BASE_URL, 1))
    soup = BeautifulSoup(html, "html.parser")
    article = find_json_ld(iter_json_ld(soup), "Article") or {}
    detail_id = detail_id_from_url(detail_page_url)
    if not detail_id:
        raise ValueError("DirtyShip detail page is missing detail id.")
    title = (
        clean_text(article.get("headline"))
        or clean_text((soup.select_one("h1.singletitle, h1") or {}).get_text(" ", strip=True) if soup.select_one("h1.singletitle, h1") else None)
        or clean_text((list_item or {}).get("title"))
        or detail_id.replace("-", " ")
    )
    description = clean_text((soup.select_one('meta[name="description"]') or {}).get("content") if soup.select_one('meta[name="description"]') else None) or title
    image = None
    article_image = article.get("thumbnailUrl") or article.get("image")
    if isinstance(article_image, dict):
        article_image = article_image.get("url")
    image = normalize_asset_url(detail_page_url, non_empty(article_image))
    if not image:
        image_meta = soup.select_one('meta[property="og:image"], meta[name="twitter:image"]')
        image = normalize_asset_url(detail_page_url, image_meta.get("content") if image_meta else None)
    image = image or (list_item or {}).get("image")
    candidates = source_candidates_from_html(html, detail_page_url)
    if not candidates:
        raise ValueError("DirtyShip detail page exposes no direct playable media candidate.")
    published_at = parse_datetime(non_empty(article.get("datePublished"))) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_datetime(non_empty(article.get("dateModified")))
    tags = tags_from_article(article, soup)
    players = []
    for index, candidate in enumerate(candidates, start=1):
        players.append(
            {
                "guid": f"{DIRTYSHIP_SOURCE}:{detail_id}:{index}" if index > 1 else f"{DIRTYSHIP_SOURCE}:{detail_id}",
                "detail_id": detail_id,
                "player_index": index,
                "video_title": title,
                "video_url": candidate["video_url"],
                "video_type": candidate["video_type"],
                "referer": candidate.get("referer") or detail_page_url,
                "label": candidate.get("label"),
                "candidate_source": candidate.get("source"),
            }
        )
    return {
        "guid": f"{DIRTYSHIP_SOURCE}:{detail_id}",
        "detail_id": detail_id,
        "url": detail_page_url,
        "title": title,
        "description": description,
        "image": image,
        "images": [image] if image else [],
        "published_at": published_at,
        "modified_at": modified_at,
        "tags": tags,
        "source_candidate_count": len(candidates),
        "players": players,
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


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"DirtyShip {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"DirtyShip {label} URL points to an ad host: {host}")
    if label == "playback" and not (host == "dirtyship.com" or host.endswith(".dirtyship.com") or host.endswith(".dirtyship.net")):
        raise ValueError(f"DirtyShip playback URL is outside expected media hosts: {host}")


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
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-KEY"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


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
        raise ValueError("DirtyShip HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < 5:
        raise ValueError("DirtyShip HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("DirtyShip HLS playlist has no media segments.")
    key_urls = playlist_key_urls(video_url, playlist)
    for key_url in key_urls[:2]:
        chunk, _response = read_media_chunk(key_url, referer, 16)
        if len(chunk) != 16:
            raise ValueError("DirtyShip HLS AES key is not 16 bytes.")
    segment_error: Exception | None = None
    for segment_url in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment_url, referer, 4096)
            if looks_like_media_chunk(chunk):
                expires_at = playback_expiry([video_url, *segments[:3], *key_urls[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("DirtyShip HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "playback_headers": playback_headers(referer),
                    "video_url_expires_at": expires_at or DIRTYSHIP_STABLE_VIDEO_URL_EXPIRES_AT,
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
    raise ValueError(f"DirtyShip HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, referer: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("DirtyShip HLS URL must be an .m3u8 URL.")
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist:
        raise ValueError("DirtyShip HLS URL is not a playlist.")
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
        raise ValueError(f"DirtyShip HLS master playlist has no playable variants: {last_error}")
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
        raise ValueError("DirtyShip direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("DirtyShip direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, referer, 4096)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206}:
        raise ValueError(f"DirtyShip direct video URL returned unexpected status {response.status_code}.")
    if not chunk or "text/html" in content_type or "image/" in content_type:
        raise ValueError(f"DirtyShip direct video URL did not return media bytes: {content_type}")
    if not looks_like_media_chunk(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError(f"DirtyShip direct video URL did not return recognizable media bytes: {content_type}")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or DIRTYSHIP_STABLE_VIDEO_URL_EXPIRES_AT,
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
    value = normalize_dirtyship_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (DIRTYSHIP_SOURCE, DIRTYSHIP_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([DIRTYSHIP_SITE_NAME, "video"]), public_pool),
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
        "display_author": DIRTYSHIP_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": DIRTYSHIP_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or DIRTYSHIP_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": DIRTYSHIP_KIND,
        "target_value": target_row["value"],
        "site_name": DIRTYSHIP_SITE_NAME,
        "source_url": detail["url"],
        "dirtyship_detail_id": detail["detail_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "variant_url": verified.get("variant_url"),
        "video_poster_url": detail.get("image"),
        "tags": detail.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "dirtyship-html-video",
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
                DIRTYSHIP_SITE_NAME,
                DIRTYSHIP_SITE_NAME,
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
    base_url = normalize_dirtyship_target_value(base_url)
    cooldown = is_in_cooldown(DIRTYSHIP_SOURCE)
    if cooldown:
        print(f"[dirtyship] cooldown active until={cooldown}")
        return {
            "pages": 0,
            "parsed_videos": 0,
            "verified": 0,
            "inserted": 0,
            "updated": 0,
            "skipped_existing": 0,
            "skipped_detail_errors": 0,
            "skipped_unverified": 0,
            "skipped_old": 0,
            "skipped_access_errors": 0,
            "skipped_cooldown": True,
            "samples": [],
        }
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = skipped_access_errors = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        try:
            list_items = parse_list_page(base_url, page)
        except TemporarySourceAccessError as exc:
            skipped_access_errors += 1
            if target_row:
                upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=str(exc), success=False)
            set_cooldown(DIRTYSHIP_SOURCE, str(exc))
            print(f"[dirtyship] skip page={page} access_error={exc}")
            break
        except Exception as exc:
            if target_row:
                upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=str(exc), success=False)
            raise
        page_inserted = page_updated = page_existing = page_old = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[dirtyship] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[dirtyship] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            mark_item_seen(DIRTYSHIP_SOURCE, list_item["guid"])
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
            except TemporarySourceAccessError as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[dirtyship] skip detail {list_item.get('url')}: {exc}")
                continue
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[dirtyship] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[dirtyship] skip unverified {player['guid']}: {exc}")
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
                            "content_type": verified.get("content_type"),
                            "content_length": verified.get("content_length"),
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
            f"[dirtyship] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if page_inserted == 0 and page_old == len(list_items):
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
        "skipped_access_errors": skipped_access_errors,
        "samples": samples[:10],
    }
