from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Callable

from ..metrics import percentile, retrieval_metrics
from ..models import RetrievalCase, load_jsonl
from ..reporting import build_manifest
from ..artifacts import write_companion_artifacts


def run_retrieval_eval(
    *,
    root: Path,
    dataset_path: Path,
    search: Callable[..., dict],
    include_needs_review: bool,
    label: str,
    output_path: Path,
) -> dict:
    cases = load_jsonl(dataset_path, RetrievalCase)
    if not include_needs_review:
        cases = [case for case in cases if case.review_status == "reviewed"]
    if not cases:
        raise ValueError("no eligible retrieval cases")
    rows = []
    for case in cases:
        response = search(case.query, top_k=case.top_k, **case.constraints)
        items = response.get("results", [])
        appids = [str(item["appid"]) for item in items]
        metrics = retrieval_metrics(appids, case.qrels, case.top_k)
        metrics["constraint_accuracy"] = _constraint_accuracy(items, case.constraints)
        hard_negatives = set(case.hard_negatives)
        metrics["hard_negative_rate"] = (
            len(hard_negatives & set(appids)) / len(appids) if appids else 0.0
        )
        rows.append({
            "case_id": case.id,
            "split": case.split,
            "metrics": metrics,
            "appids": appids,
            "timings": response.get("retrieval", {}).get("timings", {}),
        })
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["split"]].append(row)
    summary = {}
    for split, group in grouped.items():
        names = sorted({name for row in group for name in row["metrics"]})
        summary[split] = {
            name: mean(
                float(row["metrics"][name])
                for row in group
                if name in row["metrics"]
            )
            for name in names
        }
        timing_names = sorted({name for row in group for name in row.get("timings", {})})
        summary[split]["timings"] = {
            name: {
                "p50_ms": percentile(
                    [row["timings"][name] for row in group if name in row["timings"]], 50
                ),
                "p95_ms": percentile(
                    [row["timings"][name] for row in group if name in row["timings"]], 95
                ),
            }
            for name in timing_names
        }
    manifest = build_manifest(
        root=root,
        model="retrieval",
        model_parameters={},
        dataset_paths=[dataset_path],
        repeats=1,
        label=label,
        index_manifest_path=(
            root / "steam_agent" / "rag" / "chroma_data" / "index_manifest.json"
        ),
    )
    report = {"manifest": manifest.__dict__, "summary": summary, "cases": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, output_path)
    return report


def _constraint_accuracy(items: list[dict], constraints: dict) -> float:
    if not items or not constraints:
        return 1.0
    passed = 0
    for item in items:
        valid = True
        if constraints.get("free_only"):
            valid = valid and bool(item.get("is_free"))
        if constraints.get("min_year") is not None:
            valid = valid and int(item.get("release_year", 0)) >= int(constraints["min_year"])
        if constraints.get("has_multiplayer") is not None:
            valid = valid and bool(item.get("has_multiplayer")) == bool(constraints["has_multiplayer"])
        if constraints.get("min_metacritic") is not None:
            valid = valid and int(item.get("metacritic", 0)) >= int(constraints["min_metacritic"])
        passed += int(valid)
    return passed / len(items)
