from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from psycopg.types.json import Jsonb

try:
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from collector.opensearch_items import upsert_item_record as upsert_item_record_with_opensearch
except ModuleNotFoundError:
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
    from opensearch_items import upsert_item_record as upsert_item_record_with_opensearch


MH18_SITE_NAME = "禁漫天堂"
MH18_SOURCE = "18mh"
MH18_KIND = "site"
MH18_DEFAULT_BASE_URL = os.environ.get("MH18_BASE_URL", "https://18mh.net").strip().rstrip("/")
MH18_RETENTION_HOURS = int(os.environ.get("MH18_RETENTION_HOURS", "84"))
MH18_TIMEZONE = timezone(timedelta(hours=8))
MH18_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MH18_REQUEST_TIMEOUT_SECONDS", "30"))
MH18_MIN_VIDEO_DURATION_SECONDS = int(os.environ.get("MH18_MIN_VIDEO_DURATION_SECONDS", "60"))
MH18_REFRESH_WINDOW_MINUTES = int(os.environ.get("MH18_REFRESH_WINDOW_MINUTES", "90"))
MH18_CRITICAL_WINDOW_MINUTES = int(os.environ.get("MH18_CRITICAL_WINDOW_MINUTES", "15"))
MH18_STABLE_VIDEO_URL_EXPIRES_AT = datetime(2099, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def non_empty(value) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    normalized = value.strip()
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_18mh_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=MH18_TIMEZONE).astimezone(timezone.utc)
        except ValueError:
            continue
    return parse_datetime(raw)


def normalize_site_target_key(value: str) -> str:
    parsed = urlparse(value)
    return parsed.netloc.lower() or value.lower().rstrip("/")


def normalize_18mh_target_value(raw: str) -> str:
    value = (raw or MH18_DEFAULT_BASE_URL).strip().rstrip("/")
    if not value:
        value = MH18_DEFAULT_BASE_URL
    parsed = urlparse(value if "://" in value else f"https://{value}")
    if not parsed.netloc:
        raise ValueError("18mh target must be a URL or host.")
    return urlunparse((parsed.scheme or "https", parsed.netloc.lower(), "", "", "", ""))


def is_18mh_target_url(raw: str) -> bool:
    value = raw.strip().lower()
    if not value:
        return False
    try:
        parsed = urlparse(value if "://" in value else f"https://{value}")
    except Exception:
        return False
    return parsed.netloc.lower() in {"18mh.net", "www.18mh.net"}


def format_target_row(target_row: dict) -> str:
    return f"18mh:{target_row['value']}"


def headers(referer: str | None = None) -> dict[str, str]:
    result = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    if referer:
        result["Referer"] = referer
    return result


def fetch_html(url: str, referer: str | None = None) -> str:
    response = requests.get(url, headers=headers(referer), timeout=MH18_REQUEST_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def fetch_hls_text(url: str, referer: str) -> str:
    last_error: Exception | None = None
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            response = session.get(url, headers=headers(referer), timeout=MH18_REQUEST_TIMEOUT_SECONDS)
            response.raise_for_status()
            return response.text
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("18mh HLS request failed.")


def read_hls_chunk(url: str, referer: str, size: int) -> bytes:
    last_error: Exception | None = None
    for trust_env in (True, False):
        session = requests.Session()
        session.trust_env = trust_env
        try:
            with session.get(url, headers=headers(referer), timeout=MH18_REQUEST_TIMEOUT_SECONDS, stream=True) as response:
                response.raise_for_status()
                return next(response.iter_content(size), b"")
        except requests.RequestException as exc:
            last_error = exc
    raise last_error or ValueError("18mh HLS media request failed.")


def extract_meta_description(soup: BeautifulSoup) -> str:
    meta = soup.select_one('meta[name="description"], meta[property="og:description"]')
    return (meta.get("content") or "").strip() if meta else ""


def extract_page_id(url: str) -> str:
    match = re.search(r"/mv/detail/(\d+)", urlparse(url).path)
    if not match:
        return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
    return match.group(1)


def parse_list_page(base_url: str, page_url: str) -> tuple[list[dict], str | None]:
    soup = BeautifulSoup(fetch_html(page_url, base_url + "/mv/all"), "html.parser")
    items: list[dict] = []
    seen: set[str] = set()
    for list_item in soup.select("ul.dx-video-list > li"):
        link = list_item.select_one('a[href*="/mv/detail/"]')
        if not link or not link.get("href"):
            continue
        detail_url = urlunparse(urlparse(urljoin(page_url, link["href"]))._replace(fragment=""))
        if not re.search(r"/mv/detail/\d+/?$", urlparse(detail_url).path) or detail_url in seen:
            continue
        seen.add(detail_url)
        heading = list_item.select_one('a.block[href*="/mv/detail/"]') or link
        image = list_item.select_one("img")
        image_url = (image.get("data-src") or image.get("src")) if image else None
        items.append(
            {
                "url": detail_url,
                "page_id": extract_page_id(detail_url),
                "title": heading.get_text(" ", strip=True) if heading else "",
                "image": urljoin(page_url, image_url) if image_url else None,
                "raw_meta": list_item.get_text(" ", strip=True)[:500],
            }
        )

    next_url = None
    pager = soup.select_one("ul.dx-pager")
    if pager:
        try:
            rec_total = int(pager.get("data-rec-total") or 0)
            rec_per_page = max(1, int(pager.get("data-rec-per-page") or 48))
            page_match = re.search(r"/page/(\d+)/?$", urlparse(page_url).path)
            current_page = int(page_match.group(1)) if page_match else 1
            total_pages = (rec_total + rec_per_page - 1) // rec_per_page
            data_link = (pager.get("data-link") or "/mv/all").rstrip("/")
            if current_page < total_pages:
                next_url = urljoin(base_url + "/", f"{data_link.lstrip('/')}/page/{current_page + 1}")
        except (TypeError, ValueError):
            next_url = None

    if next_url:
        parsed_base = urlparse(base_url)
        parsed_next = urlparse(next_url)
        if parsed_next.netloc and parsed_next.netloc.lower() != parsed_base.netloc.lower():
            next_url = None
    return items, next_url


def extract_detail_data(html: str) -> dict:
    marker_index = html.find("const _detail_")
    if marker_index < 0:
        raise ValueError("18mh detail payload not found.")
    object_index = html.find("{", marker_index)
    if object_index < 0:
        raise ValueError("18mh detail payload has no object.")
    payload, _ = json.JSONDecoder().raw_decode(html[object_index:])
    if not isinstance(payload, dict):
        raise ValueError("18mh detail payload is not an object.")
    return payload


def normalize_asset_url(base_url: str, value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        return f"https:{raw}"
    return urljoin(base_url + "/", raw)


def detail_tags(payload: dict) -> list[str]:
    tags = []
    for tag in payload.get("tag") or []:
        title = tag.get("title") if isinstance(tag, dict) else None
        if isinstance(title, str) and title.strip():
            tags.append(title.strip())
    category = payload.get("category_Str")
    if isinstance(category, str) and category.strip():
        tags.append(category.strip())
    seen: set[str] = set()
    deduped = []
    for tag in tags:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(tag)
    return deduped


def is_ad_payload(payload: dict) -> bool:
    for key in ("is_ad", "isAd", "is_advertisement", "ad"):
        value = payload.get(key)
        if value is True or str(value).strip().lower() in {"1", "true", "y", "yes"}:
            return True
    return False


def parse_detail_page(detail_url: str, list_item: dict | None = None) -> dict:
    html = fetch_html(detail_url, (list_item or {}).get("url") or detail_url)
    soup = BeautifulSoup(html, "html.parser")
    payload = extract_detail_data(html)
    if is_ad_payload(payload):
        raise ValueError("18mh detail payload is marked as ad.")
    page_id = str(payload.get("id") or extract_page_id(detail_url)).strip()
    if page_id != extract_page_id(detail_url):
        raise ValueError("18mh detail payload id does not match URL.")
    title = str(payload.get("title") or "").strip() or (list_item or {}).get("title") or "禁漫天堂视频"
    description = str(payload.get("summary") or payload.get("origin_summary") or "").strip() or extract_meta_description(soup) or title
    published_at = parse_18mh_datetime(str(payload.get("created_at") or "")) or (list_item or {}).get("published_at") or now_utc()
    modified_at = parse_18mh_datetime(str(payload.get("updated_at") or ""))
    duration = int(payload.get("duration") or 0)
    if duration and duration < MH18_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("18mh detail payload duration is too short for a real video.")
    image = (
        normalize_asset_url(detail_url, payload.get("cover") if isinstance(payload.get("cover"), str) else None)
        or normalize_asset_url(detail_url, payload.get("frontend_cover") if isinstance(payload.get("frontend_cover"), str) else None)
        or normalize_asset_url(detail_url, payload.get("backend_cover") if isinstance(payload.get("backend_cover"), str) else None)
        or (list_item or {}).get("image")
    )
    video_url = str(payload.get("url") or payload.get("view_url") or "").strip()
    if not video_url or ".m3u8" not in video_url.lower():
        raise ValueError("18mh detail payload is missing HLS video URL.")
    tags = detail_tags(payload)
    player = {
        "guid": f"18mh:{page_id}",
        "page_id": page_id,
        "player_index": 1,
        "video_id": page_id,
        "video_title": title,
        "video_url": video_url,
        "video_type": "hls",
        "tags": tags,
    }
    return {
        "url": detail_url,
        "page_id": page_id,
        "title": title,
        "description": description,
        "image": image,
        "published_at": published_at,
        "modified_at": modified_at,
        "duration": duration,
        "view_count": payload.get("view_count_raw"),
        "created_at_label": payload.get("created_at_str"),
        "category": payload.get("category_Str"),
        "tags": tags,
        "players": [player],
    }


def parse_auth_key_expiry(video_url: str) -> datetime | None:
    auth_key = parse_qs(urlparse(video_url).query).get("auth_key", [None])[0]
    if not auth_key:
        return None
    first_part = auth_key.split("-", 1)[0]
    try:
        return datetime.fromtimestamp(int(first_part), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def video_url_expires_at(video_url: str) -> datetime:
    parsed = parse_auth_key_expiry(video_url)
    if not parsed:
        return MH18_STABLE_VIDEO_URL_EXPIRES_AT
    if parsed <= now_utc() + timedelta(minutes=MH18_CRITICAL_WINDOW_MINUTES):
        return now_utc() + timedelta(minutes=MH18_REFRESH_WINDOW_MINUTES)
    return parsed


def verify_hls_url(video_url: str, referer: str) -> dict:
    parsed = urlparse(video_url)
    if parsed.scheme not in {"http", "https"} or not parsed.path.endswith(".m3u8"):
        raise ValueError("18mh video URL must be an HLS .m3u8 URL.")
    playlist = fetch_hls_text(video_url, referer)
    if "#EXTM3U" not in playlist or "#EXTINF" not in playlist:
        raise ValueError("18mh HLS playlist is not playable media.")
    durations = [float(value) for value in re.findall(r"#EXTINF:([0-9.]+)", playlist)]
    total_duration = sum(durations)
    if durations and total_duration < MH18_MIN_VIDEO_DURATION_SECONDS:
        raise ValueError("18mh HLS playlist is too short for a real video.")
    media_urls = re.findall(r'https?://[^\s"\']+', playlist)
    if not media_urls:
        raise ValueError("18mh HLS playlist has no absolute media URLs.")

    checked_key = False
    checked_segment = False
    has_key = any(urlparse(media_url).path.lower().endswith(".key") for media_url in media_urls)
    key_error: Exception | None = None
    segment_error: Exception | None = None
    for media_url in media_urls:
        media_path = urlparse(media_url).path.lower()
        if not checked_key and media_path.endswith(".key"):
            try:
                chunk = read_hls_chunk(media_url, referer, 16)
                if len(chunk) != 16:
                    raise ValueError("18mh HLS key is not readable.")
                checked_key = True
            except Exception as exc:
                key_error = exc
                continue
        if not checked_segment and media_path.endswith(".ts"):
            try:
                chunk = read_hls_chunk(media_url, referer, 64)
                if not chunk:
                    raise ValueError("18mh HLS segment is not readable.")
                checked_segment = True
            except Exception as exc:
                segment_error = exc
                continue
        if (checked_key or not has_key) and checked_segment:
            break
    if has_key and not checked_key:
        raise ValueError(f"18mh HLS playlist has no readable key: {key_error}")
    if not checked_segment:
        raise ValueError(f"18mh HLS playlist has no readable segment: {segment_error}")
    return {
        "video_url": video_url,
        "video_url_expires_at": video_url_expires_at(video_url),
        "playlist_bytes": len(playlist.encode("utf-8")),
        "playlist_duration_seconds": total_duration,
        "media_url_count": len(media_urls),
    }


def upsert_target(conn, base_url: str) -> dict:
    value = normalize_18mh_target_value(base_url)
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO targets (source, kind, value, normalized_value)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (source, kind, normalized_value)
            DO UPDATE SET value = EXCLUDED.value
            RETURNING id, source, kind, value, normalized_value
            """,
            (MH18_SOURCE, MH18_KIND, value, normalize_site_target_key(value)),
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
            (target_row["id"], Jsonb([MH18_SITE_NAME, "18MH", "视频"]), public_pool),
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


def build_author_presentation(target_row: dict, link: str) -> dict[str, str | None]:
    return {
        "display_author": MH18_SITE_NAME,
        "display_handle": None,
        "author_profile_url": link,
        "author_profile_platform": MH18_SITE_NAME,
    }


def upsert_video_item(conn, target_row: dict, detail: dict, player: dict, verified: dict, retention_hours: int) -> bool:
    published_at = detail.get("published_at") or now_utc()
    expires_at = published_at + timedelta(hours=retention_hours)
    content = detail.get("description") or detail.get("title") or player.get("video_title")
    images = [detail["image"]] if detail.get("image") else []
    presentation = build_author_presentation(target_row, detail["url"])
    metadata = {
        "target": format_target_row(target_row),
        "target_type": MH18_KIND,
        "target_value": target_row["value"],
        "site_name": MH18_SITE_NAME,
        "source_url": detail["url"],
        "page_id": detail["page_id"],
        "player_index": player["player_index"],
        "page_video_count": len(detail.get("players") or []),
        "mh18_video_id": player["video_id"],
        "video_type": player["video_type"],
        "video_poster_url": detail.get("image"),
        "duration": detail.get("duration"),
        "view_count": detail.get("view_count"),
        "category": detail.get("category"),
        "created_at_label": detail.get("created_at_label"),
        "tags": detail.get("tags") or player.get("tags") or [],
        "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else None,
        "resolver": "18mh-detail-script",
        "resolved_at": now_iso(),
        "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
        "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
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
        author=MH18_SITE_NAME,
        fullname=MH18_SITE_NAME,
        x_url=None,
        link=detail["url"],
        images=images,
    )
    return inserted


def monitor_site(conn, *, base_url: str, max_pages: int, retention_hours: int, public_pool: bool, dry_run: bool = False) -> dict:
    base_url = normalize_18mh_target_value(base_url)
    target_row = None if dry_run else ensure_target(conn, base_url, public_pool=public_pool)
    page_url = urljoin(base_url + "/", "mv/all")
    cutoff = now_utc() - timedelta(hours=retention_hours)
    inserted = updated = parsed_videos = verified_count = skipped_existing = skipped_detail_errors = skipped_unverified = skipped_old = pages = 0
    samples = []
    latest_guid = None
    for _ in range(max_pages):
        pages += 1
        list_items, next_url = parse_list_page(base_url, page_url)
        page_inserted = page_existing = page_old = page_updated = page_verified = page_detail_errors = page_unverified = page_parsed_videos = 0
        print(f"[18mh] page={pages} list_items={len(list_items)} url={page_url}")
        if not list_items:
            print(f"[18mh] page={pages} empty_list stop=true")
            break
        for list_item in list_items:
            try:
                detail = parse_detail_page(list_item["url"], list_item)
            except Exception as exc:
                skipped_detail_errors += 1
                page_detail_errors += 1
                print(f"[18mh] skip detail {list_item.get('url')}: {exc}")
                continue
            if detail.get("published_at") and detail["published_at"] < cutoff:
                skipped_old += 1
                page_old += 1
                continue
            page_parsed_videos += len(detail["players"])
            parsed_videos += len(detail["players"])
            for player in detail["players"]:
                latest_guid = latest_guid or player["guid"]
                if target_row and item_exists_for_guid(conn, str(target_row["id"]), player["guid"]):
                    page_existing += 1
                    skipped_existing += 1
                    continue
                try:
                    verified = verify_hls_url(player["video_url"], detail["url"])
                except Exception as exc:
                    skipped_unverified += 1
                    page_unverified += 1
                    print(f"[18mh] skip unverified {player['guid']}: {exc}")
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
            f"[18mh] page={pages} parsed_videos={page_parsed_videos} verified={page_verified} "
            f"inserted={page_inserted} updated={page_updated} existing={page_existing} old={page_old} "
            f"detail_errors={page_detail_errors} unverified={page_unverified}"
        )
        if not next_url:
            break
        if page_inserted == 0 and (page_existing > 0 or page_old == len(list_items)):
            break
        page_url = next_url
    return {"pages": pages, "parsed_videos": parsed_videos, "verified": verified_count, "inserted": inserted, "updated": updated, "skipped_existing": skipped_existing, "skipped_detail_errors": skipped_detail_errors, "skipped_unverified": skipped_unverified, "skipped_old": skipped_old, "samples": samples[:10]}


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = 0
    queries = [
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC LIMIT %s""", (MH18_SOURCE, critical_window_minutes, limit)),
        ("""SELECT i.* FROM items i INNER JOIN targets t ON t.id = i.target_id WHERE t.source = %s AND i.expires_at > NOW() AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval ORDER BY i.video_url_expires_at ASC, i.published_at DESC LIMIT %s""", (MH18_SOURCE, refresh_window_minutes, limit)),
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
            source_url = metadata.get("source_url")
            video_id = metadata.get("mh18_video_id") or str(row["guid"]).replace("18mh:", "", 1)
            try:
                if not source_url or not video_id:
                    raise ValueError("missing source_url or mh18_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_hls_url(player["video_url"], detail["url"])
                next_metadata = metadata | {
                    "resolver": "18mh-detail-script",
                    "resolved_at": now_iso(),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                }
                refresh_item_playback_in_opensearch(
                    conn,
                    item_id=row_id,
                    video_url=verified["video_url"],
                    video_url_expires_at=verified["video_url_expires_at"],
                    metadata=next_metadata,
                    cover_url=detail.get("image") or metadata.get("video_poster_url"),
                )
                refreshed += 1
            except Exception as exc:
                failed += 1
                print(f"[18mh] refresh failed for {row['guid']}: {exc}")
            conn.commit()
    return {"processed": processed, "refreshed": refreshed, "failed": failed}
