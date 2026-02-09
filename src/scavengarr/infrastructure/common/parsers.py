"""Parsing utilities for data extraction."""

from __future__ import annotations

import re


def parse_size_to_bytes(size_str: str) -> int:
    """Parse size string to bytes.

    Supports formats:
        - "1234" (raw bytes)
        - "4.5 GB"
        - "500 MB"
        - "1.2 TB"

    Args:
        size_str: Size string.

    Returns:
        Size in bytes (int).
    """
    if not size_str:
        return 0

    if size_str.isdigit():
        return int(size_str)

    match = re.match(r"([\d.]+)\s*([KMGT]?B)", size_str.upper().strip())
    if not match:
        return 0

    value = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }

    return int(value * multipliers.get(unit, 1))
