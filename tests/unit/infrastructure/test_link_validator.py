"""Tests for HttpLinkValidator."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx

from scavengarr.infrastructure.validation.http_link_validator import (
    HttpLinkValidator,
)


def _mock_client(
    status_code: int = 200,
    side_effect: Exception | None = None,
) -> AsyncMock:
    """Create mock httpx.AsyncClient with HEAD response."""
    client = AsyncMock(spec=httpx.AsyncClient)
    if side_effect:
        client.head = AsyncMock(side_effect=side_effect)
    else:
        response = MagicMock()
        response.status_code = status_code
        client.head = AsyncMock(return_value=response)
    return client


class TestValidate:
    async def test_200_is_valid(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_301_redirect_is_valid(self) -> None:
        client = _mock_client(status_code=301)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_399_is_valid(self) -> None:
        client = _mock_client(status_code=399)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True

    async def test_404_is_invalid(self) -> None:
        client = _mock_client(status_code=404)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_500_is_invalid(self) -> None:
        client = _mock_client(status_code=500)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_timeout_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout")
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_http_error_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.HTTPError("connection refused")
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_unexpected_error_is_invalid(self) -> None:
        client = _mock_client(side_effect=OSError("DNS failure"))
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False


class TestValidateBatch:
    async def test_empty_list_returns_empty_dict(self) -> None:
        client = _mock_client()
        validator = HttpLinkValidator(client)
        result = await validator.validate_batch([])
        assert result == {}

    async def test_all_valid(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = ["https://a.com", "https://b.com"]
        result = await validator.validate_batch(urls)
        assert result == {
            "https://a.com": True,
            "https://b.com": True,
        }

    async def test_mixed_results(self) -> None:
        client = AsyncMock(spec=httpx.AsyncClient)
        responses = [MagicMock(status_code=200), MagicMock(status_code=404)]
        client.head = AsyncMock(side_effect=responses)

        validator = HttpLinkValidator(client)
        urls = ["https://valid.com", "https://dead.com"]
        result = await validator.validate_batch(urls)
        assert result["https://valid.com"] is True
        assert result["https://dead.com"] is False

    async def test_returns_dict_with_all_urls(self) -> None:
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        urls = ["https://a.com", "https://b.com", "https://c.com"]
        result = await validator.validate_batch(urls)
        assert set(result.keys()) == set(urls)
