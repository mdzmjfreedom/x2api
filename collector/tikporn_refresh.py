from __future__ import annotations

from psycopg.types.json import Jsonb

try:
    from collector.tikporn_source import (
        TIKPORN_DEFAULT_BASE_URL,
        TIKPORN_SOURCE,
        fetch_video_info,
        normalize_tikporn_item,
        now_iso,
        verify_hls_url,
    )
except ModuleNotFoundError:
    from tikporn_source import (
        TIKPORN_DEFAULT_BASE_URL,
        TIKPORN_SOURCE,
        fetch_video_info,
        normalize_tikporn_item,
        now_iso,
        verify_hls_url,
    )

try:
    from collector.opensearch_items import fetch_document
    from collector.opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch
except ModuleNotFoundError:
    from opensearch_items import fetch_document
    from opensearch_items import refresh_item_playback as refresh_item_playback_in_opensearch


def refresh_playback_urls(conn, limit: int, refresh_window_minutes: int, critical_window_minutes: int) -> dict[str, int]:
    processed = refreshed = failed = skipped_static = 0
    queries = [
        (
            """
            SELECT i.*
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s
              AND i.expires_at > NOW()
              AND COALESCE(i.metadata->>'playback_refresh_required', 'true') = 'true'
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (TIKPORN_SOURCE, critical_window_minutes, limit),
        ),
        (
            """
            SELECT i.*
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s
              AND i.expires_at > NOW()
              AND COALESCE(i.metadata->>'playback_refresh_required', 'true') = 'true'
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (TIKPORN_SOURCE, refresh_window_minutes, limit),
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
            source = fetch_document(row_id)
            base_url = metadata.get("target_value") or TIKPORN_DEFAULT_BASE_URL
            video_id = metadata.get("tikporn_video_id") or str(row["guid"]).replace(f"{TIKPORN_SOURCE}:", "", 1)
            try:
                detail_item = normalize_tikporn_item(base_url, fetch_video_info(video_id, base_url))
                if not detail_item:
                    raise ValueError("Tik.Porn detail API returned no playable item.")
                verified = verify_hls_url(detail_item["hls_url"], detail_item["source_url"], detail_item.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "tikporn-apiv2-detail",
                    "resolved_at": now_iso(),
                    "source_url": detail_item["source_url"],
                    "video_poster_url": detail_item.get("image") or metadata.get("video_poster_url"),
                    "duration": detail_item.get("duration") or metadata.get("duration"),
                    "view_count": detail_item.get("view_count"),
                    "like_count": detail_item.get("like_count"),
                    "watch_time": detail_item.get("watch_time"),
                    "mp4_url": detail_item.get("mp4_url"),
                    "mpd_url": detail_item.get("mpd_url"),
                    "download_url": detail_item.get("download_url"),
                    "videoexp": detail_item.get("videoexp"),
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
                refresh_item_playback_in_opensearch(
                    conn,
                    item_id=row_id,
                    video_url=verified["video_url"],
                    video_url_expires_at=verified["video_url_expires_at"],
                    metadata=next_metadata,
                    cover_url=detail_item.get("image") or source.get("cover_url") or metadata.get("video_poster_url"),
                )
                conn.commit()
                refreshed += 1
            except Exception as exc:
                failed += 1
                conn.rollback()
                print(f"[tikporn] refresh failed for {row['guid']}: {exc}")
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
