import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steam_agent.memory import insight_store


class InsightQualityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.directory.name) / "memory.db")
        self.path_patch = patch.object(insight_store, "SQLITE_DB_PATH", self.db_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.directory.cleanup()

    def test_exact_memory_is_deduplicated(self):
        first = insight_store.add_insight("u1", "用户喜欢策略游戏", "preference")
        second = insight_store.add_insight("u1", "用户喜欢策略游戏", "preference")

        self.assertEqual(first["status"], "inserted")
        self.assertEqual(second["status"], "deduplicated")
        self.assertEqual(len(insight_store.get_insights("u1")), 1)

    def test_new_opposite_preference_supersedes_old_memory(self):
        first = insight_store.add_insight("u1", "用户喜欢策略游戏", "preference")
        second = insight_store.add_insight("u1", "用户不喜欢策略游戏", "preference")

        active = insight_store.get_insights("u1")
        self.assertEqual(second["status"], "superseded")
        self.assertEqual(second["superseded"], [first["insight_id"]])
        self.assertEqual([item["insight"] for item in active], ["用户不喜欢策略游戏"])

    def test_expired_temporary_memory_is_not_recalled(self):
        insight_store.add_insight(
            "u1",
            "用户这周想玩恐怖游戏",
            "preference",
            scope="temporary",
            ttl_days=-1,
        )

        self.assertEqual(insight_store.get_insights("u1"), [])

    def test_recall_orders_by_confidence_and_honors_limit(self):
        insight_store.add_insight("u1", "用户有 Steam Deck", "fact", confidence=0.6)
        insight_store.add_insight("u1", "用户预算不超过50元", "constraint", confidence=1.0)

        result = insight_store.get_insights("u1", limit=1)

        self.assertEqual(result[0]["category"], "constraint")
        self.assertEqual(result[0]["source"], "explicit_user")


if __name__ == "__main__":
    unittest.main()
