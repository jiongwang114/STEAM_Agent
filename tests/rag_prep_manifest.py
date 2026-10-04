"""Validate and record the immutable inputs required before RAG evaluation."""
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "rag" / "chroma_data"
TESTS = ROOT / "tests"

def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def inspect_csv(path: Path) -> dict:
    rows = list(csv.DictReader(path.open(encoding="utf-8-sig", newline="")))
    required = {"query", "top_k", "relevant_appids", "ground_truth_status"}
    missing = sorted(required - set(rows[0] if rows else []))
    manual = [r["query"] for r in rows if r.get("ground_truth_status") == "needs_manual_review"]
    empty = [
        r["query"] for r in rows
        if not r.get("relevant_appids", "").strip()
        and r.get("evaluation_type") != "open_recommendation"
    ]
    if missing or manual or empty:
        raise RuntimeError(f"{path.name}: missing={missing}, manual={manual}, empty={empty}")
    return {"file": path.name, "sha256": sha256(path), "rows": len(rows), "manual_review": 0}

def main() -> None:
    manifest = json.loads((DATA / "index_manifest.json").read_text(encoding="utf-8"))
    current = json.loads((DATA / "current_index.json").read_text(encoding="utf-8"))
    if manifest["index_version"] != current["index_version"]:
        raise RuntimeError("current_index.json does not point at index_manifest.json")
    datasets = [inspect_csv(TESTS / "gt_semantic_v2.csv"), inspect_csv(TESTS / "gt_filtered_v2.csv")]
    report = {
        "prepared_at": datetime.now(timezone.utc).isoformat(),
        "index_version": manifest["index_version"],
        "cache_sha256": manifest["cache_sha256"],
        "embedding_model": manifest["embedding_model"],
        "embedding_revision": manifest["embedding_revision"],
        "reranker_model": manifest["reranker_model"],
        "reranker_revision": manifest["reranker_revision"],
        "game_count": manifest["game_count"],
        "vector_count": manifest["vector_count"],
        "datasets": datasets,
        "status": "ready_for_rag_baseline",
    }
    out = TESTS / "rag_prep_manifest.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
