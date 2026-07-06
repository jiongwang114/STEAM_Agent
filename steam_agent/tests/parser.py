"""
Parse the "预期调用的工具" and "预期不调用的工具" columns from eval_cases.csv.

Supported formats for 预期调用的工具:
    "无"           → no tools should be called
    "tool_name"    → this exact tool must be called
    "A → B → C"    → chain: A then B then C, in that order (each must appear)
    "A (可选B)"    → A required, B optional (must include A, B is bonus)
    "A + B"        → both A and B must be called (order not enforced)
    "A (描述)"     → parenthetical explanation is stripped; only A is checked
    空             → skip this check

Supported formats for 预期不调用的工具:
    "全部"         → no tools should be called at all
    "tool_name"    → this tool must NOT be called
    "A;B;C"        → none of these tools should be called
    空             → skip this check
"""

from dataclasses import dataclass, field


@dataclass
class ExpectedTools:
    required: set[str] = field(default_factory=set)
    forbidden: set[str] = field(default_factory=set)
    chain: list[str] = field(default_factory=list)
    optional: set[str] = field(default_factory=set)
    call_none: bool = False


def parse_expected(expected_str: str) -> ExpectedTools:
    """Parse the '预期调用的工具' column."""
    result = ExpectedTools()

    raw = expected_str.strip() if expected_str else ""
    if not raw or raw == "无":
        result.call_none = True
        return result

    if "→" in raw:
        parts = [p.strip() for p in raw.split("→")]
        for p in parts:
            name = _clean_tool_name(p)
            result.chain.append(name)
            result.required.add(name)
        return result

    if "可选" in raw:
        main_part = raw.split("可选")[0].strip().rstrip("(").strip()
        opt_part = raw.split("可选")[1].strip().rstrip(")").strip()
        result.required.add(_clean_tool_name(main_part))
        result.optional.add(_clean_tool_name(opt_part))
        return result

    if "+" in raw:
        parts = [p.strip() for p in raw.split("+")]
        for p in parts:
            result.required.add(_clean_tool_name(p))
        return result

    result.required.add(_clean_tool_name(raw))
    return result


def parse_forbidden(forbidden_str: str) -> set[str]:
    """Parse the '预期不调用的工具' column."""
    raw = forbidden_str.strip() if forbidden_str else ""
    if not raw or raw == "无":
        return set()
    if raw == "全部":
        return {"__all__"}
    if ";" in raw:
        return {_clean_tool_name(p.strip()) for p in raw.split(";")}
    return {_clean_tool_name(raw)}


def _clean_tool_name(text: str) -> str:
    """Extract the tool function name from text that may have extra description."""
    text = text.strip()
    if "(" in text:
        text = text.split("(")[0].strip()
    if "（" in text:
        text = text.split("（")[0].strip()
    return text


# All 5 tool names as defined in the graph
ALL_TOOLS = {
    "get_user_playtime",
    "search_steam_store",
    "rag_search_similar_games",
    "save_user_insight",
    "recall_user_memory",
}
