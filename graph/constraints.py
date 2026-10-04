"""Structured constraint tracking for bounded semantic tool loops."""

from __future__ import annotations

import re
from typing import Any


def extract_constraints(text: str) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    if re.search(r"免费|不要钱|免费游戏", text):
        constraints.append({"id": "free_only", "type": "free_only", "value": True})
    year = re.search(r"(?:19|20)\d{2}\s*年(?:之后|以后|后)", text)
    if year:
        constraints.append({"id": "min_year", "type": "min_year", "value": int(year.group()[:4])})
    if re.search(r"多人|联机|合作|本地双人", text):
        constraints.append({"id": "multiplayer", "type": "multiplayer", "value": True})
    return [dict(item, status="unresolved", evidence_ids=[]) for item in constraints]


def update_constraint_status(constraints: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use evidence payloads for deterministic hard-constraint updates."""
    blob = repr(evidence).lower()
    updated = []
    for item in constraints:
        status = item.get("status", "unresolved")
        if item["type"] == "free_only" and ("is_free': true" in blob or '"is_free": true' in blob):
            status = "satisfied"
        elif item["type"] == "multiplayer" and any(word in blob for word in ("multiplayer", "co-op", "coop", "多人")):
            status = "satisfied"
        elif item["type"] == "min_year":
            years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", blob)]
            if years and min(years) >= int(item["value"]):
                status = "satisfied"
        updated.append({**item, "status": status})
    return updated
