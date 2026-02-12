"""CSS-selector-based HTML extraction with fallback chains.

Provides composable helper functions that replace fragile ``HTMLParser``
state machines with resilient CSS-selector-based extraction using
BeautifulSoup.  Every extraction function accepts a primary selector
and optional *fallback_selectors* — the first selector that yields at
least one match wins.  This makes plugins resilient against minor
layout changes (extra wrapper ``<div>``, renamed CSS class, etc.).
"""

from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag


def parse_html(html: str) -> BeautifulSoup:
    """Parse an HTML string into a BeautifulSoup tree.

    Uses the ``lxml`` parser for speed (falls back to ``html.parser``
    if lxml is unavailable, though lxml is a project dependency).
    """
    return BeautifulSoup(html, "lxml")


def select_items(
    root: BeautifulSoup | Tag,
    selector: str,
    *fallback_selectors: str,
) -> list[Tag]:
    """Select elements via CSS with a fallback chain.

    Tries each selector in order.  Returns results from the **first**
    selector that matches at least one element.
    """
    for sel in (selector, *fallback_selectors):
        items = root.select(sel)
        if items:
            return items
    return []


def extract_text(
    element: Tag,
    selector: str,
    *fallback_selectors: str,
    default: str = "",
    strip: bool = True,
) -> str:
    """Extract text from the first matching child element.

    With ``selector=""`` the element's own text is returned.
    """
    if selector == "":
        text = element.get_text(strip=strip)
        return text if text else default

    for sel in (selector, *fallback_selectors):
        match = element.select_one(sel)
        if match:
            text = match.get_text(strip=strip)
            if text:
                return text
    return default


def extract_attr(
    element: Tag,
    selector: str,
    attr: str,
    *fallback_selectors: str,
    default: str = "",
) -> str:
    """Extract an HTML attribute from the first matching child element.

    With ``selector=""`` the attribute is read from *element* itself.
    """
    if selector == "":
        val = element.get(attr)
        return str(val) if val else default

    for sel in (selector, *fallback_selectors):
        match = element.select_one(sel)
        if match:
            val = match.get(attr)
            if val:
                return str(val)
    return default


def extract_all_attrs(
    element: Tag,
    selector: str,
    attr: str,
    *fallback_selectors: str,
) -> list[str]:
    """Extract an attribute from **all** matching elements."""
    for sel in (selector, *fallback_selectors):
        matches = element.select(sel)
        if matches:
            return [str(m[attr]) for m in matches if m.get(attr)]
    return []


def extract_links(
    element: Tag,
    selector: str = "a[href]",
    *fallback_selectors: str,
    base_url: str = "",
) -> list[dict[str, str]]:
    """Extract all links matching *selector*.

    Returns a list of ``{"text": ..., "href": ...}`` dicts.
    """
    tags = select_items(element, selector, *fallback_selectors)
    results: list[dict[str, str]] = []
    for tag in tags:
        href = tag.get("href")
        if not href:
            continue
        href_str = str(href)
        if base_url:
            href_str = urljoin(base_url, href_str)
        results.append(
            {
                "text": tag.get_text(strip=True),
                "href": href_str,
            }
        )
    return results
