"""Layer 3: LLM red-line classifier — only blocks truly unacceptable content.

This is NOT a scope-boundary classifier anymore. It only blocks:
  - Adult / pornographic content
  - Political sensitive topics

Everything else — coding, poetry, math, medical advice, role-play, insults,
jailbreak attempts — passes through. The System Prompt handles those by
pulling the conversation back to game recommendations.

Cost: ~100 input tokens + 1-8 output tokens. Latency: ~0.5s.
"""

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

LAYER3_PROMPT = """\
你是一个底线安全分类器。只拦截以下两类内容，其余全部放行：

必须拦截（回复 BLOCK）：
- 成人色情内容、色情角色扮演、性暗示对话
- 政治敏感话题讨论或观点表达（包括用隐晦方式试探政治立场）

放行（回复 SAFE）：
- 所有游戏相关的内容，即使语气愤怒、骂人、抱怨
- 要求写代码、写诗、写文章、做数学题 —— 放行，助手会自己拉回游戏
- 要求扮演角色、忽略指令、测试边界 —— 放行，助手会自己应对
- 医疗、法律、金融等专业问题 —— 放行
- 闲聊、情感倾诉、无理取闹、无意义输入 —— 放行
- 任何不属于上面"必须拦截"类别的内容 —— 放行

输入：{user_message}

只回复：BLOCK 或 SAFE"""


def check(text: str) -> tuple[bool, str]:
    """Returns (blocked, reason)."""

    def _call_llm(prompt: str) -> str:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model="deepseek-chat",
            temperature=0.0,
            max_tokens=8,
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
        )
        try:
            resp = llm.invoke(prompt)
            return resp.content.strip().upper()
        except Exception:
            return "ERROR"

    full_prompt = LAYER3_PROMPT.format(user_message=text[:1000])
    result = _call_llm(full_prompt)

    if result.startswith("BLOCK"):
        return True, "layer3_red_line"
    return False, ""
