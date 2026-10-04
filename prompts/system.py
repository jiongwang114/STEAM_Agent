from langchain_core.messages import SystemMessage

from config import MEMORY_SNAPSHOT_MAX_CHARS, MEMORY_SNAPSHOT_MAX_ITEMS
from memory.insight_store import get_insights
from prompts.modules import (
    DECISION_POLICY,
    EVIDENCE_POLICY,
    IDENTITY,
    MEMORY_POLICY,
    OUTPUT_FORMAT,
    PROMPT_VERSION,
    STYLE,
    TOOL_RESULT_POLICY,
)


def build_system_prompt(
    user_id: str,
    steam_id: str | None = None,
    *,
    thread_id: str = "",
    insights: list[dict] | None = None,
    steam_profile: str | None = None,
) -> SystemMessage:
    context = [
        f"prompt_version: {PROMPT_VERSION}",
        f"user_id: {user_id or 'unknown'}",
        f"steam_id: {steam_id if steam_id else '未提供'}",
        f"thread_id: {thread_id or '未提供'}",
    ]
    formatted_insights = _format_insights(user_id, insights)
    if formatted_insights:
        context.append("已确认用户画像：\n" + formatted_insights)
    if steam_id:
        profile = steam_profile
        if profile is None:
            try:
                from memory.game_profile import get_game_profile
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
        EVIDENCE_POLICY,
        OUTPUT_FORMAT,
        MEMORY_POLICY,
        STYLE,
    ]))


def _format_insights(user_id: str, insights: list[dict] | None = None) -> str:
    if not user_id and insights is None:
        return ""
    rows = insights if insights is not None else get_insights(user_id, limit=MEMORY_SNAPSHOT_MAX_ITEMS)
    lines: list[str] = []
    total_chars = 0
    for item in rows[:MEMORY_SNAPSHOT_MAX_ITEMS]:
        line = (
            f"- [{item['category']}] [memory_key: {item['normalized_key']}] "
            f"{item['insight']}"
        )
        if total_chars + len(line) > MEMORY_SNAPSHOT_MAX_CHARS:
            break
        lines.append(line)
        total_chars += len(line) + 1
    return "\n".join(lines)
