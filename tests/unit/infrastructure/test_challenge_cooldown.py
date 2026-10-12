"""Tests for the challenge cooldown (hoster_resolvers/challenge_cooldown.py)."""

from __future__ import annotations

import pytest

from scavengarr.infrastructure.hoster_resolvers import challenge_cooldown
from scavengarr.infrastructure.hoster_resolvers.challenge_cooldown import (
    ChallengeCooldown,
)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> dict[str, float]:
    """The cooldown's clock; the test moves it."""
    now = {"t": 1000.0}
    monkeypatch.setattr(challenge_cooldown, "_now", lambda: now["t"])
    return now


class TestChallengeCooldown:
    @pytest.mark.usefixtures("clock")
    def test_no_cooldown_without_a_challenge(self) -> None:
        assert ChallengeCooldown().left("filemoon") == 0

    def test_the_first_challenge_skips_a_minute(self, clock: dict[str, float]) -> None:
        cooldown = ChallengeCooldown()

        assert cooldown.challenged("filemoon") == 60
        clock["t"] += 59
        assert cooldown.left("filemoon") == 1
        clock["t"] += 1
        assert cooldown.left("filemoon") == 0

    def test_further_challenges_step_up_and_stay_at_30_minutes(
        self, clock: dict[str, float]
    ) -> None:
        cooldown = ChallengeCooldown()
        lengths: list[float] = []

        for _ in range(6):
            lengths.append(cooldown.challenged("filemoon"))
            clock["t"] += lengths[-1]

        assert lengths == [60, 300, 900, 1800, 1800, 1800]

    def test_a_challenge_during_the_cooldown_keeps_it(
        self, clock: dict[str, float]
    ) -> None:
        """Captures that started before the first challenge meet it too: a
        burst of them is one step, not four."""
        cooldown = ChallengeCooldown()
        cooldown.challenged("filemoon")
        clock["t"] += 10

        assert cooldown.challenged("filemoon") == 50
        clock["t"] += 50
        assert cooldown.challenged("filemoon") == 300

    def test_a_stream_ends_it_and_resets_the_steps(
        self, clock: dict[str, float]
    ) -> None:
        cooldown = ChallengeCooldown()
        cooldown.challenged("filemoon")
        clock["t"] += 60
        cooldown.challenged("filemoon")

        cooldown.streamed("filemoon")

        assert cooldown.left("filemoon") == 0
        assert cooldown.challenged("filemoon") == 60

    def test_an_unsolved_challenge_steps_up_at_once(
        self, clock: dict[str, float]
    ) -> None:
        """A capture that ran on past the cut and still found no stream: the
        next step starts now, not when the running cooldown ends."""
        cooldown = ChallengeCooldown()
        cooldown.challenged("filemoon")
        clock["t"] += 20

        assert cooldown.unsolved("filemoon") == 300
        assert cooldown.left("filemoon") == 300
        clock["t"] += 300
        assert cooldown.challenged("filemoon") == 900

    @pytest.mark.usefixtures("clock")
    def test_each_hoster_has_its_own(self) -> None:
        cooldown = ChallengeCooldown()
        cooldown.challenged("filemoon")

        assert cooldown.left("dropload") == 0
        assert cooldown.challenged("dropload") == 60
