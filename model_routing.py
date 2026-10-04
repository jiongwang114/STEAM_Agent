from __future__ import annotations

import hashlib
from dataclasses import dataclass

from config import (
    AGENT_EXPERIMENT_CANDIDATE_PERCENT,
    AGENT_EXPERIMENT_NAME,
    LLM_CANDIDATE_MODEL,
    LLM_FAST_MODEL,
    LLM_MODEL,
)


@dataclass(frozen=True)
class ModelSelection:
    model: str
    role: str
    variant: str


def assign_experiment(user_id: str, thread_id: str) -> dict[str, str | int]:
    if not AGENT_EXPERIMENT_NAME or AGENT_EXPERIMENT_CANDIDATE_PERCENT <= 0:
        return {"name": AGENT_EXPERIMENT_NAME, "variant": "control", "bucket": 0}
    digest = hashlib.sha256(
        f"{AGENT_EXPERIMENT_NAME}:{user_id}:{thread_id}".encode()
    ).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    variant = "candidate" if bucket < AGENT_EXPERIMENT_CANDIDATE_PERCENT else "control"
    return {"name": AGENT_EXPERIMENT_NAME, "variant": variant, "bucket": bucket}


def select_model(role: str, experiment: dict | None = None) -> ModelSelection:
    variant = str((experiment or {}).get("variant", "control"))
    if role in {"finalize", "repair", "summary", "title", "guard", "memory"}:
        model = LLM_FAST_MODEL
    elif variant == "candidate":
        model = LLM_CANDIDATE_MODEL
    else:
        model = LLM_MODEL
    return ModelSelection(model=model, role=role, variant=variant)
