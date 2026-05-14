from __future__ import annotations

import re


SENTENCE_SPLIT_RE = re.compile(r"[。！？!?]+")


def count_sentences(text: str) -> int:
    parts = [part.strip() for part in SENTENCE_SPLIT_RE.split(text.strip()) if part.strip()]
    terminal_marks = SENTENCE_SPLIT_RE.findall(text.strip())
    if not text.strip():
        return 0
    if terminal_marks:
        return len(parts)
    return 1


def validate_four_sentence_continuation(text: str, override: bool = False) -> dict:
    sentence_count = count_sentences(text)
    valid = sentence_count == 4
    return {
        "valid": valid or override,
        "raw_valid": valid,
        "sentence_count": sentence_count,
        "override_used": bool(override and not valid),
        "message": "" if valid or override else "续写必须包含正好四个句子。",
    }
