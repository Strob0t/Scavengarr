"""Tests for resolve_headless() — headful only when a display exists."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.browser import display
from scavengarr.infrastructure.browser.display import resolve_headless


@pytest.fixture(autouse=True)
def _reset_warning() -> None:
    display._warned_no_display = False


class TestResolveHeadless:
    def test_headless_requested_stays_headless(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY", ":99")
        assert resolve_headless(True) is True

    def test_headful_with_display(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISPLAY", ":99")
        assert resolve_headless(False) is False

    def test_headful_without_display_falls_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DISPLAY", raising=False)
        assert resolve_headless(False) is True

    def test_empty_display_counts_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISPLAY", "")
        assert resolve_headless(False) is True

    def test_fallback_warns_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISPLAY", raising=False)
        calls: list[str] = []
        monkeypatch.setattr(
            display.log, "warning", lambda event, **_: calls.append(event)
        )

        resolve_headless(False)
        resolve_headless(False)

        assert calls == ["browser_headful_no_display"]
