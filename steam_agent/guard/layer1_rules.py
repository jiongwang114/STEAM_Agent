"""Layer 1: rule-based guard. Zero-tolerance, zero ambiguity, near-zero cost.

Two categories:
  1. Technical attacks — injection markers, encoding tricks, structural attacks
  2. Content attacks — death threats, political forbidden words

Deliberately EXCLUDED:
  - Profanity / insults → NOT our problem. Let LLM handle politely.
  - "ignore" / "forget" / "扮演" → ambiguous, left to Layer 2.
  - Adult content detection → needs semantic judgment, left to Layer 3.
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORBIDDEN_WORDS_PATH = HERE / "forbidden_words.txt"

# ── Technical attack patterns ──
INJECTION_PATTERNS = [
    r"<\|im_start\|>",           # ChatML injection
    r"<\|im_end\|>",
    r"\[system\]",               # fake system messages
    r"\[/system\]",
    r"<system>",
    r"</system>",
    r"ignore all previous",      # classic jailbreak phrase
    r"disregard (all |your )?instructions",
    r"forget (all |your )?instructions",
    r"override (the |your )?system",
]

# ── Encoding / evasion ──
ZERO_WIDTH_CHARS = re.compile(r"[​‌‍﻿]")
HTML_ENTITY = re.compile(r"&#\d{2,3};")

# ── Death / violence threats ──
THREAT_PATTERN = re.compile(
    r"(我要|我想|准备).{0,5}(杀了你|弄死你|砍死你|打死你|捅死你)"
    r"|(去死|不得好死|全家死光)"
)

# Game-context words that negate threat detection
GAME_CONTEXT = re.compile(r"游戏|boss|怪物|敌人|NPC|副本|角色|剧情|任务|boss|怪|打")

# ── Forbidden words (loaded at startup) ──
_forbidden_words: list[str] = []


def _load_forbidden_words():
    global _forbidden_words
    if _forbidden_words:
        return
    if FORBIDDEN_WORDS_PATH.exists():
        with open(FORBIDDEN_WORDS_PATH, encoding="utf-8") as f:
            _forbidden_words = [line.strip() for line in f if line.strip() and not line.startswith("#")]


def check(text: str) -> tuple[bool, str]:
    """
    Returns (blocked, reason).
    blocked=True means the message should be rejected immediately.
    """
    if not text or not text.strip():
        return False, ""

    # Step 1: zero-width chars — always malicious
    if ZERO_WIDTH_CHARS.search(text):
        return True, "zero_width_chars"

    # Step 2: injection markers
    text_lower = text.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, text_lower):
            return True, "injection_pattern"

    # Step 3: HTML entity encoding
    if HTML_ENTITY.search(text):
        return True, "html_entity_encoding"

    # Step 4: death threats (skip if game context)
    if THREAT_PATTERN.search(text):
        if not GAME_CONTEXT.search(text):
            return True, "death_threat"

    # Step 5: political forbidden words
    _load_forbidden_words()
    for word in _forbidden_words:
        if word and word in text:
            return True, "forbidden_word"

    return False, ""
