from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg

from ..memory.insight_store import save_insight


def save_user_insight(
    user_id: Annotated[str, InjectedToolArg],
    memory_key: str,
    value: str = "",
    category: Literal["preference", "constraint", "fact"] = "fact",
    action: Literal["add", "replace", "delete"] = "add",
    confidence: float = 1.0,
    scope: Literal["stable", "temporary", "session"] = "stable",
    ttl_days: int | None = None,
) -> dict:
    """
    Propose an add, replace, or delete for a structured long-term user memory.
    Devices/platforms are facts; budget/time are constraints; liked/disliked
    genres are preferences. Never save a one-off play event.
    """
    valid_categories = {"preference", "constraint", "fact"}
    if category not in valid_categories:
        return {"error": f"Invalid category '{category}'. Must be one of: {', '.join(sorted(valid_categories))}"}

    if action == "delete":
        value = ""
    try:
        return save_insight(
            user_id,
            value,
            category,
            action=action,
            confidence=confidence,
            source="agent_proposed",
            scope=scope,
            ttl_days=ttl_days,
            memory_key=memory_key,
        )
    except ValueError as exc:
        return {"error": str(exc)}
