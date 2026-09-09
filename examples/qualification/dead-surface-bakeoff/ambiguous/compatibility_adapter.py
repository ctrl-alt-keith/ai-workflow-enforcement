"""Adapter with deliberately unresolved external-consumer compatibility."""


def normalize_legacy_name(value: str) -> str:
    return value.strip().lower()
