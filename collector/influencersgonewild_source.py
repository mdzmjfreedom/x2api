from __future__ import annotations

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


INFLUENCERSGONEWILD_SITE_NAME = "InfluencersGoneWild"
INFLUENCERSGONEWILD_SOURCE = "influencersgonewild"
INFLUENCERSGONEWILD_KIND = "site"
INFLUENCERSGONEWILD_DEFAULT_BASE_URL = os.environ.get("INFLUENCERSGONEWILD_BASE_URL", "https://influencersgonewild.com").strip().rstrip("/") or "https://influencersgonewild.com"
INFLUENCERSGONEWILD_RETENTION_HOURS = int(os.environ.get("INFLUENCERSGONEWILD_RETENTION_HOURS", "168"))
INFLUENCERSGONEWILD_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("INFLUENCERSGONEWILD_REQUEST_TIMEOUT_SECONDS", "12"))
INFLUENCERSGONEWILD_REFRESH_WINDOW_MINUTES = int(os.environ.get("INFLUENCERSGONEWILD_REFRESH_WINDOW_MINUTES", "90"))
INFLUENCERSGONEWILD_CRITICAL_WINDOW_MINUTES = int(os.environ.get("INFLUENCERSGONEWILD_CRITICAL_WINDOW_MINUTES", "15"))
INFLUENCERSGONEWILD_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

DIRECT_VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
EXPIRY_QUERY_KEYS = ("e", "exp", "expires", "expire", "deadline", "token_expire")
AD_HOST_KEYWORDS = (
    "ad",
    "ads",
    "adnxs",
    "adservice",
    "adsterra",
    "adtng",
    "clickadu",
    "doubleclick",
    "eunow4u",
    "exoclick",
    "magsrv",
    "popads",
    "realsrv",
    "traffic",
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
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_influencersgonewild_target_value(raw: str) -> str:
    value = (raw or INFLUENCERSGONEWILD_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("InfluencersGoneWild target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_influencersgonewild_target_url(raw: str) -> bool:
    value = raw.strip()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"influencersgonewild.com", "www.influencersgonewild.com"}


def format_target_row(target_row: dict) -> str:
    return f"influencersgonewild:{target_row['value']}"


def origin_header(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return None


def playback_headers(referer: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    if referer:
        result["Referer"] = referer
        origin = origin_header(referer)
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
                timeout=INFLUENCERSGONEWILD_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            if response.status_code in {403, 429}:
                raise TemporarySourceAccessError(f"InfluencersGoneWild returned HTTP {response.status_code} for {url}")
            response.raise_for_status()
            return response
        except TemporarySourceAccessError:
            raise
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("InfluencersGoneWild request failed.")


def fetch_text(url: str, referer: str | None = None) -> str:
    response = request_with_proxy_fallback(url, referer=referer, accept="text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
    return response.content.decode(response.encoding or "utf-8", "replace")


def build_list_page_url(base_url: str, page: int) -> str:
    value = normalize_influencersgonewild_target_value(base_url)
    if page <= 1:
        return value + "/"
    return urljoin(value + "/", f"page/{page}/")


def detail_id_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""
    return slug or re.sub(r"\W+", "-", url).strip("-").lower()


def list_item_image(article) -> str | None:
    image = article.select_one("img")
    if not image:
        return None
    for attr in ("data-src", "src", "data-lazy-src"):
        value = non_empty(image.get(attr))
        if value and not value.startswith("data:"):
            return value
    srcset = non_empty(image.get("data-srcset") or image.get("srcset"))
    if srcset:
        first = srcset.split(",", 1)[0].strip().split(" ", 1)[0]
        return first or None
    return None


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    html = fetch_text(page_url, base_url)
    soup = BeautifulSoup(html, "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for article in soup.select("article"):
        link_el = article.select_one(".entry-title a[href], h2 a[href], h3 a[href], a.g1-frame[href]")
        if not link_el:
            continue
        detail_url = urljoin(page_url, link_el.get("href"))
        parsed = urlparse(detail_url)
        if parsed.netloc.lower() not in {"influencersgonewild.com", "www.influencersgonewild.com"}:
            continue
        if any(part in parsed.path for part in ("/category/", "/tag/", "/page/")):
            continue
        guid_id = detail_id_from_url(detail_url)
        if not guid_id or guid_id in seen:
            continue
        title_el = article.select_one(".entry-title a, h2 a, h3 a") or link_el
        time_el = article.select_one("time[datetime]")
        categories = [clean_text(a.get_text(" ", strip=True)) for a in article.select(".entry-category")]
        image = list_item_image(article)
        seen.add(guid_id)
        items.append(
            {
                "guid": f"{INFLUENCERSGONEWILD_SOURCE}:{guid_id}",
                "detail_id": guid_id,
                "url": detail_url,
                "title": clean_text(title_el.get_text(" ", strip=True)) or guid_id,
                "image": urljoin(page_url, image) if image else None,
                "published_at": parse_datetime(time_el.get("datetime") if time_el else None),
                "tags": [tag for tag in categories if tag][:12],
            }
        )
    return items


def media_candidates_from_html(html: str, detail_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict] = []
    seen: set[str] = set()

    def add(raw_url: str | None, source: str = "html") -> None:
        value = non_empty(raw_url)
        if not value:
            return
        value = html_unescape(value).replace("\\/", "/")
        url = urljoin(detail_url, value)
        parsed = urlparse(url)
        path = parsed.path.lower()
        if parsed.scheme not in {"http", "https"}:
            return
        if not (path.endswith(".m3u8") or path.endswith(DIRECT_VIDEO_EXTENSIONS)):
            return
        if url in seen:
            return
        seen.add(url)
        candidates.append(
            {
                "video_url": url,
                "video_type": "hls" if path.endswith(".m3u8") else "direct",
                "source": source,
            }
        )

    for element in soup.select("video[src], video source[src], source[src]"):
        add(element.get("src"), "video-tag")
    for match in re.findall(r"https?://[^\"'<>\s]+\.(?:m3u8|mp4|webm|mov|m4v)(?:\?[^\"'<>\s]*)?", html, flags=re.I):
        add(match, "html-regex")
    for match in re.findall(r"['\"]?(?:file|source|src)['\"]?\s*[:=]\s*['\"]([^'\"]+\.(?:m3u8|mp4|webm|mov|m4v)(?:\?[^'\"]*)?)['\"]", html, flags=re.I):
        add(match, "player-config")
    return candidates


def detail_images_from_html(html: str, fallback_image: str | None = None) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    images: list[str] = []
    for selector in ('meta[property="og:image"]', 'meta[name="twitter:image"]'):
        meta = soup.select_one(selector)
        value = non_empty(meta.get("content") if meta else None)
        if value and value not in images:
            images.append(value)
    if fallback_image and fallback_image not in images:
        images.append(fallback_image)
    return images[:3]


def parse_detail(list_item: dict) -> dict:
    detail_url = list_item["url"]
    html = fetch_text(detail_url, INFLUENCERSGONEWILD_DEFAULT_BASE_URL + "/")
    soup = BeautifulSoup(html, "html.parser")
    title = clean_text((soup.select_one("h1.entry-title") or soup.select_one("meta[property='og:title']") or {}).get_text(" ", strip=True) if soup.select_one("h1.entry-title") else None)
    if not title:
        meta_title = soup.select_one("meta[property='og:title'], meta[name='twitter:title']")
        title = clean_text(meta_title.get("content") if meta_title else None)
    title = title or list_item["title"]
    published_at = None
    published_meta = soup.select_one("meta[property='article:published_time']")
    if published_meta:
        published_at = parse_datetime(published_meta.get("content"))
    if not published_at:
        time_el = soup.select_one("time[datetime]")
        published_at = parse_datetime(time_el.get("datetime") if time_el else None)
    tags = [clean_text(meta.get("content")) for meta in soup.select("meta[property='article:tag']")]
    tags = [tag for tag in tags if tag]
    if INFLUENCERSGONEWILD_SITE_NAME not in tags:
        tags.append(INFLUENCERSGONEWILD_SITE_NAME)
    candidates = media_candidates_from_html(html, detail_url)
    if not candidates:
        raise ValueError("InfluencersGoneWild detail page has no media candidates.")
    detail_id = list_item["detail_id"]
    images = detail_images_from_html(html, list_item.get("image"))
    return {
        "guid": f"{INFLUENCERSGONEWILD_SOURCE}:{detail_id}",
        "detail_id": detail_id,
        "url": detail_url,
        "title": title,
        "description": title,
        "image": images[0] if images else None,
        "images": images,
        "published_at": published_at or list_item.get("published_at") or now_utc(),
        "modified_at": parse_datetime((soup.select_one("meta[property='article:modified_time']") or {}).get("content") if soup.select_one("meta[property='article:modified_time']") else None),
        "tags": tags[:12],
        "players": [
            {
                "guid": f"{INFLUENCERSGONEWILD_SOURCE}:{detail_id}",
                "video_title": title,
                "video_url": candidates[0]["video_url"],
                "video_url_candidates": candidates,
                "video_type": candidates[0]["video_type"],
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


def reject_ad_url(url: str, label: str = "playback") -> None:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(f"InfluencersGoneWild {label} URL must be http(s).")
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"InfluencersGoneWild {label} URL points to an ad host: {host}")
    if label == "playback" and not (host == "influencersgonewild.com" or host.endswith(".influencersgonewild.com") or host.endswith(".influencersgonewild.net")):
        raise ValueError(f"InfluencersGoneWild playback URL is outside expected media hosts: {host}")


def fetch_hls_text(url: str, page_url: str | None) -> str:
    reject_ad_url(url)
    response = request_with_proxy_fallback(url, referer=page_url, accept="application/vnd.apple.mpegurl,application/x-mpegURL,text/plain,*/*")
    return response.content.decode("utf-8-sig", "replace")


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


def variant_playlists(video_url: str, playlist: str) -> list[str]:
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
    seen: set[str] = set()
    for line in playlist.splitlines():
        value = line.strip()
        if not value.startswith("#EXT-X-KEY"):
            continue
        attrs = parse_hls_attribute_list(value)
        uri = attrs.get("URI")
        if uri:
            key_url = urljoin(video_url, uri)
            if key_url not in seen:
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


def verify_media_hls_url(video_url: str, page_url: str | None) -> dict:
    playlist = fetch_hls_text(video_url, page_url)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("InfluencersGoneWild HLS URL is not a playable media playlist.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < 5:
        raise ValueError("InfluencersGoneWild HLS playlist is too short for a real video.")
    segments = playlist_segments(video_url, playlist)
    if not segments:
        raise ValueError("InfluencersGoneWild HLS playlist has no media segments.")
    for key_url in playlist_key_urls(video_url, playlist)[:3]:
        chunk, _response = read_media_chunk(key_url, page_url, 16)
        if len(chunk) != 16:
            raise ValueError("InfluencersGoneWild HLS AES key is not 16 bytes.")
    segment_error: Exception | None = None
    for segment_url in segments[:8]:
        try:
            chunk, _response = read_media_chunk(segment_url, page_url, 4096)
            if looks_like_media_segment(chunk):
                expires_at = playback_expiry([video_url, *segments[:3], *playlist_key_urls(video_url, playlist)[:1]])
                if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
                    raise ValueError("InfluencersGoneWild HLS URL is already expired or too close to expiry.")
                return {
                    "video_url": video_url,
                    "raw_video_url": video_url,
                    "playback_headers": playback_headers(page_url),
                    "video_url_expires_at": expires_at or INFLUENCERSGONEWILD_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_duration_seconds": total_duration,
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "media_url_count": len(segments),
                    "key_url_count": len(playlist_key_urls(video_url, playlist)),
                    "encrypted": bool(playlist_key_urls(video_url, playlist)),
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"InfluencersGoneWild HLS playlist has no readable media segment: {segment_error}")


def verify_hls_url(video_url: str, page_url: str | None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("InfluencersGoneWild HLS URL must be an .m3u8 URL.")
    playlist = fetch_hls_text(video_url, page_url)
    if "#EXTM3U" not in playlist:
        raise ValueError("InfluencersGoneWild HLS URL is not a playlist.")
    variants = variant_playlists(video_url, playlist)
    if variants:
        last_error: Exception | None = None
        for variant_url in variants:
            try:
                verified = verify_media_hls_url(variant_url, page_url)
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
        raise ValueError(f"InfluencersGoneWild HLS master playlist has no playable variants: {last_error}")
    return verify_media_hls_url(video_url, page_url)


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
        raise ValueError("InfluencersGoneWild direct video URL must be a supported video file.")
    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("InfluencersGoneWild direct video URL is already expired or too close to expiry.")
    chunk, response = read_media_chunk(video_url, page_url, 4096)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206} or not chunk or "text/html" in content_type:
        raise ValueError("InfluencersGoneWild direct video URL did not return media bytes.")
    if not looks_like_media_segment(chunk) and not content_type.startswith("video/") and "octet-stream" not in content_type:
        raise ValueError("InfluencersGoneWild direct video URL did not return recognizable media bytes.")
    return {
        "video_url": video_url,
        "raw_video_url": video_url,
        "playback_headers": playback_headers(page_url),
        "video_url_expires_at": expires_at or INFLUENCERSGONEWILD_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "direct",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_player_playback(player: dict, page_url: str | None) -> dict:
    errors = []
    for candidate in player.get("video_url_candidates") or [{"video_url": player["video_url"], "video_type": player.get("video_type")}]:
        video_url = candidate["video_url"] if isinstance(candidate, dict) else str(candidate)
        try:
            if urlparse(video_url).path.lower().endswith(".m3u8"):
                return verify_hls_url(video_url, page_url)
            return verify_direct_video_url(video_url, page_url)
        except Exception as exc:
            errors.append(f"{video_url}: {exc}")
    raise ValueError("; ".join(errors) or "InfluencersGoneWild playback candidates are empty.")


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_influencersgonewild_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (INFLUENCERSGONEWILD_SOURCE, INFLUENCERSGONEWILD_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([INFLUENCERSGONEWILD_SITE_NAME, "video"]), public_pool),
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
    images = item.get("images") or ([item["image"]] if item.get("image") else [])
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
        "display_author": INFLUENCERSGONEWILD_SITE_NAME,
        "display_handle": None,
        "author_profile_url": detail.get("url"),
        "author_profile_platform": INFLUENCERSGONEWILD_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title") or INFLUENCERSGONEWILD_SITE_NAME
    images = detail.get("images") or ([detail["image"]] if detail.get("image") else [])
    presentation = build_author_presentation(detail)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": INFLUENCERSGONEWILD_KIND,
        "target_value": target_row["value"],
        "site_name": INFLUENCERSGONEWILD_SITE_NAME,
        "source_url": detail["url"],
        "influencersgonewild_detail_id": detail["detail_id"],
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "raw_video_url": verified.get("raw_video_url"),
        "variant_url": verified.get("variant_url"),
        "video_poster_url": detail.get("image"),
        "tags": detail.get("tags") or [],
        "resolver": "influencersgonewild-html-video",
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
                INFLUENCERSGONEWILD_SITE_NAME,
                INFLUENCERSGONEWILD_SITE_NAME,
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


def monitor_site(
    conn,
    *,
    base_url: str,
    max_pages: int,
    retention_hours: int,
    public_pool: bool,
    dry_run: bool = False,
) -> dict:
    cooldown = is_in_cooldown(INFLUENCERSGONEWILD_SOURCE)
    if cooldown:
        print(f"[influencersgonewild] cooldown active until={cooldown}")
        return {
            "pages": 0,
            "parsed_videos": 0,
            "verified": 0,
            "inserted": 0,
            "updated": 0,
            "text_refreshed": 0,
            "skipped_existing": 0,
            "skipped_unverified": 0,
            "skipped_old": 0,
            "skipped_access_errors": 0,
            "skipped_cooldown": True,
            "samples": [],
        }
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_unverified = skipped_old = pages = text_refreshed = skipped_access_errors = 0
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
            set_cooldown(INFLUENCERSGONEWILD_SOURCE, str(exc))
            print(f"[influencersgonewild] skip page={page} access_error={exc}")
            break
        except Exception as exc:
            if target_row:
                upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=str(exc), success=False)
            raise
        page_inserted = page_updated = page_existing = page_text_refreshed = page_old = page_verified = page_unverified = page_parsed_videos = 0
        print(f"[influencersgonewild] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[influencersgonewild] page={page} empty_list stop=true")
            break
        for list_item in list_items:
            latest_guid = latest_guid or list_item["guid"]
            mark_item_seen(INFLUENCERSGONEWILD_SOURCE, list_item["guid"])
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
                detail = parse_detail(list_item)
            except TemporarySourceAccessError as exc:
                skipped_unverified += 1
                page_unverified += 1
                print(f"[influencersgonewild] skip detail {list_item.get('url')}: {exc}")
                continue
            except Exception as exc:
                skipped_unverified += 1
                page_unverified += 1
                print(f"[influencersgonewild] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_player_playback(player, detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[influencersgonewild] skip unverified {player['guid']}: {exc}")
                    continue
                verified_count += 1
                page_verified += 1
                if dry_run:
                    samples.append(
                        {
                            "guid": player["guid"],
                            "link": detail["url"],
                            "video_url": verified["video_url"],
                            "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                            "playback_refresh_required": verified.get("playback_refresh_required"),
                            "media_format": verified.get("media_format"),
                            "playback_headers": verified.get("playback_headers"),
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
            f"[influencersgonewild] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} text_refreshed={page_text_refreshed} "
            f"old={page_old} unverified={page_unverified}"
        )
    return {
        "pages": pages,
        "parsed_videos": parsed_videos,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "text_refreshed": text_refreshed,
        "skipped_existing": skipped_existing,
        "skipped_unverified": skipped_unverified,
        "skipped_old": skipped_old,
        "skipped_access_errors": skipped_access_errors,
        "samples": samples[:10],
    }


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""",
            (INFLUENCERSGONEWILD_SOURCE, critical_window_minutes, limit),
        ),
        (
            """SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""",
            (INFLUENCERSGONEWILD_SOURCE, refresh_window_minutes, limit),
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
            try:
                if not source_url:
                    raise ValueError("missing source_url")
                list_item = {
                    "guid": row["guid"],
                    "detail_id": metadata.get("influencersgonewild_detail_id") or str(row["guid"]).replace(f"{INFLUENCERSGONEWILD_SOURCE}:", "", 1),
                    "url": source_url,
                    "title": row.get("title") or INFLUENCERSGONEWILD_SITE_NAME,
                    "image": (row.get("images") or [None])[0] if isinstance(row.get("images"), list) else None,
                    "published_at": row.get("published_at"),
                    "tags": metadata.get("tags") or [],
                }
                detail = parse_detail(list_item)
                player = detail["players"][0]
                verified = verify_player_playback(player, detail["url"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "influencersgonewild-html-video",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "influencersgonewild_detail_id": detail["detail_id"],
                    "raw_video_url": verified.get("raw_video_url"),
                    "variant_url": verified.get("variant_url"),
                    "playback_headers": verified.get("playback_headers"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
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
                        """UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""",
                        (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]),
                    )
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[influencersgonewild] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
