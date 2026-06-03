from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import UUID

from collector.twitter_monitor import (
    make_youtube_queue_payload,
    parse_youtube_relative_datetime,
    youtube_lockup_entry,
)


class YouTubeVideosFallbackTest(unittest.TestCase):
    def setUp(self):
        self.reference = datetime(2026, 6, 3, 12, 0, tzinfo=timezone.utc)

    def test_parses_relative_publish_times(self):
        self.assertEqual(
            parse_youtube_relative_datetime("3d ago", self.reference).isoformat(),
            "2026-05-31T12:00:00+00:00",
        )
        self.assertEqual(
            parse_youtube_relative_datetime("3 days ago", self.reference).isoformat(),
            "2026-05-31T12:00:00+00:00",
        )
        self.assertEqual(
            parse_youtube_relative_datetime("3 日前", self.reference).isoformat(),
            "2026-05-31T12:00:00+00:00",
        )
        self.assertEqual(
            parse_youtube_relative_datetime("2 hours ago", self.reference).isoformat(),
            "2026-06-03T10:00:00+00:00",
        )

    def test_lockup_entry_matches_rss_entry_shape(self):
        lockup = {
            "contentId": "QH838aWyhAk",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "contentImage": {
                "thumbnailViewModel": {
                    "image": {
                        "sources": [
                            {"url": "https://i.ytimg.com/vi/QH838aWyhAk/small.jpg", "width": 168, "height": 94},
                            {"url": "https://i.ytimg.com/vi/QH838aWyhAk/large.jpg", "width": 336, "height": 188},
                        ]
                    }
                }
            },
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "几家欢喜几家愁 诸葛宇杰去哪了？"},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {
                                    "metadataParts": [
                                        {"text": {"content": "117K"}, "accessibilityLabel": "117 thousand views"},
                                        {"text": {"content": "3d ago"}, "accessibilityLabel": "3 days ago"},
                                    ]
                                }
                            ]
                        }
                    },
                }
            },
        }

        entry = youtube_lockup_entry(lockup, channel_title="新官场", fetched_at=self.reference)

        self.assertIsNotNone(entry)
        self.assertEqual(entry["yt_videoid"], "QH838aWyhAk")
        self.assertEqual(entry["title"], "几家欢喜几家愁 诸葛宇杰去哪了？")
        self.assertEqual(entry["author"], "新官场")
        self.assertEqual(entry["link"], "https://www.youtube.com/watch?v=QH838aWyhAk")
        self.assertEqual(entry["published"], "2026-05-31T12:00:00+00:00")
        self.assertEqual(entry["media_thumbnail"][0]["url"], "https://i.ytimg.com/vi/QH838aWyhAk/large.jpg")

        payload = make_youtube_queue_payload(
            {"id": UUID("01234567-89ab-cdef-0123-456789abcdef"), "value": "UC1QxOK5YpyAyFCN_xiPfgHw"},
            entry,
            self.reference,
        )

        self.assertIsNotNone(payload)
        self.assertEqual(payload["target_id"], "01234567-89ab-cdef-0123-456789abcdef")
        self.assertEqual(payload["guid"], "yt:video:QH838aWyhAk")
        self.assertEqual(payload["channel_id"], "UC1QxOK5YpyAyFCN_xiPfgHw")
        self.assertEqual(payload["author"], "新官场")
        self.assertEqual(payload["video_poster_url"], "https://i.ytimg.com/vi/QH838aWyhAk/large.jpg")

    def test_stale_lockup_is_not_queued(self):
        lockup = {
            "contentId": "QH838aWyhAk",
            "contentType": "LOCKUP_CONTENT_TYPE_VIDEO",
            "metadata": {
                "lockupMetadataViewModel": {
                    "title": {"content": "Old video"},
                    "metadata": {
                        "contentMetadataViewModel": {
                            "metadataRows": [
                                {"metadataParts": [{"text": {"content": "4d ago"}, "accessibilityLabel": "4 days ago"}]}
                            ]
                        }
                    },
                }
            },
        }

        entry = youtube_lockup_entry(lockup, channel_title="新官场", fetched_at=self.reference)
        payload = make_youtube_queue_payload(
            {"id": "target-id", "value": "UC1QxOK5YpyAyFCN_xiPfgHw"},
            entry,
            self.reference,
        )

        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
