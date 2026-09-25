from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from ..artifacts import write_companion_artifacts
from ..metrics import extract_recommended_appids
from ..models import AgentCase, AnswerExpectation, PersonalizationCase, ToolExpectation, load_jsonl
from ..reporting import build_manifest
from .agent import _run_case_inprocess


def run_personalization_eval(*, root: Path, dataset_path: Path, output_path: Path) -> dict:
    cases = [
        case for case in load_jsonl(dataset_path, PersonalizationCase)
        if case.review_status == "reviewed"
    ]
    rows = []
    for case in cases:
        personalized = _run_case_inprocess(_agent_case(case, personalized=True), 0, root)
        generic = _run_case_inprocess(_agent_case(case, personalized=False), 0, root)
        personalized_ids = set(extract_recommended_appids(personalized.answer))
        generic_ids = set(extract_recommended_appids(generic.answer))
        union = personalized_ids | generic_ids
        distance = 1 - len(personalized_ids & generic_ids) / len(union) if union else 0.0
        expected_hit = set(case.expected_personalized_appids).issubset(personalized_ids)
        chain = personalized.tool_calls[:2] == [
            "get_user_playtime",
            "rag_search_similar_games",
        ]
        passed = expected_hit and chain and distance >= case.min_jaccard_distance
        rows.append({
            "case_id": case.id,
            "passed": passed,
            "personalized_appids": sorted(personalized_ids),
            "generic_appids": sorted(generic_ids),
            "jaccard_distance": distance,
            "expected_hit": expected_hit,
            "tool_chain_success": chain,
            "personalized_tokens": personalized.input_tokens + personalized.output_tokens,
            "generic_tokens": generic.input_tokens + generic.output_tokens,
            "token_delta": (
                personalized.input_tokens + personalized.output_tokens
                - generic.input_tokens - generic.output_tokens
            ),
        })
    summary = {
        "cases": len(rows),
        "task_success": mean(float(row["passed"]) for row in rows) if rows else 0.0,
        "personalization_expected_hit_rate": mean(float(row["expected_hit"]) for row in rows) if rows else 0.0,
        "personalization_tool_chain_rate": mean(float(row["tool_chain_success"]) for row in rows) if rows else 0.0,
        "recommendation_jaccard_distance": mean(row["jaccard_distance"] for row in rows) if rows else 0.0,
        "avg_personalization_token_delta": mean(row["token_delta"] for row in rows) if rows else 0.0,
    }
    manifest = build_manifest(
        root=root,
        model="deepseek-chat",
        model_parameters={"paired": True},
        dataset_paths=[dataset_path],
        repeats=1,
        label="personalization-ab",
    )
    report = {"manifest": manifest.__dict__, "summary": summary, "cases": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, output_path)
    return report


def _agent_case(case: PersonalizationCase, personalized: bool) -> AgentCase:
    return AgentCase(
        id=f"{case.id}-{'personalized' if personalized else 'generic'}",
        category=case.category,
        message=case.message,
        steam_id=case.steam_id if personalized else None,
        fixture=case.fixture,
        tools=(
            ToolExpectation(
                required=["get_user_playtime", "rag_search_similar_games"],
                ordered=["get_user_playtime", "rag_search_similar_games"],
                max_calls={"get_user_playtime": 1, "rag_search_similar_games": 1},
            )
            if personalized
            else ToolExpectation(forbidden=["get_user_playtime"])
        ),
        answer=AnswerExpectation(answer_type="recommendation" if personalized else "clarification"),
    )
