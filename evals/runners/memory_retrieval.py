from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

from steam_agent.rag.embedder import embed_memory

from ..artifacts import write_companion_artifacts
from ..metrics import retrieval_metrics
from ..models import RetrievalCase, load_jsonl
from ..reporting import build_manifest


def run_memory_retrieval_eval(*, root: Path, dataset_path: Path, corpus_path: Path, output_path: Path) -> dict:
    cases = [case for case in load_jsonl(dataset_path, RetrievalCase) if case.review_status == "reviewed"]
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    document_vectors = embed_memory([item["text"] for item in corpus])
    query_vectors = embed_memory([case.query for case in cases])
    rows = []
    for case, query_vector in zip(cases, query_vectors):
        ranked = sorted(
            (
                (item["id"], sum(a * b for a, b in zip(query_vector, vector)))
                for item, vector in zip(corpus, document_vectors)
            ),
            key=lambda item: item[1],
            reverse=True,
        )
        appids = [item[0] for item in ranked[:case.top_k]]
        metrics = retrieval_metrics(appids, case.qrels, case.top_k)
        metrics["hard_negative_rate"] = len(set(appids) & set(case.hard_negatives)) / len(appids)
        rows.append({"case_id": case.id, "split": case.split, "appids": appids, "metrics": metrics})
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["split"]].append(row)
    summary = {
        split: {
            name: mean(row["metrics"][name] for row in group)
            for name in sorted({key for row in group for key in row["metrics"]})
        }
        for split, group in grouped.items()
    }
    manifest = build_manifest(
        root=root,
        model="memory-embedding",
        model_parameters={},
        dataset_paths=[dataset_path, corpus_path],
        repeats=1,
        label="memory-retrieval",
    )
    report = {"manifest": manifest.__dict__, "summary": summary, "cases": rows}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_companion_artifacts(report, output_path)
    return report
