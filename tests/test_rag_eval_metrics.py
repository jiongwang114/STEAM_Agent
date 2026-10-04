"""Regression tests for the executable RAG evaluation metrics."""

from .rag_eval import load_ground_truth, precision_at_k, recall_at_k, reciprocal_rank


def test_open_judge_receives_all_evidence(monkeypatch):
    from . import llm_judge, rag_eval
    captured = {}

    def judge(question, reply):
        captured.update(question=question, reply=reply)
        return {"score": 4, "reason": "Good semantic fit"}

    monkeypatch.setattr(llm_judge, "_judge_one", judge)
    items = [{"appid": str(i), "description": "x" * 500} for i in range(8)]
    verdict = rag_eval.judge_recommendations({"query": "action games", "free_only": True}, items)
    assert verdict["judge_score"] == 4
    assert __import__("json").loads(captured["reply"]) == items
    assert __import__("json").loads(captured["question"])["constraints"]["free_only"] is True


def test_open_judge_failure_is_not_a_score(monkeypatch):
    from . import llm_judge, rag_eval

    def fail(*args):
        raise ValueError("invalid verdict")

    monkeypatch.setattr(llm_judge, "_judge_one", fail)
    verdict = rag_eval.judge_recommendations({"query": "games"}, [])
    assert verdict["judge_score"] is None
    assert verdict["judge_error"] == "invalid verdict"


def test_open_results_and_changelog_accept_missing_metrics(tmp_path):
    from .rag_eval import write_results, write_changelog
    result = dict(query="games", top_k=8, relevant_appids="", retrieved_appids="1",
                  retrieved_names="Game", retrieved_evidence="[]", recall=None,
                  precision=None, mrr=None, ndcg=None, judge_score=4,
                  judge_reason="Fits", judge_error="", filters="",
                  filter_satisfaction=1.0, filter_violations="")
    path = tmp_path / "results.csv"
    write_results([result], "test", path)
    rows = list(__import__("csv").DictReader(path.open(encoding="utf-8-sig")))
    assert rows[0]["recall_test"] == ""
    assert rows[0]["judge_score_test"] == "4.0000"
    write_changelog("test", "", {"recall": None, "precision": None,
                    "mrr": None, "judge_score": 4}, 8, tmp_path / "changes.csv")


def test_recall_at_k_uses_unique_relevant_hits():
    assert recall_at_k(["1", "1", "2"], ["1", "2", "3"]) == 2 / 3


def test_precision_at_k_uses_requested_cutoff():
    assert precision_at_k(["1", "9", "2"], ["1", "2"], 2) == 0.5


def test_reciprocal_rank_returns_first_relevant_rank():
    assert reciprocal_rank(["9", "8", "2", "1"], ["1", "2"]) == 1 / 3


def test_semantic_ground_truth_contains_pure_and_filtered_cases():
    cases = load_ground_truth(__import__("pathlib").Path(__file__).with_name("gt_semantic_v2.csv"))
    assert cases
    # v2 keeps semantic retrieval separate from hard-filter evaluation.
    assert any(not case["free_only"] and case["min_year"] is None for case in cases)


def test_filtered_ground_truth_contains_tagged_filter_cases():
    cases = load_ground_truth(__import__("pathlib").Path(__file__).with_name("gt_filtered_v2.csv"))
    assert cases
    assert all(case["top_k"] > 0 for case in cases)
    assert all(
        case["relevant_appids"] or case["ground_truth_status"] == "needs_manual_review"
        for case in cases
    )
    assert any(case["free_only"] or case["min_year"] is not None for case in cases)
