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
    updated = []
    for item in constraints:
        status = item.get("status", "unresolved")
        matching = [row for row in evidence if str(row.get("appid", ""))]
        if item["type"] == "free_only" and any((row.get("payload") or {}).get("is_free") is True for row in matching):
            status = "satisfied"
        elif item["type"] == "multiplayer" and any((row.get("payload") or {}).get("has_multiplayer") is True for row in matching):
            status = "satisfied"
        elif item["type"] == "min_year":
            years = [int((row.get("payload") or {}).get("release_year")) for row in matching if str((row.get("payload") or {}).get("release_year", "")).isdigit()]
            if years and min(years) >= int(item["value"]):
                status = "satisfied"
        if status == "satisfied":
            evidence_ids = [str(row.get("appid")) for row in matching]
        else:
            evidence_ids = list(item.get("evidence_ids") or [])
        updated.append({**item, "status": status, "evidence_ids": evidence_ids})
    return updated
