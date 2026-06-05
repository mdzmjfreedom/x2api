from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from collector.twitter_monitor import (
    build_author_presentation,
    build_item_author_presentation,
    insert_items,
    upsert_baoliao51_video_item,
    upsert_cg91_video_item,
    upsert_douyin_video_item,
    upsert_heiliao_video_item,
    upsert_resolved_youtube_item,
)


class FakeCursor:
    rowcount = 1

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        self.sql = sql
        self.params = params
        self.assert_placeholder_count(sql, params)

    @staticmethod
    def assert_placeholder_count(sql, params):
        expected = sql.count("%s")
        actual = len(params)
        if expected != actual:
            raise AssertionError(f"SQL placeholder mismatch: expected {expected}, got {actual}")

    def fetchone(self):
        return {"id": "item-id", "inserted": True}


class FakeConnection:
    def cursor(self):
        return FakeCursor()


class AuthorPresentationTest(unittest.TestCase):
    def test_twitter_alias_builds_x_profile(self):
        presentation = build_author_presentation(
            source="x",
            target="search:AI",
            author="@openai",
            fullname="OpenAI",
            x_url="https://x.com/openai/status/1",
            link=None,
        )

        self.assertEqual(
            presentation,
            {
                "display_author": "OpenAI",
                "display_handle": "@openai",
                "author_profile_url": "https://x.com/openai",
                "author_profile_platform": "X",
            },
        )

    def test_youtube_alias_builds_channel_profile(self):
        presentation = build_author_presentation(
            source="yt",
            target="youtube:UC12345678901234567890",
            author="Channel",
            fullname="Channel",
            x_url=None,
            link="https://www.youtube.com/watch?v=abc123",
        )

        self.assertEqual(
            presentation,
            {
                "display_author": "Channel",
                "display_handle": None,
                "author_profile_url": "https://www.youtube.com/channel/UC12345678901234567890",
                "author_profile_platform": "YouTube",
            },
        )

    def test_unsupported_site_source_has_no_clickable_profile(self):
        presentation = build_item_author_presentation(
            {"source": "cg91", "kind": "site", "value": "https://www.91cg1.com"},
            author="91吃瓜网",
            fullname="91吃瓜网",
            x_url=None,
            link="https://www.91cg1.com/post/1",
        )

        self.assertEqual(presentation["display_author"], "91吃瓜网")
        self.assertIsNone(presentation["display_handle"])
        self.assertIsNone(presentation["author_profile_url"])
        self.assertIsNone(presentation["author_profile_platform"])

    def test_collector_insert_sql_params_stay_aligned(self):
        conn = FakeConnection()
        now = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
        verified = {
            "video_url": "https://media.example/video.m3u8",
            "video_url_expires_at": now + timedelta(hours=1),
        }
        detail = {
            "url": "https://www.91cg1.com/post/1",
            "page_id": "1",
            "title": "Title",
            "description": "Description",
            "image": "https://static.example/image.jpg",
            "published_at": now,
            "modified_at": now,
            "players": [{"video_id": "video-1"}],
        }
        player = {
            "guid": "site:video:1",
            "player_index": 0,
            "video_id": "video-1",
            "video_type": "hls",
            "tags": ["视频"],
            "video_title": "Video title",
        }

        inserted = insert_items(
            conn,
            {"id": "target-id", "source": "twitter", "kind": "user", "value": "openai"},
            [
                {
                    "guid": "tweet-1",
                    "author": "@openai",
                    "fullname": "OpenAI",
                    "content": "Video",
                    "raw_content": "Video",
                    "translated_content": None,
                    "link": "https://x.com/openai/status/1",
                    "x_url": "https://x.com/openai/status/1",
                    "images": [],
                    "video_url": "https://video.example/video.mp4",
                    "published": now.isoformat(),
                    "stored_at": now.isoformat(),
                    "is_retweet": False,
                }
            ],
            None,
        )
        self.assertEqual(inserted, 1)

        self.assertEqual(
            upsert_resolved_youtube_item(
                conn,
                {
                    "target_id": "target-id",
                    "payload": {
                        "channel_id": "UC12345678901234567890",
                        "provider_video_id": "abc123",
                        "guid": "yt:video:abc123",
                        "author": "Channel",
                        "fullname": "Channel",
                        "title": "Video",
                        "content": "Video",
                        "raw_content": "Video",
                        "link": "https://www.youtube.com/watch?v=abc123",
                        "images": [],
                        "expires_at": (now + timedelta(hours=3)).isoformat(),
                        "published_at": now.isoformat(),
                    },
                },
                {"video_url": "https://media.example/video.mp4", "video_url_expires_at": now + timedelta(hours=1)},
            ),
            "item-id",
        )

        self.assertTrue(upsert_heiliao_video_item(conn, {"id": "target-id", "source": "heiliao", "kind": "site", "value": "https://among.uvsoskqus.cc"}, detail, player, verified, 84))
        self.assertTrue(upsert_cg91_video_item(conn, {"id": "target-id", "source": "cg91", "kind": "site", "value": "https://www.91cg1.com"}, detail, player, verified, 84))
        self.assertTrue(upsert_baoliao51_video_item(conn, {"id": "target-id", "source": "baoliao51", "kind": "site", "value": "https://www.51baoliao01.com"}, detail, player, verified, 84))
        self.assertTrue(
            upsert_douyin_video_item(
                conn,
                {"id": "target-id", "source": "douyin", "kind": "site", "value": "https://xygrfrfb3g.b2h7y8w.com"},
                {
                    "guid": "douyin:1",
                    "source_url": "https://xygrfrfb3g.b2h7y8w.com/v/1",
                    "title": "Video",
                    "description": "Description",
                    "image": "https://static.example/image.jpg",
                    "published_at": now,
                    "id": "1",
                    "video_id": "video-1",
                    "play_links": [],
                    "tags": [],
                },
                verified,
                84,
            )
        )


if __name__ == "__main__":
    unittest.main()
