from typing import Annotated

from langchain_core.tools import InjectedToolArg

from ..memory.message_store import get_messages_by_turn, thread_belongs_to_user


def recall_message_detail(
    user_id: Annotated[str, InjectedToolArg],
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

    if not thread_belongs_to_user(user_id, thread_id):
        return {"error": "thread_id 不存在或无权访问"}

    messages = get_messages_by_turn(user_id, thread_id, turn_number, role)
    return {"messages": messages}
