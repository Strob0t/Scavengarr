"""Convert plugin SearchResults into RankedStreams for Stremio sorting.

Pure transformation logic — no I/O, no framework dependencies.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import urlparse

from scavengarr.domain.entities.stremio import RankedStream
from scavengarr.domain.plugins.base import SearchResult, link_url
from scavengarr.infrastructure.stremio.release_parser import (
    parse_language,
    parse_quality,
)

# Hoster label or second-level domain -> resolver name (None if unknown)
CanonicalHosterFn = Callable[[str], str | None]

# Labels sites use when they do not name the hoster
_PLACEHOLDER_HOSTERS = frozenset({"unknown", "n/a", "-"})

_DOMAIN_LABEL = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def _extract_hoster(url: str) -> str:
    """Extract hoster name from URL domain.

    Examples:
        "https://voe.sx/e/abc" -> "voe"
        "https://filemoon.sx/e/abc" -> "filemoon"
        "https://streamtape.com/v/abc" -> "streamtape"
    """
    try:
        hostname = urlparse(url).hostname
        if not hostname:
            return "unknown"
        # Split hostname into parts, take the second-level domain
        # e.g. "voe.sx" -> "voe", "doodstream.com" -> "doodstream"
        parts = hostname.split(".")
        if len(parts) >= 2:
            return parts[-2]
        return parts[0]
    except Exception:  # noqa: BLE001
        return "unknown"


def _normalize_hoster_name(raw: str) -> str:
    """Normalize hoster label from plugins to a clean hoster name.

    Plugins may provide labels like "VOE (HD)", "Filemoon (720p)",
    "Filemoon: HD", "Streamtape", etc. We strip quality suffixes
    and lowercase.

    Examples:
        "VOE (HD)" -> "voe"
        "Filemoon (720p)" -> "filemoon"
        "Filemoon: HD" -> "filemoon"
        "SuperVideo" -> "supervideo"
        "DoodStream" -> "doodstream"
    """
    # Strip parenthesized quality suffix: "VOE (HD)" -> "VOE"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    # Strip colon-separated suffix: "Filemoon: HD" -> "Filemoon"
    name = re.sub(r"\s*:.*$", "", name).strip().lower()
    # A domain as label ("voe.sx", "www.vinovo.to") names the hoster by its
    # second-level part, like a URL does
    if _DOMAIN_LABEL.fullmatch(name):
        name = name.split(".")[-2]
    return name or raw.lower()


def _hoster_name(url: str, label: str, canonical: CanonicalHosterFn | None) -> str:
    """Name of the hoster behind *url*.

    A label naming a known hoster wins (redirect links such as
    ``s.to/redirect/…`` carry the hoster only in the label), then a known
    URL host (``kinoger.pw``; ``kinoger.ru`` is another hoster), then a
    known URL domain, then the label, then the URL domain. Known names are
    resolver names, so mirror domains and aliases share one hoster name
    (one stream per hoster, one ranking bonus).
    """
    name = _normalize_hoster_name(label) if label else ""
    if name in _PLACEHOLDER_HOSTERS:
        name = ""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    domain = _extract_hoster(url)
    if domain == "unknown":
        domain = ""
    if canonical is not None:
        for candidate in (name, host, domain):
            known = canonical(candidate) if candidate else None
            if known:
                return known
    return name or domain or "unknown"


def _convert_single_result(
    result: SearchResult,
    plugin_default_language: str | None = None,
    canonical_hoster: CanonicalHosterFn | None = None,
) -> list[RankedStream]:
    """Convert a single SearchResult into one or more RankedStreams."""
    streams: list[RankedStream] = []
    source_plugin = str(result.metadata.get("source_plugin", ""))

    if result.download_links:
        for link in result.download_links:
            url = link_url(link)
            if not url:
                continue

            quality = parse_quality(
                release_name=result.release_name,
                quality_badge=result.metadata.get("quality"),
                link_quality=link.get("quality"),
            )
            language = parse_language(
                release_name=result.release_name,
                link_language=link.get("language"),
                plugin_default_language=plugin_default_language,
            )
            hoster = _hoster_name(url, link.get("hoster") or "", canonical_hoster)
            size = link.get("size") or result.size

            streams.append(
                RankedStream(
                    url=url,
                    hoster=hoster,
                    quality=quality,
                    language=language,
                    size=size,
                    release_name=result.release_name,
                    title=result.title,
                    source_plugin=source_plugin,
                )
            )
    elif result.download_link:
        quality = parse_quality(
            release_name=result.release_name,
            quality_badge=result.metadata.get("quality"),
            link_quality=None,
        )
        language = parse_language(
            release_name=result.release_name,
            link_language=None,
            plugin_default_language=plugin_default_language,
        )
        hoster = _hoster_name(result.download_link, "", canonical_hoster)

        streams.append(
            RankedStream(
                url=result.download_link,
                hoster=hoster,
                quality=quality,
                language=language,
                size=result.size,
                release_name=result.release_name,
                title=result.title,
                source_plugin=source_plugin,
            )
        )

    return streams


def convert_search_results(
    results: list[SearchResult],
    plugin_languages: dict[str, str] | None = None,
    *,
    canonical_hoster: CanonicalHosterFn | None = None,
) -> list[RankedStream]:
    """Convert plugin SearchResults into RankedStreams for sorting.

    Args:
        results: Plugin search results to convert.
        plugin_languages: Mapping of plugin name to default language code.
            Used as fallback when language can't be determined from the
            release name or link metadata.
        canonical_hoster: Maps a hoster label or domain to its resolver
            name (``HosterResolverRegistry.canonical_hoster``).

    For each SearchResult:
    - If download_links exists and is non-empty, create one RankedStream per link.
    - Each link dict may have: url, quality, language, hoster, size.
    - If no download_links, create a single RankedStream from download_link.
    - Entries without a valid URL are skipped.
    """
    langs = plugin_languages or {}
    streams: list[RankedStream] = []
    for result in results:
        source = str(result.metadata.get("source_plugin", ""))
        streams.extend(
            _convert_single_result(
                result,
                plugin_default_language=langs.get(source),
                canonical_hoster=canonical_hoster,
            )
        )
    return streams
