from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import sys
import types

fake_opensearchpy = types.ModuleType("opensearchpy")
fake_opensearchpy.OpenSearch = object
fake_opensearchpy.helpers = types.SimpleNamespace(bulk=None)
sys.modules.setdefault("opensearchpy", fake_opensearchpy)

from collector import sync_to_opensearch as sync


class SyncToOpenSearchTests(unittest.TestCase):
    def test_sync_items_uses_updated_at_checkpoint_and_persists_v2_meta(self):
        updated_at = datetime(2026, 6, 16, 21, 25, 54, 331546, tzinfo=timezone.utc)
        row = {
            "id": "item-2",
            "updated_at": updated_at,
            "stored_at": datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        }

        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [row]
        fake_cursor.__enter__.return_value = fake_cursor
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_conn.__enter__.return_value = fake_conn

        os_client = MagicMock()

        with patch.object(sync, "get_last_sync_checkpoint", return_value=("2026-06-16T21:21:44.020773+00:00", "item-1")) as get_checkpoint, \
             patch.object(sync.psycopg, "connect", return_value=fake_conn), \
             patch.object(sync, "build_document", return_value={"id": "item-2"}), \
             patch.object(sync.helpers, "bulk", return_value=(1, [])), \
             patch.object(sync, "set_last_sync_timestamp") as set_checkpoint:
            sync.sync_items(os_client, "postgres://example", full=False, limit=None, shard_index=1, shard_count=4)

        get_checkpoint.assert_called_once_with(os_client, meta_key="last_sync_v2_shard_1_of_4")
        executed_sql, executed_params = fake_cursor.execute.call_args.args
        self.assertIn("i.updated_at > %s OR (i.updated_at = %s AND i.id::text > %s)", executed_sql)
        self.assertIn("MOD(hashtext(i.id::text)::bigint + 2147483648, %s::int) = %s::int", executed_sql)
        self.assertIn("ORDER BY i.updated_at ASC, i.id ASC", executed_sql)
        self.assertEqual(
            executed_params,
            ["2026-06-16T21:21:44.020773+00:00", "2026-06-16T21:21:44.020773+00:00", "item-1", 4, 1],
        )
        set_checkpoint.assert_called_once_with(
            os_client,
            updated_at.isoformat(),
            meta_key="last_sync_v2_shard_1_of_4",
            item_id="item-2",
        )

    def test_sync_items_does_not_advance_checkpoint_when_bulk_has_errors(self):
        updated_at = datetime(2026, 6, 16, 21, 25, 54, 331546, tzinfo=timezone.utc)
        row = {
            "id": "item-2",
            "updated_at": updated_at,
            "stored_at": datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc),
        }

        fake_cursor = MagicMock()
        fake_cursor.fetchall.return_value = [row]
        fake_cursor.__enter__.return_value = fake_cursor
        fake_conn = MagicMock()
        fake_conn.cursor.return_value = fake_cursor
        fake_conn.__enter__.return_value = fake_conn

        os_client = MagicMock()

        with patch.object(sync, "get_last_sync_checkpoint", return_value=("2026-06-16T21:21:44.020773+00:00", "item-1")), \
             patch.object(sync.psycopg, "connect", return_value=fake_conn), \
             patch.object(sync, "build_document", return_value={"id": "item-2"}), \
             patch.object(sync.helpers, "bulk", return_value=(0, [{"index": {"error": "boom"}}])), \
             patch.object(sync, "set_last_sync_timestamp") as set_checkpoint:
            sync.sync_items(os_client, "postgres://example", full=False, limit=None, shard_index=0, shard_count=1)

        set_checkpoint.assert_not_called()

    def test_stable_shard_is_deterministic(self):
        self.assertEqual(sync.stable_shard("item-123", 4), sync.stable_shard("item-123", 4))
        self.assertIn(sync.stable_shard("item-123", 4), {0, 1, 2, 3})


if __name__ == "__main__":
    unittest.main()
