from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from collector import opensearch_items


class OpenSearchItemsTest(unittest.TestCase):
    def test_update_item_document_sends_tags_and_images(self):
        fake_client = MagicMock()

        with patch.object(opensearch_items, "is_opensearch_write_enabled", return_value=True), \
             patch.object(opensearch_items, "get_client", return_value=fake_client):
            updated = opensearch_items.update_item_document(
                "item-1",
                title="Title",
                tags=["News", "news", " Video "],
                images=["https://example.com/1.jpg", "", None],
            )

        self.assertTrue(updated)
        fake_client.update.assert_called_once()
        payload = fake_client.update.call_args.kwargs["body"]["doc"]
        self.assertEqual(payload["title"], "Title")
        self.assertEqual(payload["tags"], ["news", "video"])
        self.assertEqual(payload["images"], ["https://example.com/1.jpg"])


if __name__ == "__main__":
    unittest.main()
