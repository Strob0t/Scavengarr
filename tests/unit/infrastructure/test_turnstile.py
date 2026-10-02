"""Tests for the Cloudflare Turnstile solver (browser/turnstile.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from scavengarr.infrastructure.browser import turnstile
from scavengarr.infrastructure.browser.turnstile import (
    is_challenge_page,
    pass_turnstile_widget,
    read_when_settled,
    solve_cloudflare,
)


class TestReadWhenSettled:
    """Reads that race a post-challenge navigation are retried."""

    async def test_returns_first_successful_read(self) -> None:
        page = MagicMock(wait_for_load_state=AsyncMock())
        read = AsyncMock(return_value="<html/>")

        assert await read_when_settled(page, read) == "<html/>"
        page.wait_for_load_state.assert_not_awaited()

    async def test_retries_after_navigation_error(self) -> None:
        page = MagicMock(wait_for_load_state=AsyncMock())
        read = AsyncMock(
            side_effect=[
                RuntimeError("Execution context was destroyed, most likely ..."),
                "<html/>",
            ]
        )

        assert await read_when_settled(page, read) == "<html/>"
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded")

    async def test_other_errors_propagate(self) -> None:
        page = MagicMock(wait_for_load_state=AsyncMock())
        read = AsyncMock(side_effect=RuntimeError("Target closed"))

        with pytest.raises(RuntimeError, match="Target closed"):
            await read_when_settled(page, read)

    async def test_gives_up_after_attempts(self) -> None:
        page = MagicMock(wait_for_load_state=AsyncMock())
        read = AsyncMock(side_effect=RuntimeError("page is navigating"))

        with pytest.raises(RuntimeError):
            await read_when_settled(page, read, attempts=3)
        assert read.await_count == 3


def _frame(url: str, *, click_error: Exception | None = None) -> MagicMock:
    frame = MagicMock()
    frame.url = url
    first = MagicMock()
    first.click = AsyncMock(side_effect=click_error)
    frame.locator = MagicMock(return_value=MagicMock(first=first))
    return frame


def _page(titles: list[str], frames: list[MagicMock] | None = None) -> MagicMock:
    """Page whose title() returns *titles* in order (last one repeats)."""
    page = MagicMock()
    seq = list(titles)

    async def _title() -> str:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    page.title = AsyncMock(side_effect=_title)
    page.frames = frames or []
    page.wait_for_timeout = AsyncMock()
    page.wait_for_url = AsyncMock()
    page.wait_for_load_state = AsyncMock()
    return page


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """Deterministic clock: every poll advances 0.5 s."""
    now = {"t": 0.0}

    def _clock() -> float:
        now["t"] += 0.5
        return now["t"]

    monkeypatch.setattr(turnstile, "_now", _clock)


class TestIsChallengePage:
    @pytest.mark.parametrize(
        "title",
        ["Just a moment...", "Attention Required! | Cloudflare", "Nur einen Moment…"],
    )
    async def test_challenge_titles(self, title: str) -> None:
        assert await is_challenge_page(_page([title])) is True

    async def test_normal_page(self) -> None:
        assert await is_challenge_page(_page(["FilmFans"])) is False

    async def test_title_error_counts_as_not_challenge(self) -> None:
        page = _page(["x"])
        page.title = AsyncMock(side_effect=RuntimeError("navigating"))
        assert await is_challenge_page(page) is False


class TestSolveCloudflare:
    async def test_no_challenge_returns_immediately(self) -> None:
        page = _page(["FilmFans"])
        assert await solve_cloudflare(page, timeout_ms=10_000) is True
        page.wait_for_timeout.assert_not_awaited()

    async def test_auto_clear_without_click(self) -> None:
        frame = _frame("https://challenges.cloudflare.com/cdn-cgi/x")
        page = _page(["Just a moment...", "FilmFans"], [frame])

        assert await solve_cloudflare(page, timeout_ms=10_000) is True
        frame.locator.return_value.first.click.assert_not_awaited()

    async def test_clicks_checkbox_in_challenge_frame(self) -> None:
        other = _frame("https://filmfans.org/")
        challenge = _frame("https://challenges.cloudflare.com/cdn-cgi/x")
        titles = ["Just a moment..."] * 8 + ["FilmFans"]
        page = _page(titles, [other, challenge])

        assert await solve_cloudflare(page, timeout_ms=30_000) is True
        challenge.locator.return_value.first.click.assert_awaited()
        other.locator.return_value.first.click.assert_not_awaited()

    async def test_times_out_when_never_cleared(self) -> None:
        challenge = _frame("https://challenges.cloudflare.com/cdn-cgi/x")
        page = _page(["Just a moment..."], [challenge])

        assert await solve_cloudflare(page, timeout_ms=10_000) is False

    async def test_click_errors_are_tolerated(self) -> None:
        challenge = _frame(
            "https://challenges.cloudflare.com/cdn-cgi/x",
            click_error=TimeoutError("not visible yet"),
        )
        page = _page(["Just a moment..."], [challenge])

        assert await solve_cloudflare(page, timeout_ms=10_000) is False
        challenge.locator.return_value.first.click.assert_awaited()


class TestSettleAfterSolve:
    """After the title changes, Cloudflare still redirects (drops __cf_chl_tk)."""

    async def test_waits_for_redirect_and_dom(self) -> None:
        page = _page(["Just a moment...", "FilmFans"])

        assert await solve_cloudflare(page, timeout_ms=10_000) is True

        page.wait_for_url.assert_awaited_once()
        predicate = page.wait_for_url.await_args.args[0]
        assert predicate("https://x.org/?s=a&__cf_chl_tk=abc") is False
        assert predicate("https://x.org/?s=a") is True
        page.wait_for_load_state.assert_awaited_once_with("domcontentloaded")

    async def test_settle_timeout_still_counts_as_solved(self) -> None:
        page = _page(["Just a moment...", "FilmFans"])
        page.wait_for_url = AsyncMock(side_effect=TimeoutError("no redirect"))

        assert await solve_cloudflare(page, timeout_ms=10_000) is True

    async def test_no_settle_without_challenge(self) -> None:
        page = _page(["FilmFans"])

        await solve_cloudflare(page, timeout_ms=10_000)

        page.wait_for_url.assert_not_awaited()


def _widget_page(
    tokens: list[str], frames: list[MagicMock] | None = None, *, shown: bool = True
) -> tuple[MagicMock, MagicMock]:
    """Page with a Turnstile widget in a form; token values come in order.

    Returns the page and the widget's form (to check the submit).
    """
    page = _page(["S01E02"], frames)
    seq = list(tokens)

    async def _token() -> str:
        return seq.pop(0) if len(seq) > 1 else seq[0]

    token_input = MagicMock()
    token_input.input_value = AsyncMock(side_effect=_token)
    form = MagicMock()
    form.evaluate = AsyncMock()

    def _locator(selector: str) -> MagicMock:
        if selector.startswith("form"):
            return MagicMock(first=form)
        found = MagicMock(first=token_input)
        found.count = AsyncMock(return_value=1 if shown else 0)
        return found

    page.locator = MagicMock(side_effect=_locator)
    return page, form


class TestPassTurnstileWidget:
    """Link-out gates (s.to) show Turnstile in a modal form instead of a page."""

    async def test_no_widget_returns_false_at_once(self) -> None:
        page, form = _widget_page([""], shown=False)

        assert await pass_turnstile_widget(page, timeout_ms=10_000) is False
        page.wait_for_timeout.assert_not_awaited()
        form.evaluate.assert_not_awaited()

    async def test_cleared_widget_submits_its_form(self) -> None:
        page, form = _widget_page(["", "token"])

        assert await pass_turnstile_widget(page, timeout_ms=10_000) is True
        form.evaluate.assert_awaited_once()
        assert "requestSubmit" in form.evaluate.await_args.args[0]

    async def test_ticks_checkbox_when_not_cleared(self) -> None:
        challenge = _frame("https://challenges.cloudflare.com/cdn-cgi/x")
        page, form = _widget_page([""] * 8 + ["token"], [challenge])

        assert await pass_turnstile_widget(page, timeout_ms=30_000) is True
        challenge.locator.return_value.first.click.assert_awaited()
        form.evaluate.assert_awaited_once()

    async def test_times_out_without_token(self) -> None:
        page, form = _widget_page([""])

        assert await pass_turnstile_widget(page, timeout_ms=10_000) is False
        form.evaluate.assert_not_awaited()
