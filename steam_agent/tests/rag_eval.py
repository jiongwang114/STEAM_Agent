"""
RAG retrieval evaluation — measures embedding/retrieval quality directly.
No LLM calls, no token cost. Pure vector search metrics.

Each run appends a new column group to rag_eval_results.csv for easy before/after comparison.

Usage:
    python -m steam_agent.tests.rag_eval                         # auto-increment version
    python -m steam_agent.tests.rag_eval --label v2              # named version
    python -m steam_agent.tests.rag_eval --label baseline        # first run
    python -m steam_agent.tests.rag_eval --top-k 5 --label top5  # override top_k
    python -m steam_agent.tests.rag_eval --query-id 3            # single query, no csv write
"""

import argparse
import csv
import io
import math
import sys
from datetime import datetime
from pathlib import Path

from ..tools.rag_search import rag_search_similar_games

CSV_PATH = Path(__file__).resolve().parent / "rag_ground_truth.csv"
RESULT_CSV_PATH = Path(__file__).resolve().parent / "rag_eval_results.csv"


# ── metrics ──────────────────────────────────────────────────────────

def recall_at_k(retrieved: list[str], relevant: list[str]) -> float:
    if not relevant:
        return 1.0
    return sum(1 for r in relevant if r in retrieved) / len(relevant)


def mrr(retrieved: list[str], relevant: list[str]) -> float:
    for i, rid in enumerate(retrieved):
        if rid in relevant:
            return 1.0 / (i + 1)
    return 0.0


def ndcg(retrieved: list[str], relevant: list[str], k: int) -> float:
    dcg = 0.0
    for i, rid in enumerate(retrieved[:k]):
        if rid in relevant:
            dcg += 1.0 / math.log2(i + 2)

    ideal_count = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(i + 2) for i in range(ideal_count))

    return dcg / idcg if idcg > 0 else 0.0


# ── ground truth ─────────────────────────────────────────────────────

def load_ground_truth(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        cases = []
        for row in csv.DictReader(f):
            row["top_k"] = int(row["top_k"])
            row["relevant_appids"] = [
                aid.strip() for aid in row["relevant_appids"].split(";") if aid.strip()
            ]
            cases.append(row)
        return cases


# ── run ──────────────────────────────────────────────────────────────

def run_batch(cases: list[dict], top_k_override: int | None = None) -> list[dict]:
    results = []
    for i, case in enumerate(cases):
        k = top_k_override or case["top_k"]
        query = case["query"]
        relevant = case["relevant_appids"]

        rag_result = rag_search_similar_games(query, top_k=k)
        items = rag_result.get("results", [])
        retrieved = [item["appid"] for item in items]

        recall = recall_at_k(retrieved, relevant)
        mrr_val = mrr(retrieved, relevant)
        ndcg_val = ndcg(retrieved, relevant, k)

        results.append({
            "query": query,
            "top_k": k,
            "relevant_appids": ";".join(relevant),
            "retrieved_appids": ";".join(retrieved),
            "retrieved_names": ";".join(item["name"] for item in items),
            "top_score": items[0]["similarity_score"] if items else 0.0,
            "recall": recall,
            "mrr": mrr_val,
            "ndcg": ndcg_val,
        })

        status = "OK" if recall >= 0.5 else "LOW"
        print(f"{i+1:2d}/{len(cases)} [{status}] {query[:50]:<50s} "
              f"recall={recall:.2f}  mrr={mrr_val:.2f}  ndcg={ndcg_val:.2f}  "
              f"top={items[0]['name'][:25] if items else 'N/A'}")

    return results


# ── CSV: append-column mode ──────────────────────────────────────────

def _read_existing_csv() -> tuple[list[str], list[dict]]:
    """Read existing result CSV. Returns (headers, rows)."""
    if not RESULT_CSV_PATH.exists():
        return [], []

    with open(RESULT_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def write_results(results: list[dict], label: str):
    """Append new metric columns to the shared result CSV."""
    existing_headers, existing_rows = _read_existing_csv()

    # Build new column names
    new_cols = [f"recall_{label}", f"mrr_{label}", f"ndcg_{label}"]
    top_score_col = f"top_score_{label}"

    if not existing_headers:
        # First run: create CSV with fixed columns + metric columns
        headers = [
            "query", "top_k", "relevant_appids",
            *new_cols, top_score_col, "retrieved_names",
        ]
        rows_out = []
        for r in results:
            rows_out.append({
                "query": r["query"],
                "top_k": str(r["top_k"]),
                "relevant_appids": r["relevant_appids"],
                new_cols[0]: f"{r['recall']:.4f}",
                new_cols[1]: f"{r['mrr']:.4f}",
                new_cols[2]: f"{r['ndcg']:.4f}",
                top_score_col: f"{r['top_score']:.4f}",
                "retrieved_names": r["retrieved_names"],
            })
    else:
        # Append new columns to existing rows
        headers = existing_headers + new_cols + [top_score_col]
        rows_out = []

        for i, r in enumerate(results):
            row = dict(existing_rows[i]) if i < len(existing_rows) else {}
            row[new_cols[0]] = f"{r['recall']:.4f}"
            row[new_cols[1]] = f"{r['mrr']:.4f}"
            row[new_cols[2]] = f"{r['ndcg']:.4f}"
            row[top_score_col] = f"{r['top_score']:.4f}"
            rows_out.append(row)

    with open(RESULT_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows_out)

    # Print per-column averages
    n = len(results)
    avg_r = sum(r["recall"] for r in results) / n
    avg_m = sum(r["mrr"] for r in results) / n
    avg_n = sum(r["ndcg"] for r in results) / n
    print(f"\n  → 写入 {RESULT_CSV_PATH.name}")
    print(f"  [{label}] Recall avg={avg_r:.3f}  MRR avg={avg_m:.3f}  NDCG avg={avg_n:.3f}")


# ── summary ──────────────────────────────────────────────────────────

def print_summary(results: list[dict]):
    n = len(results)
    avg_recall = sum(r["recall"] for r in results) / n
    avg_mrr = sum(r["mrr"] for r in results) / n
    avg_ndcg = sum(r["ndcg"] for r in results) / n

    perfect = sum(1 for r in results if r["recall"] >= 1.0)
    good = sum(1 for r in results if 0.5 <= r["recall"] < 1.0)
    poor = sum(1 for r in results if r["recall"] < 0.5)

    print(f"\n{'='*70}")
    print(f"  RAG 检索评测  |  {n} 条查询")
    print(f"{'='*70}")
    print(f"  Recall@{results[0]['top_k'] if results else 'K'}  avg: {avg_recall:.3f}")
    print(f"  MRR            avg: {avg_mrr:.3f}")
    print(f"  NDCG           avg: {avg_ndcg:.3f}")
    print(f"  ----")
    print(f"  Recall=1.0 (完美): {perfect}")
    print(f"  Recall≥0.5 (可用): {good}")
    print(f"  Recall<0.5 (差):   {poor}")
    print(f"{'='*70}")

    if poor:
        print(f"\n低分查询:")
        for r in results:
            if r["recall"] < 0.5:
                print(f"  [{r['query'][:40]}] recall={r['recall']:.2f}")
                print(f"    预期: {r['relevant_appids']}")
                print(f"    实际: {r['retrieved_appids']}")


# ── main ─────────────────────────────────────────────────────────────

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="RAG retrieval evaluation")
    parser.add_argument("--top-k", type=int, help="Override top_k for all queries")
    parser.add_argument("--query-id", type=int, help="Run a single query by index (1-based)")
    parser.add_argument("--label", default="", help="Version label for CSV column (auto if empty)")
    args = parser.parse_args()

    cases = load_ground_truth(CSV_PATH)
    print(f"加载 {len(cases)} 条 RAG ground truth\n")

    if args.query_id:
        cases = [cases[args.query_id - 1]]

    results = run_batch(cases, top_k_override=args.top_k)
    print_summary(results)

    # Write CSV (skip for single-query runs)
    if not args.query_id:
        label = args.label or datetime.now().strftime("%m%d_%H%M")
        write_results(results, label)


if __name__ == "__main__":
    main()
