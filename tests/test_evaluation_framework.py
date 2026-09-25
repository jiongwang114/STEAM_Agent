import json
import tempfile
import unittest
from pathlib import Path

from evals.baseline import compare_summaries
from evals.metrics import (
    bootstrap_mean_ci,
    grounding_metrics,
    retrieval_metrics,
    recommendation_diversity,
    tool_trace_metrics,
    tool_argument_metrics,
)
from evals.judge import parse_judge_score
from evals.models import AgentCase, RetrievalCase, ToolExpectation, load_jsonl, stable_hash
from evals.validate import validate_datasets
from evals.runners.retrieval import run_retrieval_eval


class EvaluationSchemaTests(unittest.TestCase):
    def test_agent_case_rejects_conflicting_tool_labels(self):
        case = AgentCase(
            id="case-1",
            category="tool",
            message="test",
            tools=ToolExpectation(required=["rag"], forbidden=["rag"]),
        )

        self.assertIn("tools both required and forbidden: ['rag']", case.validate())

    def test_retrieval_case_rejects_invalid_grades(self):
        case = RetrievalCase(id="r1", query="query", qrels={"10": 3}, split="test")

        self.assertIn("qrel grades must be 0, 1, or 2", case.validate())

    def test_jsonl_loader_reports_line_number(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.jsonl"
            path.write_text('{}\n{"id":"x"}\n', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, r"bad\.jsonl:1"):
                load_jsonl(path, AgentCase)

    def test_stable_hash_is_independent_of_mapping_order(self):
        self.assertEqual(stable_hash({"a": 1, "b": 2}), stable_hash({"b": 2, "a": 1}))


class EvaluationMetricTests(unittest.TestCase):
    def test_tool_trace_supports_partial_order_and_limits(self):
        expected = ToolExpectation(
            required=["library", "rag"],
            forbidden=["store"],
            ordered=["library", "rag"],
            max_calls={"rag": 1},
        )

        result = tool_trace_metrics(["library", "rag"], expected)

        self.assertEqual(result["trace_success"], 1.0)
        self.assertEqual(result["f1"], 1.0)

    def test_tool_trace_enforces_round_limit_when_observed(self):
        expected = ToolExpectation(required=["rag"], max_rounds=1)

        result = tool_trace_metrics(["rag"], expected, actual_rounds=2)

        self.assertEqual(result["trace_success"], 0.0)
        self.assertIn("round_limit:2>1", result["failures"])

    def test_tool_argument_expectations_check_memory_payload(self):
        expected = ToolExpectation(
            argument_equals={"save_user_insight": {"category": "constraint"}},
            argument_contains={"save_user_insight": {"insight": ["50", "预算"]}},
        )
        history = [{
            "tool": "save_user_insight",
            "status": "success",
            "arguments": {"category": "constraint", "insight": "用户预算不超过50元"},
        }]

        self.assertEqual(tool_argument_metrics(history, expected)["success"], 1.0)

    def test_grounding_only_accepts_appids_in_evidence(self):
        answer = "[A](https://store.steampowered.com/app/10/) [B](https://store.steampowered.com/app/20/)"

        result = grounding_metrics(answer, [{"appid": "10"}])

        self.assertEqual(result["coverage"], 0.5)
        self.assertEqual(result["unsupported_appids"], ["20"])

    def test_retrieval_metrics_use_graded_relevance(self):
        result = retrieval_metrics(["b", "a", "x"], {"a": 2, "b": 1}, k=3)

        self.assertEqual(result["recall@3"], 1.0)
        self.assertEqual(result["mrr@3"], 1.0)
        self.assertLess(result["ndcg@3"], 1.0)

    def test_bootstrap_interval_is_deterministic(self):
        first = bootstrap_mean_ci([0.0, 0.5, 1.0], samples=100, seed=3)
        second = bootstrap_mean_ci([0.0, 0.5, 1.0], samples=100, seed=3)

        self.assertEqual(first, second)

    def test_recommendation_diversity_uses_grounded_tags(self):
        answer = (
            "[A](https://store.steampowered.com/app/10/) "
            "[B](https://store.steampowered.com/app/20/)"
        )
        evidence = [
            {"appid": "10", "payload": {"tags": ["Action", "RPG"]}},
            {"appid": "20", "payload": {"tags": ["Cozy", "Simulation"]}},
        ]

        result = recommendation_diversity(answer, evidence)

        self.assertEqual(result["recommendation_count"], 2)
        self.assertEqual(result["unique_tag_count"], 4)
        self.assertEqual(result["pairwise_tag_distance"], 1.0)

    def test_judge_parser_rejects_scores_outside_rubric(self):
        valid = parse_judge_score(
            '{"relevance":5,"helpfulness":4,"groundedness":5,"overall":4,"reason":"ok"}'
        )
        self.assertEqual(valid.overall, 4)
        with self.assertRaisesRegex(ValueError, "out of range"):
            parse_judge_score(
                '{"relevance":6,"helpfulness":4,"groundedness":5,"overall":4}'
            )


class BaselineGateTests(unittest.TestCase):
    def test_gate_detects_quality_and_cost_regressions(self):
        baseline = {"task_success": 0.8, "avg_total_tokens": 1000}
        candidate = {"task_success": 0.7, "avg_total_tokens": 1300}

        result = compare_summaries(candidate, baseline)

        self.assertFalse(result["passed"])
        self.assertEqual(len(result["failures"]), 2)

    def test_current_migrated_datasets_are_structurally_valid(self):
        root = Path(__file__).resolve().parent.parent

        result = validate_datasets(root / "evals" / "datasets")

        self.assertTrue(result.passed, result.errors)
        self.assertEqual(result.files["agent_behavior.v1.jsonl"]["count"], 63)
        self.assertEqual(result.files["retrieval.v1.jsonl"]["count"], 50)

    def test_retrieval_report_supports_mixed_top_k(self):
        cases = [
            {
                "id": "r5", "query": "a", "qrels": {"1": 2}, "split": "test",
                "top_k": 5, "review_status": "reviewed",
            },
            {
                "id": "r10", "query": "b", "qrels": {"1": 2}, "split": "test",
                "top_k": 10, "review_status": "reviewed",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "mixed.jsonl"
            dataset.write_text(
                "\n".join(json.dumps(case) for case in cases) + "\n", encoding="utf-8"
            )
            output = root / "report.json"
            report = run_retrieval_eval(
                root=Path(__file__).resolve().parent.parent,
                dataset_path=dataset,
                search=lambda *_args, **_kwargs: {"results": [{"appid": "1"}]},
                include_needs_review=False,
                label="test",
                output_path=output,
            )

        self.assertEqual(report["summary"]["test"]["recall@5"], 1.0)
        self.assertEqual(report["summary"]["test"]["recall@10"], 1.0)


if __name__ == "__main__":
    unittest.main()
