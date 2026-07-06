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
            "You are a game search query translator. "
            "Convert the user's Chinese game-related query into English keywords for a vector search engine. "
            "Rules:\n"
            "- Output ONLY keywords and key phrases, separated by spaces. No full sentences.\n"
            "- Preserve game titles in their official English names (e.g. 黑帝斯 -> Hades, 艾尔登法环 -> Elden Ring).\n"
            "- Include: genres, themes, gameplay mechanics, art style, platform, and similar game titles mentioned.\n"
            "- Keep it concise, at most 10 words.\n"
            "- Never add explanations or extra text."
        ),
    }, {
        "role": "user",
        "content": query,
    }]
    response = _translate_llm.invoke(messages)
    return response.content.strip()
