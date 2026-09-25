import unittest
from unittest.mock import Mock, patch

from steam_agent.memory.reindex import rebuild_memory_index


class MemoryReindexTests(unittest.TestCase):
    def test_rebuild_uses_deterministic_ids_and_upsert(self):
        collection = Mock()
        turns = [{
            "user_id": "u1",
            "thread_id": "t1",
            "turn_number": 2,
            "user_message": "喜欢策略",
            "assistant_reply": "记住了",
            "timestamp": "2026-01-01",
        }]
        with (
            patch("steam_agent.memory.reindex.get_all_conversation_turns", return_value=turns),
            patch("steam_agent.memory.reindex.get_user_memory_collection", return_value=collection),
            patch("steam_agent.memory.reindex.embed_memory", return_value=[[0.1, 0.2]]),
        ):
            result = rebuild_memory_index()

        self.assertEqual(result["turns_indexed"], 1)
        self.assertEqual(collection.upsert.call_args.kwargs["ids"], ["u1:t1:2"])


if __name__ == "__main__":
    unittest.main()
