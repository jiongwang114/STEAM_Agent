from typing import Annotated, Literal

from langchain_core.tools import InjectedToolArg

from memory.insight_store import save_insight


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
    为当前已认证用户提交一条结构化记忆操作。

    适用场景：
    - 用户明确要求记住、修改或忘记偏好、限制或事实；
    - 用户明确表达了预计会持续有效、且对未来游戏推荐有帮助的信息。

    不适用场景：
    - 保存当前这一次推荐的临时条件；
    - 保存单次游戏行为、情绪、闲聊或助手推断；
    - 查询、读取或总结已有记忆；
    - 替代当前对话中的游戏推荐工具。

    参数说明：
    - user_id：由系统注入的当前认证用户 ID，不由模型填写或修改。
    - memory_key：记忆主题的稳定键。replace 和 delete 必须复用已有记忆的键。
    - value：简短、原子化的记忆内容。delete 操作时留空。
    - category：只能是 preference、constraint 或 fact。
      玩法和类型喜恶属于 preference，预算和时间限制属于 constraint，
      设备、平台等客观属性属于 fact。
    - action：只能是 add、replace 或 delete。
    - confidence：对用户表达明确程度的判断，范围为 0 到 1。
    - scope：只能是 stable、temporary 或 session。
    - ttl_days：temporary 或 session 记忆的有效天数；稳定记忆通常留空。

    调用要求：
    - value 必须根据用户原话整理，不得使用助手内容作为事实来源。
    - 用户明确要求忘记时使用 delete，并提供 memory_key。
    - 用户明确改变已有偏好时使用 replace，不要用 add 创建重复主题。
    - 不要把一次性请求自动升级为长期偏好。

    返回内容：
    - 操作状态、记忆键及必要的去重或替换结果。
    - 返回结果是内部操作结果，不应直接暴露给用户。
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
