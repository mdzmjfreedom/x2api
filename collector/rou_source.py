from __future__ import annotations

import base64
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb


ROU_SITE_NAME = "肉視頻"
ROU_SOURCE = "rou"
ROU_KIND = "site"
ROU_DEFAULT_BASE_URL = os.environ.get("ROU_BASE_URL", "https://rou.video").strip().rstrip("/")
ROU_RETENTION_HOURS = int(os.environ.get("ROU_RETENTION_HOURS", "84"))
ROU_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("ROU_REQUEST_TIMEOUT_SECONDS", "30"))
ROU_MIN_PLAYLIST_DURATION_SECONDS = int(os.environ.get("ROU_MIN_PLAYLIST_DURATION_SECONDS", "15"))
ROU_REFRESH_WINDOW_MINUTES = int(os.environ.get("ROU_REFRESH_WINDOW_MINUTES", "90"))
ROU_CRITICAL_WINDOW_MINUTES = int(os.environ.get("ROU_CRITICAL_WINDOW_MINUTES", "15"))

FAR_FUTURE_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
AD_HOST_KEYWORDS = ("magsrv", "tsyndicate", "clickadu", "clammyendearedkeg")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_rou_target_value(raw: str) -> str:
    value = (raw or ROU_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = ROU_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("rou target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_rou_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"rou.video", "www.rou.video"}


def format_target_row(target_row: dict) -> str:
    return f"rou:{target_row['value']}"


def headers(referer: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    return result


def hls_headers(referer: str) -> dict[str, str]:
    result = headers(referer)
    result["Accept"] = "*/*"
    return result


def fetch_text(url: str, referer: str | None = None) -> str:
    response = requests.get(url, headers=headers(referer), timeout=ROU_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def fetch_next_data(url: str, referer: str | None = None) -> dict:
    html = fetch_text(url, referer)
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        raise ValueError("rou page is missing __NEXT_DATA__.")
    payload = json.loads(script.string or script.get_text())
    page_props = payload.get("props", {}).get("pageProps")
    if not isinstance(page_props, dict):
        raise ValueError("rou __NEXT_DATA__ is missing pageProps.")
    return page_props


def build_list_page_url(base_url: str, page: int) -> str:
    list_url = urljoin(normalize_rou_target_value(base_url) + "/", "v")
    if page <= 1:
        return list_url
    return f"{list_url}?page={page}"


def detail_url(base_url: str, video_id: str) -> str:
    return urljoin(normalize_rou_target_value(base_url) + "/", f"v/{video_id}")


def non_empty(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def unique_tags(values: list[object] | None) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        tag = str(value).strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tags


def normalize_list_item(base_url: str, item: dict) -> dict | None:
    if item.get("ad") or item.get("archived") or item.get("published") is False:
        return None
    video_id = non_empty(item.get("id"))
    if not video_id:
        return None
    created_at = parse_datetime(non_empty(item.get("createdAt"))) or now_utc()
    return {
        "guid": f"{ROU_SOURCE}:{video_id}",
        "video_id": video_id,
        "vid": non_empty(item.get("vid")),
        "title": non_empty(item.get("name")) or non_empty(item.get("nameZh")) or "肉視頻",
        "description": non_empty(item.get("description")),
        "ref": non_empty(item.get("ref")),
        "tags": unique_tags(item.get("tags") if isinstance(item.get("tags"), list) else []),
        "published_at": created_at,
        "modified_at": parse_datetime(non_empty(item.get("updatedAt"))),
        "duration": float_or_none(item.get("duration")),
        "view_count": int_or_none(item.get("viewCount")),
        "like_count": int_or_none(item.get("likeCount")),
        "dislike_count": int_or_none(item.get("dislikeCount")),
        "sources": item.get("sources") if isinstance(item.get("sources"), list) else [],
        "image": non_empty(item.get("coverImageUrl")),
        "source_url": detail_url(base_url, video_id),
    }


def parse_list_page(base_url: str, page: int) -> tuple[list[dict], int, int]:
    page_url = build_list_page_url(base_url, page)
    page_props = fetch_next_data(page_url, build_list_page_url(base_url, 1))
    raw_items = page_props.get("videos") if isinstance(page_props.get("videos"), list) else []
    items = [item for item in (normalize_list_item(base_url, raw_item) for raw_item in raw_items if isinstance(raw_item, dict)) if item]
    current_page = int(page_props.get("pageNum") or page)
    total_pages = int(page_props.get("totalPage") or current_page)
    return items, current_page, total_pages


def decode_ev_payload(ev: dict | None) -> dict:
    if not isinstance(ev, dict):
        raise ValueError("rou detail page is missing encrypted video payload.")
    encoded = non_empty(ev.get("d"))
    key = int_or_none(ev.get("k"))
    if not encoded or key is None:
        raise ValueError("rou encrypted video payload is incomplete.")
    raw = base64.b64decode(encoded)
    decoded = bytes((byte - key) % 256 for byte in raw).decode("utf-8")
    payload = json.loads(decoded)
    if not isinstance(payload, dict):
        raise ValueError("rou encrypted video payload is not an object.")
    return payload


def normalize_hls_playlist_url(video_url: str) -> str:
    parsed = urlparse(video_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("rou video URL must be absolute HTTP(S).")
    path = parsed.path
    if path.lower().endswith("/index.jpg"):
        path = path[: -len(".jpg")] + ".m3u8"
    normalized = urlunparse((parsed.scheme, parsed.netloc, path, "", parsed.query, ""))
    if not urlparse(normalized).path.lower().endswith(".m3u8"):
        raise ValueError("rou video URL could not be normalized to .m3u8.")
    return normalized


def playback_expiry(video_url: str) -> datetime | None:
    exp = parse_qs(urlparse(video_url).query).get("exp", [None])[0]
    if not exp:
        return None
    try:
        return datetime.fromtimestamp(int(exp), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def video_url_expires_at(video_url: str) -> datetime:
    expires_at = playback_expiry(video_url)
    return expires_at or FAR_FUTURE_EXPIRES_AT


def reject_ad_url(url: str) -> None:
    host = urlparse(url).netloc.lower()
    if any(keyword in host for keyword in AD_HOST_KEYWORDS):
        raise ValueError(f"rou playback URL points to an ad host: {host}")


def playlist_media_urls(video_url: str, playlist: str) -> list[str]:
    urls = []
    for line in playlist.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        urls.append(urljoin(video_url, value))
    return urls


def expected_duration_floor(expected_duration: float | None) -> float:
    if expected_duration and expected_duration > 0:
        return max(15.0, expected_duration * 0.6)
    return float(ROU_MIN_PLAYLIST_DURATION_SECONDS)


def verify_hls_url(video_url: str, referer: str, expected_duration: float | None = None) -> dict:
    reject_ad_url(video_url)
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.lower().endswith(".m3u8"):
        raise ValueError("rou video URL must be an HLS .m3u8 URL.")

    expires_at = playback_expiry(video_url)
    if expires_at and expires_at <= now_utc() + timedelta(minutes=1):
        raise ValueError("rou HLS URL is already expired or too close to expiry.")

    response = requests.get(video_url, headers=hls_headers(referer), timeout=ROU_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    playlist = response.text
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("rou HLS playlist is not playable media.")

    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if total_duration < expected_duration_floor(expected_duration):
        raise ValueError("rou HLS playlist is too short for the video metadata.")

    media_urls = playlist_media_urls(video_url, playlist)
    if not media_urls:
        raise ValueError("rou HLS playlist has no media segments.")

    checked_segment = False
    for media_url in media_urls[:6]:
        reject_ad_url(media_url)
        media_response = requests.get(media_url, headers=hls_headers(referer), timeout=ROU_REQUEST_TIMEOUT_SECONDS, stream=True)
        media_response.raise_for_status()
        chunk = next(media_response.iter_content(376), b"")
        if len(chunk) >= 188 and chunk[0] == 0x47:
            checked_segment = True
            break
    if not checked_segment:
        raise ValueError("rou HLS playlist has no readable MPEG-TS segment.")

    return {
        "video_url": video_url,
        "video_url_expires_at": video_url_expires_at(video_url),
        "playback_refresh_required": expires_at is not None,
        "playlist_bytes": len(playlist.encode("utf-8")),
        "playlist_duration_seconds": total_duration,
        "media_url_count": len(media_urls),
    }


def parse_detail_page(detail_page_url: str, list_item: dict | None = None) -> dict:
    page_props = fetch_next_data(detail_page_url, (list_item or {}).get("source_url") or detail_page_url)
    video = page_props.get("video")
    if not isinstance(video, dict):
        raise ValueError("rou detail page is missing video data.")
    if video.get("archived") or video.get("published") is False:
        raise ValueError("rou detail video is archived or unpublished.")

    video_id = non_empty(video.get("id"))
    if not video_id:
        raise ValueError("rou detail video is missing id.")
    if list_item and list_item.get("video_id") and list_item["video_id"] != video_id:
        raise ValueError("rou detail video id does not match list item.")

    payload = decode_ev_payload(page_props.get("ev"))
    raw_video_url = non_empty(payload.get("videoUrl"))
    if not raw_video_url:
        raise ValueError("rou detail encrypted payload is missing videoUrl.")

    video_url = normalize_hls_playlist_url(raw_video_url)
    tags = unique_tags(video.get("tags") if isinstance(video.get("tags"), list) else (list_item or {}).get("tags"))
    title = non_empty(video.get("name")) or non_empty(video.get("nameZh")) or (list_item or {}).get("title") or ROU_SITE_NAME
    description = non_empty(video.get("description")) or (list_item or {}).get("description") or title
    published_at = parse_datetime(non_empty(video.get("createdAt"))) or (list_item or {}).get("published_at") or now_utc()
    duration = float_or_none(video.get("duration")) or (list_item or {}).get("duration")
    parsed_detail_url = urlparse(detail_page_url)
    source_url = detail_url(f"{parsed_detail_url.scheme}://{parsed_detail_url.netloc}", video_id)
    player = {
        "guid": f"{ROU_SOURCE}:{video_id}",
        "video_id": video_id,
        "player_index": 1,
        "video_title": title,
        "video_url": video_url,
        "video_type": "hls",
        "thumb_vtt_url": non_empty(payload.get("thumbVTTUrl")),
        "tags": tags,
    }
    return {
        "url": source_url,
        "video_id": video_id,
        "vid": non_empty(video.get("vid")) or (list_item or {}).get("vid"),
        "title": title,
        "description": description,
        "ref": non_empty(video.get("ref")) or (list_item or {}).get("ref"),
        "tags": tags,
        "published_at": published_at,
        "modified_at": parse_datetime(non_empty(video.get("updatedAt"))) or (list_item or {}).get("modified_at"),
        "duration": duration,
        "view_count": int_or_none(video.get("viewCount")) or (list_item or {}).get("view_count"),
        "like_count": int_or_none(video.get("likeCount")) or (list_item or {}).get("like_count"),
        "dislike_count": int_or_none(video.get("dislikeCount")) or (list_item or {}).get("dislike_count"),
        "sources": (list_item or {}).get("sources") or [],
        "image": non_empty(video.get("coverImageUrl")) or (list_item or {}).get("image"),
        "click_ad_domain": non_empty(page_props.get("clickADUDomain")),
        "players": [player],
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_rou_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (ROU_SOURCE, ROU_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([ROU_SITE_NAME, "RouVideo", "视频"]), public_pool),
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
        "display_author": ROU_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": ROU_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": ROU_KIND,
        "target_value": target_row["value"],
        "site_name": ROU_SITE_NAME,
        "source_url": detail["url"],
        "rou_video_id": detail["video_id"],
        "rou_vid": detail.get("vid"),
        "ref": detail.get("ref"),
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "video_type": player["video_type"],
        "video_poster_url": detail.get("image"),
        "thumb_vtt_url": player.get("thumb_vtt_url"),
        "duration": detail.get("duration"),
        "view_count": detail.get("view_count"),
        "like_count": detail.get("like_count"),
        "dislike_count": detail.get("dislike_count"),
        "sources": detail.get("sources") or [],
        "tags": detail.get("tags") or player.get("tags") or [],
        "click_ad_domain": detail.get("click_ad_domain"),
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "rou-next-ev",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playback_refresh_required": verified.get("playback_refresh_required"),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
        "media_url_count": verified.get("media_url_count"),
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
                ROU_SITE_NAME,
                ROU_SITE_NAME,
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
    base_url = normalize_rou_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for page in range(1, max_pages + 1):
        pages += 1
        list_items, current_page, total_pages = parse_list_page(base_url, page)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[rou] page={page} list_items={len(list_items)} current_page={current_page} total_pages={total_pages}")
        if not list_items:
            print(f"[rou] page={page} empty_list stop=true")
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
                detail = parse_detail_page(list_item["source_url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[rou] skip detail {list_item.get('source_url')}: {exc}")
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                try:
                    verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[rou] skip unverified {player['guid']}: {exc}")
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
            f"[rou] page={page} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if current_page >= total_pages:
            break
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (ROU_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (ROU_SOURCE, refresh_window_minutes, limit)),
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
            video_id = metadata.get("rou_video_id") or str(row["guid"]).replace(f"{ROU_SOURCE}:", "", 1)
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or ROU_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or rou_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_hls_url(player["video_url"], detail["url"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "rou-next-ev",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "thumb_vtt_url": player.get("thumb_vtt_url") or metadata.get("thumb_vtt_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                }
                with conn.cursor() as cur:
                    cur.execute("""UPDATE items SET video_url = %s, video_url_expires_at = %s, metadata = %s, stored_at = stored_at WHERE id = %s""", (verified["video_url"], verified["video_url_expires_at"], Jsonb(next_metadata), row["id"]))
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[rou] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
