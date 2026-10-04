"""Compare pure Dense, pure BM25, and Dense+BM25 RRF retrieval.

This runner deliberately disables the reranker so the three measurements only
cover candidate retrieval and fusion.
"""

import argparse
import csv
from pathlib import Path

from rag.hybrid import hybrid_search
from rag.translate import translate_to_english
from tests.validate_ground_truth import validate


def load_cases(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def run_case(case: dict, mode: str, top_k: int) -> dict:
    flags = {
        "dense": (True, False),
        "bm25": (False, True),
        "rrf": (True, True),
    }[mode]
    query = case["query"]
    if any("一" <= char <= "鿿" for char in query):
        query = translate_to_english(query) or query
    conditions = []
    if case.get("free_only", "").strip() == "1":
        conditions.append({"is_free": True})
    if case.get("min_year", "").strip():
        conditions.append({"release_year": {"$gte": int(case["min_year"])}})
    where = conditions[0] if len(conditions) == 1 else ({"$and": conditions} if conditions else None)
    raw = hybrid_search(
        query, top_k=top_k, where=where, required_genre=case.get("filter_tags") or None,
        use_dense=flags[0], use_lexical=flags[1], use_reranker=False,
    )
    retrieved = [str(row["appid"]) for row in raw["results"]]
    relevant = {item.strip() for item in case.get("relevant_appids", "").split(";") if item.strip()}
    hits = len(set(retrieved) & relevant)
    return {
        "id": case.get("id", ""), "mode": mode, "recall": hits / len(relevant) if relevant else 1.0,
        "precision": hits / top_k if top_k else 0.0, "retrieved_appids": ";".join(retrieved),
        "reranker_used": raw["retrieval"]["reranker_used"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ground-truth", type=Path, default=Path(__file__).with_name("gt_semantic_v2.csv"))
    parser.add_argument("--mode", choices=("dense", "bm25", "rrf", "all"), default="all")
    parser.add_argument("--top-k", type=int, default=10)
    args = parser.parse_args()
    validation_errors = validate(
        args.ground_truth,
        Path(__file__).parents[1] / "rag/chroma_data/game_cache.json",
    )
    if validation_errors:
        raise SystemExit("Ground truth validation failed:\n" + "\n".join(validation_errors))
    cases = load_cases(args.ground_truth)
    modes = ("dense", "bm25", "rrf") if args.mode == "all" else (args.mode,)
    for mode in modes:
        results = [run_case(case, mode, args.top_k) for case in cases]
        average_recall = sum(row["recall"] for row in results) / len(results)
        average_precision = sum(row["precision"] for row in results) / len(results)
        denominator = sum(
            len({item.strip() for item in case.get("relevant_appids", "").split(";") if item.strip()})
            for case in cases
        )
        print(
            f"mode={mode} queries={len(results)} relevant_denominator={denominator} "
            f"recall={average_recall:.4f} precision={average_precision:.4f} reranker=disabled"
        )


if __name__ == "__main__":
    main()
