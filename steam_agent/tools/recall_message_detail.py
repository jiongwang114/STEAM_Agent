from ..memory.message_store import get_messages_by_turn


def recall_message_detail(
    user_id: str,
    thread_id: str,
    turn_number: int | None = None,
    role: str | None = None,
) -> dict:
    """Look up an explicitly requested conversation thread, turn, or role exactly."""
    if not thread_id or len(thread_id) > 128:
        return {"error": "thread_id 无效"}
    if turn_number is not None and (turn_number < 1 or turn_number > 1000000):
        return {"error": "turn_number 无效"}
    valid_roles = {None, "user", "assistant"}
    if role not in valid_roles:
        return {"error": f"role 必须为 'user'、'assistant' 或省略，收到: '{role}'"}

    messages = get_messages_by_turn(user_id, thread_id, turn_number, role)
    return {"messages": messages}
