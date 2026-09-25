from typing import Literal

from ..memory.insight_store import add_insight, remove_insight


def save_user_insight(
    user_id: str,
    insight: str,
    category: Literal["preference", "constraint", "fact"],
    action: Literal["add", "remove"] = "add",
    confidence: float = 1.0,
    scope: Literal["stable", "temporary", "session"] = "stable",
    ttl_days: int | None = None,
    memory_key: str | None = None,
) -> dict:
    """
    Persist one explicit user memory. Devices/platforms are facts; budget/time are
    constraints; liked/disliked genres are preferences. Use temporary scope for
    explicitly time-limited interests. Never save a one-off play event.
    """
    valid_categories = {"preference", "constraint", "fact"}
    if category not in valid_categories:
        return {"error": f"Invalid category '{category}'. Must be one of: {', '.join(sorted(valid_categories))}"}

    if action == "remove":
        remove_insight(user_id, insight)
        return {"status": "removed", "insight": insight}
    elif action == "add":
        return add_insight(
            user_id,
            insight,
            category,
            confidence=confidence,
            source="explicit_user",
            scope=scope,
            ttl_days=ttl_days,
            memory_key=memory_key,
        )
    else:
        return {"error": f"Invalid action '{action}'. Must be 'add' or 'remove'."}
