import unittest
from unittest.mock import Mock, patch

from steam_agent.rag.fusion import reciprocal_rank_fusion
from steam_agent.rag.hybrid import hybrid_search
from steam_agent.rag.lexical import BM25Index, LexicalDocument


class LexicalRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.documents = [
            LexicalDocument("10", "Hades action roguelike underworld", {"is_free": False}),
            LexicalDocument("20", "Card strategy deck building", {"is_free": True}),
            LexicalDocument("30", "Farm cozy life simulation", {"is_free": False}),
        ]
        self.index = BM25Index(self.documents)

    def test_bm25_prefers_exact_lexical_match(self):
        rows = self.index.search("Hades roguelike", 3)

        self.assertEqual(rows[0][0].doc_id, "10")

    def test_bm25_applies_metadata_predicate(self):
        rows = self.index.search(
            "card strategy",
            3,
            predicate=lambda metadata: not metadata["is_free"],
        )

        self.assertEqual(rows, [])

    def test_rrf_rewards_documents_present_in_both_rankings(self):
        fused = reciprocal_rank_fusion([["10", "20"], ["20", "30"]])

        self.assertEqual(fused[0][0], "20")


class HybridPipelineTests(unittest.TestCase):
    def test_pipeline_fuses_dense_and_lexical_then_reranks(self):
        documents = {
            "10": LexicalDocument("10", "action roguelike", {"name": "A"}),
            "20": LexicalDocument("20", "card roguelike", {"name": "B"}),
        }
        index = BM25Index(list(documents.values()))
        collection = Mock()
        collection.query.return_value = {
            "ids": [["10", "20"]],
            "distances": [[0.2, 0.3]],
        }
        with (
            patch("steam_agent.rag.hybrid._get_lexical_index", return_value=(index, documents)),
            patch("steam_agent.rag.hybrid.get_games_collection", return_value=collection),
            patch("steam_agent.rag.hybrid.embed_query", return_value=[[0.1, 0.2]]),
            patch("steam_agent.rag.hybrid.rerank", return_value=([0.1, 0.9], True)),
        ):
            result = hybrid_search("roguelike", top_k=2, reranker_weight=1.0)

        self.assertEqual(result["results"][0]["appid"], "20")
        self.assertTrue(result["retrieval"]["reranker_used"])
        self.assertIn("dense_ms", result["retrieval"]["timings"])

    def test_ablation_flags_skip_disabled_stages(self):
        documents = {
            "10": LexicalDocument("10", "exact roguelike", {"name": "A"}),
        }
        index = BM25Index(list(documents.values()))
        with (
            patch("steam_agent.rag.hybrid._get_lexical_index", return_value=(index, documents)),
            patch("steam_agent.rag.hybrid.get_games_collection") as collection,
            patch("steam_agent.rag.hybrid.rerank") as reranker,
        ):
            result = hybrid_search(
                "exact roguelike",
                top_k=1,
                use_dense=False,
                use_lexical=True,
                use_reranker=False,
            )

        collection.assert_not_called()
        reranker.assert_not_called()
        self.assertEqual(result["results"][0]["appid"], "10")
        self.assertFalse(result["retrieval"]["mode"]["dense"])


if __name__ == "__main__":
    unittest.main()
