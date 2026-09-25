from __future__ import annotations

import json
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Sequence

from .metrics import bootstrap_mean_ci, percentile
from .models import CaseResult, EvalReport, RunManifest, stable_hash
from .artifacts import write_companion_artifacts


def build_manifest(
    *,
    root: Path,
    model: str,
    model_parameters: dict[str, Any],
    dataset_paths: Sequence[Path],
    repeats: int,
    label: str,
    index_manifest_path: Path | None = None,
) -> RunManifest:
    try:
        git_sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    except Exception:
        git_sha = "unknown"
    prompt_dir = root / "steam_agent" / "prompts"
    prompt_files = sorted(prompt_dir.glob("*.py"))
    prompt_hash = stable_hash({path.name: stable_hash(path) for path in prompt_files})
    now = datetime.now(timezone.utc)
    return RunManifest(
        run_id=f"{label}-{now.strftime('%Y%m%dT%H%M%SZ')}",
        created_at=now.isoformat(),
        git_sha=git_sha,
        model=model,
        model_parameters=model_parameters,
        prompt_hash=prompt_hash,
        dataset_hashes={path.name: stable_hash(path) for path in dataset_paths},
        index_manifest_hash=(
            stable_hash(index_manifest_path)
            if index_manifest_path and index_manifest_path.exists()
            else ""
        ),
        repeats=repeats,
        candidate_label=label,
    )


def summarize_agent_results(results: Sequence[CaseResult], categories: dict[str, str]) -> dict[str, Any]:
    if not results:
        return {"case_runs": 0, "task_success": 0.0}
    metric_names = sorted({key for result in results for key in result.metrics})
    summary: dict[str, Any] = {
        "case_runs": len(results),
        "unique_cases": len({result.case_id for result in results}),
        "task_success": mean(1.0 if result.passed else 0.0 for result in results),
        "latency_p50_seconds": percentile([result.latency_seconds for result in results], 50),
        "latency_p95_seconds": percentile([result.latency_seconds for result in results], 95),
        "avg_total_tokens": mean(result.input_tokens + result.output_tokens for result in results),
        "termination_reasons": dict(Counter(result.termination_reason for result in results)),
        "budget_violations": sum(int(result.metrics.get("budget_violation", 0)) for result in results),
        "duplicate_external_calls": sum(int(result.metrics.get("duplicate_calls", 0)) for result in results),
        "unsupported_recommendations": sum(
            int(result.metrics.get("unsupported_recommendations", 0)) for result in results
        ),
        "unsupported_fact_claims": sum(
            int(result.metrics.get("unsupported_fact_claims", 0)) for result in results
        ),
        "total_estimated_cost_cny": sum(
            float(result.metrics.get("estimated_cost_cny", 0)) for result in results
        ),
    }
    attribution_names = sorted({
        key
        for result in results
        for key in result.metrics
        if key.startswith("input_attribution_")
    })
    for name in attribution_names:
        summary[name] = mean(float(result.metrics.get(name, 0)) for result in results)
    for name in metric_names:
        values = [float(result.metrics[name]) for result in results if name in result.metrics]
        if values:
            summary[name] = mean(values)
            low, high = bootstrap_mean_ci(values)
            summary[f"{name}_ci95"] = [low, high]
    grouped: dict[str, list[CaseResult]] = defaultdict(list)
    for result in results:
        grouped[categories.get(result.case_id, "unknown")].append(result)
    summary["by_category"] = {
        category: {
            "runs": len(group),
            "task_success": mean(1.0 if item.passed else 0.0 for item in group),
            "avg_tokens": mean(item.input_tokens + item.output_tokens for item in group),
            "p95_latency": percentile([item.latency_seconds for item in group], 95),
        }
        for category, group in sorted(grouped.items())
    }
    return summary


def save_report(report: EvalReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = report.to_dict()
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_companion_artifacts(payload, path)


def load_report(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
