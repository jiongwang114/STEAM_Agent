from ..memory.insight_store import add_insight, remove_insight


def save_user_insight(
    user_id: str,
    insight: str,
    category: str,
    action: str = "add",
) -> str:
    """
    Persist a user preference, constraint, or fact for cross-session personalization.
    category: "preference" | "constraint" | "fact"
    action: "add" | "remove"
    """
    valid_categories = {"preference", "constraint", "fact"}
    if category not in valid_categories:
        return f"Invalid category '{category}'. Must be one of: {', '.join(sorted(valid_categories))}"

    if action == "remove":
        remove_insight(user_id, insight)
        return f"Removed insight: \"{insight}\""
    elif action == "add":
        add_insight(user_id, insight, category)
        return f"Saved insight ({category}): \"{insight}\""
    else:
        return f"Invalid action '{action}'. Must be 'add' or 'remove'."
