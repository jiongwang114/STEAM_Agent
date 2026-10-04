from typing import Annotated

from langchain_core.tools import InjectedToolArg

from memory.message_store import get_messages_by_turn, thread_belongs_to_user


def recall_message_detail(
    user_id: Annotated[str, InjectedToolArg],
    thread_id: str,
    turn_number: int | None = None,
    role: str | None = None,
) -> dict:
    """
    精确读取当前用户明确指定的历史对话内容。

    适用场景：
    - 用户要求查看某个指定会话；
    - 用户要求找回某一轮对话或某一方的原始消息；
    - 用户明确要求引用历史对话中的原文。

    不适用场景：
    - 普通游戏推荐；
    - 根据当前上下文判断用户偏好；
    - 没有指定会话或轮次时搜索全部历史；
    - 用历史原文替代当前用户的最新表达。

    参数说明：
    - user_id：由系统注入的当前认证用户 ID，不由模型填写或修改。
    - thread_id：当前用户拥有的目标会话 ID。
    - turn_number：可选的对话轮次；省略时读取该会话中符合其他条件的消息。
    - role：可选的消息角色，只能是 user 或 assistant。

    调用要求：
    - 只有用户明确指定历史会话、轮次或原文时才使用本工具。
    - 不得访问其他用户的会话，也不得猜测 thread_id。
    - 历史内容只是过去的记录；如果与用户当前表达冲突，优先当前表达。

    返回内容：
    - messages：符合会话、轮次和角色条件的历史消息列表。
    - 返回空列表表示没有找到符合条件的记录，不代表可以凭记忆补全内容。
    """
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
