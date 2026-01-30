"""
Torznab XML presenter.

Renders Torznab-compliant RSS/XML feeds according to:
- Torznab specification: http://torznab.com/schemas/2015/feed
- RSS 2.0 specification
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from scavengarr.domain.entities import TorznabCaps, TorznabItem

_TORZNAB_NS = "http://torznab.com/schemas/2015/feed"
_ATOM_NS = "http://www.w3.org/2005/Atom"

ET.register_namespace("torznab", _TORZNAB_NS)
ET.register_namespace("atom", _ATOM_NS)


@dataclass(frozen=True)
class TorznabRendered:
    """Rendered Torznab XML response."""

    payload: bytes
    media_type: str = "application/xml"


def render_caps_xml(caps: TorznabCaps) -> TorznabRendered:
    root = ET.Element("caps")

    server = ET.SubElement(root, "server")
    server.set("title", caps.server_title)
    server.set("version", caps.server_version)

    limits = ET.SubElement(root, "limits")
    limits.set("max", str(caps.limits_max))
    limits.set("default", str(caps.limits_default))

    searching = ET.SubElement(root, "searching")
    search = ET.SubElement(searching, "search")
    search.set("available", "yes")
    search.set("supportedParams", "q")

    categories = ET.SubElement(root, "categories")

    cat_movies = ET.SubElement(categories, "category")
    cat_movies.set("id", "2000")
    cat_movies.set("name", "Movies")

    cat_tv = ET.SubElement(categories, "category")
    cat_tv.set("id", "5000")
    cat_tv.set("name", "TV")

    cat_other = ET.SubElement(categories, "category")
    cat_other.set("id", "8000")
    cat_other.set("name", "Other")

    return TorznabRendered(ET.tostring(root, encoding="utf-8", xml_declaration=True))


def render_rss_xml(
    *,
    title: str,
    items: list[TorznabItem],
    description: str | None = None,
) -> TorznabRendered:
    rss = ET.Element("rss", attrib={"version": "2.0"})
    channel = ET.SubElement(rss, "channel")

    ET.SubElement(channel, "title").text = title
    ET.SubElement(channel, "description").text = (
        description or "Scavengarr Torznab feed"
    )
    ET.SubElement(channel, "link").text = "http://localhost"
    ET.SubElement(channel, "language").text = "en-us"

    for it in items:
        item = ET.SubElement(channel, "item")

        release = (
            getattr(it, "release_name", None)
            or getattr(it, "real_name", None)
            or getattr(it, "release", None)
        )
        rendered_title = release or it.title
        ET.SubElement(item, "title").text = rendered_title

        ET.SubElement(item, "guid").text = it.download_url

        link_url = getattr(it, "source_url", None) or it.download_url
        ET.SubElement(item, "link").text = link_url

        # Prefer a real description if available, fallback to title.
        desc = getattr(it, "description", None) or it.title
        ET.SubElement(item, "description").text = desc

        pub = ET.SubElement(item, "pubDate")
        pub.text = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")

        category = getattr(it, "category", 2000)
        _add_torznab_attr(item, "category", str(category))

        size_bytes = _parse_size_to_bytes(it.size) if it.size else 0
        _add_torznab_attr(item, "size", str(size_bytes))

        _add_torznab_attr(item, "seeders", str(it.seeders or 0))
        _add_torznab_attr(item, "peers", str(getattr(it, "peers", None) or 0))

        grabs = getattr(it, "grabs", 0)
        _add_torznab_attr(item, "grabs", str(grabs))

        download_factor = getattr(it, "download_volume_factor", 0)
        upload_factor = getattr(it, "upload_volume_factor", 0)
        _add_torznab_attr(item, "downloadvolumefactor", str(download_factor))
        _add_torznab_attr(item, "uploadvolumefactor", str(upload_factor))

        _add_torznab_attr(item, "minimumratio", "0")
        _add_torznab_attr(item, "minimumseedtime", "0")

        enclosure = ET.SubElement(item, "enclosure")
        enclosure.set("url", it.download_url)
        enclosure.set("length", str(size_bytes))
        enclosure.set("type", "application/x-bittorrent")

    return TorznabRendered(ET.tostring(rss, encoding="utf-8", xml_declaration=True))


def _add_torznab_attr(parent: ET.Element, name: str, value: str) -> None:
    attr = ET.SubElement(parent, f"{{{_TORZNAB_NS}}}attr")
    attr.set("name", name)
    attr.set("value", value)


def _parse_size_to_bytes(size_str: str) -> int:
    if not size_str:
        return 0

    if size_str.isdigit():
        return int(size_str)

    match = re.match(r"([\d.]+)\s*([KMGT]?B)", size_str.upper().strip())
    if not match:
        return 0

    value = float(match.group(1))
    unit = match.group(2)

    multipliers = {
        "B": 1,
        "KB": 1024,
        "MB": 1024**2,
        "GB": 1024**3,
        "TB": 1024**4,
    }
    return int(value * multipliers.get(unit, 1))
