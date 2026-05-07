"""Explainable text heuristics for notes vs playbook comparisons."""

from __future__ import annotations

from collections import Counter
import re


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$", re.MULTILINE)
WORD_RE = re.compile(r"[a-z0-9]+")

GENERIC_HEADINGS = {
    "background",
    "context",
    "examples",
    "goal",
    "goals",
    "limitations",
    "notes",
    "overview",
    "purpose",
    "scope",
    "summary",
    "validation",
}

GENERIC_PHRASE_TOKENS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "be",
    "by",
    "for",
    "from",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "with",
}


def normalize_text(text: str) -> str:
    return " ".join(WORD_RE.findall(text.lower()))


def normalized_words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def normalized_headings(text: str) -> set[str]:
    headings: set[str] = set()
    for match in HEADING_RE.finditer(text):
        heading = normalize_text(match.group(2))
        if heading and heading not in GENERIC_HEADINGS:
            headings.add(heading)
    return headings


def normalized_phrases(text: str, phrase_words: int) -> Counter[str]:
    words = normalized_words(_strip_markdown_syntax(text))
    phrases: Counter[str] = Counter()
    if len(words) < phrase_words:
        return phrases

    for index in range(0, len(words) - phrase_words + 1):
        phrase_tokens = words[index : index + phrase_words]
        if _is_low_signal_phrase(phrase_tokens):
            continue
        phrases[" ".join(phrase_tokens)] += 1
    return phrases


def token_similarity(left: str, right: str) -> float:
    left_tokens = set(normalized_words(left))
    right_tokens = set(normalized_words(right))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def has_canonical_reference(text: str) -> bool:
    normalized = normalize_text(text)
    reference_terms = (
        "ai workflow playbook",
        "workflow playbook",
        "canonical playbook",
        "playbook guidance",
        "docs start here",
    )
    return any(term in normalized for term in reference_terms)


def _strip_markdown_syntax(text: str) -> str:
    text = re.sub(r"`[^`]+`", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"^#{1,6}\s+", " ", text, flags=re.MULTILINE)
    return text


def _is_low_signal_phrase(tokens: list[str]) -> bool:
    unique_tokens = set(tokens)
    if len(unique_tokens) < max(4, len(tokens) // 2):
        return True
    content_tokens = unique_tokens - GENERIC_PHRASE_TOKENS
    return len(content_tokens) < 4

