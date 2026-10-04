"""
RAG retrieval evaluation with LLM-as-Judge for open recommendations.

Each ground truth CSV gets its own results and changelog files:
  gt_semantic.csv -> gt_semantic_results.csv + gt_semantic_changelog.csv

Usage:
    python -m tests.rag_eval --label baseline
    python -m tests.rag_eval -g gt_semantic --label v2 --note "chunk: v1 -> v2"
    python -m tests.rag_eval -g gt_filtered --label v1 --note "filter: off -> on"
    python -m tests.rag_eval -g gt_semantic --query-id 3
"""

import argparse
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
DEFAULT_GT = "gt_semantic_v2.csv"
DEFAULT_TOP_K = 8


def judge_recommendations(case: dict, items: list[dict]) -> dict:
    from .llm_judge import _judge_one

    request = json.dumps({
        "query": case["query"],
        "constraints": {key: case.get(key) for key in ("free_only", "min_year", "filter_tags")},
    }, ensure_ascii=False)
    try:
        verdict = _judge_one(request, json.dumps(items, ensure_ascii=False))
        return {"judge_score": verdict["score"], "judge_reason": verdict["reason"], "judge_error": ""}
    except Exception as exc:
        return {"judge_score": None, "judge_reason": "", "judge_error": str(exc)}


def recall_at_k(retrieved: list[str], relevant: list[str]) -> float:
    if not relevant:
        return None
    return len(set(retrieved) & set(relevant)) / len(relevant)


def precision_at_k(retrieved: list[str], relevant: list[str], k: int) -> float:
    if k <= 0:
        return 0.0
    if not relevant:
        return None
    relevant_set = set(relevant)
    return sum(item in relevant_set for item in retrieved[:k]) / k


def reciprocal_rank(retrieved: list[str], relevant: list[str]) -> float:
    if not relevant:
        return None
    relevant_set = set(relevant)
    return next(
        (1.0 / rank for rank, item in enumerate(retrieved, start=1) if item in relevant_set),
        0.0,
    )


def ndcg_at_k(retrieved: list[str], grades: dict[str, int], k: int) -> float:
    import math
    def dcg(values):
        return sum((2 ** value - 1) / math.log2(index + 2) for index, value in enumerate(values))
    actual = dcg([grades.get(appid, 0) for appid in retrieved[:k]])
    ideal = dcg(sorted(grades.values(), reverse=True)[:k])
    return actual / ideal if ideal else 1.0


def load_ground_truth(path: Path) -> list[dict]:
    """Load CSV. Parse hard-constraint columns (free_only, min_year)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        cases = []
        for row in reader:
            row["top_k"] = int(row["top_k"])
            row["relevant_appids"] = [
                aid.strip() for aid in row["relevant_appids"].split(";") if aid.strip()
            ]
            # Parse optional hard-constraint columns
            row["free_only"] = row.get("free_only", "").strip() == "1"
            year_str = row.get("min_year", "").strip()
            row["min_year"] = int(year_str) if year_str else None
            row["relevance_grades"] = {
                item.split(":", 1)[0]: int(item.split(":", 1)[1])
                for item in row.get("relevance_grades", "").split(";")
                if ":" in item
            }
            cases.append(row)
        return cases


def run_batch(cases: list[dict], top_k_override: int | None = DEFAULT_TOP_K, min_sim: float = 0, no_filters: bool = False) -> list[dict]:
    # Support both ``python -m tests.rag_eval`` from the parent
    # directory and ``python -m tests.rag_eval`` from the package directory.
    try:
        from ..tools.rag_search import rag_search_similar_games
        from ..tools.search_plan import SearchPlan
    except ImportError:
        # When launched from inside the package directory, restore the
        # package parent so tools with their own relative imports remain valid.
        package_parent = str(Path(__file__).resolve().parents[2])
        if package_parent not in sys.path:
            sys.path.insert(0, package_parent)
        from tools.rag_search import rag_search_similar_games
        from tools.search_plan import SearchPlan

    results = []
    for i, case in enumerate(cases):
        k = top_k_override or case["top_k"]
        query = case["query"]
        relevant = case["relevant_appids"]

        fo = False if no_filters else case.get("free_only", False)
        my = None if no_filters else case.get("min_year")
        genre = None if no_filters else (case.get("filter_tags") or None)

        rag_result = rag_search_similar_games(plan=SearchPlan(
            query=query, top_k=k, free_only=fo, min_year=my,
            genre=genre, min_similarity=min_sim,
        ))
        items = rag_result.get("results", [])
        retrieved = [str(item["appid"]) for item in items]
        is_open = case.get("evaluation_type") == "open_recommendation" or not relevant
        if is_open:
            relevant = []
        verdict = judge_recommendations(case, items) if is_open else {
            "judge_score": None, "judge_reason": "", "judge_error": "",
        }
        recall = recall_at_k(retrieved, relevant)
        precision = precision_at_k(retrieved, relevant, k)
        mrr = reciprocal_rank(retrieved, relevant)
        grades = case.get("relevance_grades", {})
        ndcg = ndcg_at_k(retrieved, grades, k) if grades and not is_open else None
        weak_coverage = sum(1 for appid in retrieved if grades.get(appid, 0) > 0) / k if k else 0.0

        filters_used = []
        if fo:
            filters_used.append("free")
        if my:
            filters_used.append(f"y>={my}")

        violations = []
        for item in items:
            if fo and not item.get("is_free", False):
                violations.append(f"{item['appid']}:free")
            if my is not None and int(item.get("release_year", 0) or 0) < my:
                violations.append(f"{item['appid']}:year")

        results.append({
            **verdict,
            "query": query,
            "top_k": k,
            "relevant_appids": ";".join(relevant),
            "retrieved_appids": ";".join(retrieved),
            "retrieved_names": ";".join(item["name"] for item in items),
            "retrieved_evidence": json.dumps(items, ensure_ascii=False),
            "recall": recall,
            "precision": precision,
            "mrr": mrr,
            "ndcg": ndcg,
            "weak_coverage": weak_coverage,
            "filters": ", ".join(filters_used),
            "filter_satisfaction": 1.0 if not items or not violations else 0.0,
            "filter_violations": ";".join(violations),
        })

        filter_str = f"  [{', '.join(filters_used)}]" if filters_used else ""
        status = "OPEN" if not relevant else ("OK" if recall >= 0.5 else "LOW")
        score_text = (
            f"Judge={verdict['judge_score']} error={verdict['judge_error']}" if is_open else
            f"R={recall:.2f} P={precision:.2f} MRR={mrr:.2f}"
        )
        print(f"{i+1:2d}/{len(cases)} [{status}] {query[:45]:<45s} "
              f"{score_text}  "
              f"top={items[0]['name'][:20] if items else 'N/A'}{filter_str}")

    return results


# ── CSV I/O ────────────────────────────────────────────────────────────

def _derive_paths(gt_path: Path) -> tuple[Path, Path]:
    """gt_xxx.csv -> (gt_xxx_results.csv, gt_xxx_changelog.csv)."""
    stem = gt_path.stem  # e.g. "gt_semantic"
    return (
        gt_path.parent / f"{stem}_results.csv",
        gt_path.parent / f"{stem}_changelog.csv",
    )


def write_results(results: list[dict], label: str, result_path: Path):
    existing_headers, existing_rows = [], []
    if result_path.exists():
        with open(result_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_headers = reader.fieldnames or []
            existing_rows = list(reader)

    metric_columns = {
        metric: f"{metric}_{label}" for metric in ("recall", "precision", "mrr", "ndcg", "judge_score")
    }

    if not existing_headers:
        headers = ["query", "top_k", "relevant_appids", *metric_columns.values(), "filters", "filter_satisfaction", "filter_violations", "retrieved_names"]
        rows_out = []
        for r in results:
            rows_out.append({
                "query": r["query"],
                "top_k": str(r["top_k"]),
                "relevant_appids": r["relevant_appids"],
            **{column: (f"{r[metric]:.4f}" if r[metric] is not None else "") for metric, column in metric_columns.items()},
                "filters": r["filters"],
                "filter_satisfaction": f"{r['filter_satisfaction']:.4f}",
                "filter_violations": r["filter_violations"],
                "retrieved_names": r["retrieved_names"],
            })
    else:
        headers = existing_headers + [column for column in metric_columns.values() if column not in existing_headers]
        headers += [column for column in ("filter_satisfaction", "filter_violations") if column not in headers]
        rows_out = []
        previous_by_query = {row.get("query"): row for row in existing_rows}
        for r in results:
            row = dict(previous_by_query.get(r["query"], {}))
            row.update({column: (f"{r[metric]:.4f}" if r[metric] is not None else "") for metric, column in metric_columns.items()})
            row["filter_satisfaction"] = f"{r['filter_satisfaction']:.4f}"
            row["filter_violations"] = r["filter_violations"]
            rows_out.append(row)

    evidence_columns = [f"{key}_{label}" for key in ("judge_reason", "judge_error", "retrieved_appids", "retrieved_evidence")]
    headers += [column for column in evidence_columns if column not in headers]
    for row, result in zip(rows_out, results):
        row.update({f"{key}_{label}": result.get(key, "") for key in ("judge_reason", "judge_error", "retrieved_appids", "retrieved_evidence")})
        row.update({key: result[key] for key in ("query", "top_k", "relevant_appids", "retrieved_names", "filters")})
    with open(result_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows_out)

    n = len(results)
    averages = {
        metric: (sum(r[metric] for r in results if r[metric] is not None) /
                 sum(1 for r in results if r[metric] is not None))
        if any(r[metric] is not None for r in results) else 0.0
        for metric in metric_columns
    }
    print(f"\n  -> {result_path.name}  [{label}] "
          f"Recall={averages['recall']:.3f} Precision={averages['precision']:.3f} "
          f"MRR={averages['mrr']:.3f}")


def write_changelog(label: str, note: str, averages: dict[str, float], top_k: int, cl_path: Path):
    existing_headers, existing_rows = [], []
    if cl_path.exists():
        with open(cl_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            existing_headers = reader.fieldnames or []
            existing_rows = list(reader)
    for row in existing_rows:
        overflow = row.pop(None, [])
        for column, value in zip(("avg_precision", "avg_mrr"), overflow):
            row.setdefault(column, value)
    required_headers = [
        "label", "date", "top_k", "variable_changed",
        "avg_recall", "avg_precision", "avg_mrr", "avg_judge_score",
    ]
    headers = existing_headers + [
        column for column in required_headers if column not in existing_headers
    ]
    rows = [
        {column: row.get(column, "") for column in headers}
        for row in existing_rows
    ]
    rows.append({
        "label": label,
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "top_k": top_k,
        "variable_changed": note,
        **{f"avg_{key}": f"{value:.4f}" if value is not None else ""
           for key, value in averages.items() if f"avg_{key}" in headers},
    })
    with open(cl_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> {cl_path.name}: {note or '(无)'}")


# ── summary ────────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    if not results:
        print("No evaluation cases to summarize.")
        return
    scored = [r for r in results if r["recall"] is not None]
    n = len(scored)
    open_count = len(results) - n
    scores = [r["judge_score"] for r in results if r.get("judge_score") is not None]
    errors = sum(bool(r.get("judge_error")) for r in results)
    print(f"LLM Judge: scored={len(scores)} errors={errors}; "
          f"average={sum(scores) / len(scores) if scores else 'N/A'}")
    if not scored:
        print(f"OPEN queries: {open_count}; no fixed-ID metrics available.")
        return
    averages = {
        metric: sum(r[metric] for r in scored) / n
        for metric in ("recall", "precision", "mrr")
    }
    ndcg_scores = [r["ndcg"] for r in scored if r["ndcg"] is not None]
    averages["ndcg"] = sum(ndcg_scores) / len(ndcg_scores) if ndcg_scores else 0.0
    averages["filter_satisfaction"] = sum(r["filter_satisfaction"] for r in results) / len(results)
    perfect = sum(1 for r in scored if r["recall"] >= 1.0)
    good = sum(1 for r in scored if 0.5 <= r["recall"] < 1.0)
    poor = sum(1 for r in scored if r["recall"] < 0.5)

    print(f"\n{'='*50}")
    print(f"  RAG Recall  |  {n} fixed-ID queries; OPEN={open_count}")
    print(f"{'='*50}")
    print(f"  Recall@k    avg: {averages['recall']:.3f}")
    print(f"  Precision@k avg: {averages['precision']:.3f}")
    print(f"  MRR         avg: {averages['mrr']:.3f}")
    print(f"  NDCG@k      avg: {averages['ndcg']:.3f}")
    print(f"  Filter satisfaction avg: {averages['filter_satisfaction']:.3f}")
    print(f"  Recall=1.0   (perfect):  {perfect}")
    print(f"  Recall>=0.5  (usable):   {good}")
    print(f"  Recall<0.5   (poor):     {poor}")
    print(f"{'='*50}")

    if poor:
        print(f"\nPoor queries:")
        for r in scored:
            if r["recall"] < 0.5:
                print(f"  recall={r['recall']:.2f}  [{r['query'][:45]}]")
                print(f"    expected: {r['relevant_appids']}")
                print(f"    got:      {r['retrieved_appids']}")


# ── main ───────────────────────────────────────────────────────────────

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="RAG recall evaluation")
    parser.add_argument("-g", "--ground-truth", default=DEFAULT_GT,
                        help=f"Ground truth CSV (default: {DEFAULT_GT})")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="Games per question (default: 8)")
    parser.add_argument("--query-id", type=int, help="Single query by index (1-based)")
    parser.add_argument("--label", default="", help="Version label (auto timestamp if empty)")
    parser.add_argument("--note", default="", help="What variable changed and what it changed to")
    parser.add_argument("--min-sim", type=float, default=0, help="min_similarity threshold (default 0=off)")
    parser.add_argument("--no-filters", action="store_true", help="Ignore all CSV hard-constraint filters")
    args = parser.parse_args()
    if args.top_k <= 0:
        parser.error("--top-k must be positive")

    gt_path = TESTS_DIR / args.ground_truth
    if not gt_path.exists():
        print(f"ERROR: {gt_path} not found")
        sys.exit(1)

    result_path, changelog_path = _derive_paths(gt_path)

    cases = load_ground_truth(gt_path)
    print(f"Loaded {len(cases)} queries from {gt_path.name}\n")

    if args.query_id:
        cases = [cases[args.query_id - 1]]

    results = run_batch(cases, top_k_override=args.top_k, min_sim=args.min_sim, no_filters=args.no_filters)
    print_summary(results)

    if not args.query_id:
        label = args.label or datetime.now().strftime("%m%d_%H%M")
        top_k = args.top_k or cases[0]["top_k"]
        write_results(results, label, result_path)
        averages = {
            metric: (sum(r[metric] for r in results if r[metric] is not None) /
                     sum(r[metric] is not None for r in results))
            if any(r[metric] is not None for r in results) else None
            for metric in ("recall", "precision", "mrr", "ndcg", "judge_score")
        }
        write_changelog(label, args.note, averages, top_k, changelog_path)


if __name__ == "__main__":
    main()
