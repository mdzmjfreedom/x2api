from __future__ import annotations

import unittest
from types import SimpleNamespace

from collector.twitter_monitor import lock_key_for_source, lock_name_for_command


class LockNameTest(unittest.TestCase):
    def test_monitor_lock_uses_action_prefix(self):
        args = SimpleNamespace(shard_index=None, shard_count=None)
        self.assertEqual(lock_name_for_command("command_monitor_91cg", args), "monitor-91cg")

    def test_refresh_lock_uses_action_prefix(self):
        args = SimpleNamespace(shard_index=None, shard_count=None)
        self.assertEqual(lock_name_for_command("command_refresh_91cg_playback_urls", args), "refresh-91cg")

    def test_sharded_monitor_lock_includes_shard(self):
        args = SimpleNamespace(shard_index=2, shard_count=4)
        self.assertEqual(lock_name_for_command("command_monitor", args), "monitor-twitter-shard-2-of-4")

    def test_sharded_refresh_lock_includes_shard(self):
        args = SimpleNamespace(shard_index=1, shard_count=4)
        self.assertEqual(lock_name_for_command("command_refresh_youtube_playback_urls", args), "refresh-youtube-shard-1-of-4")

    def test_unknown_sources_hash_to_distinct_db_keys(self):
        self.assertNotEqual(lock_key_for_source("monitor-91cg"), lock_key_for_source("refresh-91cg"))
        self.assertNotEqual(lock_key_for_source("monitor-91cg-shard-0-of-4"), lock_key_for_source("monitor-91cg-shard-1-of-4"))


if __name__ == "__main__":
    unittest.main()
