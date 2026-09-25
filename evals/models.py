from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


ReviewStatus = Literal["reviewed", "needs_review", "rejected"]


@dataclass
class ToolExpectation:
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    ordered: list[str] = field(default_factory=list)
    max_calls: dict[str, int] = field(default_factory=dict)
    max_rounds: int | None = None
    argument_equals: dict[str, dict[str, Any]] = field(default_factory=dict)
    argument_contains: dict[str, dict[str, list[str]]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "ToolExpectation":
        return cls(**(value or {}))


@dataclass
class AnswerExpectation:
    answer_type: Literal["answer", "recommendation", "clarification", "refusal"] = "answer"
    required_appids: list[str] = field(default_factory=list)
    forbidden_appids: list[str] = field(default_factory=list)
    must_contain: list[str] = field(default_factory=list)
    must_not_contain: list[str] = field(default_factory=list)
    require_grounding: bool = False

    @classmethod
    def from_dict(cls, value: dict[str, Any] | None) -> "AnswerExpectation":
        return cls(**(value or {}))


@dataclass
class AgentCase:
    id: str
    category: str
    message: str
    user_id: str = "eval_user"
    steam_id: str | None = None
    fixture: str = "default"
    tools: ToolExpectation = field(default_factory=ToolExpectation)
    answer: AnswerExpectation = field(default_factory=AnswerExpectation)
    expected_termination: list[str] = field(default_factory=lambda: ["completed"])
    tags: list[str] = field(default_factory=list)
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AgentCase":
        data = dict(value)
        data["tools"] = ToolExpectation.from_dict(data.get("tools"))
        data["answer"] = AnswerExpectation.from_dict(data.get("answer"))
        return cls(**data)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id or not self.message.strip():
            errors.append("id and message are required")
        overlap = set(self.tools.required) & set(self.tools.forbidden)
        if overlap:
            errors.append(f"tools both required and forbidden: {sorted(overlap)}")
        if self.tools.ordered and not set(self.tools.ordered).issubset(self.tools.required):
            errors.append("ordered tools must also appear in required")
        if self.answer.require_grounding and self.answer.answer_type != "recommendation":
            errors.append("require_grounding is only valid for recommendation cases")
        return errors


@dataclass
class RetrievalCase:
    id: str
    query: str
    qrels: dict[str, int]
    split: Literal["dev", "test"]
    top_k: int = 10
    constraints: dict[str, Any] = field(default_factory=dict)
    hard_negatives: list[str] = field(default_factory=list)
    category: str = "semantic"
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RetrievalCase":
        data = dict(value)
        data["qrels"] = {str(k): int(v) for k, v in data.get("qrels", {}).items()}
        return cls(**data)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id or not self.query.strip():
            errors.append("id and query are required")
        if not self.qrels:
            errors.append("qrels must not be empty")
        if any(grade not in {0, 1, 2} for grade in self.qrels.values()):
            errors.append("qrel grades must be 0, 1, or 2")
        if set(self.hard_negatives) & {key for key, grade in self.qrels.items() if grade > 0}:
            errors.append("hard negatives cannot also be relevant")
        return errors


@dataclass
class MemoryCase:
    id: str
    category: str
    operations: list[dict[str, Any]]
    expected_active: list[str]
    expected_inactive: list[str] = field(default_factory=list)
    expected_statuses: list[str] = field(default_factory=list)
    recall_limit: int = 50
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"
    notes: str = ""

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "MemoryCase":
        return cls(**value)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not self.id or not self.operations:
            errors.append("id and operations are required")
        if set(self.expected_active) & set(self.expected_inactive):
            errors.append("memory cannot be both active and inactive")
        if self.expected_statuses and len(self.expected_statuses) != len(self.operations):
            errors.append("expected_statuses must align with operations")
        return errors


@dataclass
class RobustnessCase:
    id: str
    category: str
    outcomes: list[str]
    expected_status: str
    expected_error_code: str = ""
    max_retries: int = 0
    timeout_ms: int = 20
    invocations: int = 1
    circuit_threshold: int = 3
    deadline_expired: bool = False
    expected_attempts: int | None = None
    expected_function_calls: int | None = None
    max_duration_ms: int = 500
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "RobustnessCase":
        return cls(**value)

    def validate(self) -> list[str]:
        errors = []
        if not self.id or not self.outcomes:
            errors.append("id and outcomes are required")
        if self.max_retries < 0 or self.invocations < 1:
            errors.append("retry and invocation counts are invalid")
        return errors


@dataclass
class JudgeCalibrationCase:
    id: str
    query: str
    answer: str
    human_scores: dict[str, int]
    evidence: list[dict[str, Any]] = field(default_factory=list)
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "JudgeCalibrationCase":
        return cls(**value)

    def validate(self) -> list[str]:
        errors = []
        required = {"relevance", "helpfulness", "groundedness", "overall"}
        if not self.id or not self.query or not self.answer:
            errors.append("id, query and answer are required")
        if set(self.human_scores) != required:
            errors.append(f"human_scores must contain exactly {sorted(required)}")
        if any(int(value) not in range(1, 6) for value in self.human_scores.values()):
            errors.append("human judge scores must be between 1 and 5")
        return errors


@dataclass
class PersonalizationCase:
    id: str
    message: str
    steam_id: str
    fixture: str
    expected_personalized_appids: list[str]
    min_jaccard_distance: float = 0.8
    category: str = "personalization"
    provenance: str = "manual"
    review_status: ReviewStatus = "reviewed"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PersonalizationCase":
        data = dict(value)
        data["expected_personalized_appids"] = [
            str(item) for item in data.get("expected_personalized_appids", [])
        ]
        return cls(**data)

    def validate(self) -> list[str]:
        errors = []
        if not self.id or not self.message or not self.steam_id or not self.fixture:
            errors.append("id, message, steam_id and fixture are required")
        if not self.expected_personalized_appids:
            errors.append("expected_personalized_appids must not be empty")
        if not 0 <= self.min_jaccard_distance <= 1:
            errors.append("min_jaccard_distance must be between 0 and 1")
        return errors


@dataclass
class AnnotationAgreementCase:
    id: str
    query: str
    item: str
    reviewer_a: int
    reviewer_b: int
    notes: str = ""
    provenance: str = "two-pass-rubric-review"
    review_status: ReviewStatus = "reviewed"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "AnnotationAgreementCase":
        return cls(**value)

    def validate(self) -> list[str]:
        errors = []
        if not self.id or not self.query or not self.item:
            errors.append("id, query and item are required")
        if self.reviewer_a not in {0, 1, 2} or self.reviewer_b not in {0, 1, 2}:
            errors.append("reviewer labels must be 0, 1, or 2")
        return errors


@dataclass
class RunManifest:
    run_id: str
    created_at: str
    git_sha: str
    model: str
    model_parameters: dict[str, Any]
    prompt_hash: str
    dataset_hashes: dict[str, str]
    index_manifest_hash: str = ""
    repeats: int = 1
    candidate_label: str = "candidate"


@dataclass
class CaseResult:
    case_id: str
    repeat: int
    passed: bool
    tool_calls: list[str] = field(default_factory=list)
    termination_reason: str = ""
    answer: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    latency_seconds: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    trace_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvalReport:
    manifest: RunManifest
    summary: dict[str, Any]
    cases: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_jsonl(path: Path, model_type):
    items = []
    with path.open(encoding="utf-8") as file:
        for line_number, raw in enumerate(file, start=1):
            if not raw.strip():
                continue
            try:
                items.append(model_type.from_dict(json.loads(raw)))
            except Exception as exc:
                raise ValueError(f"{path}:{line_number}: {exc}") from exc
    return items


def stable_hash(value: Any) -> str:
    if isinstance(value, Path):
        payload = value.read_bytes()
    else:
        payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()
