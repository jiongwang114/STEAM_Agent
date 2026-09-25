from __future__ import annotations

import json
from pathlib import Path
from statistics import mean

from ..artifacts import write_companion_artifacts
from ..judge import build_judge_llm, judge_answer
from ..models import JudgeCalibrationCase, load_jsonl
from ..metrics import quadratic_weighted_kappa
from ..reporting import build_manifest


def run_judge_calibration(*, root: Path, dataset_path: Path, output_path: Path) -> dict:
    cases = [
        case for case in load_jsonl(dataset_path, JudgeCalibrationCase)
        if case.review_status == "reviewed"
    ]
    llm = build_judge_llm()
    rows = []
    dimensions = ("relevance", "helpfulness", "groundedness", "overall")
    for index, case in enumerate(cases, start=1):
        score = judge_answer(case.query, case.answer, case.evidence, llm=llm)
        predicted = {name: int(getattr(score, name)) for name in dimensions}
        errors = {name: abs(predicted[name] - case.human_scores[name]) for name in dimensions}
        rows.append({
            "case_id": case.id,
            "passed": errors["overall"] <= 1,
            "human": case.human_scores,
            "predicted": predicted,
            "absolute_errors": errors,
            "reason": score.reason,
        })
        partial = _report(root, dataset_path, cases, rows)
        _save(partial, output_path)
        print(f"[{index}/{len(cases)}] {case.id} human={case.human_scores['overall']} judge={score.overall}", flush=True)
    report = _report(root, dataset_path, cases, rows)
    _save(report, output_path)
    return report


def calibration_passed(summary: dict) -> bool:
    return (
        summary.get("overall_mae", 99) <= 1.0
        and summary.get("overall_within_one", 0) >= 0.8
        and summary.get("quadratic_weighted_kappa", -1) >= 0.4
    )


def _report(root: Path, dataset_path: Path, cases, rows) -> dict:
    dimensions = ("relevance", "helpfulness", "groundedness", "overall")
    summary = {
        f"{name}_mae": mean(row["absolute_errors"][name] for row in rows)
        for name in dimensions
    }
    summary.update({
        "cases": len(rows),
        "overall_exact": mean(row["absolute_errors"]["overall"] == 0 for row in rows),
        "overall_within_one": mean(row["absolute_errors"]["overall"] <= 1 for row in rows),
        "quadratic_weighted_kappa": quadratic_weighted_kappa(
            [row["human"]["overall"] for row in rows],
            [row["predicted"]["overall"] for row in rows],
            1,
            5,
        ),
    })
    summary["gate_passed"] = calibration_passed(summary)
    manifest = build_manifest(
        root=root,
        model="deepseek-chat-judge",
        model_parameters={"temperature": 0},
        dataset_paths=[dataset_path],
        repeats=1,
        label="judge-calibration",
    )
    return {"manifest": manifest.__dict__, "summary": summary, "cases": rows}


def _save(report: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, path)
