"""data-load.me Python plugin for Scavengarr.

data-load.me is a German XenForo 2 download forum. Login, search (category
filter by forum node), pagination and the thread links come from
``XenForoPluginBase`` (``infrastructure/plugins/xenforo.py``); this module
holds the site's domain and forum map.

Credentials via env vars: SCAVENGARR_DATALOAD_USERNAME / SCAVENGARR_DATALOAD_PASSWORD
"""

from __future__ import annotations

from scavengarr.infrastructure.plugins.xenforo import XenForoPluginBase

_DOMAINS = ["www.data-load.me"]

# Forum node → Torznab category, from the forum index (checked 2026-09-29).
# The request forums ("... Suche") hold no downloads.
_NODE_CATEGORIES: dict[int, int] = {
    # Filme: SD, HD, UHD / 4K, DVD, 3D, Complete Bluray; Sport, Musikvideos
    **dict.fromkeys((6, 7, 8, 9, 10, 11, 95), 2000),
    108: 5060,
    109: 3020,
    # Fremdsprachige Filme and its SD ... Complete Bluray
    **dict.fromkeys((161, 162, 163, 164, 165, 166, 167), 2010),
    # Animation / Zeichentrick (films)
    **dict.fromkeys((34, 35, 36, 37, 38, 39, 99), 2000),
    # Serien, Reality-TV Serien
    **dict.fromkeys((12, 13, 14, 15, 16, 96, 116), 5000),
    **dict.fromkeys((27, 28, 29, 30, 31, 98), 5070),  # Anime
    **dict.fromkeys((17, 18, 19, 147, 145, 110, 97), 5080),  # Dokumentationen
    # Audio: Alben, Singles, Diskographien, Soundtracks, Sampler, Samples/SFX
    **dict.fromkeys((41, 42, 43, 44, 46, 47, 169), 3000),
    48: 3040,  # Lossless
    45: 3030,  # Hörbücher & Hörspiele
    # Spiele: PC, Mac, Linux, VR, Sonstiges
    **dict.fromkeys((50, 51, 52, 53, 130, 106), 4050),
    **dict.fromkeys((54, 55, 56), 1000),  # Sony, Microsoft, Nintendo
    57: 4070,  # Spiele / Android
    58: 4060,  # Spiele / iOS
    168: 7000,  # Spiele / Pen & Paper (rule books)
    # Software: Windows, Dauerangebote, Freischaltung, Tutorials, Auto & Motor
    **dict.fromkeys((59, 61, 67, 68, 69, 115), 4000),
    64: 4030,  # Software / Mac
    65: 4060,  # Software / iOS
    66: 4070,  # Software / Android
    107: 4040,  # Software / PDA, Navigator
    # Dokumente: Unterhaltung, Magazine, Fachbücher, Comics, Manga
    **dict.fromkeys((70, 71, 72, 160, 94, 73, 74, 75, 76), 7000),
}


class DataloadPlugin(XenForoPluginBase):
    """Python plugin for data-load.me (XenForo download forum)."""

    name = "dataload"
    _domains = _DOMAINS
    _node_categories = _NODE_CATEGORIES


plugin = DataloadPlugin()
