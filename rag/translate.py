from functools import lru_cache

from llm_client import create_chat_model

from config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
)
from model_routing import select_model

_translate_llm = create_chat_model(
    model=select_model("translate").model,
    temperature=0.0,
    max_tokens=512,
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=LLM_REQUEST_TIMEOUT_SECONDS,
    max_retries=LLM_MAX_RETRIES,
)


@lru_cache(maxsize=512)
def translate_to_english(query: str) -> str:
    """
    Translate a Chinese game-related query to English.
    Used to bridge the gap between Chinese queries and the English game knowledge base.
    """
    messages = [{
        "role": "system",
        "content": (
            "You are a constrained game search query translator. "
            "Convert the user's Chinese game-related query into English search terms without losing constraints. "
            "Rules:\n"
            "- Output ONLY the translated search query. Use sentences where necessary to preserve relationships and exclusions.\n"
            "- Preserve game titles in their official English names (e.g. 黑帝斯 -> Hades, 艾尔登法环 -> Elden Ring).\n"
            "- Preserve every qualifier, including free, single-player, multiplayer, co-op, beginner-friendly, highly rated, release period, mood, and exclusions.\n"
            "- Include the canonical English genre/tag plus all preserved qualifiers and game titles.\n"
            "- Keep it concise without a word limit; never drop a qualifier or detach negation from its target.\n"
            "- Never add explanations or extra text."
        ),
    }, {
        "role": "user",
        "content": query,
    }]
    response = _translate_llm.invoke(messages)
    return response.content.strip()
