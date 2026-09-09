"""Deterministic topic identity helpers for the durable content pipeline."""

from __future__ import annotations

import re

_STOP_WORDS = {
    "a",
    "ako",
    "da",
    "do",
    "i",
    "iz",
    "je",
    "kako",
    "ko",
    "koji",
    "na",
    "o",
    "od",
    "po",
    "sa",
    "se",
    "sta",
    "što",
    "u",
    "za",
}


def topic_tokens(value: str) -> set[str]:
    """Return stable significant tokens across punctuation and letter case."""
    return {
        token
        for token in re.findall(r"\w+", value.casefold(), flags=re.UNICODE)
        if len(token) > 1 and token not in _STOP_WORDS
    }


def topics_equivalent(first: str, second: str) -> bool:
    """Detect exact or strongly overlapping titles without an LLM call."""
    left = topic_tokens(first)
    right = topic_tokens(second)
    if not left or not right:
        return " ".join(first.casefold().split()) == " ".join(second.casefold().split())
    common = len(left & right)
    return common >= 3 and common / min(len(left), len(right)) >= 0.65
