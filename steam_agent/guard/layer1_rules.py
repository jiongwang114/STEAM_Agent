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

    # Political forbidden words
    _load_forbidden_words()
    for word in _forbidden_words:
        if word and word in text:
            return True, "forbidden_word"

    return False, ""
