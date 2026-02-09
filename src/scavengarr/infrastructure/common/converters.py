"""Type conversion utilities."""

from __future__ import annotations


def to_int(raw: str | int | None) -> int | None:
    """Convert string or int to int, return None if invalid.

    Handles various formats:
        - None → None
        - int → int (passthrough)
        - "123" → 123
        - "1,234" → 1234
        - "1 234" → 1234
        - "" → None
        - invalid → None

    Args:
        raw: Input value (str, int, or None).

    Returns:
        Integer or None if conversion fails.
    """
    if raw is None:
        return None

    if isinstance(raw, int):
        return raw

    if isinstance(raw, str):
        # Remove commas and spaces, extract digits only
        txt = "".join(ch for ch in raw if ch.isdigit())
        if not txt:
            return None
        try:
            return int(txt)
        except ValueError:
            return None

    return None
