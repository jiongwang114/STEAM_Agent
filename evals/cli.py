from __future__ import annotations

import argparse
import json
from pathlib import Path

from .baseline import compare_summaries, normalize_summary
from .migrate_legacy import migrate_all
from .validate import validate_datasets


ROOT = Path(__file__).resolve().parent.parent
DATASETS = ROOT / "evals" / "datasets"


def _validate() -> int:
    result = validate_datasets(DATASETS)
    print(json.dumps({"passed": result.passed, "files": result.files, "errors": result.errors}, ensure_ascii=False, indent=2))
    return 0 if result.passed else 1


def _migrate() -> int:
    counts = migrate_all(ROOT)
    print(json.dumps({"migrated": counts}, ensure_ascii=False, indent=2))
    return _validate()


def _compare(candidate_path: Path, baseline_path: Path) -> int:
    candidate = normalize_summary(json.loads(candidate_path.read_text(encoding="utf-8"))["summary"])
    baseline = normalize_summary(json.loads(baseline_path.read_text(encoding="utf-8"))["summary"])
    result = compare_summaries(candidate, baseline)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


def _promote(candidate_path: Path, output_path: Path) -> int:
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    if "summary" not in candidate:
        raise ValueError("candidate report has no summary")
    baseline = {
        "source_report": candidate_path.name,
        "manifest": candidate.get("manifest", {}),
        "summary": candidate["summary"],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"promoted baseline: {output_path}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam Agent evaluation framework")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("validate")
    subparsers.add_parser("migrate-legacy")
    compare = subparsers.add_parser("compare")
    compare.add_argument("--candidate", type=Path, required=True)
    compare.add_argument("--baseline", type=Path, required=True)
    promote = subparsers.add_parser("promote-baseline")
    promote.add_argument("--candidate", type=Path, required=True)
    promote.add_argument("--output", type=Path, required=True)
    agent = subparsers.add_parser("run-agent")
    agent.add_argument("--base-url", default="http://localhost:8000")
    agent.add_argument("--dataset", type=Path, default=DATASETS / "agent_gate.v1.jsonl")
    agent.add_argument("--model", default="deepseek-chat")
    agent.add_argument("--repeats", type=int, default=3)
    agent.add_argument("--label", default="candidate")
    agent.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "agent.json")
    agent.add_argument("--include-needs-review", action="store_true")
    agent.add_argument("--mode", choices=["inprocess", "http"], default="inprocess")
    agent.add_argument("--case-id", action="append", dest="case_ids")
    agent.add_argument("--judge", action="store_true")
    retrieval = subparsers.add_parser("run-retrieval")
    retrieval.add_argument("--dataset", type=Path, default=DATASETS / "retrieval_gate.v1.jsonl")
    retrieval.add_argument("--label", default="candidate")
    retrieval.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "retrieval.json")
    retrieval.add_argument("--include-needs-review", action="store_true")
    memory = subparsers.add_parser("run-memory")
    memory.add_argument("--dataset", type=Path, default=DATASETS / "memory.v1.jsonl")
    memory.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "memory.json")
    memory.add_argument("--include-needs-review", action="store_true")
    robustness = subparsers.add_parser("run-robustness")
    robustness.add_argument("--dataset", type=Path, default=DATASETS / "robustness.v1.jsonl")
    robustness.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "robustness.json")
    judge = subparsers.add_parser("run-judge-calibration")
    judge.add_argument("--dataset", type=Path, default=DATASETS / "judge_calibration.v1.jsonl")
    judge.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "judge-calibration.json")
    ablation = subparsers.add_parser("run-retrieval-ablation")
    ablation.add_argument("--dataset", type=Path, default=DATASETS / "retrieval_gate.v1.jsonl")
    ablation.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "retrieval-ablation.json")
    personalization = subparsers.add_parser("run-personalization")
    personalization.add_argument("--dataset", type=Path, default=DATASETS / "personalization.v1.jsonl")
    personalization.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "personalization.json")
    memory_retrieval = subparsers.add_parser("run-memory-retrieval")
    memory_retrieval.add_argument("--dataset", type=Path, default=DATASETS / "memory_retrieval.v1.jsonl")
    memory_retrieval.add_argument("--corpus", type=Path, default=DATASETS / "memory_corpus.v1.json")
    memory_retrieval.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "memory-retrieval.json")
    stress = subparsers.add_parser("run-stress")
    stress.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "stress.json")
    agreement = subparsers.add_parser("run-annotation-agreement")
    agreement.add_argument("--dataset", type=Path, default=DATASETS / "annotation_agreement.v1.jsonl")
    agreement.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "annotation-agreement.json")
    coverage = subparsers.add_parser("audit-coverage")
    coverage.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "coverage-audit.json")
    stream = subparsers.add_parser("run-stream-latency")
    stream.add_argument("--dataset", type=Path, default=DATASETS / "agent_gate.v1.jsonl")
    stream.add_argument("--output", type=Path, default=ROOT / "evals" / "reports" / "stream-latency.json")
    stream.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.command == "validate":
        code = _validate()
    elif args.command == "migrate-legacy":
        code = _migrate()
    elif args.command == "compare":
        code = _compare(args.candidate, args.baseline)
    elif args.command == "promote-baseline":
        code = _promote(args.candidate, args.output)
    elif args.command == "run-agent":
        from .runners.agent import run_agent_eval

        report = run_agent_eval(
            root=ROOT,
            dataset_path=args.dataset,
            base_url=args.base_url,
            model=args.model,
            repeats=args.repeats,
            include_needs_review=args.include_needs_review,
            label=args.label,
            output_path=args.output,
            mode=args.mode,
            case_ids=set(args.case_ids or []),
            judge_enabled=args.judge,
        )
        print(json.dumps(report.summary, ensure_ascii=False, indent=2))
        code = 0
    elif args.command == "run-retrieval":
        from steam_agent.tools.rag_search import rag_search_similar_games
        from .runners.retrieval import run_retrieval_eval

        report = run_retrieval_eval(
            root=ROOT,
            dataset_path=args.dataset,
            search=rag_search_similar_games,
            include_needs_review=args.include_needs_review,
            label=args.label,
            output_path=args.output,
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0
    elif args.command == "run-memory":
        from .runners.memory import run_memory_eval

        report = run_memory_eval(ROOT, args.dataset, args.include_needs_review)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        from .artifacts import write_companion_artifacts
        write_companion_artifacts(report, args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["task_success"] == 1.0 else 1
    elif args.command == "run-robustness":
        from .artifacts import write_companion_artifacts
        from .runners.robustness import run_robustness_eval

        report = run_robustness_eval(args.dataset)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        write_companion_artifacts(report, args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["task_success"] == 1.0 else 1
    elif args.command == "run-judge-calibration":
        from .runners.judge import calibration_passed, run_judge_calibration

        report = run_judge_calibration(root=ROOT, dataset_path=args.dataset, output_path=args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if calibration_passed(report["summary"]) else 1
    elif args.command == "run-retrieval-ablation":
        from .runners.ablation import run_retrieval_ablation

        report = run_retrieval_ablation(root=ROOT, dataset_path=args.dataset, output_path=args.output)
        print(json.dumps({
            "selected_on_dev": report["selected_on_dev"],
            "delta_vs_selected": report["delta_vs_selected"],
        }, ensure_ascii=False, indent=2))
        code = 0
    elif args.command == "run-personalization":
        from .runners.personalization import run_personalization_eval

        report = run_personalization_eval(root=ROOT, dataset_path=args.dataset, output_path=args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["task_success"] >= 0.8 else 1
    elif args.command == "run-memory-retrieval":
        from .runners.memory_retrieval import run_memory_retrieval_eval

        report = run_memory_retrieval_eval(
            root=ROOT,
            dataset_path=args.dataset,
            corpus_path=args.corpus,
            output_path=args.output,
        )
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0
    elif args.command == "run-stress":
        import asyncio
        from .runners.stress import run_stress_eval

        report = asyncio.run(run_stress_eval(args.output))
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["task_success"] == 1.0 else 1
    elif args.command == "run-annotation-agreement":
        from .runners.agreement import run_annotation_agreement

        report = run_annotation_agreement(args.dataset, args.output)
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["gate_passed"] else 1
    elif args.command == "audit-coverage":
        from .audit import audit_coverage, write_audit

        report = audit_coverage(DATASETS)
        write_audit(report, args.output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        code = 0 if report["passed"] else 1
    else:
        import asyncio
        from .runners.stream import run_stream_latency_eval

        report = asyncio.run(run_stream_latency_eval(
            root=ROOT,
            dataset_path=args.dataset,
            output_path=args.output,
            limit=args.limit,
        ))
        print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
        code = 0 if report["summary"]["task_success"] == 1.0 else 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
