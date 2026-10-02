"""Tests for the shared episode labels of series links."""

from __future__ import annotations

from scavengarr.infrastructure.plugins.episodes import episode_label, filter_episodes


class TestFilterEpisodes:
    _LINKS = [  # noqa: RUF012
        {"hoster": "a", "link": "https://a/1", "label": "1x1 a"},
        {"hoster": "a", "link": "https://a/2", "label": "1x2 a"},
        {"hoster": "b", "link": "https://b/2", "label": "1x2 b"},
        {"hoster": "a", "link": "https://a/3", "label": "2x1 a"},
        {"hoster": "a", "link": "https://a/x", "label": "a"},
    ]

    def test_season_and_episode(self) -> None:
        links = filter_episodes(self._LINKS, 1, 2)
        assert [link["link"] for link in links] == ["https://a/2", "https://b/2"]

    def test_whole_season(self) -> None:
        assert len(filter_episodes(self._LINKS, 1, None)) == 3

    def test_unlabelled_and_other_seasons_dropped(self) -> None:
        assert filter_episodes(self._LINKS, 3, None) == []

    def test_multi_digit_numbers(self) -> None:
        links = [{"hoster": "a", "link": "https://a", "label": "12x105 a"}]
        assert filter_episodes(links, 12, 105) == links
        assert filter_episodes(links, 1, 2) == []


class TestEpisodeLabel:
    def test_label(self) -> None:
        assert episode_label(1, 5, "doodstream") == "1x5 doodstream"

    def test_prefix(self) -> None:
        assert episode_label(12, 105, "") == "12x105 "
