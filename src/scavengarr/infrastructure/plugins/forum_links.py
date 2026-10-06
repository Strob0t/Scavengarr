"""Download links in forum posts: link containers and the hosters behind them.

Download forums (boerse and mygully on vBulletin, dataload and myboerse on
XenForo) link a release's files through link containers (crypters such as
filecrypt); a link in a post counts as a download only there. The anchor
text names the hoster behind the container.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

LINK_CONTAINER_HOSTS = frozenset(
    {
        "filecrypt.cc",
        "filecrypt.co",
        "hide.cx",
        "keeplinks.co",
        "keeplinks.eu",
        "keeplinks.org",
        "linkcrypt.ws",
        "protectlinks.com",
        "safelinks.to",
        "share-links.biz",
        "share-links.org",
        "tolink.to",
    }
)

# "download via ddownload.com" (vBulletin), "Online rapidgator.net" (XenForo)
_NAMED_HOST_RE = re.compile(r"(?:via|online)\s+(\S+)", re.IGNORECASE)


def is_link_container(url: str) -> bool:
    """Whether *url* points to a link container: its host is one of
    ``LINK_CONTAINER_HOSTS`` or a subdomain of one (``notfilecrypt.cc`` is
    none)."""
    host = _host(url)
    return any(host == c or host.endswith(f".{c}") for c in LINK_CONTAINER_HOSTS)


def hoster_from_text(text: str) -> str:
    """The hoster an anchor text names: ``RapidGator``, ``download via
    ddownload.com`` or ``Online rapidgator.net``; ``""`` for other texts."""
    if m := _NAMED_HOST_RE.search(text):
        return m.group(1).rstrip(".").removeprefix("www.").split(".")[0].lower()
    # Plain hoster name like "RapidGator", "DDownload"
    if not text.startswith("http") and len(text.split()) <= 2:
        return text.strip().lower()
    return ""


def hoster_from_url(url: str) -> str:
    """Name of the URL's domain (``https://hide.cx/...`` -> ``hide``)."""
    return _host(url).removeprefix("www.").split(".")[0] or "unknown"


def _host(url: str) -> str:
    try:
        return urlparse(url).hostname or ""
    except ValueError:
        return ""
