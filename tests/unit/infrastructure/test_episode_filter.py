"""Tests for season/episode filtering of Stremio search results."""

from __future__ import annotations

import pytest

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.episode_filter import (
    filter_by_episode,
    filter_links_by_episode,
    parse_episode_from_label,
)


def _make_search_result(
    *,
    title: str = "Test Movie",
    download_link: str = "https://voe.sx/e/abc",
    download_links: list[dict[str, str]] | None = None,
    release_name: str | None = None,
    metadata: dict | None = None,
) -> SearchResult:
    return SearchResult(
        title=title,
        download_link=download_link,
        download_links=download_links,
        release_name=release_name,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# filter_by_episode
# ---------------------------------------------------------------------------


class TestFilterByEpisode:
    def test_no_season_no_episode_returns_all(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S01E02"),
        ]
        assert filter_by_episode(results, season=None, episode=None) == results

    def test_filters_wrong_season(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S02E01"),
        ]
        filtered = filter_by_episode(results, season=1, episode=None)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S01E01"

    def test_filters_wrong_episode(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S01E02"),
            _make_search_result(title="Show S01E03"),
        ]
        filtered = filter_by_episode(results, season=1, episode=2)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S01E02"

    def test_drops_titles_without_episode_info(self) -> None:
        """A result without an episode number and without labelled links (a
        show page) is dropped: it would pass with every episode."""
        results = [
            _make_search_result(title="Random Movie Title"),
            _make_search_result(title="Show S01E03"),
        ]
        filtered = filter_by_episode(results, season=1, episode=3)
        assert [r.title for r in filtered] == ["Show S01E03"]

    def test_season_only_keeps_all_episodes_of_season(self) -> None:
        results = [
            _make_search_result(title="Show S02E01"),
            _make_search_result(title="Show S02E05"),
            _make_search_result(title="Show S03E01"),
        ]
        filtered = filter_by_episode(results, season=2, episode=None)
        assert len(filtered) == 2
        assert all("S02" in r.title for r in filtered)

    def test_release_name_style_titles(self) -> None:
        """Guessit should parse release-name style titles."""
        results = [
            _make_search_result(title="Breaking.Bad.S05E03.1080p.WEB-DL"),
            _make_search_result(title="Breaking.Bad.S05E04.720p.BluRay"),
            _make_search_result(title="Breaking.Bad.S04E01.HDTV"),
        ]
        filtered = filter_by_episode(results, season=5, episode=3)
        assert len(filtered) == 1
        assert "S05E03" in filtered[0].title

    def test_empty_results_returns_empty(self) -> None:
        assert filter_by_episode([], season=1, episode=1) == []

    def test_season_zero_filters_correctly(self) -> None:
        """Season 0 (Specials) must not bypass the filter."""
        results = [
            _make_search_result(title="Show S00E01"),
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S00E02"),
        ]
        filtered = filter_by_episode(results, season=0, episode=1)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S00E01"

    def test_episode_zero_filters_correctly(self) -> None:
        """Episode 0 (Pilot/Special) must not bypass the filter."""
        results = [
            _make_search_result(title="Show S01E00"),
            _make_search_result(title="Show S01E01"),
        ]
        filtered = filter_by_episode(results, season=1, episode=0)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S01E00"

    def test_unparseable_title_with_episode_labels_filters_links(self) -> None:
        """Streamcloud: no SxxExx in title, 1x5 labels in links."""
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/1x1", "label": "1x1 Episode 1"},
            {"hoster": "VOE", "link": "https://voe.sx/e/1x2", "label": "1x2 Episode 2"},
            {"hoster": "VOE", "link": "https://voe.sx/e/1x5", "label": "1x5 Episode 5"},
            {
                "hoster": "Filemoon",
                "link": "https://fm.sx/e/1x5",
                "label": "1x5 Episode 5",
            },
        ]
        results = [
            _make_search_result(
                title="Naruto Shippuden",
                download_links=links,
            ),
        ]
        filtered = filter_by_episode(results, season=1, episode=5)
        assert len(filtered) == 1
        # Only the two 1x5 links should survive
        assert len(filtered[0].download_links) == 2
        assert all("1x5" in lnk["label"] for lnk in filtered[0].download_links)

    def test_unparseable_title_all_wrong_episodes_dropped(self) -> None:
        """All download_links are for wrong episodes -> result dropped entirely."""
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/1x1", "label": "1x1 Episode 1"},
            {"hoster": "VOE", "link": "https://voe.sx/e/1x2", "label": "1x2 Episode 2"},
        ]
        results = [
            _make_search_result(title="Naruto Shippuden", download_links=links),
        ]
        filtered = filter_by_episode(results, season=1, episode=5)
        assert len(filtered) == 0

    def test_unparseable_title_no_labels_dropped(self) -> None:
        """Links without episode labels (just hosters) -> dropped: a show
        page without an episode number would leak other episodes."""
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/abc", "label": "Stream HD+"},
            {"hoster": "Filemoon", "link": "https://fm.sx/e/def", "label": "Stream SD"},
        ]
        results = [
            _make_search_result(title="Naruto Shippuden", download_links=links),
        ]
        assert filter_by_episode(results, season=1, episode=5) == []

    def test_streamcloud_massive_episode_list_filtered(self) -> None:
        """100 episode links from all seasons reduced to 2 links for S02E03."""
        links = []
        for s in range(1, 6):
            for e in range(1, 21):
                links.append(
                    {
                        "hoster": "VOE",
                        "link": f"https://voe.sx/e/{s}x{e}",
                        "label": f"{s}x{e} Episode {e}",
                    }
                )
                links.append(
                    {
                        "hoster": "Filemoon",
                        "link": f"https://fm.sx/e/{s}x{e}",
                        "label": f"{s}x{e} Episode {e}",
                    }
                )
        # 5 seasons × 20 episodes × 2 hosters = 200 links
        assert len(links) == 200

        results = [
            _make_search_result(title="Breaking Bad", download_links=links),
        ]
        filtered = filter_by_episode(results, season=2, episode=3)
        assert len(filtered) == 1
        assert len(filtered[0].download_links) == 2
        for lnk in filtered[0].download_links:
            assert "2x3" in lnk["label"]


# ---------------------------------------------------------------------------
# parse_episode_from_label
# ---------------------------------------------------------------------------


class TestParseEpisodeFromLabel:
    def test_standard_format(self) -> None:
        assert parse_episode_from_label("1x5 Episode 5") == (1, 5)

    def test_zero_padded(self) -> None:
        assert parse_episode_from_label("1x05 Episode 5") == (1, 5)

    def test_high_numbers(self) -> None:
        assert parse_episode_from_label("21x1042") == (21, 1042)

    def test_no_match(self) -> None:
        assert parse_episode_from_label("Stream HD+") == (None, None)

    def test_empty_string(self) -> None:
        assert parse_episode_from_label("") == (None, None)

    def test_hoster_name_only(self) -> None:
        assert parse_episode_from_label("streamtape") == (None, None)

    def test_uppercase_x(self) -> None:
        assert parse_episode_from_label("2X10 Title") == (2, 10)

    def test_with_surrounding_text(self) -> None:
        assert parse_episode_from_label("Season 3x12 - The Final") == (3, 12)

    def test_sxxexx_format(self) -> None:
        assert parse_episode_from_label("S01E05 Episode 5") == (1, 5)

    def test_sxxexx_no_padding(self) -> None:
        assert parse_episode_from_label("S1E5") == (1, 5)

    def test_sxxexx_lowercase(self) -> None:
        assert parse_episode_from_label("s02e10 title") == (2, 10)

    def test_sxxexx_high_numbers(self) -> None:
        assert parse_episode_from_label("S21E1042") == (21, 1042)

    def test_sxxexx_with_surrounding_text(self) -> None:
        assert parse_episode_from_label("Show S03E12 - The Final") == (3, 12)


# ---------------------------------------------------------------------------
# filter_links_by_episode
# ---------------------------------------------------------------------------


class TestFilterLinksByEpisode:
    def test_returns_none_when_no_labels_have_episodes(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/a", "label": "Stream HD+"},
            {"hoster": "Filemoon", "link": "https://fm.sx/e/b", "label": "Mirror"},
        ]
        assert filter_links_by_episode(links, season=1, episode=5) is None

    def test_filters_to_matching_episode(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/1", "label": "1x1 Ep 1"},
            {"hoster": "VOE", "link": "https://voe.sx/e/2", "label": "1x2 Ep 2"},
            {"hoster": "VOE", "link": "https://voe.sx/e/3", "label": "1x3 Ep 3"},
        ]
        result = filter_links_by_episode(links, season=1, episode=2)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "1x2 Ep 2"

    def test_filters_by_season(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x1"},
            {"hoster": "VOE", "link": "https://b", "label": "2x1"},
            {"hoster": "VOE", "link": "https://c", "label": "2x2"},
        ]
        result = filter_links_by_episode(links, season=2, episode=None)
        assert result is not None
        assert len(result) == 2

    def test_empty_result_when_no_match(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x1"},
            {"hoster": "VOE", "link": "https://b", "label": "1x2"},
        ]
        result = filter_links_by_episode(links, season=1, episode=5)
        assert result is not None
        assert len(result) == 0

    def test_season_zero_not_treated_as_falsy(self) -> None:
        """Season 0 (Specials/OVAs) must filter correctly, not bypass."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "0x1 Special 1"},
            {"hoster": "VOE", "link": "https://b", "label": "1x1 Episode 1"},
            {"hoster": "VOE", "link": "https://c", "label": "0x2 Special 2"},
        ]
        result = filter_links_by_episode(links, season=0, episode=1)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "0x1 Special 1"

    def test_episode_zero_not_treated_as_falsy(self) -> None:
        """Episode 0 must filter correctly, not bypass."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x0 Pilot"},
            {"hoster": "VOE", "link": "https://b", "label": "1x1 Episode 1"},
        ]
        result = filter_links_by_episode(links, season=1, episode=0)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "1x0 Pilot"

    def test_sxxexx_labels_filtered(self) -> None:
        """SxxExx format in labels should be parsed and filtered."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "S01E01 Pilot"},
            {"hoster": "VOE", "link": "https://b", "label": "S01E02 Episode 2"},
            {"hoster": "VOE", "link": "https://c", "label": "S02E01 New Season"},
        ]
        result = filter_links_by_episode(links, season=1, episode=2)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "S01E02 Episode 2"

    def test_orphaned_mirrors_skipped(self) -> None:
        """Links without episode labels are skipped (orphaned mirrors)."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x5 Ep 5"},
            {"hoster": "Streamtape", "link": "https://b", "label": "streamtape"},
            {"hoster": "VOE", "link": "https://c", "label": "1x6 Ep 6"},
        ]
        result = filter_links_by_episode(links, season=1, episode=5)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "1x5 Ep 5"


class TestMultiEpisodeReleases:
    """guessit returns lists for multi-episode / multi-season releases."""

    def test_multi_episode_release_containing_episode_is_kept(self) -> None:
        r = _make_search_result(title="Show.S01E01-E03.German.1080p.WEB")
        assert filter_by_episode([r], 1, 2) == [r]

    def test_multi_episode_release_without_episode_is_dropped(self) -> None:
        r = _make_search_result(title="Show.S01E01-E03.German.1080p.WEB")
        assert filter_by_episode([r], 1, 5) == []

    def test_multi_season_release_containing_season_is_kept(self) -> None:
        r = _make_search_result(title="Show.S01-S03.German.1080p.WEB")
        assert filter_by_episode([r], 2, None) == [r]


class TestSeasonTitles:
    """A title with a season but no episode (a season page, a season pack)
    still narrows its links by their episode labels."""

    _LINKS = [
        {"hoster": "VOE", "link": "https://voe.sx/e/a", "label": "1x1 Voe"},
        {"hoster": "VOE", "link": "https://voe.sx/e/b", "label": "1x2 Voe"},
    ]

    @pytest.mark.parametrize(
        "title", ["Show - Staffel 1", "Show.S01.German.DL.1080p.WEB.x264-GRP"]
    )
    def test_links_narrowed_to_the_episode(self, title: str) -> None:
        r = _make_search_result(title=title, download_links=self._LINKS)
        filtered = filter_by_episode([r], season=1, episode=2)
        assert len(filtered) == 1
        assert filtered[0].download_links == [self._LINKS[1]]
        assert filtered[0].download_link == "https://voe.sx/e/b"

    def test_other_season_dropped(self) -> None:
        r = _make_search_result(title="Show S02", download_links=self._LINKS)
        assert filter_by_episode([r], season=1, episode=1) == []

    def test_links_without_labels_dropped(self) -> None:
        links = [{"hoster": "VOE", "link": "https://voe.sx/e/a", "label": ""}]
        r = _make_search_result(title="Show - Staffel 1", download_links=links)
        assert filter_by_episode([r], season=1, episode=2) == []

    def test_a_season_request_keeps_links_without_labels(self) -> None:
        links = [{"hoster": "VOE", "link": "https://voe.sx/e/a", "label": ""}]
        r = _make_search_result(title="Show - Staffel 1", download_links=links)
        assert filter_by_episode([r], season=1, episode=None) == [r]


class TestWithoutLeaks:
    """The spec scenarios of continue-cut-searches: metadata first, no
    episode number -> labelled links only, an episode without a season
    names season 1."""

    _LINKS = [
        {"hoster": "VOE", "link": "https://voe.sx/e/4", "label": "S01E04 VOE"},
        {"hoster": "VOE", "link": "https://voe.sx/e/5", "label": "S01E05 VOE"},
    ]

    def test_show_page_without_labelled_links_is_dropped(self) -> None:
        links = [{"hoster": "VOE", "link": "https://voe.sx/e/a", "label": "VOE"}]
        r = _make_search_result(title="Severance", download_links=links)
        assert filter_by_episode([r], season=1, episode=5) == []

    def test_season_pack_passes_with_the_episodes_link_only(self) -> None:
        r = _make_search_result(title="Severance S01", download_links=self._LINKS)
        filtered = filter_by_episode([r], season=1, episode=5)
        assert len(filtered) == 1
        assert filtered[0].download_links == [self._LINKS[1]]
        assert filtered[0].download_link == "https://voe.sx/e/5"

    def test_an_episode_without_a_season_is_dropped_for_season_2(self) -> None:
        r = _make_search_result(title="Severance E05")
        assert filter_by_episode([r], season=2, episode=5) == []

    def test_an_episode_without_a_season_passes_for_season_1(self) -> None:
        r = _make_search_result(title="Severance E05")
        assert filter_by_episode([r], season=1, episode=5) == [r]

    def test_metadata_is_trusted(self) -> None:
        r = _make_search_result(title="Severance", metadata={"season": 2, "episode": 5})
        assert filter_by_episode([r], season=2, episode=5) == [r]

    def test_metadata_of_another_episode_drops(self) -> None:
        r = _make_search_result(title="Severance", metadata={"season": 2, "episode": 4})
        assert filter_by_episode([r], season=2, episode=5) == []

    def test_metadata_comes_before_the_title(self) -> None:
        """A page that knows its episode outranks a release name."""
        r = _make_search_result(
            title="Severance S01E04", metadata={"season": 2, "episode": 5}
        )
        assert filter_by_episode([r], season=2, episode=5) == [r]

    def test_a_metadata_string_is_ignored(self) -> None:
        """Older plugins stored the season as a string: the title decides."""
        r = _make_search_result(
            title="Severance S02E05", metadata={"season": "1", "episode": "4"}
        )
        assert filter_by_episode([r], season=2, episode=5) == [r]
