"""Helpers for plugin parsers on selectolax (lexbor) trees.

``LexborNode.css_matches()`` tests a node's whole subtree, not the node:
ancestor checks walk ``ancestors()`` and test each node themselves.
"""

from __future__ import annotations

from collections.abc import Iterator

from selectolax.lexbor import LexborNode


def classes(node: LexborNode) -> list[str]:
    """The class names of *node*."""
    return (node.attributes.get("class") or "").split()


def ancestors(node: LexborNode) -> Iterator[LexborNode]:
    """The ancestors of *node*, nearest first (the document node last)."""
    parent = node.parent
    while parent is not None:
        yield parent
        parent = parent.parent
