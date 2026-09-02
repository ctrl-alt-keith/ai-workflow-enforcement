"""Byte-blind final emission for one compact prompt-delivery handoff."""

from __future__ import annotations

from typing import TextIO


def emit_handoff(handoff: str, output: TextIO) -> None:
    """Write and flush metadata-only handoff text supplied by the fixed DAG."""
    payload = handoff + "\n"
    written = output.write(payload)
    if written != len(payload):
        raise OSError("incomplete handoff write")
    output.flush()
