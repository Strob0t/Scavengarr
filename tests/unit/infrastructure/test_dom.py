"""Tests for the plugin parser helpers on selectolax (dom.py)."""

from __future__ import annotations

import threading

from selectolax.lexbor import LexborHTMLParser

from scavengarr.infrastructure.plugins.dom import ancestors, classes, parse_page


class _ParagraphCounter:
    """Records the thread that parsed and the paragraphs it found."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.paragraphs = 0

    def feed(self, html: str) -> None:
        self.thread = threading.current_thread()
        self.paragraphs = len(LexborHTMLParser(html).css("p"))


class TestParsePage:
    """Plugins parse their pages through parse_page(): a 1.3 MB page took
    23 ms on x86, several times as long on a Raspberry Pi."""

    async def test_big_pages_are_parsed_in_a_worker_thread(self) -> None:
        parser = await parse_page(_ParagraphCounter(), "<p>x</p>" * 10_000)

        assert parser.thread is not threading.current_thread()
        assert parser.paragraphs == 10_000

    async def test_small_pages_are_parsed_inline(self) -> None:
        parser = await parse_page(_ParagraphCounter(), "<p>x</p>")

        assert parser.thread is threading.current_thread()
        assert parser.paragraphs == 1


class TestClasses:
    def test_class_names(self) -> None:
        node = LexborHTMLParser('<div class=" a  b "></div>').css("div")[0]

        assert classes(node) == ["a", "b"]

    def test_no_class(self) -> None:
        node = LexborHTMLParser("<div></div>").css("div")[0]

        assert classes(node) == []


class TestAncestors:
    def test_nearest_first_up_to_the_document(self) -> None:
        tree = LexborHTMLParser("<section><div><p>x</p></div></section>")
        paragraph = tree.css("p")[0]

        assert [node.tag for node in ancestors(paragraph)] == [
            "div",
            "section",
            "body",
            "html",
            "-document",
        ]
