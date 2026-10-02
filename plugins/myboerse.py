"""myboerse.bz Python plugin for Scavengarr.

myboerse.bz (Boerse.to successor) is a German XenForo 2 download forum.
Login, search (category filter by forum node), pagination and the thread
links come from ``XenForoPluginBase`` (``infrastructure/plugins/xenforo.py``);
this module holds the site's domains and forum map. myboerse.ws/.me forward
to myboerse.bz over plain HTTP.

Credentials via env vars: SCAVENGARR_MYBOERSE_USERNAME / SCAVENGARR_MYBOERSE_PASSWORD
"""

from __future__ import annotations

from scavengarr.infrastructure.plugins.xenforo import XenForoPluginBase

_DOMAINS = ["myboerse.bz", "myboerse.ws", "myboerse.me"]

# Forum node → Torznab category, from the forum index (checked 2026-09-29).
# The request ("Suche ...") and talk forums hold no downloads.
_NODE_CATEGORIES: dict[int, int] = {
    # Videoboerse: Filme, DVD, HD, UHD / 4K, 3D, Sonstige,
    # Cartoon / Zeichentrick, Sammlungen / Collections
    **dict.fromkeys((60, 61, 62, 75, 72, 67, 68, 74), 2000),
    71: 2010,  # Englisch
    70: 3020,  # Konzerte / Musik
    69: 8000,  # Tutorials (video courses)
    63: 5000,  # Serien
    64: 5070,  # Anime
    65: 5080,  # Dokumentationen
    # Audioboerse: Musik, Diskographien, Soundtracks / OST, Klassik
    **dict.fromkeys((51, 50, 57, 113), 3000),
    56: 3040,  # HQ Audio / Lossless
    **dict.fromkeys((52, 53), 3030),  # (Englische) Hörbücher und Hörspiele
    # Spieleboerse: PC, Mac, Linux, Sonstige, Freischaltung
    **dict.fromkeys((24, 25, 35, 26, 34), 4050),
    **dict.fromkeys((28, 29, 30), 1000),  # Microsoft, Sony, Nintendo
    31: 4070,  # Spiele / Android
    32: 4060,  # Spiele / iPad / iPhone
    # Softwareboerse: Windows, Audio, Nulled Scripts, Templates / GFX,
    # Freischaltung, Portable, Sammelthreads
    **dict.fromkeys((9, 16, 17, 18, 20, 21, 22), 4000),
    10: 4030,  # Software / Mac
    **dict.fromkeys((11, 12), 4060),  # iPad / iPhone, Cydia Apps
    13: 4070,  # Software / Android
    14: 4040,  # Smartphones, Organizer und Sonstige
    # Dokumentenboerse: Comics, Fachbücher, Magazine, Mangas, Unterhaltung,
    # Englische Ebooks, Sammelthreads, Sonstige
    **dict.fromkeys((37, 38, 39, 40, 111, 41, 42, 44, 46, 47, 48), 7000),
    # XXX / Porn: Filme, Clips, MDH / PA, Magazine, Bilder, Spiele, Hentai,
    # Sammlungen
    **dict.fromkeys((83, 84, 85, 86, 87, 88, 89, 92), 6000),
}


class MyboersePlugin(XenForoPluginBase):
    """Python plugin for myboerse.bz (XenForo download forum)."""

    name = "myboerse"
    _domains = _DOMAINS
    _node_categories = _NODE_CATEGORIES


plugin = MyboersePlugin()
