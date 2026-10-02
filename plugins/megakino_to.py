"""megakino.org / megakino.to Python plugin for Scavengarr.

German streaming aggregator on the shared "/data" JSON API (movies and TV
series with TMDB metadata, stream links from several hosters); the search,
detail, season/episode and stream logic lives in
``scavengarr.infrastructure.plugins.data_api``.

Multi-domain support: megakino.org, megakino.to
No authentication required.
"""

from __future__ import annotations

from scavengarr.infrastructure.plugins.data_api import DataApiPluginBase

_DOMAINS = ["megakino.org", "megakino.to"]


class MegakinoToPlugin(DataApiPluginBase):
    """Python plugin for megakino.org using httpx (JSON API)."""

    name = "megakino_to"
    provides = "stream"
    _domains = _DOMAINS


plugin = MegakinoToPlugin()
