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
    get_status_code: int | None = None,
    get_side_effect: Exception | None = None,
) -> AsyncMock:
    """Create mock httpx.AsyncClient with HEAD and GET responses.

    By default GET mirrors HEAD (same status/side_effect) unless
    get_status_code or get_side_effect is explicitly provided.
    """
    client = AsyncMock(spec=httpx.AsyncClient)

    # HEAD mock
    if side_effect:
        client.head = AsyncMock(side_effect=side_effect)
    else:
        response = MagicMock()
        response.status_code = status_code
        client.head = AsyncMock(return_value=response)

    # GET mock — mirrors HEAD by default, but explicit get_* overrides
    if get_side_effect is not None:
        client.get = AsyncMock(side_effect=get_side_effect)
    elif get_status_code is not None:
        get_response = MagicMock()
        get_response.status_code = get_status_code
        client.get = AsyncMock(return_value=get_response)
    elif side_effect:
        client.get = AsyncMock(side_effect=side_effect)
    else:
        get_response = MagicMock()
        get_response.status_code = status_code
        client.get = AsyncMock(return_value=get_response)

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
        client = _mock_client(status_code=404, get_status_code=404)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_500_is_invalid(self) -> None:
        client = _mock_client(status_code=500, get_status_code=500)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_timeout_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_side_effect=httpx.TimeoutException("timeout"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_http_error_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=httpx.HTTPError("connection refused"),
            get_side_effect=httpx.HTTPError("connection refused"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    async def test_unexpected_error_is_invalid(self) -> None:
        client = _mock_client(
            side_effect=OSError("DNS failure"),
            get_side_effect=OSError("DNS failure"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is False

    # --- HEAD/GET fallback tests ---

    async def test_head_403_get_200_is_valid(self) -> None:
        """Hoster blocks HEAD but allows GET."""
        client = _mock_client(status_code=403, get_status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://veev.to/dl/1") is True

    async def test_head_403_get_403_is_invalid(self) -> None:
        """Both HEAD and GET fail — genuinely dead link."""
        client = _mock_client(status_code=403, get_status_code=403)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://dead.com/dl/1") is False

    async def test_head_405_get_200_is_valid(self) -> None:
        """405 Method Not Allowed on HEAD, GET works."""
        client = _mock_client(status_code=405, get_status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://savefiles.com/dl/1") is True

    async def test_head_200_no_get_fallback(self) -> None:
        """HEAD succeeds — GET should not be called."""
        client = _mock_client(status_code=200)
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://example.com") is True
        client.get.assert_not_called()

    async def test_head_timeout_get_200_is_valid(self) -> None:
        """HEAD times out, GET works."""
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_status_code=200,
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://slow-head.com/dl") is True

    async def test_head_timeout_get_timeout_is_invalid(self) -> None:
        """Both HEAD and GET timeout."""
        client = _mock_client(
            side_effect=httpx.TimeoutException("timeout"),
            get_side_effect=httpx.TimeoutException("timeout"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://unreachable.com") is False

    async def test_head_error_get_error_is_invalid(self) -> None:
        """Both HEAD and GET raise HTTPError."""
        client = _mock_client(
            side_effect=httpx.HTTPError("refused"),
            get_side_effect=httpx.HTTPError("refused"),
        )
        validator = HttpLinkValidator(client)
        assert await validator.validate("https://broken.com") is False


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
        head_responses = [MagicMock(status_code=200), MagicMock(status_code=404)]
        client.head = AsyncMock(side_effect=head_responses)
        # GET fallback for the 404 HEAD — also fails
        get_response = MagicMock(status_code=404)
        client.get = AsyncMock(return_value=get_response)

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
