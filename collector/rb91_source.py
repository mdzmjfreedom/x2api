from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timedelta, timezone
from html import unescape as html_unescape
from urllib.parse import parse_qs, parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


RB91_SITE_NAME = "91热爆"
RB91_SOURCE = "91rb"
RB91_KIND = "site"
RB91_DEFAULT_BASE_URL = os.environ.get("RB91_BASE_URL", "https://www.91rb.com/latest-updates/").strip()
RB91_RETENTION_HOURS = int(os.environ.get("RB91_RETENTION_HOURS", "84"))
RB91_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("RB91_REQUEST_TIMEOUT_SECONDS", "30"))
RB91_MIN_VIDEO_DURATION_SECONDS = int(os.environ.get("RB91_MIN_VIDEO_DURATION_SECONDS", "5"))
RB91_REFRESH_WINDOW_MINUTES = int(os.environ.get("RB91_REFRESH_WINDOW_MINUTES", "90"))
RB91_CRITICAL_WINDOW_MINUTES = int(os.environ.get("RB91_CRITICAL_WINDOW_MINUTES", "15"))
RB91_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "11345.ee",
    "histats",
    "sstatic1.histats",
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "adnxs",
    "doubleclick",
)
EXPIRY_QUERY_KEYS = ("t", "e", "exp", "expires", "expire")


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
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


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


def parse_duration_seconds(value: str | None) -> int | None:
    raw = non_empty(value)
    if not raw:
        return None
    parts = raw.split(":")
    if not all(part.isdigit() for part in parts):
        return None
    values = [int(part) for part in parts]
    if len(values) == 2:
        minutes, seconds = values
        return minutes * 60 + seconds
    if len(values) == 3:
        hours, minutes, seconds = values
        return hours * 3600 + minutes * 60 + seconds
    return None


def parse_iso_duration_seconds(value: str | None) -> int | None:
    raw = non_empty(value)
    if not raw:
        return None
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        raw,
    )
    if not match:
        return None
    total = (
        int(match.group("days") or 0) * 86400
        + int(match.group("hours") or 0) * 3600
        + int(match.group("minutes") or 0) * 60
        + int(match.group("seconds") or 0)
    )
    return total or None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_91rb_target_value(raw: str) -> str:
    value = (raw or RB91_DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("91rb target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_91rb_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"91rb.com", "www.91rb.com"}


def format_target_row(target_row: dict) -> str:
    return f"91rb:{target_row['value']}"


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
    if referer:
        result["Referer"] = referer
        if accept:
            origin = origin_header(referer)
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
                timeout=RB91_REQUEST_TIMEOUT_SECONDS,
                stream=stream,
                allow_redirects=True,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("91rb request failed.")


def fetch_html(url: str, referer: str | None = None) -> str:
    return request_with_proxy_fallback(url, referer=referer).text


def fetch_text(url: str, referer: str) -> str:
    return request_with_proxy_fallback(url, referer=referer, accept="*/*").text


def read_media_chunk(url: str, referer: str, size: int = 2048) -> tuple[bytes, requests.Response]:
    response = request_with_proxy_fallback(
        url,
        referer=referer,
        accept="video/mp4,video/MP2T,application/vnd.apple.mpegurl,application/x-mpegURL,*/*",
        range_header=f"bytes=0-{size - 1}",
        stream=True,
    )
    return next(response.iter_content(size), b""), response


def build_list_page_url(base_url: str, page: int) -> str:
    target = normalize_91rb_target_value(base_url)
    if page <= 1:
        return f"{target}/latest-updates/"
    return f"{target}/latest-updates/{page}/"


def detail_id_from_url(url: str) -> str | None:
    match = re.search(r"/videos/(?P<id>\d+)/(?P<slug>[^/?#]+)/?", urlparse(url).path)
    return match.group("id") if match else None


def detail_url(base_url: str, video_id: str, slug: str | None = None) -> str:
    target = normalize_91rb_target_value(base_url)
    safe_slug = (slug or "").strip("/")
    return f"{target}/videos/{video_id}/{safe_slug}/" if safe_slug else f"{target}/videos/{video_id}/"


def embed_url(base_url: str, video_id: str) -> str:
    return f"{normalize_91rb_target_value(base_url)}/embed/{video_id}"


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw or raw.startswith("data:"):
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    normalized = urljoin(base_url, html_unescape(raw))
    return urlunparse(urlparse(normalized)._replace(fragment=""))


def parse_list_page(base_url: str, page: int) -> list[dict]:
    page_url = build_list_page_url(base_url, page)
    soup = BeautifulSoup(fetch_html(page_url, build_list_page_url(base_url, 1)), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for link in soup.select('#list_videos_latest_videos_list_items a[href*="/videos/"]'):
        href = link.get("href")
        source_url = normalize_asset_url(page_url, href)
        video_id = detail_id_from_url(source_url or "")
        if not source_url or not video_id or video_id in seen:
            continue
        seen.add(video_id)
        path_parts = [part for part in urlparse(source_url).path.split("/") if part]
        slug = path_parts[2] if len(path_parts) >= 3 else None
        image = link.select_one("img")
        image_url = image.get("data-original") or image.get("data-webp") or image.get("src") if image else None
        duration = parse_duration_seconds((link.select_one(".time") or BeautifulSoup("", "html.parser")).get_text(" ", strip=True))
        title = non_empty(link.get("title")) or non_empty((link.select_one(".title") or BeautifulSoup("", "html.parser")).get_text(" ", strip=True))
        items.append(
            {
                "guid": f"{RB91_SOURCE}:{video_id}",
                "video_id": video_id,
                "slug": slug,
                "url": detail_url(base_url, video_id, slug),
                "source_url": source_url,
                "title": title or RB91_SITE_NAME,
                "image": normalize_asset_url(page_url, image_url),
                "duration": duration,
                "published_at": now_utc(),
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


def js_string_value(raw: str) -> str:
    try:
        return ast.literal_eval(raw)
    except (SyntaxError, ValueError):
        quote = raw[:1]
        body = raw[1:-1] if quote in {"'", '"'} else raw
        return bytes(body.replace("\\/", "/"), "utf-8").decode("unicode_escape")


def extract_source_candidates(html: str, page_url: str) -> list[dict[str, str | None]]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[dict[str, str | None]] = []
    seen: set[str] = set()

    def add(src: str | None, label: str | None = None) -> None:
        video_url = normalize_asset_url(page_url, src)
        if not video_url or video_url in seen:
            return
        seen.add(video_url)
        path = urlparse(video_url).path.lower()
        if ".m3u8" in path:
            video_type = "hls"
        elif ".mp4" in path or "/get_file/" in path:
            video_type = "mp4"
        else:
            return
        candidates.append({"video_url": video_url, "video_type": video_type, "label": label, "referer": page_url})

    for source in soup.select("video source[src], source[src]"):
        add(source.get("src"), non_empty(source.get("label")) or non_empty(source.get("res")))

    for match in re.finditer(r"\bvideo_(?:alt_)?url(?:_hd)?\s*:\s*(?P<value>'(?:\\.|[^'])*'|\"(?:\\.|[^\"])*\")", html):
        add(js_string_value(match.group("value")), None)

    for match in re.finditer(r"\bsrc\s*=\s*(?P<value>'https?://[^']+?\.(?:m3u8|mp4)[^']*'|\"https?://[^\"]+?\.(?:m3u8|mp4)[^\"]*\")", html):
        add(js_string_value(match.group("value")), None)

    return candidates


def reject_ad_url(url: str, label: str = "playback") -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"91rb {label} URL points to an ad host: {host}")


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


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-KEY"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def playlist_variant_urls(video_url: str, playlist: str) -> list[tuple[int, str, str]]:
    variants: list[tuple[int, str, str]] = []
    lines = playlist.splitlines()
    for index, line in enumerate(lines):
        if not line.startswith("#EXT-X-STREAM-INF"):
            continue
        bandwidth = int_or_none((re.search(r"\bBANDWIDTH=(\d+)", line) or [None, None])[1]) or 0
        for next_line in lines[index + 1 :]:
            value = next_line.strip()
            if not value or value.startswith("#"):
                continue
            variants.append((bandwidth, urljoin(video_url, value), line.strip()))
            break
    return variants


def has_media_signature(chunk: bytes) -> bool:
    if len(chunk) >= 188 and chunk[0] == 0x47:
        return len(chunk) < 376 or chunk[188] == 0x47
    return b"ftyp" in chunk[:32] or chunk[:4] in {b"moof", b"mdat"}


def verify_hls_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or ".m3u8" not in parsed.path.lower():
        raise ValueError("91rb HLS URL must be an .m3u8 URL.")

    playlist_url = video_url
    playlist = fetch_text(playlist_url, referer)
    if "#EXTM3U" not in playlist:
        raise ValueError("91rb HLS response is not a playlist.")

    variants = playlist_variant_urls(playlist_url, playlist)
    selected_variant_url = None
    selected_variant_stream_inf = None
    if "#EXTINF" not in playlist and variants:
        _bandwidth, selected_variant_url, selected_variant_stream_inf = sorted(variants, key=lambda item: item[0])[-1]
        reject_ad_url(selected_variant_url, "variant")
        playlist_url = selected_variant_url
        playlist = fetch_text(playlist_url, referer)

    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("91rb HLS playlist is not playable media.")

    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if expected_duration and total_duration < max(RB91_MIN_VIDEO_DURATION_SECONDS, expected_duration * 0.5):
        raise ValueError("91rb HLS playlist is too short for the video metadata.")

    media_urls = playlist_media_urls(playlist_url, playlist)
    if not media_urls:
        raise ValueError("91rb HLS playlist has no media segments.")

    key_urls = playlist_key_urls(playlist_url, playlist)
    expires_at = playback_expiry([video_url, playlist_url, *media_urls[:3], *key_urls[:1]])
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("91rb HLS URL is already expired or too close to expiry.")

    segment_error: Exception | None = None
    for media_url in media_urls[:6]:
        reject_ad_url(media_url, "segment")
        try:
            chunk, _response = read_media_chunk(media_url, referer, 752)
            if has_media_signature(chunk):
                return {
                    "video_url": video_url,
                    "playback_headers": playback_headers(referer),
                    "video_url_expires_at": expires_at or RB91_STABLE_VIDEO_URL_EXPIRES_AT,
                    "playback_refresh_required": expires_at is not None,
                    "media_format": "hls",
                    "playlist_bytes": len(playlist.encode("utf-8")),
                    "playlist_duration_seconds": total_duration,
                    "media_url_count": len(media_urls),
                    "key_url_count": len(key_urls),
                    "variant_count": len(variants),
                    "selected_variant_url": selected_variant_url,
                    "selected_variant_stream_inf": selected_variant_stream_inf,
                }
        except Exception as exc:
            segment_error = exc
    raise ValueError(f"91rb HLS playlist has no readable media segment: {segment_error}")


def parse_content_length(response: requests.Response) -> int | None:
    content_range = response.headers.get("Content-Range") or ""
    match = re.search(r"/(\d+)$", content_range)
    if match:
        return int(match.group(1))
    return int_or_none(response.headers.get("Content-Length"))


def verify_mp4_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("91rb MP4 URL must be absolute HTTP(S).")

    expires_at = parse_query_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("91rb MP4 URL is already expired or too close to expiry.")

    chunk, response = read_media_chunk(video_url, referer, 2048)
    content_type = (response.headers.get("Content-Type") or "").lower()
    if response.status_code not in {200, 206}:
        raise ValueError(f"91rb MP4 URL returned unexpected status {response.status_code}.")
    if not chunk:
        raise ValueError("91rb MP4 URL returned an empty media chunk.")
    if "text/html" in content_type or "image/" in content_type:
        raise ValueError(f"91rb MP4 URL returned non-media content type: {content_type}")
    if b"Access denied" in chunk[:128] or b"<html" in chunk[:256].lower():
        raise ValueError("91rb MP4 URL returned an access/HTML response instead of media.")
    if not (content_type.startswith("video/") or b"ftyp" in chunk[:64]):
        raise ValueError(f"91rb MP4 URL did not look like playable MP4 media: {content_type}")
    if expected_duration and expected_duration < RB91_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("91rb MP4 duration is too short for a real video.")

    return {
        "video_url": video_url,
        "playback_headers": playback_headers(referer),
        "video_url_expires_at": expires_at or RB91_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "media_format": "mp4",
        "content_type": content_type,
        "content_length": parse_content_length(response),
        "media_probe_bytes": len(chunk),
    }


def verify_playback_url(video_url: str, referer: str, video_type: str, expected_duration: int | None = None) -> dict:
    if video_type == "hls" or ".m3u8" in urlparse(video_url).path.lower():
        return verify_hls_url(video_url, referer, expected_duration)
    return verify_mp4_url(video_url, referer, expected_duration)


def detail_title(soup: BeautifulSoup, video_object: dict, fallback: str | None = None) -> str:
    title = non_empty(video_object.get("name"))
    if title:
        return html_unescape(title)
    heading = soup.select_one("h1.title")
    if heading:
        title = non_empty(heading.get_text(" ", strip=True))
        if title:
            return html_unescape(title)
    raw_title = non_empty(soup.title.string if soup.title else None)
    if raw_title:
        return html_unescape(re.sub(r"\s*-\s*91热爆\s*$", "", raw_title).strip())
    return fallback or RB91_SITE_NAME


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    parsed = urlparse(detail_page_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    html = fetch_html(detail_page_url, (list_item or {}).get("source_url") or build_list_page_url(base_url, 1))
    soup = BeautifulSoup(html, "html.parser")
    video_object = find_json_ld(iter_json_ld(soup), "VideoObject") or {}
    embed_url_value = non_empty(video_object.get("embedUrl"))
    embed_video_id = embed_url_value.rstrip("/").rsplit("/", 1)[-1] if embed_url_value else None
    video_id = detail_id_from_url(detail_page_url) or embed_video_id or non_empty((list_item or {}).get("video_id"))
    if not video_id:
        raise ValueError("91rb detail page is missing video id.")

    candidates = extract_source_candidates(html, detail_page_url)
    embed_candidates = []
    if not candidates:
        embed_html = fetch_html(embed_url(base_url, video_id), detail_page_url)
        embed_candidates = extract_source_candidates(embed_html, embed_url(base_url, video_id))
        candidates.extend(embed_candidates)
    if not candidates:
        if 'id="limitplayer"' in html or "试看已达上限" in html:
            raise ValueError("91rb public detail page is gated by the trial limit and exposes no playable media.")
        raise ValueError("91rb detail/embed page exposes no playable media source.")

    title = detail_title(soup, video_object, non_empty((list_item or {}).get("title")))
    thumbnail = video_object.get("thumbnailUrl")
    if isinstance(thumbnail, list):
        thumbnail = next((non_empty(value) for value in thumbnail), None)
    image = normalize_asset_url(detail_page_url, non_empty(thumbnail) or non_empty((list_item or {}).get("image")))
    keywords = video_object.get("keywords")
    if isinstance(keywords, list):
        tags = [tag for tag in (non_empty(value) for value in keywords) if tag]
    elif isinstance(keywords, str):
        tags = [tag.strip() for tag in re.split(r"[,，]", keywords) if tag.strip()]
    else:
        tags = [link.get_text(" ", strip=True) for link in soup.select(".tags-row a") if non_empty(link.get_text(" ", strip=True))]
    published_at = (
        parse_datetime(non_empty(video_object.get("uploadDate")))
        or parse_datetime(non_empty(video_object.get("datePublished")))
        or parse_datetime(non_empty(soup.select_one('meta[property="video:release_date"]') and soup.select_one('meta[property="video:release_date"]').get("content")))
        or (list_item or {}).get("published_at")
        or now_utc()
    )
    duration = (
        parse_iso_duration_seconds(non_empty(video_object.get("duration")))
        or int_or_none(soup.select_one('meta[property="video:duration"]').get("content") if soup.select_one('meta[property="video:duration"]') else None)
        or (list_item or {}).get("duration")
    )
    if duration and duration < RB91_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("91rb detail duration is too short for a real video.")

    players = []
    for index, candidate in enumerate(candidates, start=1):
        players.append(
            {
                "guid": f"{RB91_SOURCE}:{video_id}:{index}" if index > 1 else f"{RB91_SOURCE}:{video_id}",
                "video_id": video_id,
                "player_index": index,
                "video_title": title,
                "video_url": candidate["video_url"],
                "video_type": candidate["video_type"],
                "referer": candidate.get("referer") or detail_page_url,
                "label": candidate.get("label"),
            }
        )

    return {
        "url": detail_url(base_url, video_id, (list_item or {}).get("slug")),
        "video_id": video_id,
        "title": title,
        "description": non_empty(video_object.get("description")) or title,
        "image": image,
        "tags": tags,
        "duration": duration,
        "published_at": published_at,
        "modified_at": parse_datetime(non_empty(video_object.get("dateModified"))),
        "source_candidate_count": len(candidates),
        "embed_candidate_count": len(embed_candidates),
        "players": players,
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_91rb_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (RB91_SOURCE, RB91_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([RB91_SITE_NAME, "视频"]), public_pool),
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
        "display_author": RB91_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": RB91_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": RB91_KIND,
        "target_value": target_row["value"],
        "site_name": RB91_SITE_NAME,
        "source_url": detail["url"],
        "rb91_video_id": detail["video_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "source_candidate_count": detail.get("source_candidate_count"),
        "embed_candidate_count": detail.get("embed_candidate_count"),
        "video_type": player["video_type"],
        "media_format": verified.get("media_format"),
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "tags": detail.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "91rb-public-player",
        "resolved_at": now_iso(),
        "playback_headers": verified.get("playback_headers"),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "content_type": verified.get("content_type"),
        "content_length": verified.get("content_length"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "media_url_count": verified.get("media_url_count"),
        "key_url_count": verified.get("key_url_count"),
        "variant_count": verified.get("variant_count"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
    }
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
        author=RB91_SITE_NAME,
        fullname=RB91_SITE_NAME,
        x_url=detail["url"],
        link=detail.get("title"),
        images=images,
        playback_headers=verified.get("playback_headers"),
    )
    return inserted


def monitor_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_91rb_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items = parse_list_page(base_url, page)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[91rb] page={page} list_items={len(list_items)} url={build_list_page_url(base_url, page)}")
        if not list_items:
            print(f"[91rb] page={page} empty_list stop=true")
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
                print(f"[91rb] skip detail {list_item.get('url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            verified_for_item = False
            for player in detail["players"]:
                try:
                    verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[91rb] skip unverified {player['guid']}: {exc}")
                    continue
                verified_for_item = True
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
                            "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
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
            if verified_for_item:
                continue
        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[91rb] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
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
        "samples": samples[:10],
    }
