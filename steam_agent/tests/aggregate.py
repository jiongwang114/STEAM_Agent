"""
Aggregate multi-run evaluation results into a statistical summary.

Reads eval_detailed.csv and computes:
  - Per-category pass rates, latency (P50/P95/P99), token usage, hallucination rate
  - Overall stats and a terminal + JSON report

Usage:
    python -m tests.aggregate                        # summarize eval_detailed.csv
    python -m tests.aggregate --csv eval_detailed.csv
    python -m tests.aggregate --json report.json      # also write JSON
"""

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_CSV = HERE / "eval_detailed.csv"


def load_results(path: Path) -> tuple[list[str], list[dict]]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or [], list(reader)


def _p(sorted_vals: list[float], percentile: int) -> float:
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    idx = max(0, min(math.ceil(n * percentile / 100) - 1, n - 1))
    return sorted_vals[idx]


def _pct(part: float, total: float) -> str:
    return f"{part / total * 100:.1f}%" if total else "0%"


def summarize(rows: list[dict]) -> dict:
    """Compute all aggregate metrics."""
    valid = [r for r in rows if not r.get("错误", "")]

    # --- basic counts ---
    total = len(valid)
    passed = sum(1 for r in valid if r.get("是否通过", "").strip() == "PASS")
    failed = total - passed

    # --- by category ---
    by_cat: dict[str, dict] = {}
    for r in valid:
        cat = r.get("category", r.get("类别", "unknown"))
        if cat not in by_cat:
            by_cat[cat] = {"total": 0, "passed": 0, "durations": [], "tokens": []}
        by_cat[cat]["total"] += 1
        if r.get("是否通过", "").strip() == "PASS":
            by_cat[cat]["passed"] += 1
        try:
            d = float(r.get("耗时(s)", 0))
        except ValueError:
            d = 0.0
        if d > 0:
            by_cat[cat]["durations"].append(d)
        try:
            tok = int(r.get("总token", 0))
        except ValueError:
            tok = 0
        if tok > 0:
            by_cat[cat]["tokens"].append(tok)

    # --- latency ---
    all_durations = sorted(
        float(r.get("耗时(s)", 0)) for r in valid
        if r.get("耗时(s)", "") and float(r.get("耗时(s)", 0)) > 0
    )
    latency = {
        "count": len(all_durations),
        "avg": round(sum(all_durations) / len(all_durations), 2) if all_durations else 0,
        "p50": round(_p(all_durations, 50), 2),
        "p95": round(_p(all_durations, 95), 2),
        "p99": round(_p(all_durations, 99), 2),
    }

    # --- token ---
    token_vals = [
        (int(r.get("输入token", 0) or 0), int(r.get("输出token", 0) or 0), int(r.get("总token", 0) or 0))
        for r in valid
        if r.get("总token", "") and int(r.get("总token", 0) or 0) > 0
    ]
    if token_vals:
        in_toks = sorted(t[0] for t in token_vals)
        out_toks = sorted(t[1] for t in token_vals)
        all_toks = sorted(t[2] for t in token_vals)
    else:
        in_toks = out_toks = all_toks = []

    token_stats = {
        "avg_input": round(sum(in_toks) / len(in_toks)) if in_toks else 0,
        "avg_output": round(sum(out_toks) / len(out_toks)) if out_toks else 0,
        "avg_total": round(sum(all_toks) / len(all_toks)) if all_toks else 0,
        "total_all": sum(all_toks),
    }

    # --- tool rounds ---
    rounds_vals = [
        int(r.get("工具轮次", 0)) for r in valid
        if r.get("工具轮次", "") and int(r.get("工具轮次", 0)) > 0
    ]
    round_dist = dict(sorted(Counter(rounds_vals).items())) if rounds_vals else {}

    # --- hallucination ---
    hallu_count = sum(1 for r in valid if r.get("幻觉数", "") and int(r.get("幻觉数", 0) or 0) > 0)

    # --- diversity ---
    tag_counts = [
        int(r.get("标签数", 0)) for r in valid
        if r.get("标签数", "") and int(r.get("标签数", 0) or 0) > 0
    ]
    entropies = [
        float(r.get("标签熵", 0)) for r in valid
        if r.get("标签熵", "") and float(r.get("标签熵", 0) or 0) > 0
    ]

    # --- save_insight ---
    insight_triggered = sum(1 for r in valid if r.get("触发save_insight", "").strip().lower() == "true")

    # --- judge scores (if LLM-as-Judge has been run) ---
    judge_scores = [
        int(r.get("judge_score", 0)) for r in valid
        if r.get("judge_score", "") and int(r.get("judge_score", 0) or 0) > 0
    ]

    # --- build per-category summary ---
    cat_summary = {}
    for cat, data in sorted(by_cat.items()):
        durs = sorted(data["durations"])
        toks = sorted(data["tokens"])
        cat_summary[cat] = {
            "total": data["total"],
            "passed": data["passed"],
            "pass_rate": _pct(data["passed"], data["total"]),
            "avg_latency": round(sum(durs) / len(durs), 2) if durs else 0,
            "avg_tokens": round(sum(toks) / len(toks)) if toks else 0,
        }

    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "pass_rate": _pct(passed, total),
        "latency": latency,
        "token": token_stats,
        "tool_rounds_dist": round_dist,
        "hallucination_rate": f"{hallu_count}/{total} ({_pct(hallu_count, total)})",
        "avg_tag_count": round(sum(tag_counts) / len(tag_counts), 1) if tag_counts else 0,
        "avg_entropy": round(sum(entropies) / len(entropies), 3) if entropies else 0,
        "save_insight_triggered": insight_triggered,
        "judge_avg_score": round(sum(judge_scores) / len(judge_scores), 2) if judge_scores else None,
        "judge_gte_4": sum(1 for s in judge_scores if s >= 4) if judge_scores else None,
        "judge_count": len(judge_scores),
        "by_category": cat_summary,
    }


def print_report(report: dict):
    print(f"\n{'=' * 70}")
    print(f"  Steam Agent 多维评测聚合报告")
    print(f"{'=' * 70}")

    print(f"\n  [概览] {report['total']} 条 | 通过 {report['passed']}/{report['total']} "
          f"({report['pass_rate']})")

    print(f"\n  [延迟]")
    l = report["latency"]
    print(f"    avg={l['avg']}s  P50={l['p50']}s  P95={l['p95']}s  P99={l['p99']}s")

    print(f"\n  [Token]")
    t = report["token"]
    print(f"    平均 输入={t['avg_input']}  输出={t['avg_output']}  总={t['avg_total']}")
    print(f"    全量总计={t['total_all']}")

    print(f"\n  [工具调用轮次分布]")
    for k, v in report["tool_rounds_dist"].items():
        bar = "#" * v
        print(f"    {k}轮: {v} {bar}")

    print(f"\n  [幻觉率] {report['hallucination_rate']}")

    if report["avg_tag_count"]:
        print(f"  [多样性] 平均标签数: {report['avg_tag_count']}  平均熵: {report['avg_entropy']}")

    print(f"  [save_insight触发] {report['save_insight_triggered']} 次")

    if report["judge_avg_score"] is not None:
        print(f"\n  [LLM-as-Judge] avg={report['judge_avg_score']}  "
              f">=4: {report['judge_gte_4']}/{report['judge_count']}")

    print(f"\n  [按类别]")
    for cat, data in report["by_category"].items():
        print(f"    {cat:12s}: {data['pass_rate']:>6s}  延迟={data['avg_latency']}s  "
              f"token={data['avg_tokens']}")


def main():
    parser = argparse.ArgumentParser(description="Aggregate evaluation results")
    parser.add_argument("--csv", default=str(DEFAULT_CSV), help="Input CSV path")
    parser.add_argument("--json", default="", help="Also write JSON report to path")
    args = parser.parse_args()

    path = Path(args.csv)
    print(f"加载: {path}")
    _, rows = load_results(path)
    print(f"共 {len(rows)} 行数据")

    report = summarize(rows)
    print_report(report)

    if args.json:
        json_path = Path(args.json)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        print(f"\nJSON 报告: {json_path}")


if __name__ == "__main__":
    main()
