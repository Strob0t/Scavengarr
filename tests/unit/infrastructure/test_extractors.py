"""Tests for infrastructure extractors."""

from __future__ import annotations

from scavengarr.infrastructure.common.extractors import (
    extract_download_link,
)


class TestExtractDownloadLink:
    def test_extracts_link_field(self) -> None:
        result = extract_download_link({"link": "https://example.com/dl"})
        assert result == "https://example.com/dl"

    def test_extracts_url_field(self) -> None:
        result = extract_download_link({"url": "https://example.com/dl"})
        assert result == "https://example.com/dl"

    def test_prefers_link_over_url(self) -> None:
        result = extract_download_link({
            "link": "https://link.com",
            "url": "https://url.com",
        })
        assert result == "https://link.com"

    def test_empty_dict_returns_none(self) -> None:
        result = extract_download_link({})
        assert result is None

    def test_other_keys_ignored(self) -> None:
        result = extract_download_link({"href": "https://example.com"})
        assert result is None
