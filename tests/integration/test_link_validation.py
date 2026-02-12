"""Integration tests for HttpLinkValidator with real httpx + respx mocking.

Tests the full HEAD→GET fallback chain, batch validation, and edge cases
using real httpx transport intercepted by respx.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from scavengarr.infrastructure.validation.http_link_validator import HttpLinkValidator

pytestmark = pytest.mark.integration


@pytest.fixture()
def validator(http_client: httpx.AsyncClient) -> HttpLinkValidator:
    return HttpLinkValidator(
        http_client=http_client,
        timeout_seconds=5.0,
        max_concurrent=10,
    )


class TestSingleValidation:
    """Single URL validation with HEAD/GET fallback."""

    @respx.mock
    async def test_head_200_is_valid(self, validator: HttpLinkValidator) -> None:
        url = "https://hoster.example.com/file.mp4"
        respx.head(url).respond(200)

        assert await validator.validate(url) is True

    @respx.mock
    async def test_head_301_redirect_is_valid(
        self, validator: HttpLinkValidator
    ) -> None:
        """301 redirect chain: validator follows redirects and checks final status."""
        url = "https://hoster.example.com/redirect"
        final_url = "https://cdn.example.com/file.mp4"
        respx.head(url).respond(301, headers={"Location": final_url})
        respx.head(final_url).respond(200)

        assert await validator.validate(url) is True

    @respx.mock
    async def test_head_403_falls_back_to_get_200(
        self, validator: HttpLinkValidator
    ) -> None:
        """Some hosters block HEAD but allow GET — validator should try GET."""
        url = "https://veev.to/embed/abc123"
        respx.head(url).respond(403)
        respx.get(url).respond(200)

        assert await validator.validate(url) is True

    @respx.mock
    async def test_head_and_get_both_404_is_invalid(
        self, validator: HttpLinkValidator
    ) -> None:
        url = "https://hoster.example.com/dead-link"
        respx.head(url).respond(404)
        respx.get(url).respond(404)

        assert await validator.validate(url) is False

    @respx.mock
    async def test_head_timeout_falls_back_to_get(
        self, validator: HttpLinkValidator
    ) -> None:
        url = "https://slow-hoster.example.com/file.mp4"
        respx.head(url).mock(side_effect=httpx.ReadTimeout("timeout"))
        respx.get(url).respond(200)

        assert await validator.validate(url) is True

    @respx.mock
    async def test_both_timeout_is_invalid(self, validator: HttpLinkValidator) -> None:
        url = "https://dead-hoster.example.com/file.mp4"
        respx.head(url).mock(side_effect=httpx.ReadTimeout("timeout"))
        respx.get(url).mock(side_effect=httpx.ReadTimeout("timeout"))

        assert await validator.validate(url) is False


class TestBatchValidation:
    """Batch validation with multiple URLs in parallel."""

    @respx.mock
    async def test_batch_mixed_results(self, validator: HttpLinkValidator) -> None:
        valid_url = "https://hoster.example.com/good.mp4"
        dead_url = "https://hoster.example.com/dead.mp4"
        fallback_url = "https://veev.to/embed/xyz"

        respx.head(valid_url).respond(200)
        respx.head(dead_url).respond(404)
        respx.get(dead_url).respond(404)
        respx.head(fallback_url).respond(403)
        respx.get(fallback_url).respond(200)

        result = await validator.validate_batch([valid_url, dead_url, fallback_url])

        assert result[valid_url] is True
        assert result[dead_url] is False
        assert result[fallback_url] is True

    @respx.mock
    async def test_batch_empty_list(self, validator: HttpLinkValidator) -> None:
        result = await validator.validate_batch([])
        assert result == {}

    @respx.mock
    async def test_batch_all_valid(self, validator: HttpLinkValidator) -> None:
        urls = [f"https://hoster.example.com/file{i}.mp4" for i in range(5)]
        for url in urls:
            respx.head(url).respond(200)

        result = await validator.validate_batch(urls)
        assert all(result.values())
        assert len(result) == 5
