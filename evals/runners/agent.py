from __future__ import annotations

import json
import concurrent.futures
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from ..metrics import (
    extract_recommended_appids,
    grounding_metrics,
    recommendation_diversity,
    tool_argument_metrics,
    tool_trace_metrics,
)
from ..models import AgentCase, CaseResult, EvalReport, load_jsonl
from ..reporting import build_manifest, save_report, summarize_agent_results


_CASE_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent-eval")


def run_agent_eval(
    *,
    root: Path,
    dataset_path: Path,
    base_url: str,
    model: str,
    repeats: int,
    include_needs_review: bool,
    label: str,
    output_path: Path,
    mode: str = "inprocess",
    case_ids: set[str] | None = None,
    judge_enabled: bool = False,
) -> EvalReport:
    cases = load_jsonl(dataset_path, AgentCase)
    if not include_needs_review:
        cases = [case for case in cases if case.review_status == "reviewed"]
    if case_ids:
        cases = [case for case in cases if case.id in case_ids]
    if not cases:
        raise ValueError("no eligible Agent cases; review cases or pass --include-needs-review")
    from steam_agent.config import (
        LLM_INPUT_CNY_PER_MILLION,
        LLM_OUTPUT_CNY_PER_MILLION,
    )

    manifest = build_manifest(
        root=root,
        model=model,
        model_parameters={
            "temperature": 0.3,
            "input_cny_per_million": LLM_INPUT_CNY_PER_MILLION,
            "output_cny_per_million": LLM_OUTPUT_CNY_PER_MILLION,
        },
        dataset_paths=[dataset_path],
        repeats=repeats,
        label=label,
    )
    results: list[CaseResult] = []
    total_runs = len(cases) * repeats
    for case in cases:
        for repeat in range(repeats):
            if mode == "http":
                result = _run_case_http(case, repeat, base_url)
            else:
                result = _run_case_inprocess(case, repeat, root)
            if judge_enabled:
                _add_judge_metrics(case, result)
            results.append(result)
            partial_categories = {item.id: item.category for item in cases}
            partial_report = EvalReport(
                manifest=manifest,
                summary=summarize_agent_results(results, partial_categories),
                cases=results,
            )
            save_report(partial_report, output_path)
            latest = results[-1]
            print(
                f"[{len(results)}/{total_runs}] {case.id} repeat={repeat} "
                f"passed={latest.passed} termination={latest.termination_reason}",
                flush=True,
            )
    categories = {case.id: case.category for case in cases}
    report = EvalReport(
        manifest=manifest,
        summary=summarize_agent_results(results, categories),
        cases=results,
    )
    save_report(report, output_path)
    return report


def _run_case_http(case: AgentCase, repeat: int, base_url: str) -> CaseResult:
    started = time.perf_counter()
    status, payload, error = _post_json(
        f"{base_url.rstrip('/')}/chat",
        {
            "thread_id": f"eval_{case.id}_{repeat}",
            "user_id": case.user_id,
            "steam_id": case.steam_id,
            "message": case.message,
        },
    )
    latency = time.perf_counter() - started
    if error or payload is None:
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=False,
            failures=[f"http_error:{status}:{error}"],
            latency_seconds=latency,
            termination_reason="http_error",
        )
    answer = payload.get("reply", "")
    calls = payload.get("tool_calls_made", [])
    metadata = payload.get("run_metadata", {})
    evidence = metadata.get("evidence", [])
    tool_metric = tool_trace_metrics(calls, case.tools, payload.get("tool_rounds"))
    argument_metric = tool_argument_metrics(metadata.get("tool_history", []), case.tools)
    ground_metric = grounding_metrics(answer, evidence)
    termination = metadata.get("termination_reason", "completed")
    failures = list(tool_metric["failures"])
    failures.extend(argument_metric["failures"])
    if termination not in case.expected_termination:
        failures.append(f"termination:{termination}")
    appids = set(extract_recommended_appids(answer))
    for appid in case.answer.required_appids:
        if appid not in appids:
            failures.append(f"missing_appid:{appid}")
    for appid in case.answer.forbidden_appids:
        if appid in appids:
            failures.append(f"forbidden_appid:{appid}")
    for text in case.answer.must_contain:
        if text not in answer:
            failures.append(f"missing_text:{text}")
    for text in case.answer.must_not_contain:
        if text in answer:
            failures.append(f"forbidden_text:{text}")
    if case.answer.require_grounding and ground_metric["unsupported_appids"]:
        failures.append("unsupported_recommendation")
    usage = payload.get("token_usage", {})
    budget = metadata.get("budget", {})
    budget_violation = int(
        int(usage.get("total_tokens", 0)) > int(budget.get("max_total_tokens", 10**12))
        or int(payload.get("tool_rounds", 0)) > int(budget.get("max_tool_rounds", 10**9))
    )
    duplicate_calls = _duplicate_execution_count(metadata.get("tool_history", []))
    parallel_calls = sum(bool(item.get("parallel")) for item in metadata.get("tool_history", []))
    metrics = {
        "tool_precision": float(tool_metric["precision"]),
        "tool_recall": float(tool_metric["recall"]),
        "tool_f1": float(tool_metric["f1"]),
        "tool_trace_success": float(tool_metric["trace_success"]),
        "tool_argument_success": float(argument_metric["success"]),
        "grounding_coverage": float(ground_metric["coverage"]),
        "unsupported_recommendations": float(len(ground_metric["unsupported_appids"])),
        "unsupported_fact_claims": float(len((metadata.get("validation") or {}).get("violations", []))),
        "budget_violation": float(budget_violation),
        "duplicate_calls": float(duplicate_calls),
        "parallel_tool_calls": float(parallel_calls),
        **recommendation_diversity(answer, evidence),
    }
    _add_attribution_metrics(metrics, metadata.get("usage", usage))
    return CaseResult(
        case_id=case.id,
        repeat=repeat,
        passed=not failures,
        tool_calls=calls,
        termination_reason=termination,
        answer=answer,
        evidence=evidence,
        metrics=metrics,
        failures=failures,
        latency_seconds=latency,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        details={
            "tool_history": metadata.get("tool_history", []),
            "validation": metadata.get("validation", {}),
        },
    )


def _run_case_inprocess(case: AgentCase, repeat: int, root: Path) -> CaseResult:
    from steam_agent.graph.builder import _compile
    from steam_agent.graph.run_context import new_run_context, run_metadata
    from steam_agent.graph.nodes import get_tool_map
    from steam_agent.tools.registry import FixtureToolRegistry

    fixture_path = root / "evals" / "fixtures" / f"{case.fixture}.json"
    if not fixture_path.exists():
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=False,
            failures=[f"fixture_missing:{case.fixture}"],
            termination_reason="fixture_error",
        )
    registry = FixtureToolRegistry.from_path(fixture_path)
    tool_map = registry.as_tool_map(list(get_tool_map()))
    graph = _compile(MemorySaver())
    initial_state = {
        "messages": [HumanMessage(content=case.message)],
        "steam_id": case.steam_id,
        "user_id": case.user_id,
        **new_run_context(),
    }
    started = time.perf_counter()
    try:
        future = _CASE_POOL.submit(
            graph.invoke,
            initial_state,
            {
                "configurable": {
                    "thread_id": f"eval_{case.id}_{repeat}",
                    "tool_map": tool_map,
                }
            },
        )
        result = future.result(timeout=float(initial_state["budget"]["deadline_at"] - time.time()))
    except concurrent.futures.TimeoutError:
        future.cancel()
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=False,
            failures=["agent_deadline_exceeded"],
            latency_seconds=time.perf_counter() - started,
            termination_reason="deadline",
        )
    except Exception as exc:
        error_name = type(exc).__name__
        error_text = str(exc)
        dependency_failure = error_name in {
            "APIConnectionError",
            "ConnectError",
            "ReadTimeout",
            "ConnectTimeout",
        } or "connection error" in error_text.lower()
        termination = "dependency_unavailable" if dependency_failure else "model_error"
        failure_prefix = "dependency_unavailable" if dependency_failure else "agent_exception"
        return CaseResult(
            case_id=case.id,
            repeat=repeat,
            passed=False,
            failures=[f"{failure_prefix}:{error_name}:{exc}"],
            latency_seconds=time.perf_counter() - started,
            termination_reason=termination,
        )
    metadata = run_metadata(result)
    answer = _latest_answer(result.get("messages", []))
    calls = [
        item["tool"]
        for item in metadata.get("tool_history", [])
        if item.get("status") != "policy_blocked"
    ]
    return _evaluate_payload(
        case=case,
        repeat=repeat,
        answer=answer,
        calls=calls,
        metadata=metadata,
        latency=time.perf_counter() - started,
    )


def _evaluate_payload(
    *,
    case: AgentCase,
    repeat: int,
    answer: str,
    calls: list[str],
    metadata: dict,
    latency: float,
) -> CaseResult:
    evidence = metadata.get("evidence", [])
    rounds = len({
        item.get("round")
        for item in metadata.get("tool_history", [])
        if item.get("round") is not None
    })
    tool_metric = tool_trace_metrics(calls, case.tools, rounds)
    argument_metric = tool_argument_metrics(metadata.get("tool_history", []), case.tools)
    ground_metric = grounding_metrics(answer, evidence)
    termination = metadata.get("termination_reason", "completed")
    failures = list(tool_metric["failures"])
    failures.extend(argument_metric["failures"])
    if termination not in case.expected_termination:
        failures.append(f"termination:{termination}")
    appids = set(extract_recommended_appids(answer))
    for appid in case.answer.required_appids:
        if appid not in appids:
            failures.append(f"missing_appid:{appid}")
    for appid in case.answer.forbidden_appids:
        if appid in appids:
            failures.append(f"forbidden_appid:{appid}")
    for text in case.answer.must_contain:
        if text not in answer:
            failures.append(f"missing_text:{text}")
    for text in case.answer.must_not_contain:
        if text in answer:
            failures.append(f"forbidden_text:{text}")
    if case.answer.require_grounding and ground_metric["unsupported_appids"]:
        failures.append("unsupported_recommendation")
    usage = metadata.get("usage", {})
    budget = metadata.get("budget", {})
    budget_violation = int(
        int(usage.get("total_tokens", 0)) > int(budget.get("max_total_tokens", 10**12))
    )
    duplicate_calls = _duplicate_execution_count(metadata.get("tool_history", []))
    parallel_calls = sum(bool(item.get("parallel")) for item in metadata.get("tool_history", []))
    metrics = {
        "tool_precision": float(tool_metric["precision"]),
        "tool_recall": float(tool_metric["recall"]),
        "tool_f1": float(tool_metric["f1"]),
        "tool_trace_success": float(tool_metric["trace_success"]),
        "tool_argument_success": float(argument_metric["success"]),
        "grounding_coverage": float(ground_metric["coverage"]),
        "unsupported_recommendations": float(len(ground_metric["unsupported_appids"])),
        "unsupported_fact_claims": float(len((metadata.get("validation") or {}).get("violations", []))),
        "budget_violation": float(budget_violation),
        "duplicate_calls": float(duplicate_calls),
        "parallel_tool_calls": float(parallel_calls),
        **recommendation_diversity(answer, evidence),
    }
    _add_attribution_metrics(metrics, usage)
    return CaseResult(
        case_id=case.id,
        repeat=repeat,
        passed=not failures,
        tool_calls=calls,
        termination_reason=termination,
        answer=answer,
        evidence=evidence,
        metrics=metrics,
        failures=failures,
        latency_seconds=latency,
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        details={
            "tool_history": metadata.get("tool_history", []),
            "validation": metadata.get("validation", {}),
        },
    )


def _add_judge_metrics(case: AgentCase, result: CaseResult) -> None:
    from ..judge import judge_answer

    score = judge_answer(case.message, result.answer, result.evidence)
    result.metrics.update({
        "judge_relevance": score.relevance / 5,
        "judge_helpfulness": score.helpfulness / 5,
        "judge_groundedness": score.groundedness / 5,
        "judge_overall": score.overall / 5,
    })


def _add_attribution_metrics(metrics: dict[str, float], usage: dict) -> None:
    from steam_agent.config import (
        LLM_INPUT_CNY_PER_MILLION,
        LLM_OUTPUT_CNY_PER_MILLION,
    )

    total = max(1, int(usage.get("input_tokens", 0)))
    mapping = {
        "system": "input_system_tokens",
        "history": "input_history_tokens",
        "tool_results": "input_tool_results_tokens",
        "tool_schema": "input_tool_schema_tokens",
    }
    for label, key in mapping.items():
        value = int(usage.get(key, 0))
        metrics[f"input_attribution_{label}_tokens"] = float(value)
        metrics[f"input_attribution_{label}_ratio"] = value / total
    metrics["estimated_cost_cny"] = (
        int(usage.get("input_tokens", 0)) * LLM_INPUT_CNY_PER_MILLION
        + int(usage.get("output_tokens", 0)) * LLM_OUTPUT_CNY_PER_MILLION
    ) / 1_000_000


def _latest_answer(messages) -> str:
    for message in reversed(messages):
        if getattr(message, "type", "") == "ai" and not getattr(message, "tool_calls", None):
            return str(getattr(message, "content", ""))
    return ""


def _duplicate_execution_count(history: list[dict[str, Any]]) -> int:
    signatures = []
    for item in history:
        if item.get("status") == "policy_blocked":
            continue
        signatures.append(json.dumps(
            [item.get("tool"), item.get("arguments", {})],
            ensure_ascii=False,
            sort_keys=True,
        ))
    counts = Counter(signatures)
    return sum(count - 1 for count in counts.values() if count > 1)


def _post_json(url: str, body: dict, timeout: int = 90) -> tuple[int, dict | None, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read()), ""
    except urllib.error.HTTPError as exc:
        return exc.code, None, exc.read().decode(errors="replace")
    except Exception as exc:
        return 0, None, str(exc)
