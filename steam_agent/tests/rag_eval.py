"""
RAG retrieval evaluation — only measures Recall@K.
No LLM calls, no token cost. Pure vector search.

Each run appends a recall_{label} column to rag_eval_results.csv and
records the change metadata in rag_eval_changelog.csv.

Usage:
    python -m steam_agent.tests.rag_eval --label baseline
    python -m steam_agent.tests.rag_eval --label v2 --note "embedder: MiniLM -> multilingual-MiniLM"
    python -m steam_agent.tests.rag_eval --top-k 5 --label top5 --note "top_k: 10 -> 5"
    python -m steam_agent.tests.rag_eval --query-id 3            # single query, no csv
"""

import argparse
import csv
import io
import sys
from datetime import datetime
from pathlib import Path

from ..tools.rag_search import rag_search_similar_games

CSV_PATH = Path(__file__).resolve().parent / "rag_ground_truth.csv"
RESULT_CSV_PATH = Path(__file__).resolve().parent / "rag_eval_results.csv"
CHANGELOG_CSV_PATH = Path(__file__).resolve().parent / "rag_eval_changelog.csv"


def recall_at_k(retrieved: list[str], relevant: list[str]) -> float:
    if not relevant:
        return 1.0
    return sum(1 for r in relevant if r in retrieved) / len(relevant)


def _filter_summary(case: dict) -> str:
    parts = []
    if case.get("free_only"):
        parts.append("free_only")
    if case.get("min_year"):
        parts.append(f"year>={case['min_year']}")
    if case.get("filter_tags"):
        parts.append(f"tags={','.join(case['filter_tags'])}")
    return ", ".join(parts)


def load_ground_truth(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        cases = []
        for row in csv.DictReader(f):
            row["top_k"] = int(row["top_k"])
            row["relevant_appids"] = [
                aid.strip() for aid in row["relevant_appids"].split(";") if aid.strip()
            ]
            # Parse optional filter columns
            row["free_only"] = row.get("free_only", "").strip() == "1"
            row["min_year"] = int(row["min_year"]) if row.get("min_year", "").strip() else None
            raw_tags = row.get("filter_tags", "").strip()
            row["filter_tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()] if raw_tags else None
            cases.append(row)
        return cases


def run_batch(cases: list[dict], top_k_override: int | None = None) -> list[dict]:
    results = []
    for i, case in enumerate(cases):
        k = top_k_override or case["top_k"]
        query = case["query"]
        relevant = case["relevant_appids"]

        rag_result = rag_search_similar_games(
            query,
            top_k=k,
            filter_tags=case.get("filter_tags"),
            free_only=case.get("free_only", False),
            min_year=case.get("min_year"),
        )
        items = rag_result.get("results", [])
        retrieved = [item["appid"] for item in items]

        recall = recall_at_k(retrieved, relevant)

        results.append({
            "query": query,
            "top_k": k,
            "relevant_appids": ";".join(relevant),
            "retrieved_appids": ";".join(retrieved),
            "retrieved_names": ";".join(item["name"] for item in items),
            "recall": recall,
            "filters_used": _filter_summary(case),
        })

        filters_str = _filter_summary(case)
        status = "OK" if recall >= 0.5 else "LOW"
        print(f"{i+1:2d}/{len(cases)} [{status}] {query[:45]:<45s} "
              f"recall={recall:.2f}  top={items[0]['name'][:20] if items else 'N/A'}"
              f"{'  [' + filters_str + ']' if filters_str else ''}")

    return results


# ── result CSV ────────────────────────────────────────────────────────

def _read_existing_csv() -> tuple[list[str], list[dict]]:
    if not RESULT_CSV_PATH.exists():
        return [], []
    with open(RESULT_CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        rows = list(reader)
    return headers, rows


def write_results(results: list[dict], label: str):
    existing_headers, existing_rows = _read_existing_csv()
    recall_col = f"recall_{label}"

    if not existing_headers:
        headers = ["query", "top_k", "relevant_appids", recall_col, "retrieved_names"]
        rows_out = []
        for r in results:
            rows_out.append({
                "query": r["query"],
                "top_k": str(r["top_k"]),
                "relevant_appids": r["relevant_appids"],
                recall_col: f"{r['recall']:.4f}",
                "retrieved_names": r["retrieved_names"],
            })
    else:
        headers = existing_headers + [recall_col]
        rows_out = []
        for i, r in enumerate(results):
            row = dict(existing_rows[i]) if i < len(existing_rows) else {}
            row[recall_col] = f"{r['recall']:.4f}"
            rows_out.append(row)

    with open(RESULT_CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows_out)

    n = len(results)
    avg_r = sum(r["recall"] for r in results) / n
    print(f"\n  -> 写入 {RESULT_CSV_PATH.name}")
    print(f"  [{label}] Recall avg={avg_r:.3f}")


# ── changelog CSV ─────────────────────────────────────────────────────

def write_changelog(label: str, note: str, avg_recall: float, top_k: int):
    """Append a row to the changelog to track what changed in each run."""
    file_exists = CHANGELOG_CSV_PATH.exists()

    with open(CHANGELOG_CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
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

    print(f"  -> 写入 {CHANGELOG_CSV_PATH.name}: {note or '(无变更说明)'}")


# ── summary ───────────────────────────────────────────────────────────

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


# ── main ──────────────────────────────────────────────────────────────

def main():
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

    parser = argparse.ArgumentParser(description="RAG recall evaluation")
    parser.add_argument("--top-k", type=int, help="Override top_k")
    parser.add_argument("--query-id", type=int, help="Single query by index (1-based)")
    parser.add_argument("--label", default="", help="Version label (auto timestamp if empty)")
    parser.add_argument("--note", default="", help="What variable changed and what it changed to")
    args = parser.parse_args()

    cases = load_ground_truth(CSV_PATH)
    print(f"Loaded {len(cases)} RAG ground truth queries\n")

    if args.query_id:
        cases = [cases[args.query_id - 1]]

    results = run_batch(cases, top_k_override=args.top_k)
    print_summary(results)

    if not args.query_id:
        label = args.label or datetime.now().strftime("%m%d_%H%M")
        top_k = args.top_k or cases[0]["top_k"]
        avg_r = sum(r["recall"] for r in results) / len(results)
        write_results(results, label)
        write_changelog(label, args.note, avg_r, top_k)


if __name__ == "__main__":
    main()
