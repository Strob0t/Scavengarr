"""Convert plugin SearchResults into RankedStreams for Stremio sorting.

Pure transformation logic — no I/O, no framework dependencies.
"""

from __future__ import annotations

from urllib.parse import urlparse

from scavengarr.domain.entities.stremio import RankedStream
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.release_parser import (
    parse_language,
    parse_quality,
)


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
    "Streamtape", etc. We strip quality suffixes and lowercase.

    Examples:
        "VOE (HD)" -> "voe"
        "Filemoon (720p)" -> "filemoon"
        "SuperVideo" -> "supervideo"
        "DoodStream" -> "doodstream"
    """
    import re

    # Strip parenthesized quality suffix: "VOE (HD)" -> "VOE"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip().lower()
    return name or raw.lower()


def _convert_single_result(
    result: SearchResult,
    plugin_default_language: str | None = None,
) -> list[RankedStream]:
    """Convert a single SearchResult into one or more RankedStreams."""
    streams: list[RankedStream] = []
    source_plugin = str(result.metadata.get("source_plugin", ""))

    if result.download_links:
        for link in result.download_links:
            # Plugins use "link" key (some older ones use "url")
            url = link.get("link", "") or link.get("url", "")
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
            raw_hoster = link.get("hoster", "")
            hoster = (
                _normalize_hoster_name(raw_hoster)
                if raw_hoster
                else _extract_hoster(url)
            )
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
        hoster = _extract_hoster(result.download_link)

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
) -> list[RankedStream]:
    """Convert plugin SearchResults into RankedStreams for sorting.

    Args:
        results: Plugin search results to convert.
        plugin_languages: Mapping of plugin name to default language code.
            Used as fallback when language can't be determined from the
            release name or link metadata.

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
            _convert_single_result(result, plugin_default_language=langs.get(source))
        )
    return streams
