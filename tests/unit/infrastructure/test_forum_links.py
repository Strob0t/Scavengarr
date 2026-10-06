"""Tests for the download links of forum posts (link containers, hoster names)."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.plugins.forum_links import (
    hoster_from_text,
    hoster_from_url,
    is_link_container,
)


class TestIsLinkContainer:
    @pytest.mark.parametrize(
        "url",
        [
            "https://keeplinks.org/p53/abc",
            "https://www.keeplinks.org/p53/abc",
            "https://keeplinks.co/p/abc",
            "https://filecrypt.cc/Container/ABC.html",
            "https://www.filecrypt.co/Container/ABC.html",
            "https://hide.cx/container/abc",
            "https://linkcrypt.ws/dir/abc",
            "https://share-links.biz/_abc",
            "https://tolink.to/f/abc",
        ],
    )
    def test_known_containers_count(self, url: str) -> None:
        assert is_link_container(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://notfilecrypt.cc/Container/ABC.html",
            "https://nothide.cx/container/abc",
            "https://filecrypt.cc.example.com/Container/ABC.html",
            "https://mygully.com/showthread.php?t=1",
            "https://www.imdb.com/title/tt123",
            "https://rapidgator.net/file/abc",
            "not a url",
            "",
        ],
    )
    def test_other_hosts_do_not(self, url: str) -> None:
        assert not is_link_container(url)


class TestHosterFromText:
    @pytest.mark.parametrize(
        ("text", "hoster"),
        [
            ("download via ddownload.com", "ddownload"),
            ("download via rapidgator.net", "rapidgator"),
            ("Online rapidgator.net", "rapidgator"),
            ("Online www.ddownload.com.", "ddownload"),
            ("RapidGator", "rapidgator"),
            ("DDownload", "ddownload"),
            ("", ""),
            ("https://example.com", ""),
            ("more than two words here", ""),
        ],
    )
    def test_names_the_hoster(self, text: str, hoster: str) -> None:
        assert hoster_from_text(text) == hoster


class TestHosterFromUrl:
    @pytest.mark.parametrize(
        ("url", "hoster"),
        [
            ("https://hide.cx/container/abc", "hide"),
            ("https://www.keeplinks.org/p53/abc", "keeplinks"),
            ("https://rapidgator.net/file/abc", "rapidgator"),
            ("no host", "unknown"),
        ],
    )
    def test_first_domain_label(self, url: str, hoster: str) -> None:
        assert hoster_from_url(url) == hoster
