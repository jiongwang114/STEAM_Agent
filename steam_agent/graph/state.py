from typing import Annotated, Any, Sequence

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import NotRequired, TypedDict


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    steam_id: str | None
    user_id: str
    budget: NotRequired[dict[str, Any]]
    usage: NotRequired[dict[str, int]]
    tool_history: NotRequired[list[dict[str, Any]]]
    evidence: NotRequired[list[dict[str, Any]]]
    termination_reason: NotRequired[str]
    repair_attempts: NotRequired[int]
    validation: NotRequired[dict[str, Any]]
    context_stats: NotRequired[dict[str, int]]
    experiment: NotRequired[dict[str, Any]]
    model_history: NotRequired[list[dict[str, str]]]
