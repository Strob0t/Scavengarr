"""A site's official domain list: the addresses it publishes for itself.

Some sites name their current addresses on a list site of their own
(``filmo.domains``, ``aniworld.domains``, ``serien.domains``): a
single-page app whose bundle carries the addresses as ``url:``/``href:``
values, or a plain page of links. ``read_domain_list()`` reads such a
list through the plugin's client (so the shared client's
``PrivateAddressGuard`` refuses LAN targets) and returns its host names,
at most ``MAX_LISTED_HOSTS`` and no IP literals. A listed host is only an
address of the site once it serves the site itself: ``serves_site()``
looks for the plugin's marker, since parked and for-sale domains answer
below 400 too.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.lexbor import LexborHTMLParser

from scavengarr.infrastructure.plugins.constants import DEFAULT_DOMAIN_CHECK_TIMEOUT
from scavengarr.infrastructure.plugins.dom import parse_page

MAX_LISTED_HOSTS = 20
# How long a list's hosts stay cached; a list that failed is read again an
# hour later, so a long outage of the list site costs one read an hour
DOMAIN_LIST_TTL_S = 24 * 3600
DOMAIN_LIST_FAILED_TTL_S = 3600
# The scripts of a list page read at most (a Vite app has one bundle)
_MAX_SCRIPTS = 3

# ``url:"https://host"`` / ``href:`https://host``` in a script; ``siteUrl``
# and ``probeScriptUrl`` are other fields (the match is case-sensitive)
_SCRIPT_VALUE = re.compile(
    r"""(?<![\w$])(?:url|href)\s*:\s*["'`](https?://[^"'`\s]+)"""
)
_ANCHOR = re.compile(r"""<a\b[^>]*?\bhref\s*=\s*["'](https?://[^"'\s]+)""", re.I)
_SCRIPT_SRC = re.compile(r"""<script\b[^>]*?\bsrc\s*=\s*["']([^"']+)["']""", re.I)


def _host(url: str) -> str | None:
    """*url*'s host name, ``None`` for an IP literal or a name without a dot."""
    host = (urlsplit(url).hostname or "").lower()
    if "." not in host:
        return None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return host
    return None


def listed_hosts(page: str, scripts: Iterable[str]) -> list[str]:
    """The host names a list *page* and its *scripts* name, in order: the
    page's links and the scripts' ``url``/``href`` values, at most
    ``MAX_LISTED_HOSTS``."""
    urls = _ANCHOR.findall(page) + _SCRIPT_VALUE.findall(page)
    for script in scripts:
        urls += _SCRIPT_VALUE.findall(script)
    hosts = dict.fromkeys(h for h in map(_host, urls) if h)
    return list(hosts)[:MAX_LISTED_HOSTS]


async def read_domain_list(
    client: httpx.AsyncClient, url: str, user_agent: str
) -> list[str]:
    """The host names of the list at *url* (its own host excluded).

    Reads the page and the scripts it loads from its own host. Raises
    ``httpx.HTTPError`` when the list does not load and ``ValueError``
    when it names no host (its form changed).
    """
    headers = {"User-Agent": user_agent}
    resp = await client.get(url, headers=headers, timeout=DEFAULT_DOMAIN_CHECK_TIMEOUT)
    resp.raise_for_status()
    page_url = str(resp.url)
    own = urlsplit(page_url).hostname
    sources = [urljoin(page_url, src) for src in _SCRIPT_SRC.findall(resp.text)]
    scripts = []
    for src in [s for s in sources if urlsplit(s).hostname == own][:_MAX_SCRIPTS]:
        script = await client.get(
            src, headers=headers, timeout=DEFAULT_DOMAIN_CHECK_TIMEOUT
        )
        script.raise_for_status()
        scripts.append(script.text)
    hosts = [h for h in listed_hosts(resp.text, scripts) if h != own]
    if not hosts:
        raise ValueError("the domain list names no host")
    return hosts


class _Page:
    """A page's tree, for a selector test."""

    tree: LexborHTMLParser | None = None

    def feed(self, html: str, /) -> None:
        self.tree = LexborHTMLParser(html)


async def serves_site(html: str, marker: str) -> bool:
    """Whether *html* shows the plugin's *marker* (a CSS selector)."""
    page = await parse_page(_Page(), html)
    return page.tree is not None and page.tree.css_first(marker) is not None
