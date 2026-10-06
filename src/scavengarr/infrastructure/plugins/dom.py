"""Helpers for plugin page parsers on selectolax (lexbor) trees.

``LexborNode.css_matches()`` tests a node's whole subtree, not the node:
ancestor checks walk ``ancestors()`` and test each node themselves.
A group selector returns a node once per part it matches (``a, a.x``
gives ``<a class="x">`` twice): parts that can match one node go into
``:is()`` (``a:is(.x, [href])``). ``LexborNode.__eq__`` compares the
nodes' HTML, not their identity: compare ``mem_id``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Protocol

from selectolax.lexbor import LexborNode

# Pages from this size parse in a worker thread: selectolax took 0.3 ms for
# 32-64 KiB on x86 (about 1 ms on a Raspberry Pi 4), a thread hop 0.06 ms
THREAD_PARSE_CHARS = 32 * 1024


class PageParser(Protocol):
    """A plugin's page parser: ``feed()`` reads a whole page."""

    def feed(self, html: str, /) -> None: ...


async def parse_page[P: PageParser](parser: P, html: str) -> P:
    """Feed *html* to *parser*; a big page in a worker thread.

    lexbor builds the tree in C without the GIL, and the parser's queries
    on it hand the GIL back every few ms: in a thread the event loop keeps
    running. A 1.3 MB page (burningseries' series list) took 23 ms on x86,
    several times as long on a Raspberry Pi.
    """
    if len(html) < THREAD_PARSE_CHARS:
        parser.feed(html)
    else:
        await asyncio.to_thread(parser.feed, html)
    return parser


def classes(node: LexborNode) -> list[str]:
    """The class names of *node*."""
    return (node.attributes.get("class") or "").split()


def ancestors(node: LexborNode) -> Iterator[LexborNode]:
    """The ancestors of *node*, nearest first (the document node last)."""
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent


def outermost(nodes: list[LexborNode]) -> list[LexborNode]:
    """*nodes* without the ones nested in another of them.

    For the matches of one selector, where sites nest their markup: a card
    inside another card is part of the outer one.
    """
    found = {node.mem_id for node in nodes}
    return [
        node
        for node in nodes
        if not any(parent.mem_id in found for parent in ancestors(node))
    ]
