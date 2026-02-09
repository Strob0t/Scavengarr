"""Data extraction utilities."""

from __future__ import annotations


def extract_download_link(raw_data: dict) -> str | None:
    """Extract download link from raw data.

    Placeholder for future common extraction logic.

    Args:
        raw_data: Raw data dictionary.

    Returns:
        Extracted download link or None.
    """
    # Placeholder implementation
    # This can be extended when common extraction patterns are identified
    return raw_data.get("link") or raw_data.get("url")
