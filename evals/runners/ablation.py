from __future__ import annotations

import json
from pathlib import Path

from steam_agent.rag.hybrid import hybrid_search
from steam_agent.rag.translate import translate_to_english

from .retrieval import run_retrieval_eval


VARIANTS = {
    "dense_only": {"use_dense": True, "use_lexical": False, "use_reranker": False},
    "hybrid_no_rerank": {"use_dense": True, "use_lexical": True, "use_reranker": False},
    "hybrid_rerank_025": {"use_dense": True, "use_lexical": True, "use_reranker": True, "reranker_weight": 0.25},
    "hybrid_rerank_050": {"use_dense": True, "use_lexical": True, "use_reranker": True, "reranker_weight": 0.50},
    "hybrid_rerank_100": {"use_dense": True, "use_lexical": True, "use_reranker": True, "reranker_weight": 1.0},
}


def run_retrieval_ablation(*, root: Path, dataset_path: Path, output_path: Path) -> dict:
    variant_reports = {}
    for name, options in VARIANTS.items():
        report_path = output_path.with_name(f"{output_path.stem}-{name}.json")
        report = run_retrieval_eval(
            root=root,
            dataset_path=dataset_path,
            search=_search_function(options),
            include_needs_review=False,
            label=f"ablation-{name}",
            output_path=report_path,
        )
        variant_reports[name] = report["summary"]
    selected = _select_on_dev(variant_reports)
    full = variant_reports[selected]["test"]
    deltas = {}
    for name, summary in variant_reports.items():
        test = summary["test"]
        deltas[name] = {
            metric: float(test[metric]) - float(full[metric])
            for metric in ("recall@10", "mrr@10", "ndcg@10")
        }
    combined = {
        "selected_on_dev": selected,
        "selection_metric": "dev_ndcg@10_then_mrr@10",
        "variants": variant_reports,
        "delta_vs_selected": deltas,
    }
    output_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    return combined


def _select_on_dev(reports: dict) -> str:
    candidates = [name for name in reports if name.startswith("hybrid_")]
    return max(
        candidates,
        key=lambda name: (
            reports[name]["dev"]["ndcg@10"],
            reports[name]["dev"]["mrr@10"],
        ),
    )


def _search_function(options: dict):
    def search(query: str, top_k: int = 10, **constraints):
        translated = translate_to_english(query) if _contains_chinese(query) else query
        where = _where(constraints)
        raw = hybrid_search(translated, top_k=top_k, where=where, **options)
        rows = [row for row in raw["results"] if _active_score(row) >= 0.3]
        return {
            "results": [
                {"appid": row["appid"], **row["metadata"]}
                for row in rows
            ],
            "retrieval": raw["retrieval"],
        }
    return search


def _active_score(row: dict) -> float:
    if row.get("rerank_score") is not None:
        return float(row.get("final_score", row["rerank_score"]))
    if row.get("dense_similarity") is not None:
        return float(row["dense_similarity"])
    return min(1.0, float(row.get("fusion_score", 0)) * 30)


def _where(constraints: dict) -> dict | None:
    conditions = []
    if constraints.get("free_only"):
        conditions.append({"is_free": True})
    for argument, field in (
        ("min_year", "release_year"),
        ("min_metacritic", "metacritic"),
    ):
        if constraints.get(argument) is not None:
            conditions.append({field: {"$gte": constraints[argument]}})
    if constraints.get("has_multiplayer") is not None:
        conditions.append({"has_multiplayer": constraints["has_multiplayer"]})
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions} if conditions else None


def _contains_chinese(text: str) -> bool:
    return any("一" <= character <= "鿿" for character in text)
