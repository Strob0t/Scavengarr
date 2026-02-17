"""Shared video URL extraction utilities for XFS-based video hosters.

Functions to extract playable video URLs (HLS m3u8, MP4) from embed pages
that use JWPlayer, Dean Edwards packed JavaScript, or direct URL patterns.

Used by both the generic XFS resolver and the Filemoon resolver.
"""

from __future__ import annotations

import re


def unpack_p_a_c_k(packed: str) -> str | None:
    """Unpack Dean Edwards packed JavaScript.

    Format: eval(function(p,a,c,k,e,d){...}('payload',base,count,'dict'.split('|')))

    The algorithm replaces base-N encoded tokens in the payload
    with words from the dictionary.
    """
    match = re.search(
        r"}\('(.*?)',\s*(\d+),\s*(\d+),\s*'([^']*)'\s*\.split\('\|'\)",
        packed,
        re.DOTALL,
    )
    if not match:
        return None

    payload = match.group(1)
    base = int(match.group(2))
    count = int(match.group(3))
    keywords = match.group(4).split("|")

    if len(keywords) < count:
        keywords.extend([""] * (count - len(keywords)))

    def _base_n(num: int, radix: int) -> str:
        """Convert integer to base-N string (supports up to base 36)."""
        if num < 0:
            return ""
        chars = "0123456789abcdefghijklmnopqrstuvwxyz"
        if num < radix:
            return chars[num]
        return _base_n(num // radix, radix) + chars[num % radix]

    def _replace_word(m: re.Match[str]) -> str:
        word = m.group(0)
        try:
            index = int(word, base)
        except ValueError:
            return word
        if index < len(keywords) and keywords[index]:
            return keywords[index]
        return word

    result = re.sub(r"\b\w+\b", _replace_word, payload)
    return result


def extract_hls_from_unpacked(js: str) -> str | None:
    """Extract HLS/MP4 URL from unpacked JWPlayer config.

    Handles both regular quotes and escaped quotes (\\' or \\")
    that appear in unpacked output.
    """
    normalized = js.replace("\\'", "'").replace('\\"', '"')

    # Pattern 1: sources:[{file:"https://...master.m3u8"}]
    m = re.search(
        r"""sources\s*:\s*\[\s*\{[^}]*file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)""",
        normalized,
    )
    if m:
        return m.group(1)

    # Pattern 2: file:"https://...m3u8"
    m = re.search(
        r"""file\s*:\s*["'](https?://[^"']+\.m3u8[^"']*)""",
        normalized,
    )
    if m:
        return m.group(1)

    # Pattern 3: source/src with video URL
    m = re.search(
        r"""(?:source|src)\s*:\s*["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)""",
        normalized,
    )
    if m:
        return m.group(1)

    return None


def extract_video_url(html: str) -> str | None:
    """Extract a playable video URL from embed page HTML.

    Tries multiple extraction strategies in order:
    1. Streamwish-specific ``"hls2":"http..."`` pattern
    2. Dean Edwards packed JavaScript blocks (JWPlayer config)
    3. Direct ``sources:[{file:"..."}]`` in page source
    4. Direct HLS/MP4 URL in page source

    Returns the first video URL found, or ``None``.
    """
    # Strategy 1: Streamwish "hls2" pattern (highest priority — very specific)
    m = re.search(r'"hls2"\s*:\s*"(https?://[^"]+)"', html)
    if m:
        return m.group(1)

    # Strategy 2: Packed JS blocks (Dean Edwards packer)
    for pm in re.finditer(
        r"eval\s*\(\s*function\s*\(\s*p\s*,\s*a\s*,\s*c\s*,\s*k\s*,\s*e\s*,\s*d\s*\)",
        html,
    ):
        chunk = html[pm.start() : pm.start() + 65536]
        unpacked = unpack_p_a_c_k(chunk)
        if not unpacked:
            continue
        url = extract_hls_from_unpacked(unpacked)
        if url:
            return url

    # Strategy 3: JWPlayer sources directly in page
    m = re.search(
        r"""sources\s*:\s*\[\s*\{[^}]*file\s*:\s*["'](https?://[^"']+\.(?:m3u8|mp4)[^"']*)""",
        html,
    )
    if m:
        url = m.group(1)
        if "thumbnail" not in url.lower() and "track" not in url.lower():
            return url

    # Strategy 4: Direct HLS/MP4 URL in page (quoted, not in CSS/JS artifacts)
    m = re.search(
        r"""["'](https?://[^"']+\.m3u8[^"']*)["']""",
        html,
    )
    if m:
        url = m.group(1)
        if "thumbnail" not in url.lower() and "track" not in url.lower():
            return url

    return None
