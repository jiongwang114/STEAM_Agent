from ..memory.message_store import get_messages_by_turn


def recall_message_detail(
    user_id: str,
    thread_id: str,
    turn_number: int | None = None,
    role: str | None = None,
) -> dict:
    """
    Exact lookup of conversation messages by user, thread, and optional turn/role.

    Use this when the user asks about a SPECIFIC turn or wants to see the full
    transcript of a past conversation — not for fuzzy/semantic recall.

    Examples:
    - "上次对话第 1 轮我问了什么" → turn_number=1, role="user"
    - "把之前那个会话的完整对话发给我" → no filters, returns all
    - "第 3 轮你推荐了什么" → turn_number=3, role="assistant"

    Args:
        user_id: system user ID (use the value from session state)
        thread_id: which conversation session to query
        turn_number: optional, filter to a specific turn
        role: optional, filter to "user" or "assistant"

    Returns:
        {"messages": [{"turn": 1, "role": "user", "content": "...", "time": "..."}, ...]}
    """
    valid_roles = {None, "user", "assistant"}
    if role not in valid_roles:
        return {"error": f"role 必须为 'user'、'assistant' 或省略，收到: '{role}'"}

    messages = get_messages_by_turn(user_id, thread_id, turn_number, role)
    return {"messages": messages}
