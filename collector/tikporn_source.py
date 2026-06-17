from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from psycopg.types.json import Jsonb


TIKPORN_SITE_NAME = "Tik.Porn"
TIKPORN_SOURCE = "tikporn"
TIKPORN_KIND = "site"
TIKPORN_DEFAULT_BASE_URL = os.environ.get("TIKPORN_BASE_URL", "https://tik.porn").strip().rstrip("/")
TIKPORN_API_BASE_URL = os.environ.get("TIKPORN_API_BASE_URL", "https://apiv2.tik.porn").strip().rstrip("/")
TIKPORN_RETENTION_HOURS = int(os.environ.get("TIKPORN_RETENTION_HOURS", "84"))
TIKPORN_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("TIKPORN_REQUEST_TIMEOUT_SECONDS", "30"))
TIKPORN_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("TIKPORN_MIN_PLAYLIST_DURATION_SECONDS", "4"))
TIKPORN_REFRESH_WINDOW_MINUTES = int(os.environ.get("TIKPORN_REFRESH_WINDOW_MINUTES", "90"))
TIKPORN_CRITICAL_WINDOW_MINUTES = int(os.environ.get("TIKPORN_CRITICAL_WINDOW_MINUTES", "15"))
TIKPORN_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

AD_HOST_KEYWORDS = (
    "magsrv",
    "tsyndicate",
    "clickadu",
    "exoclick",
    "popads",
    "adsterra",
    "ratecams",
    "dscgirls",
    "xxxiijmp",
    "xlovecam",
    "bcprm",
)
VIDEO_CDN_HOSTS = {"video-cdn.tik.porn", "media-cdn.tik.porn"}
EXPIRY_QUERY_KEYS = ("exp", "expires", "expire", "e", "t")


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


def float_or_none(value) -> float | None:
    try:
        return float(value)
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


def parse_tikporn_datetime(value: str | None) -> datetime | None:
    raw = non_empty(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_tikporn_target_value(raw: str) -> str:
    value = (raw or TIKPORN_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = TIKPORN_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("Tik.Porn target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_tikporn_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"tik.porn", "www.tik.porn"}


def format_target_row(target_row: dict) -> str:
    return f"tikporn:{target_row['value']}"


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_tikporn_target_value(base_url) + "/", f"video/{video_id}")


def headers(referer: str | None = None, *, accept: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": accept or "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Origin": "https://tik.porn",
    }
    if referer:
        result["Referer"] = referer
    return result


def api_get(path: str, *, params: dict | None = None, referer: str | None = None) -> dict:
    url = urljoin(TIKPORN_API_BASE_URL + "/", path.lstrip("/"))
    response = requests.get(
        url,
        params=params,
        headers=headers(referer or TIKPORN_DEFAULT_BASE_URL),
        timeout=TIKPORN_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or int(payload.get("code") or 0) != 200:
        raise ValueError(f"Tik.Porn API returned an invalid response for {path}.")
    return payload


def fetch_recent_videos(base_url: str) -> list[dict]:
    payload = api_get("/getrecentvideos", referer=base_url)
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("Tik.Porn recent videos response has no data list.")
    return [item for item in data if isinstance(item, dict)]


def fetch_video_info(video_id: str, base_url: str = TIKPORN_DEFAULT_BASE_URL) -> dict:
    payload = api_get("/getvideoinfo", params={"videoid": video_id}, referer=detail_url(base_url, video_id))
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Tik.Porn video detail response has no data object.")
    return data


def nested_video_text(video_text: dict, key: str, *, prefer_parsed: bool = False) -> str | None:
    value = video_text.get(key)
    if not isinstance(value, dict):
        return None
    default = value.get("default")
    if isinstance(default, dict):
        candidates = []
        if prefer_parsed:
            candidates.append(default.get("parsed_text"))
        candidates.append(default.get("text"))
        candidates.append(default.get("parsed_text"))
        for candidate in candidates:
            text = non_empty(candidate)
            if text:
                return text
    for candidate in value.values():
        if isinstance(candidate, dict):
            text = non_empty(candidate.get("parsed_text")) or non_empty(candidate.get("text"))
            if text:
                return text
    return None


def clean_title(value: str | None) -> str | None:
    title = non_empty(value)
    if not title:
        return None
    return re.sub(r"\s*\|\s*Tik\.Porn\s*$", "", title, flags=re.IGNORECASE).strip() or None


def unique_tags(values: list[object] | None) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        raw = value
        if isinstance(value, dict):
            raw = value.get("name") or value.get("slug") or value.get("id")
        tag = non_empty(raw)
        key = tag.lower() if tag else ""
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def creator_name(item: dict) -> str | None:
    creators = item.get("creator") if isinstance(item.get("creator"), list) else []
    for creator in creators:
        if isinstance(creator, dict):
            name = non_empty(creator.get("name")) or non_empty(creator.get("slug"))
            if name:
                return name
    return (
        non_empty(item.get("username"))
        or non_empty(item.get("user_slug"))
        or non_empty(item.get("user_name"))
        or non_empty(item.get("producer_name"))
    )


def normalize_asset_url(value: str | None) -> str | None:
    raw = non_empty(value)
    if not raw:
        return None
    if raw.startswith("//"):
        raw = f"https:{raw}"
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunparse(parsed._replace(fragment=""))


def normalize_tikporn_item(base_url: str, item: dict) -> dict | None:
    if int_or_none(item.get("status")) not in (None, 1):
        return None
    if int_or_none(item.get("is_video_active")) not in (None, 1):
        return None
    if int_or_none(item.get("original_deleted")):
        return None

    video_id = non_empty(item.get("video_id"))
    hls_url = normalize_asset_url(non_empty(item.get("hls_url")))
    if not video_id or not hls_url or not urlparse(hls_url).path.lower().endswith(".m3u8"):
        return None

    video_text = item.get("video_text") if isinstance(item.get("video_text"), dict) else {}
    title = clean_title(
        nested_video_text(video_text, "user_display_video_title", prefer_parsed=True)
        or nested_video_text(video_text, "meta_title")
        or non_empty(item.get("action_name"))
    )
    if not title:
        title = TIKPORN_SITE_NAME
    description = clean_title(nested_video_text(video_text, "meta_description")) or title

    action_name = non_empty(item.get("action_name"))
    tags = unique_tags(item.get("tags") if isinstance(item.get("tags"), list) else [])
    if action_name:
        tags = unique_tags([action_name, *tags])
    keywords = item.get("keywords") if isinstance(item.get("keywords"), list) else []
    tags = unique_tags([*tags, *keywords])

    image = (
        normalize_asset_url(item.get("poster_url"))
        or normalize_asset_url(item.get("thumbnail_url"))
        or normalize_asset_url(item.get("medium_thumb"))
        or normalize_asset_url(item.get("small_thumb"))
    )
    source_url = detail_url(base_url, video_id)
    published_at = (
        parse_tikporn_datetime(non_empty(item.get("published")))
        or parse_tikporn_datetime(non_empty(item.get("video_date")))
        or parse_tikporn_datetime(non_empty(item.get("upload_date")))
        or now_utc()
    )
    author = creator_name(item)
    return {
        "guid": f"{TIKPORN_SOURCE}:{video_id}",
        "video_id": video_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": published_at,
        "duration": int_or_none(item.get("duration")),
        "view_count": int_or_none(item.get("view_count")),
        "like_count": int_or_none(item.get("like_count")),
        "watch_time": int_or_none(item.get("watch_time")),
        "author": author,
        "source_url": source_url,
        "hls_url": hls_url,
        "videoexp": int_or_none(item.get("videoexp")),
        "mp4_url": normalize_asset_url(item.get("mp4_url")),
        "mpd_url": normalize_asset_url(item.get("mpd_url")),
        "download_url": normalize_asset_url(item.get("download_url")),
        "action_id": int_or_none(item.get("action_id")),
        "action_name": action_name,
        "action_slug": non_empty(item.get("action_slug")),
        "porn_level": non_empty(item.get("porn_level")),
        "video_type": non_empty(item.get("video_type")),
        "upload_status": non_empty(item.get("upload_status")),
        "user_upload_status": non_empty(item.get("user_upload_status")),
        "creator_id": int_or_none(item.get("user_account_id")),
        "creator_slug": non_empty(item.get("user_slug")),
        "tags": tags,
    }


def reject_ad_url(url: str) -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"Tik.Porn playback URL points to an ad host: {host}")
    if host not in VIDEO_CDN_HOSTS:
        raise ValueError(f"Tik.Porn playback URL is not on an allowed video CDN: {host}")


def parse_query_expiry(video_url: str) -> datetime | None:
    query = parse_qs(urlparse(video_url).query)
    for key in EXPIRY_QUERY_KEYS:
        parsed = parse_epoch_datetime((query.get(key) or [None])[0])
        if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
            return parsed
    return None


def parse_path_expiry(video_url: str) -> datetime | None:
    expiries = []
    for part in urlparse(video_url).path.split("/"):
        if re.fullmatch(r"\d{10,13}", part):
            parsed = parse_epoch_datetime(part)
            if parsed and datetime(2020, 1, 1, tzinfo=timezone.utc) <= parsed <= datetime(2100, 1, 1, tzinfo=timezone.utc):
                expiries.append(parsed)
    return min(expiries) if expiries else None


def playback_expiry(urls: list[str]) -> datetime | None:
    expiries = []
    for url in urls:
        parsed = parse_query_expiry(url) or parse_path_expiry(url)
        if parsed:
            expiries.append(parsed)
    return min(expiries) if expiries else None


def fetch_hls_text(url: str, referer: str) -> str:
    reject_ad_url(url)
    response = requests.get(
        url,
        headers=headers(referer, accept="*/*"),
        timeout=TIKPORN_REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    return response.text


def read_hls_chunk(url: str, referer: str, size: int) -> bytes:
    reject_ad_url(url)
    with requests.get(
        url,
        headers=headers(referer, accept="*/*"),
        timeout=TIKPORN_REQUEST_TIMEOUT_SECONDS,
        stream=True,
    ) as response:
        response.raise_for_status()
        return next(response.iter_content(size), b"")


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
    def priority(variant: dict) -> tuple[int, int]:
        stream_inf = str(variant.get("stream_inf") or "").lower()
        if "avc1" in stream_inf:
            codec_rank = 0
        elif "hev1" in stream_inf or "hvc1" in stream_inf:
            codec_rank = 1
        else:
            codec_rank = 2
        return (codec_rank, variant_bandwidth(stream_inf))

    return sorted(variants, key=priority)


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def playlist_map_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-MAP"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def playlist_key_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        if not line.startswith("#EXT-X-KEY"):
            continue
        for uri in re.findall(r'URI="([^"]+)"', line):
            urls.append(urljoin(video_url, uri))
    return urls


def expected_duration_floor(expected_duration: int | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(float(TIKPORN_MIN_PLAYLIST_DURATION_SECONDS), expected_duration * 0.6)
    return float(TIKPORN_MIN_PLAYLIST_DURATION_SECONDS)


def looks_like_media_segment(chunk: bytes) -> bool:
    if len(chunk) >= 188 and chunk[0] == 0x47:
        return True
    prefix = chunk[:96]
    return any(marker in prefix for marker in (b"ftyp", b"moof", b"mdat", b"sidx"))


def verify_media_playlist(media_url: str, playlist: str, referer: str, expected_duration: int | None) -> dict:
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("Tik.Porn HLS media playlist is not playable media.")

    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("Tik.Porn HLS playlist is too short for the video metadata.")

    media_urls = playlist_media_urls(media_url, playlist)
    if not media_urls:
        raise ValueError("Tik.Porn HLS playlist has no media segments.")

    map_urls = playlist_map_urls(media_url, playlist)
    key_urls = playlist_key_urls(media_url, playlist)

    checked_init = False
    for map_url in map_urls[:2]:
        chunk = read_hls_chunk(map_url, referer, 128)
        if chunk and looks_like_media_segment(chunk):
            checked_init = True
            break
    if map_urls and not checked_init:
        raise ValueError("Tik.Porn HLS playlist has no readable fMP4 init segment.")

    checked_key = False
    for key_url in key_urls[:2]:
        chunk = read_hls_chunk(key_url, referer, 16)
        if len(chunk) == 16:
            checked_key = True
            break
    if key_urls and not checked_key:
        raise ValueError("Tik.Porn HLS playlist has no readable AES key.")

    checked_segment = False
    segment_error: Exception | None = None
    for media_segment_url in media_urls[:8]:
        try:
            chunk = read_hls_chunk(media_segment_url, referer, 376)
            if looks_like_media_segment(chunk):
                checked_segment = True
                break
        except Exception as exc:
            segment_error = exc
    if not checked_segment:
        raise ValueError(f"Tik.Porn HLS playlist has no readable media segment: {segment_error}")

    return {
        "playlist_duration_seconds": total_duration,
        "media_url_count": len(media_urls),
        "map_url_count": len(map_urls),
        "key_url_count": len(key_urls),
        "checked_media_playlist_url": media_url,
    }


def verify_hls_url(video_url: str, referer: str, expected_duration: int | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("Tik.Porn video URL must be an HLS .m3u8 URL.")

    master_playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in master_playlist:
        raise ValueError("Tik.Porn HLS URL is not a playlist.")

    variants = playlist_stream_variants(video_url, master_playlist)
    checked_urls = [video_url]
    media_result = None
    media_error: Exception | None = None
    if variants:
        for variant in sort_variants_for_probe(variants):
            variant_url = variant["url"]
            try:
                reject_ad_url(variant_url)
                media_playlist = fetch_hls_text(variant_url, referer)
                checked_urls.append(variant_url)
                media_urls = playlist_media_urls(variant_url, media_playlist)
                checked_urls.extend(media_urls[:3])
                checked_urls.extend(playlist_map_urls(variant_url, media_playlist)[:1])
                checked_urls.extend(playlist_key_urls(variant_url, media_playlist)[:1])
                media_result = {
                    **verify_media_playlist(variant_url, media_playlist, referer, expected_duration),
                    "variant_count": len(variants),
                    "selected_variant_url": variant_url,
                    "selected_variant_stream_inf": variant.get("stream_inf"),
                }
                break
            except Exception as exc:
                media_error = exc
        if media_result is None:
            raise ValueError(f"Tik.Porn HLS master playlist has no playable variant: {media_error}")
    else:
        media_urls = playlist_media_urls(video_url, master_playlist)
        checked_urls.extend(media_urls[:3])
        checked_urls.extend(playlist_map_urls(video_url, master_playlist)[:1])
        checked_urls.extend(playlist_key_urls(video_url, master_playlist)[:1])
        media_result = {
            **verify_media_playlist(video_url, master_playlist, referer, expected_duration),
            "variant_count": 0,
            "selected_variant_url": None,
            "selected_variant_stream_inf": None,
        }

    expires_at = playback_expiry(checked_urls)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("Tik.Porn HLS URL is already expired or too close to expiry.")

    return {
        "video_url": video_url,
        "video_url_expires_at": expires_at or TIKPORN_STABLE_VIDEO_URL_EXPIRES_AT,
        "playback_refresh_required": expires_at is not None,
        "playlist_bytes": len(master_playlist.encode("utf-8")),
        **media_result,
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_tikporn_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (TIKPORN_SOURCE, TIKPORN_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([TIKPORN_SITE_NAME, "video"]), public_pool),
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


def build_author_presentation(item: dict) -> dict[str, str | None]:
    display_author = non_empty(item.get("author")) or TIKPORN_SITE_NAME
    return {
        "display_author": display_author,
        "display_handle": None,
        "author_profile_url": item["source_url"],
        "author_profile_platform": TIKPORN_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, item: dict, verified: dict, retention_hours: int) -> bool:
    published_at = item.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = item.get("description") or item.get("title")
    images = [item["image"]] if item.get("image") else []
    presentation = build_author_presentation(item)
    metadata = {
        "target": format_target_row(target_row),
        "target_type": TIKPORN_KIND,
        "target_value": target_row["value"],
        "site_name": TIKPORN_SITE_NAME,
        "source_url": item["source_url"],
        "tikporn_video_id": item["video_id"],
        "action_id": item.get("action_id"),
        "action_name": item.get("action_name"),
        "action_slug": item.get("action_slug"),
        "video_type": item.get("video_type"),
        "porn_level": item.get("porn_level"),
        "upload_status": item.get("upload_status"),
        "user_upload_status": item.get("user_upload_status"),
        "video_poster_url": item.get("image"),
        "duration": item.get("duration"),
        "view_count": item.get("view_count"),
        "like_count": item.get("like_count"),
        "watch_time": item.get("watch_time"),
        "creator": item.get("author"),
        "creator_id": item.get("creator_id"),
        "creator_slug": item.get("creator_slug"),
        "tags": item.get("tags") or [],
        "mp4_url": item.get("mp4_url"),
        "mpd_url": item.get("mpd_url"),
        "download_url": item.get("download_url"),
        "videoexp": item.get("videoexp"),
        "resolver": "tikporn-apiv2-recent",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "media_url_count": verified.get("media_url_count"),
        "variant_count": verified.get("variant_count"),
        "selected_variant_url": verified.get("selected_variant_url"),
        "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
        "map_url_count": verified.get("map_url_count"),
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
                item["guid"],
                presentation["display_author"],
                presentation["display_author"],
                presentation["display_author"],
                presentation["display_handle"],
                presentation["author_profile_url"],
                presentation["author_profile_platform"],
                item.get("title"),
                content,
                item["source_url"],
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
    base_url = normalize_tikporn_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_items = verified_count = skipped_existing = skipped_unverified = skipped_invalid = skipped_old = 0
    samples = []
    latest_guid = None
    pages = 0

    # Tik.Porn's current public latest endpoint returns one fixed latest page.
    for page in range(1, max_pages + 1):
        pages += 1
        raw_items = fetch_recent_videos(base_url)
        print(f"[tikporn] page={page} raw_items={len(raw_items)} endpoint=getrecentvideos")
        if not raw_items:
            print(f"[tikporn] page={page} empty_list stop=true")
            break

        page_inserted = page_existing = page_old = page_updated = page_verified = page_unverified = page_invalid = page_parsed_items = 0
        for raw_item in raw_items:
            item = normalize_tikporn_item(base_url, raw_item)
            if not item:
                skipped_invalid += 1
                page_invalid += 1
                continue
            parsed_items += 1
            page_parsed_items += 1
            latest_guid = latest_guid or item["guid"]
            if item["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            if target_row and item_exists_for_guid(conn, str(target_row["id"]), item["guid"]):
                skipped_existing += 1
                page_existing += 1
                continue
            try:
                verified = verify_hls_url(item["hls_url"], item["source_url"], item.get("duration"))
            except Exception as exc:
                skipped_unverified += 1
                page_unverified += 1
                print(f"[tikporn] skip unverified {item['guid']}: {exc}")
                continue
            verified_count += 1
            page_verified += 1
            if dry_run:
                samples.append(
                    {
                        "guid": item["guid"],
                        "link": item["source_url"],
                        "published_at": item["published_at"].isoformat(),
                        "video_url": verified["video_url"],
                        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                        "playback_refresh_required": verified.get("playback_refresh_required"),
                        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    }
                )
                continue
            if upsert_video_item(conn, target_row, item, verified, retention_hours):
                inserted += 1
                page_inserted += 1
            else:
                updated += 1
                page_updated += 1

        if target_row:
            upsert_crawl_state(conn, target_row["id"], last_guid=latest_guid, last_error=None, success=True)
        print(
            f"[tikporn] page={page} parsed_items={page_parsed_items} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"invalid_or_ad={page_invalid} unverified={page_unverified}"
        )
        break

    return {
        "pages": pages,
        "parsed_items": parsed_items,
        "verified": verified_count,
        "inserted": inserted,
        "updated": updated,
        "skipped_existing": skipped_existing,
        "skipped_unverified": skipped_unverified,
        "skipped_invalid": skipped_invalid,
        "skipped_old": skipped_old,
        "samples": samples[:10],
    }
