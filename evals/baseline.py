from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GateConfig:
    absolute_zero: tuple[str, ...] = (
        "budget_violations",
        "duplicate_external_calls",
        "unsupported_recommendations",
        "unsupported_fact_claims",
        "retrieval_hard_negative_rate",
    )
    max_absolute_drop: dict[str, float] = field(default_factory=lambda: {
        "tool_trace_success": 0.03,
        "task_success": 0.03,
        "retrieval_recall@10": 0.02,
        "retrieval_mrr@10": 0.02,
        "retrieval_ndcg@10": 0.02,
        "memory_precision": 0.02,
        "memory_recall": 0.02,
        "judge_overall": 0.05,
        "judge_groundedness": 0.03,
        "pairwise_tag_distance": 0.08,
        "personalization_expected_hit_rate": 0.05,
        "personalization_tool_chain_rate": 0.05,
        "recommendation_jaccard_distance": 0.10,
    })
    max_ratio_increase: dict[str, float] = field(default_factory=lambda: {
        "latency_p95_seconds": 1.20,
        "avg_total_tokens": 1.15,
    })


def compare_summaries(candidate: dict[str, Any], baseline: dict[str, Any], config: GateConfig | None = None) -> dict:
    config = config or GateConfig()
    failures: list[str] = []
    deltas: dict[str, float] = {}
    for name in config.absolute_zero:
        value = float(candidate.get(name, 0))
        if value != 0:
            failures.append(f"{name} must be 0, got {value}")
    for name, tolerance in config.max_absolute_drop.items():
        if name not in candidate or name not in baseline:
            continue
        delta = float(candidate[name]) - float(baseline[name])
        deltas[name] = delta
        if delta < -tolerance:
            failures.append(f"{name} regressed by {delta:.4f}, limit {-tolerance:.4f}")
    for name, ratio in config.max_ratio_increase.items():
        if name not in candidate or name not in baseline or not baseline[name]:
            continue
        actual_ratio = float(candidate[name]) / float(baseline[name])
        deltas[name] = actual_ratio - 1
        if actual_ratio > ratio:
            failures.append(f"{name} ratio {actual_ratio:.3f} exceeds {ratio:.3f}")
    return {"passed": not failures, "failures": failures, "deltas": deltas}


def normalize_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if "test" not in summary or not isinstance(summary.get("test"), dict):
        return summary
    test = summary["test"]
    normalized = {
        f"retrieval_{name}": value
        for name, value in test.items()
    }
    return normalized
