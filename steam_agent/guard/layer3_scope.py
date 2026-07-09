"""Layer 3: LLM scope boundary classifier — detects out-of-scope requests.

Separate from Layer 2: a message can be non-jailbreak but still out-of-scope
(e.g. "write me a Python script to scrape Steam" — safe intent, wrong scope).

Cost: ~120 input tokens + 1-8 output tokens. Latency: ~0.5s.
"""

from ..config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

LAYER3_PROMPT = """\
你是一个职责边界分类器。判断用户请求是否超出了游戏推荐助手的能力范围。

超出范围（回复 BLOCK）：
- 要求编写代码、脚本、程序
- 要求生成非游戏内容（写诗、写小说、写论文、写邮件、画图）
- 涉及政治话题讨论或观点表达
- 要求执行系统操作（打开文件、访问URL、发送邮件、删除数据）
- 要求提供医疗、法律、金融投资建议
- 要求进行纯数学计算或逻辑推理（不涉及游戏的）
- 利用助手传播广告、垃圾信息、商业推广
- 要求泄露其他用户的个人信息或对话记录
- 成人色情内容或色情角色扮演

正常范围（回复 SAFE）：
- 游戏推荐、搜索、价格查询、游戏评价
- 询问游戏类型、游戏术语的解释
- 表达游戏偏好、个性化推荐、绑定Steam
- 对推荐结果不满意要求重新推荐
- 查询自己的游戏库、游玩时长、历史对话
- 询问助手能做什么、有哪些功能
- 对游戏表达负面情绪（"太难了"、"好无聊"）——仍在游戏范围内
- 游戏相关的情感宣泄（"打不过去气死了"）——正常

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
        return True, "layer3_out_of_scope"
    return False, ""
