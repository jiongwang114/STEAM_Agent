from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_openai import ChatOpenAI

from config import CUSTOM_LLM_REASONING_EFFORT, LLM_PROVIDER


class CustomChatOpenAI(ChatOpenAI):
    """Adapter for custom gateways that return the assistant text directly."""

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> ChatResult:
        if isinstance(response, str):
            return ChatResult(
                generations=[ChatGeneration(message=AIMessage(content=response), generation_info=generation_info or {})]
            )
        return super()._create_chat_result(response, generation_info)


def create_chat_model(**kwargs: Any) -> ChatOpenAI:
    cls = CustomChatOpenAI if LLM_PROVIDER == "custom" else ChatOpenAI
    if LLM_PROVIDER == "custom":
        kwargs.setdefault("reasoning_effort", CUSTOM_LLM_REASONING_EFFORT)
    return cls(**kwargs)
