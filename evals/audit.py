from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .models import AgentCase, RetrievalCase, load_jsonl


AGENT_TOOLS = {
    "get_user_playtime",
    "search_steam_store",
    "rag_search_similar_games",
    "save_user_insight",
    "recall_user_memory",
    "recall_message_detail",
}


def audit_coverage(dataset_dir: Path) -> dict:
    failures = []
    counts = {}
    minimums = {
        "agent_gate.v1.jsonl": 20,
        "agent_memory.v1.jsonl": 12,
        "retrieval_gate.v1.jsonl": 20,
        "memory_retrieval.v1.jsonl": 10,
        "memory.v1.jsonl": 20,
        "robustness.v1.jsonl": 10,
        "judge_calibration.v1.jsonl": 12,
        "personalization.v1.jsonl": 5,
        "annotation_agreement.v1.jsonl": 20,
    }
    for filename, minimum in minimums.items():
        path = dataset_dir / filename
        count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        counts[filename] = count
        if count < minimum:
            failures.append(f"{filename}: {count} < {minimum}")

    agent_cases = []
    for filename in ("agent_gate.v1.jsonl", "agent_memory.v1.jsonl"):
        agent_cases.extend(load_jsonl(dataset_dir / filename, AgentCase))
    required_counts = Counter(tool for case in agent_cases for tool in case.tools.required)
    forbidden_counts = Counter(tool for case in agent_cases for tool in case.tools.forbidden)
    missing_positive = sorted(AGENT_TOOLS - set(required_counts))
    missing_negative = sorted(AGENT_TOOLS - set(forbidden_counts))
    if missing_positive:
        failures.append(f"tools missing positive cases: {missing_positive}")
    if missing_negative:
        failures.append(f"tools missing negative cases: {missing_negative}")

    retrieval_cases = load_jsonl(dataset_dir / "retrieval_gate.v1.jsonl", RetrievalCase)
    splits = Counter(case.split for case in retrieval_cases)
    if not splits.get("dev") or not splits.get("test"):
        failures.append("retrieval gate must contain both dev and test")
    without_negatives = [case.id for case in retrieval_cases if not case.hard_negatives]
    if without_negatives:
        failures.append(f"retrieval cases missing hard negatives: {without_negatives}")
    filtered = sum(bool(case.constraints) for case in retrieval_cases)
    if filtered < 5:
        failures.append(f"filtered retrieval cases: {filtered} < 5")

    return {
        "passed": not failures,
        "failures": failures,
        "dataset_counts": counts,
        "tool_positive_counts": dict(required_counts),
        "tool_negative_counts": dict(forbidden_counts),
        "retrieval_splits": dict(splits),
        "filtered_retrieval_cases": filtered,
    }


def write_audit(report: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
