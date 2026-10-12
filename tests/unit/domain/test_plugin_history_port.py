"""Tests for the challenge flag of the plugin history port (row 70)."""

from __future__ import annotations

from scavengarr.domain.ports.plugin_history import (
    ChallengeFlag,
    challenge_flag,
    mark_challenge,
    settle_challenge,
)


def _run(*steps: bool | None) -> ChallengeFlag:
    """A search's flag after a challenge per step, settled with its outcome
    (``None``: left open)."""
    flag = ChallengeFlag()
    token = challenge_flag.set(flag)
    try:
        for solved in steps:
            mark_challenge()
            if solved is not None:
                settle_challenge(solved=solved)
    finally:
        challenge_flag.reset(token)
    return flag


def test_a_fresh_flag_saw_nothing() -> None:
    flag = ChallengeFlag()

    assert (flag.seen, flag.unsolved_search) == (False, False)


def test_a_solved_challenge_is_not_unsolved() -> None:
    flag = _run(True)

    assert (flag.seen, flag.unsolved_search) == (True, False)


def test_a_failed_challenge_is_unsolved() -> None:
    assert _run(False).unsolved_search is True


def test_a_challenge_left_open_is_unsolved() -> None:
    """A search cut before the browser finished did not get past it."""
    assert _run(None).unsolved_search is True


def test_one_failure_among_solves_makes_the_search_unsolved() -> None:
    assert _run(True, False, True).unsolved_search is True


def test_marks_outside_a_search_do_nothing() -> None:
    assert challenge_flag.get() is None
    mark_challenge()
    settle_challenge(solved=False)
