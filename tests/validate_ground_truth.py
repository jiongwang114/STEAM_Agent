"""Validate ground-truth denominators before running retrieval ablations."""

import argparse
import csv
import json
from pathlib import Path


def validate(path: Path, cache_path: Path) -> list[str]:
    games = {str(item["appid"]) for item in json.loads(cache_path.read_text(encoding="utf-8"))}
    errors = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for number, row in enumerate(csv.DictReader(handle), start=2):
            ids = [item.strip() for item in row.get("relevant_appids", "").split(";") if item.strip()]
            if not ids:
                if row.get("evaluation_type") != "open_recommendation":
                    errors.append(f"line {number}: empty relevant_appids")
                continue
            if len(ids) != len(set(ids)):
                errors.append(f"line {number}: duplicate relevant_appids")
            stale = sorted(set(ids) - games)
            if stale:
                errors.append(f"line {number}: relevant apps absent from cache: {','.join(stale)}")
            declared = row.get("available_relevant_count", "").strip()
            if declared and int(declared) != len(ids):
                errors.append(f"line {number}: denominator {declared} != relevant_appids {len(ids)}")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ground_truth", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(__file__).parents[1] / "rag/chroma_data/game_cache.json")
    args = parser.parse_args()
    errors = validate(args.ground_truth, args.cache)
    if errors:
        print("INVALID")
        print("\n".join(errors))
        raise SystemExit(1)
    print(f"VALID {args.ground_truth.name}")


if __name__ == "__main__":
    main()
