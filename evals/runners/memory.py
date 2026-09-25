from __future__ import annotations

import tempfile
from pathlib import Path
from statistics import mean
from unittest.mock import patch

from ..models import MemoryCase, load_jsonl
from ..reporting import build_manifest


def run_memory_eval(root: Path, dataset_path: Path, include_needs_review: bool = False) -> dict:
    from steam_agent.memory import insight_store

    cases = load_jsonl(dataset_path, MemoryCase)
    if not include_needs_review:
        cases = [case for case in cases if case.review_status == "reviewed"]
    results = []
    for case in cases:
        with tempfile.TemporaryDirectory() as directory:
            db_path = str(Path(directory) / "memory.db")
            with patch.object(insight_store, "SQLITE_DB_PATH", db_path):
                statuses = []
                for operation in case.operations:
                    result = insight_store.add_insight("eval-user", **operation)
                    statuses.append(result["status"])
                active_rows = insight_store.get_insights("eval-user", limit=case.recall_limit)
        actual = {row["insight"] for row in active_rows}
        expected = set(case.expected_active)
        forbidden = set(case.expected_inactive)
        hits = actual & expected
        precision = len(hits) / len(actual) if actual else (1.0 if not expected else 0.0)
        recall = len(hits) / len(expected) if expected else (1.0 if not actual else 0.0)
        contradictions = len(actual & forbidden)
        status_match = not case.expected_statuses or statuses == case.expected_statuses
        results.append({
            "case_id": case.id,
            "passed": precision == 1.0 and recall == 1.0 and contradictions == 0 and status_match,
            "memory_precision": precision,
            "memory_recall": recall,
            "contradictions": contradictions,
            "status_match": status_match,
            "actual": sorted(actual),
            "statuses": statuses,
        })
    manifest = build_manifest(
        root=root,
        model="deterministic-memory",
        model_parameters={},
        dataset_paths=[dataset_path],
        repeats=1,
        label="memory",
    )
    return {
        "manifest": manifest.__dict__,
        "summary": {
            "cases": len(results),
            "task_success": mean(float(item["passed"]) for item in results) if results else 0.0,
            "memory_precision": mean(item["memory_precision"] for item in results) if results else 0.0,
            "memory_recall": mean(item["memory_recall"] for item in results) if results else 0.0,
            "contradiction_rate": (
                sum(item["contradictions"] for item in results) / len(results) if results else 0.0
            ),
        },
        "cases": results,
    }
