"""
RAG retrieval evaluation — Recall@K only. No LLM calls, no token cost.

Each ground truth CSV gets its own results and changelog files:
  gt_semantic.csv -> gt_semantic_results.csv + gt_semantic_changelog.csv

Usage:
    python -m steam_agent.tests.rag_eval --label baseline
    python -m steam_agent.tests.rag_eval -g gt_semantic --label v2 --note "chunk: v1 -> v2"
    python -m steam_agent.tests.rag_eval -g gt_filtered --label v1 --note "filter: off -> on"
    python -m steam_agent.tests.rag_eval -g gt_semantic --query-id 3
"""

import argparse
import csv
import io
import sys
from datetime import datetime
from pathlib import Path

from ..tools.rag_search import rag_search_similar_games

TESTS_DIR = Path(__file__).resolve().parent
DEFAULT_GT = "gt_semantic.csv"


def recall_at_k(retrieved: list[str], relevant: list[str]) -> float:
    if not relevant:
        return 1.0
    return sum(1 for r in relevant if r in retrieved) / len(relevant)


def load_ground_truth(path: Path) -> list[dict]:
    """Load CSV. Auto-detect filter columns (free_only, filter_tags, min_year)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        has_filters = any(h in headers for h in ["free_only", "filter_tags", "min_year"])

        cases = []
        for row in reader:
            row["top_k"] = int(row["top_k"])
            row["relevant_appids"] = [
                aid.strip() for aid in row["relevant_appids"].split(";") if aid.strip()
            ]
            if has_filters:
                row["free_only"] = row.get("free_only", "").strip() == "1"
                row["min_year"] = int(row["min_year"]) if row.get("min_year", "").strip() else None
                raw_tags = row.get("filter_tags", "").strip()
                row["filter_tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else None
            cases.append(row)
        return cases


def run_batch(cases: list[dict], top_k_override: int | None = None, min_sim: float = 0, filter_mode: str = "all") -> list[dict]:
    results = []
    for i, case in enumerate(cases):
        k = top_k_override or case["top_k"]
        query = case["query"]
        relevant = case["relevant_appids"]

        ft = case.get("filter_tags") if filter_mode in ("all", "tags") else None
        fo = case.get("free_only", False) if filter_mode in ("all", "free") else False
        my = case.get("min_year") if filter_mode in ("all", "year") else None

        rag_result = rag_search_similar_games(
            query,
            top_k=k,
            filter_tags=ft,
            free_only=fo,
            min_year=my,
            min_similarity=min_sim,
        )
        items = rag_result.get("results", [])
        retrieved = [item["appid"] for item in items]
        recall = recall_at_k(retrieved, relevant)

        filters_used = []
        if filter_mode == "free" or (filter_mode == "all" and case.get("free_only")):
            filters_used.append("free")
        if filter_mode == "year" or (filter_mode == "all" and case.get("min_year")):
            filters_used.append(f"y>={case['min_year']}")
        if filter_mode == "tags" or (filter_mode == "all" and case.get("filter_tags")):
            ft = case.get("filter_tags") or []
            filters_used.append(f"tags={','.join(ft)}")

        results.append({
            "query": query,
            "top_k": k,
            "relevant_appids": ";".join(relevant),
            "retrieved_appids": ";".join(retrieved),
            "retrieved_names": ";".join(item["name"] for item in items),
            "recall": recall,
            "filters": ", ".join(filters_used),
        })

        filter_str = f"  [{', '.join(filters_used)}]" if filters_used else ""
        status = "OK" if recall >= 0.5 else "LOW"
        print(f"{i+1:2d}/{len(cases)} [{status}] {query[:45]:<45s} "
              f"recall={recall:.2f}  top={items[0]['name'][:20] if items else 'N/A'}{filter_str}")

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

    recall_col = f"recall_{label}"

    if not existing_headers:
        headers = ["query", "top_k", "relevant_appids", recall_col, "filters", "retrieved_names"]
        rows_out = []
        for r in results:
            rows_out.append({
                "query": r["query"],
                "top_k": str(r["top_k"]),
                "relevant_appids": r["relevant_appids"],
                recall_col: f"{r['recall']:.4f}",
                "filters": r["filters"],
                "retrieved_names": r["retrieved_names"],
            })
    else:
        headers = existing_headers + [recall_col]
        rows_out = []
        for i, r in enumerate(results):
            row = dict(existing_rows[i]) if i < len(existing_rows) else {}
            row[recall_col] = f"{r['recall']:.4f}"
            rows_out.append(row)

    with open(result_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows_out)

    n = len(results)
    avg_r = sum(r["recall"] for r in results) / n
    print(f"\n  -> {result_path.name}  [{label}] Recall avg={avg_r:.3f}")


def write_changelog(label: str, note: str, avg_recall: float, top_k: int, cl_path: Path):
    file_exists = cl_path.exists()
    with open(cl_path, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["label", "date", "top_k", "variable_changed", "avg_recall"])
        writer.writerow([
            label,
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            top_k,
            note,
            f"{avg_recall:.4f}",
        ])
    print(f"  -> {cl_path.name}: {note or '(无)'}")


# ── summary ────────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    n = len(results)
    avg_recall = sum(r["recall"] for r in results) / n
    perfect = sum(1 for r in results if r["recall"] >= 1.0)
    good = sum(1 for r in results if 0.5 <= r["recall"] < 1.0)
    poor = sum(1 for r in results if r["recall"] < 0.5)

    print(f"\n{'='*50}")
    print(f"  RAG Recall  |  {n} queries")
    print(f"{'='*50}")
    print(f"  Recall@{results[0]['top_k'] if results else 'K'}  avg: {avg_recall:.3f}")
    print(f"  Recall=1.0   (perfect):  {perfect}")
    print(f"  Recall>=0.5  (usable):   {good}")
    print(f"  Recall<0.5   (poor):     {poor}")
    print(f"{'='*50}")

    if poor:
        print(f"\nPoor queries:")
        for r in results:
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
    parser.add_argument("--top-k", type=int, help="Override top_k for all queries")
    parser.add_argument("--query-id", type=int, help="Single query by index (1-based)")
    parser.add_argument("--label", default="", help="Version label (auto timestamp if empty)")
    parser.add_argument("--note", default="", help="What variable changed and what it changed to")
    parser.add_argument("--min-sim", type=float, default=0, help="min_similarity threshold (default 0=off)")
    parser.add_argument("--filter-mode", choices=["all", "none", "tags", "free", "year"], default="all",
                        help="Which filters to apply: all (default), none, tags, free, year")
    args = parser.parse_args()

    gt_path = TESTS_DIR / args.ground_truth
    if not gt_path.exists():
        print(f"ERROR: {gt_path} not found")
        sys.exit(1)

    result_path, changelog_path = _derive_paths(gt_path)

    cases = load_ground_truth(gt_path)
    print(f"Loaded {len(cases)} queries from {gt_path.name}\n")

    if args.query_id:
        cases = [cases[args.query_id - 1]]

    results = run_batch(cases, top_k_override=args.top_k, min_sim=args.min_sim, filter_mode=args.filter_mode)
    print_summary(results)

    if not args.query_id:
        label = args.label or datetime.now().strftime("%m%d_%H%M")
        top_k = args.top_k or cases[0]["top_k"]
        avg_r = sum(r["recall"] for r in results) / len(results)
        write_results(results, label, result_path)
        write_changelog(label, args.note, avg_r, top_k, changelog_path)


if __name__ == "__main__":
    main()
