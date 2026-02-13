"""Tests for shared Cloudflare challenge detection."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.hoster_resolvers.cloudflare import (
    _CF_MARKERS,
    is_cloudflare_challenge,
)

# ------------------------------------------------------------------
# Positive: CF challenge detected
# ------------------------------------------------------------------


class TestCloudflareDetected:
    """Responses that SHOULD be classified as Cloudflare challenges."""

    @pytest.mark.parametrize("status", [403, 503])
    @pytest.mark.parametrize("marker", _CF_MARKERS)
    def test_marker_with_cf_status(self, status: int, marker: str) -> None:
        html = f"<html><head><title>blocked</title></head><body>{marker}</body></html>"
        assert is_cloudflare_challenge(status, html) is True

    def test_marker_embedded_in_attribute(self) -> None:
        html = '<div id="challenge-platform" class="cf">loading...</div>'
        assert is_cloudflare_challenge(403, html) is True

    def test_multiple_markers_present(self) -> None:
        html = "<html>Just a moment<script>challenge-platform</script></html>"
        assert is_cloudflare_challenge(503, html) is True


# ------------------------------------------------------------------
# Negative: NOT a CF challenge
# ------------------------------------------------------------------


class TestNotCloudflare:
    """Responses that should NOT be classified as Cloudflare challenges."""

    @pytest.mark.parametrize("status", [200, 301, 302, 404, 410, 500, 502])
    def test_non_cf_status_codes(self, status: int) -> None:
        html = "<html>Just a moment</html>"
        assert is_cloudflare_challenge(status, html) is False

    def test_403_without_markers(self) -> None:
        html = "<html><body>Access denied</body></html>"
        assert is_cloudflare_challenge(403, html) is False

    def test_503_without_markers(self) -> None:
        html = "<html><body>Service Unavailable</body></html>"
        assert is_cloudflare_challenge(503, html) is False

    def test_200_with_marker_text(self) -> None:
        html = "<html><body>Just a moment please</body></html>"
        assert is_cloudflare_challenge(200, html) is False

    def test_empty_html(self) -> None:
        assert is_cloudflare_challenge(403, "") is False

    def test_normal_page(self) -> None:
        html = "<html><body><video src='video.mp4'></video></body></html>"
        assert is_cloudflare_challenge(200, html) is False
