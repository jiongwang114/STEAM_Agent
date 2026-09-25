from langchain_core.messages import SystemMessage

from ..memory.insight_store import get_insights
from .modules import (
    DECISION_POLICY,
    GROUNDING_POLICY,
    IDENTITY,
    MEMORY_POLICY,
    PROMPT_VERSION,
    STYLE,
    TOOL_RESULT_POLICY,
)


def build_system_prompt(user_id: str, steam_id: str | None = None) -> SystemMessage:
    context = [
        f"prompt_version: {PROMPT_VERSION}",
        f"user_id: {user_id or 'unknown'}",
        f"steam_id: {steam_id if steam_id else '未提供'}",
    ]
    insights = _format_insights(user_id)
    if insights:
        context.append("已确认用户画像：\n" + insights)
    if steam_id:
        try:
            from ..memory.game_profile import get_game_profile
            profile = get_game_profile(steam_id)
        except Exception:
            profile = ""
        if profile:
            context.append("Steam 游戏档案（缓存摘要，仅用于个性化线索）：\n" + profile[:1800])
    return SystemMessage(content="\n\n".join([
        IDENTITY,
        "## 当前上下文\n" + "\n".join(context),
        DECISION_POLICY,
        TOOL_RESULT_POLICY,
        GROUNDING_POLICY,
        MEMORY_POLICY,
        STYLE,
    ]))


def _format_insights(user_id: str) -> str:
    if not user_id:
        return ""
    rows = get_insights(user_id)
    return "\n".join(
        f"- [{item['category']}] {item['insight']}" for item in rows[:12]
    )
