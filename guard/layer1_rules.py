"""Layer 1: rule-based guard. Zero-tolerance, zero ambiguity, near-zero cost.

Only blocks unambiguous attacks:
  - Zero-width characters (always malicious)
  - Political forbidden words

Everything else — injection phrases, role-hijack, insults, death threats, "ignore all
instructions" — is NOT blocked here. Let the System Prompt handle them by pulling the
conversation back to game recommendations.
"""

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORBIDDEN_WORDS_PATH = HERE / "forbidden_words.txt"

ZERO_WIDTH_CHARS = re.compile(r"[​‌‍﻿]")
INTERNAL_INFO_PATTERNS = (
    re.compile(r"(?:\u7cfb\u7edf|system)\s*(?:\u63d0\u793a\u8bcd|prompt|\u6307\u4ee4|\u89c4\u5219)", re.I),
    re.compile(r"(?:rag|\u68c0\u7d22)\s*(?:\u53c2\u6570|\u914d\u7f6e|prompt|\u63d0\u793a\u8bcd|\u4e0a\u4e0b\u6587)", re.I),
    re.compile(r"(?:\u5185\u90e8|\u9690\u85cf|\u79c1\u6709)\s*(?:\u4fe1\u606f|\u89c4\u5219|\u6307\u4ee4|\u53c2\u6570|\u4e0a\u4e0b\u6587)", re.I),
    re.compile(r"(?:\u5de5\u5177|tool)\s*(?:\u540d\u79f0|\u53c2\u6570|\u8c03\u7528|\u534f\u8bae)", re.I),
)

_forbidden_words: list[str] = []


def _load_forbidden_words():
    global _forbidden_words
    if _forbidden_words:
        return
    if FORBIDDEN_WORDS_PATH.exists():
        with open(FORBIDDEN_WORDS_PATH, encoding="utf-8") as f:
            _forbidden_words = [line.strip() for line in f if line.strip() and not line.startswith("#")]


def check(text: str) -> tuple[bool, str]:
    """Returns (blocked, reason). blocked=True means reject immediately."""
    if not text or not text.strip():
        return False, ""

    # Zero-width characters — always malicious
    if ZERO_WIDTH_CHARS.search(text):
        return True, "zero_width_chars"

    if any(pattern.search(text) for pattern in INTERNAL_INFO_PATTERNS):
        return True, "internal_information_disclosure"

    # Political forbidden words
    _load_forbidden_words()
    for word in _forbidden_words:
        if word and word in text:
            return True, "forbidden_word"

    return False, ""
