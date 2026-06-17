from __future__ import annotations

from psycopg.types.json import Jsonb

try:
    from collector.baoliao51_source import (
        BAOLIAO51_SOURCE,
        now_iso,
        parse_detail_page,
        verify_playback_url,
    )
except ModuleNotFoundError:
    from baoliao51_source import (
        BAOLIAO51_SOURCE,
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
              AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC
            LIMIT %s
            """,
            (BAOLIAO51_SOURCE, critical_window_minutes, limit),
        ),
        (
            """
            SELECT i.*
            FROM items i
            INNER JOIN targets t ON t.id = i.target_id
            WHERE t.source = %s
              AND i.expires_at > NOW()
              AND COALESCE((i.metadata->>'playback_refresh_required')::boolean, false) = TRUE
              AND i.video_url_expires_at <= NOW() + (%s || ' minutes')::interval
            ORDER BY i.video_url_expires_at ASC, i.published_at DESC
            LIMIT %s
            """,
            (BAOLIAO51_SOURCE, refresh_window_minutes, limit),
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
            video_id = metadata.get("baoliao51_video_id")
            try:
                if not source_url or not video_id:
                    raise ValueError("missing source_url or baoliao51_video_id")
                detail = parse_detail_page(
                    source_url,
                    {
                        "guid": row["guid"],
                        "page_id": metadata.get("page_id"),
                        "url": source_url,
                        "title": source.get("title"),
                        "published_at": row.get("published_at"),
                    },
                )
                player = next((candidate for candidate in detail["players"] if candidate["video_id"] == video_id), None)
                if not player:
                    raise ValueError("matching player not found")
                verified = verify_playback_url(player["video_url"], player.get("referer") or detail["url"], player["video_type"])
                if not verified.get("playback_refresh_required"):
                    skipped_static += 1
                next_metadata = metadata | {
                    "resolver": "baoliao51-dplayer",
                    "resolved_at": now_iso(),
                    "source_url": detail["url"],
                    "page_id": detail["page_id"],
                    "baoliao51_video_id": player["video_id"],
                    "raw_video_url": verified.get("raw_video_url"),
                    "variant_url": verified.get("variant_url"),
                    "playback_headers": verified.get("playback_headers"),
                    "video_url_expires_at": verified["video_url_expires_at"].isoformat(),
                    "playback_refresh_required": verified.get("playback_refresh_required"),
                    "media_format": verified.get("media_format"),
                    "date_modified": detail.get("modified_at").isoformat() if detail.get("modified_at") else metadata.get("date_modified"),
                    "playlist_duration_seconds": verified.get("playlist_duration_seconds"),
                    "playlist_bytes": verified.get("playlist_bytes"),
                    "master_playlist_bytes": verified.get("master_playlist_bytes"),
                    "media_url_count": verified.get("media_url_count"),
                    "key_url_count": verified.get("key_url_count"),
                    "encrypted": verified.get("encrypted"),
                    "content_type": verified.get("content_type"),
                    "content_length": verified.get("content_length"),
                }
                refresh_item_playback_in_opensearch(
                    conn,
                    item_id=row_id,
                    video_url=verified["video_url"],
                    video_url_expires_at=verified["video_url_expires_at"],
                    metadata=next_metadata,
                    playback_headers=verified.get("playback_headers"),
                    cover_url=detail.get("image") or metadata.get("video_poster_url"),
                )
                conn.commit()
                refreshed += 1
            except Exception as exc:
                failed += 1
                conn.rollback()
                print(f"[51baoliao] refresh failed for {row['guid']}: {exc}")
    return {"processed": processed, "refreshed": refreshed, "failed": failed, "skipped_static": skipped_static}
