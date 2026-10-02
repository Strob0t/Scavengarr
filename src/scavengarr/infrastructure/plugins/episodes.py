"""Episode labels of series links: ``<season>x<episode> <label>``.

Plugins whose series pages carry every episode label each link this way
(``1x5 doodstream``); a season/episode request keeps the matching links.
"""

from __future__ import annotations

import re

_EPISODE_LABEL_RE = re.compile(r"(\d+)x(\d+) ")


def episode_label(season: int, episode: int, label: str) -> str:
    """``<season>x<episode> <label>``; an empty *label* gives the prefix."""
    return f"{season}x{episode} {label}"


def filter_episodes(
    links: list[dict[str, str]], season: int, episode: int | None
) -> list[dict[str, str]]:
    """Links of *season* (and *episode*, when given); unlabelled links drop."""
    matched: list[dict[str, str]] = []
    for link in links:
        m = _EPISODE_LABEL_RE.match(link.get("label", ""))
        if not m or int(m.group(1)) != season:
            continue
        if episode is not None and int(m.group(2)) != episode:
            continue
        matched.append(link)
    return matched
