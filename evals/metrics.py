from __future__ import annotations

import math
import random
import re
from collections import Counter
from typing import Iterable, Sequence

from .models import ToolExpectation


APP_URL_PATTERN = re.compile(r"store\.steampowered\.com/app/(\d+)", re.IGNORECASE)


def tool_trace_metrics(
    actual: Sequence[str],
    expected: ToolExpectation,
    actual_rounds: int | None = None,
) -> dict[str, float | list[str]]:
    actual_counts = Counter(actual)
    actual_set = set(actual)
    required = set(expected.required)
    forbidden = set(expected.forbidden)
    true_positive = len(required & actual_set)
    precision = true_positive / len(actual_set) if actual_set else (1.0 if not required else 0.0)
    recall = true_positive / len(required) if required else (1.0 if not actual_set else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    failures: list[str] = []
    for tool in sorted(required - actual_set):
        failures.append(f"missing:{tool}")
    for tool in sorted(forbidden & actual_set):
        failures.append(f"forbidden:{tool}")
    if expected.ordered and not is_subsequence(expected.ordered, actual):
        failures.append("ordered_trace_mismatch")
    for tool, limit in expected.max_calls.items():
        if actual_counts[tool] > limit:
            failures.append(f"call_limit:{tool}:{actual_counts[tool]}>{limit}")
    if expected.max_rounds is not None and actual_rounds is not None:
        if actual_rounds > expected.max_rounds:
            failures.append(f"round_limit:{actual_rounds}>{expected.max_rounds}")
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "trace_success": 1.0 if not failures else 0.0,
        "failures": failures,
    }


def tool_argument_metrics(history: Sequence[dict], expected: ToolExpectation) -> dict:
    executed = [item for item in history if item.get("status") != "policy_blocked"]
    failures = []
    checked_tools = set(expected.argument_equals) | set(expected.argument_contains)
    for tool in sorted(checked_tools):
        calls = [item.get("arguments", {}) for item in executed if item.get("tool") == tool]
        equals = expected.argument_equals.get(tool, {})
        contains = expected.argument_contains.get(tool, {})
        matched = False
        for arguments in calls:
            if any(arguments.get(key) != value for key, value in equals.items()):
                continue
            if any(
                not all(
                    str(fragment).lower() in str(arguments.get(key, "")).lower()
                    for fragment in fragments
                )
                for key, fragments in contains.items()
            ):
                continue
            matched = True
            break
        if not matched:
            failures.append(f"tool_arguments:{tool}")
    return {
        "success": 1.0 if not failures else 0.0,
        "failures": failures,
    }


def is_subsequence(expected: Sequence[str], actual: Sequence[str]) -> bool:
    cursor = 0
    for item in actual:
        if cursor < len(expected) and item == expected[cursor]:
            cursor += 1
    return cursor == len(expected)


def extract_recommended_appids(answer: str) -> list[str]:
    return list(dict.fromkeys(APP_URL_PATTERN.findall(answer or "")))


def grounding_metrics(answer: str, evidence: Sequence[dict]) -> dict[str, float | list[str]]:
    recommended = extract_recommended_appids(answer)
    supported = {str(item.get("appid")) for item in evidence if item.get("appid") is not None}
    unsupported = [appid for appid in recommended if appid not in supported]
    coverage = (len(recommended) - len(unsupported)) / len(recommended) if recommended else 1.0
    return {"coverage": coverage, "unsupported_appids": unsupported}


def recommendation_diversity(answer: str, evidence: Sequence[dict]) -> dict[str, float]:
    appids = extract_recommended_appids(answer)
    by_appid = {str(item.get("appid")): item for item in evidence if item.get("appid") is not None}
    tag_sets = []
    for appid in appids:
        payload = (by_appid.get(appid) or {}).get("payload", {})
        tags = payload.get("tags", [])
        if isinstance(tags, list):
            tag_sets.append({str(tag).strip().lower() for tag in tags if str(tag).strip()})
    pairwise = []
    for left in range(len(tag_sets)):
        for right in range(left + 1, len(tag_sets)):
            union = tag_sets[left] | tag_sets[right]
            pairwise.append(1 - len(tag_sets[left] & tag_sets[right]) / len(union) if union else 0.0)
    counts = Counter(tag for tags in tag_sets for tag in tags)
    total = sum(counts.values())
    entropy = -sum((count / total) * math.log2(count / total) for count in counts.values()) if total else 0.0
    return {
        "recommendation_count": float(len(appids)),
        "unique_tag_count": float(len(counts)),
        "tag_entropy": entropy,
        "pairwise_tag_distance": sum(pairwise) / len(pairwise) if pairwise else 0.0,
    }


def retrieval_metrics(retrieved: Sequence[str], qrels: dict[str, int], k: int = 10) -> dict[str, float]:
    ranked = [str(item) for item in retrieved[:k]]
    relevant = {str(item) for item, grade in qrels.items() if grade > 0}
    hits = [item for item in ranked if item in relevant]
    recall = len(set(hits)) / len(relevant) if relevant else 1.0
    precision = len(hits) / len(ranked) if ranked else 0.0
    reciprocal_rank = next((1.0 / (index + 1) for index, item in enumerate(ranked) if item in relevant), 0.0)
    gains = [(2 ** qrels.get(item, 0) - 1) / math.log2(index + 2) for index, item in enumerate(ranked)]
    ideal_grades = sorted(qrels.values(), reverse=True)[:k]
    ideal = sum((2 ** grade - 1) / math.log2(index + 2) for index, grade in enumerate(ideal_grades))
    ndcg = sum(gains) / ideal if ideal else 1.0
    return {
        f"recall@{k}": recall,
        f"precision@{k}": precision,
        f"hit_rate@{k}": 1.0 if hits else 0.0,
        f"mrr@{k}": reciprocal_rank,
        f"ndcg@{k}": ndcg,
    }


def percentile(values: Iterable[float], value: int) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = max(0, min(math.ceil(len(ordered) * value / 100) - 1, len(ordered) - 1))
    return ordered[index]


def bootstrap_mean_ci(values: Sequence[float], *, samples: int = 2000, seed: int = 7) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draw = [values[rng.randrange(len(values))] for _ in values]
        means.append(sum(draw) / len(draw))
    return percentile(means, 2.5), percentile(means, 97.5)


def quadratic_weighted_kappa(left: Sequence[int], right: Sequence[int], minimum: int, maximum: int) -> float:
    if not left or len(left) != len(right):
        return 0.0
    size = maximum - minimum + 1
    n = len(left)
    left_counts = [left.count(score) for score in range(minimum, maximum + 1)]
    right_counts = [right.count(score) for score in range(minimum, maximum + 1)]
    denominator = max(1, maximum - minimum)
    observed = sum(((a - b) / denominator) ** 2 for a, b in zip(left, right)) / n
    expected = sum(
        left_counts[i] * right_counts[j] * (((i - j) / denominator) ** 2)
        for i in range(size) for j in range(size)
    ) / (n * n)
    return 1 - observed / expected if expected else 1.0
