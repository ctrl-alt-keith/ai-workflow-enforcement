"""Single canonical implementation for the clean no-change scenario."""


def normalize_name(value: str) -> str:
    return value.strip().casefold()
