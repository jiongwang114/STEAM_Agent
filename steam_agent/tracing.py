import logging
import os
from contextvars import ContextVar
from typing import Any

from .config import LANGCHAIN_API_KEY, LANGCHAIN_PROJECT, LANGCHAIN_TRACING_V2

logger = logging.getLogger(__name__)

_trace_context: ContextVar[dict[str, str]] = ContextVar("trace_context", default={})


def setup_langsmith():
    """Initialize LangSmith tracing. Called once at app startup."""
    if LANGCHAIN_TRACING_V2 != "true":
        logger.info("LangSmith tracing is disabled (LANGCHAIN_TRACING_V2 != 'true').")
        return

    if not LANGCHAIN_API_KEY:
        logger.warning(
            "LANGCHAIN_TRACING_V2=true but LANGCHAIN_API_KEY is not set. "
            "Tracing will not work."
        )
        return

    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_API_KEY", LANGCHAIN_API_KEY)
    os.environ.setdefault("LANGCHAIN_PROJECT", LANGCHAIN_PROJECT)
    os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")

    logger.info(
        "LangSmith tracing enabled (project=%s).", LANGCHAIN_PROJECT,
    )


def set_trace_context(user_id: str = "", thread_id: str = ""):
    """Set per-request trace metadata."""
    _trace_context.set({"user_id": user_id, "thread_id": thread_id})


def get_trace_metadata() -> dict[str, Any]:
    """Get the current trace metadata for enriching LangSmith runs."""
    return dict(_trace_context.get())
