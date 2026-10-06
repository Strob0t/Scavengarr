"""Tests for the cached guessit parse of release names."""

from __future__ import annotations

from typing import Any

import pytest

from scavengarr.infrastructure.stremio import release_guess
from scavengarr.infrastructure.stremio.release_guess import guess_release


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    release_guess.clear_cache()


class TestGuessRelease:
    def test_properties_of_a_release_name(self) -> None:
        guess = guess_release("Iron.Man.2008.German.DL.1080p.BluRay.x264-GROUP")

        assert guess.get("title") == "Iron Man"
        assert guess.get("year") == 2008
        assert guess.get("screen_size") == "1080p"

    def test_each_name_is_parsed_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """On a Raspberry Pi guessit took 25-33% of the Python CPU of a
        stream request: the title matcher, the release parser and the
        episode filter parsed the same names again (2026-10-04)."""
        calls: list[str] = []
        real = release_guess.guessit

        def _counting(name: str) -> Any:
            calls.append(name)
            return real(name)

        monkeypatch.setattr(release_guess, "guessit", _counting)

        for _ in range(3):
            guess_release("Dark.S01E02.German.720p.WEB.x264-GROUP")

        assert calls == ["Dark.S01E02.German.720p.WEB.x264-GROUP"]

    def test_cached_properties_are_read_only(self) -> None:
        guess = guess_release("Dark.S01E02.German.720p.WEB.x264-GROUP")

        with pytest.raises(TypeError):
            guess["title"] = "changed"  # type: ignore[index]
