"""Byte-blind final emission for one compact prompt-delivery handoff."""

from __future__ import annotations

from typing import TextIO


def emit_handoff(handoff: str, output: TextIO) -> None:
    """Write and flush metadata-only handoff text supplied by the fixed DAG."""
    output.write(handoff + "\n")
    output.flush()
