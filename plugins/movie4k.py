"""movie4k.sx Python plugin for Scavengarr.

German streaming aggregator on the shared "/data" JSON API (movies and TV
series with TMDB metadata, stream links from several hosters); the search,
detail, season/episode and stream logic lives in
``scavengarr.infrastructure.plugins.data_api``.

Multi-domain support: movie4k.sx, movie4k.ag, movie4k.stream
No authentication required.
"""

from __future__ import annotations

from scavengarr.infrastructure.plugins.data_api import DataApiPluginBase

_DOMAINS = ["movie4k.sx", "movie4k.ag", "movie4k.stream"]


class Movie4kPlugin(DataApiPluginBase):
    """Python plugin for movie4k.sx using httpx (JSON API)."""

    name = "movie4k"
    provides = "stream"
    _domains = _DOMAINS


plugin = Movie4kPlugin()
