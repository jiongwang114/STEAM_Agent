from __future__ import annotations

import json
from pathlib import Path

from ..artifacts import write_companion_artifacts
from ..metrics import quadratic_weighted_kappa
from ..models import AnnotationAgreementCase, load_jsonl


def run_annotation_agreement(dataset_path: Path, output_path: Path) -> dict:
    cases = load_jsonl(dataset_path, AnnotationAgreementCase)
    left = [case.reviewer_a for case in cases]
    right = [case.reviewer_b for case in cases]
    exact = sum(a == b for a, b in zip(left, right)) / len(cases) if cases else 0.0
    kappa = quadratic_weighted_kappa(left, right, 0, 2)
    rows = [{
        "case_id": case.id,
        "passed": abs(case.reviewer_a - case.reviewer_b) <= 1,
        "reviewer_a": case.reviewer_a,
        "reviewer_b": case.reviewer_b,
    } for case in cases]
    report = {
        "summary": {
            "cases": len(cases),
            "exact_agreement": exact,
            "quadratic_weighted_kappa": kappa,
            "gate_passed": kappa >= 0.7,
        },
        "cases": rows,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, output_path)
    return report
