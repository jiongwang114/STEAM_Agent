from langchain_openai import ChatOpenAI

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

_translate_llm = ChatOpenAI(
    model="deepseek-chat",
    temperature=0.0,
    max_tokens=128,
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


def translate_to_english(query: str) -> str:
    """
    Translate a Chinese game-related query to English.
    Used to bridge the gap between Chinese queries and the English game knowledge base.
    """
    messages = [{
        "role": "system",
        "content": (
            "Translate the following game-related query into English. "
            "Only output the translation, nothing else."
        ),
    }, {
        "role": "user",
        "content": query,
    }]
    response = _translate_llm.invoke(messages)
    return response.content.strip()
