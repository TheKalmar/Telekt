"""Conservative recovery for UTF-8 text accidentally decoded as Latin-1.

Browsers and the API already speak UTF-8.  This helper only exists to repair
legacy records (and defensive API input) containing unmistakable mojibake such
as ``PoveÄ\u0087ati``.  Correct Unicode is returned unchanged.
"""

from __future__ import annotations

from typing import Any


_MOJIBAKE_PREFIXES = {"\u00c2", "\u00c3", "\u00c4", "\u00c5", "\u00e2"}


def _mojibake_score(value: str) -> int:
    """Score only high-confidence UTF-8-as-Latin-1 fingerprints."""
    return sum(
        2 if "\u0080" <= character <= "\u009f" else 1
        for character in value
        if character in _MOJIBAKE_PREFIXES or "\u0080" <= character <= "\u009f"
    )


def repair_text_encoding(value: Any) -> Any:
    """Recursively repair high-confidence mojibake without touching valid text.

    Two passes cover records that crossed a misconfigured boundary twice.  A
    candidate is accepted only when it strictly reduces the fingerprint score,
    making the function safe and idempotent for ordinary Serbian/English text.
    """
    if isinstance(value, str):
        repaired = value
        for _ in range(2):
            if _mojibake_score(repaired) == 0:
                break
            try:
                candidate = repaired.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
            if _mojibake_score(candidate) >= _mojibake_score(repaired):
                break
            repaired = candidate
        return repaired
    if isinstance(value, dict):
        return {
            repair_text_encoding(key): repair_text_encoding(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [repair_text_encoding(item) for item in value]
    if isinstance(value, tuple):
        return tuple(repair_text_encoding(item) for item in value)
    return value
