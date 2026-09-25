from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .models import (
    AgentCase,
    AnnotationAgreementCase,
    JudgeCalibrationCase,
    MemoryCase,
    PersonalizationCase,
    RetrievalCase,
    RobustnessCase,
    load_jsonl,
    stable_hash,
)


@dataclass
class ValidationResult:
    files: dict[str, dict] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.errors


def validate_datasets(dataset_dir: Path) -> ValidationResult:
    result = ValidationResult()
    required = {
        "agent_behavior.v1.jsonl",
        "agent_gate.v1.jsonl",
        "agent_memory.v1.jsonl",
        "annotation_agreement.v1.jsonl",
        "memory.v1.jsonl",
        "memory_retrieval.v1.jsonl",
        "personalization.v1.jsonl",
        "judge_calibration.v1.jsonl",
        "retrieval_gate.v1.jsonl",
        "retrieval.v1.jsonl",
        "robustness.v1.jsonl",
    }
    present = {path.name for path in dataset_dir.glob("*.jsonl")}
    for filename in sorted(required - present):
        result.errors.append(f"missing dataset: {dataset_dir / filename}")
    specs: dict[str, type] = {}
    for path in sorted(dataset_dir.glob("*.jsonl")):
        if path.name.startswith("agent_"):
            specs[path.name] = AgentCase
        elif path.name.startswith("annotation_agreement"):
            specs[path.name] = AnnotationAgreementCase
        elif path.name.startswith("judge_calibration"):
            specs[path.name] = JudgeCalibrationCase
        elif path.name.startswith("retrieval"):
            specs[path.name] = RetrievalCase
        elif path.name.startswith("memory_retrieval"):
            specs[path.name] = RetrievalCase
        elif path.name.startswith("memory"):
            specs[path.name] = MemoryCase
        elif path.name.startswith("personalization"):
            specs[path.name] = PersonalizationCase
        elif path.name.startswith("robustness"):
            specs[path.name] = RobustnessCase
        else:
            result.errors.append(f"unknown dataset schema: {path.name}")
    global_ids: set[str] = set()
    for filename, model_type in specs.items():
        path = dataset_dir / filename
        if not path.exists():
            result.errors.append(f"missing dataset: {path}")
            continue
        items = load_jsonl(path, model_type)
        category_counts = Counter(getattr(item, "category", "unknown") for item in items)
        review_counts = Counter(getattr(item, "review_status", "unknown") for item in items)
        local_ids: set[str] = set()
        for item in items:
            if item.id in local_ids:
                result.errors.append(f"{filename}: duplicate id {item.id}")
            local_ids.add(item.id)
            qualified = f"{filename}:{item.id}"
            if qualified in global_ids:
                result.errors.append(f"duplicate qualified id: {qualified}")
            global_ids.add(qualified)
            for error in item.validate():
                result.errors.append(f"{filename}:{item.id}: {error}")
            if isinstance(item, AgentCase) and item.review_status == "reviewed":
                fixture_path = dataset_dir.parent / "fixtures" / f"{item.fixture}.json"
                if not fixture_path.exists():
                    result.errors.append(
                        f"{filename}:{item.id}: missing fixture {item.fixture}"
                    )
            if isinstance(item, PersonalizationCase):
                fixture_path = dataset_dir.parent / "fixtures" / f"{item.fixture}.json"
                if not fixture_path.exists():
                    result.errors.append(
                        f"{filename}:{item.id}: missing fixture {item.fixture}"
                    )
        result.files[filename] = {
            "count": len(items),
            "sha256": stable_hash(path),
            "categories": dict(sorted(category_counts.items())),
            "review_status": dict(sorted(review_counts.items())),
        }
    return result
