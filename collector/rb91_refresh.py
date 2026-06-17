from __future__ import annotations

from psycopg.types.json import Jsonb

try:
    from collector.rb91_source import (
        RB91_DEFAULT_BASE_URL,
        RB91_SOURCE,
        detail_url,
        now_iso,
        parse_detail_page,
        verify_playback_url,
    )
except ModuleNotFoundError:
    from rb91_source import (
        RB91_DEFAULT_BASE_URL,
        RB91_SOURCE,
        detail_url,
        now_iso,
        parse_detail_page,
        verify_playback_url,
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
              AND COALESCE(i.metadata->>'playback_refresh_required', 'false') = 'true'
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (RB91_SOURCE, critical_window_minutes, limit),
        ),
        (
            """
            SELECT i.*
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s
              AND i.expires_at > NOW()
              AND COALESCE(i.metadata->>'playback_refresh_required', 'false') = 'true'
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (RB91_SOURCE, refresh_window_minutes, limit),
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
            source_url = metadata.get("source_url")
            video_id = metadata.get("rb91_video_id") or str(row["guid"]).replace(f"{RB91_SOURCE}:", "", 1).split(":", 1)[0]
            try:
                if not source_url and video_id:
                    source_url = detail_url(metadata.get("target_value") or RB91_DEFAULT_BASE_URL, video_id)
                if not source_url or not video_id:
                    raise ValueError("missing source_url or rb91_video_id")
                detail = parse_detail_page(source_url)
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"], detail.get("duration"))
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "91rb-public-player",
                    "resolved_at": now_iso(),
                    "playback_headers": verified.get("playback_headers"),
                    "source_url": detail["url"],
                    "rb91_video_id": detail["video_id"],
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "video_poster_url": detail.get("image") or metadata.get("video_poster_url"),
                    "duration": detail.get("duration") or metadata.get("duration"),
                    "tags": detail.get("tags") or metadata.get("tags") or [],
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                    "content_type": verified.get("content_type"),
                    "content_length": verified.get("content_length"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "variant_count": verified.get("variant_count"),
                    "selected_variant_url": verified.get("selected_variant_url"),
                    "selected_variant_stream_inf": verified.get("selected_variant_stream_inf"),
                }
                refresh_item_playback_in_opensearch(
                    conn,
                    item_id=row_id,
                    video_url=verified["video_url"],
                    video_url_expires_at=verified["video_url_expires_at"],
                    metadata=next_metadata,
                    playback_headers=verified.get("playback_headers"),
                    cover_url=detail.get("image") or source.get("cover_url") or metadata.get("video_poster_url"),
                )
                conn.commit()
                refreshed += 1
            except Exception as exc:
                failed += 1
                conn.rollback()
                print(f"[91rb] refresh failed for {row['guid']}: {exc}")
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
