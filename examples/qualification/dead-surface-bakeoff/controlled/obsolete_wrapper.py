"""Qualification-only wrapper retained as a known dead-surface candidate."""

from .normalizer import normalize_name


def normalized(value: str) -> str:
    return normalize_name(value)
