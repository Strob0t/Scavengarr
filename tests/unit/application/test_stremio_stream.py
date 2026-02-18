"""Tests for StremioStreamUseCase."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from scavengarr.application.use_cases.stremio_stream import (
    StremioStreamUseCase,
    _build_search_queries,
    _build_search_query,
    _deduplicate_by_hoster,
    _filter_by_episode,
    _filter_links_by_episode,
    _format_stream,
    _is_direct_video_url,
    _parse_episode_from_label,
)
from scavengarr.domain.entities.stremio import (
    RankedStream,
    ResolvedStream,
    StreamLanguage,
    StreamQuality,
    StremioStreamRequest,
    TitleMatchInfo,
)
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.config.schema import StremioConfig
from scavengarr.infrastructure.plugins.constants import (
    DEFAULT_USER_AGENT,
    search_max_results,
)
from scavengarr.infrastructure.stremio.stream_converter import convert_search_results
from scavengarr.infrastructure.stremio.stream_sorter import StreamSorter
from scavengarr.infrastructure.stremio.title_matcher import filter_by_title_match

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides: object) -> StremioConfig:
    defaults = {
        "max_concurrent_plugins": 5,
        "language_scores": {"de": 1000, "en": 150},
        "default_language_score": 100,
        "quality_multiplier": 10,
        "hoster_scores": {"voe": 4},
    }
    defaults.update(overrides)
    return StremioConfig(**defaults)


def _make_request(
    *,
    imdb_id: str = "tt1234567",
    content_type: str = "movie",
    season: int | None = None,
    episode: int | None = None,
) -> StremioStreamRequest:
    return StremioStreamRequest(
        imdb_id=imdb_id,
        content_type=content_type,
        season=season,
        episode=episode,
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


def _make_use_case(
    *,
    tmdb: AsyncMock | None = None,
    plugins: MagicMock | None = None,
    search_engine: AsyncMock | None = None,
    config: StremioConfig | None = None,
    stream_link_repo: AsyncMock | None = None,
    probe_fn: AsyncMock | None = None,
    resolve_fn: AsyncMock | None = None,
) -> StremioStreamUseCase:
    engine = search_engine or AsyncMock()
    # Default: validate_results returns input unchanged
    if not search_engine:
        engine.validate_results = AsyncMock(side_effect=lambda r: r)
        engine.search = AsyncMock(return_value=[])
    cfg = config or _make_config()
    return StremioStreamUseCase(
        tmdb=tmdb or AsyncMock(),
        plugins=plugins or MagicMock(),
        search_engine=engine,
        config=cfg,
        sorter=StreamSorter(cfg),
        convert_fn=convert_search_results,
        filter_fn=filter_by_title_match,
        user_agent=DEFAULT_USER_AGENT,
        max_results_var=search_max_results,
        stream_link_repo=stream_link_repo,
        probe_fn=probe_fn,
        resolve_fn=resolve_fn,
    )


# ---------------------------------------------------------------------------
# _filter_by_episode
# ---------------------------------------------------------------------------


class TestFilterByEpisode:
    def test_no_season_no_episode_returns_all(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S01E02"),
        ]
        assert _filter_by_episode(results, season=None, episode=None) == results

    def test_filters_wrong_season(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S02E01"),
        ]
        filtered = _filter_by_episode(results, season=1, episode=None)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S01E01"

    def test_filters_wrong_episode(self) -> None:
        results = [
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S01E02"),
            _make_search_result(title="Show S01E03"),
        ]
        filtered = _filter_by_episode(results, season=1, episode=2)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S01E02"

    def test_keeps_unparseable_titles(self) -> None:
        """Results without season/episode info are kept (benefit of the doubt)."""
        results = [
            _make_search_result(title="Random Movie Title"),
            _make_search_result(title="Show S01E03"),
        ]
        filtered = _filter_by_episode(results, season=1, episode=3)
        assert len(filtered) == 2

    def test_season_only_keeps_all_episodes_of_season(self) -> None:
        results = [
            _make_search_result(title="Show S02E01"),
            _make_search_result(title="Show S02E05"),
            _make_search_result(title="Show S03E01"),
        ]
        filtered = _filter_by_episode(results, season=2, episode=None)
        assert len(filtered) == 2
        assert all("S02" in r.title for r in filtered)

    def test_release_name_style_titles(self) -> None:
        """Guessit should parse release-name style titles."""
        results = [
            _make_search_result(title="Breaking.Bad.S05E03.1080p.WEB-DL"),
            _make_search_result(title="Breaking.Bad.S05E04.720p.BluRay"),
            _make_search_result(title="Breaking.Bad.S04E01.HDTV"),
        ]
        filtered = _filter_by_episode(results, season=5, episode=3)
        assert len(filtered) == 1
        assert "S05E03" in filtered[0].title

    def test_empty_results_returns_empty(self) -> None:
        assert _filter_by_episode([], season=1, episode=1) == []

    def test_season_zero_filters_correctly(self) -> None:
        """Season 0 (Specials) must not bypass the filter."""
        results = [
            _make_search_result(title="Show S00E01"),
            _make_search_result(title="Show S01E01"),
            _make_search_result(title="Show S00E02"),
        ]
        filtered = _filter_by_episode(results, season=0, episode=1)
        assert len(filtered) == 1
        assert filtered[0].title == "Show S00E01"

    def test_episode_zero_filters_correctly(self) -> None:
        """Episode 0 (Pilot/Special) must not bypass the filter."""
        results = [
            _make_search_result(title="Show S01E00"),
            _make_search_result(title="Show S01E01"),
        ]
        filtered = _filter_by_episode(results, season=1, episode=0)
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
        filtered = _filter_by_episode(results, season=1, episode=5)
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
        filtered = _filter_by_episode(results, season=1, episode=5)
        assert len(filtered) == 0

    def test_unparseable_title_no_labels_kept(self) -> None:
        """Links without episode labels -> kept (kinoger-style, just hosters)."""
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/abc", "label": "Stream HD+"},
            {"hoster": "Filemoon", "link": "https://fm.sx/e/def", "label": "Stream SD"},
        ]
        results = [
            _make_search_result(title="Naruto Shippuden", download_links=links),
        ]
        filtered = _filter_by_episode(results, season=1, episode=5)
        assert len(filtered) == 1
        assert len(filtered[0].download_links) == 2

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
        filtered = _filter_by_episode(results, season=2, episode=3)
        assert len(filtered) == 1
        assert len(filtered[0].download_links) == 2
        for lnk in filtered[0].download_links:
            assert "2x3" in lnk["label"]


# ---------------------------------------------------------------------------
# _parse_episode_from_label
# ---------------------------------------------------------------------------


class TestParseEpisodeFromLabel:
    def test_standard_format(self) -> None:
        assert _parse_episode_from_label("1x5 Episode 5") == (1, 5)

    def test_zero_padded(self) -> None:
        assert _parse_episode_from_label("1x05 Episode 5") == (1, 5)

    def test_high_numbers(self) -> None:
        assert _parse_episode_from_label("21x1042") == (21, 1042)

    def test_no_match(self) -> None:
        assert _parse_episode_from_label("Stream HD+") == (None, None)

    def test_empty_string(self) -> None:
        assert _parse_episode_from_label("") == (None, None)

    def test_hoster_name_only(self) -> None:
        assert _parse_episode_from_label("streamtape") == (None, None)

    def test_uppercase_x(self) -> None:
        assert _parse_episode_from_label("2X10 Title") == (2, 10)

    def test_with_surrounding_text(self) -> None:
        assert _parse_episode_from_label("Season 3x12 - The Final") == (3, 12)

    def test_sxxexx_format(self) -> None:
        assert _parse_episode_from_label("S01E05 Episode 5") == (1, 5)

    def test_sxxexx_no_padding(self) -> None:
        assert _parse_episode_from_label("S1E5") == (1, 5)

    def test_sxxexx_lowercase(self) -> None:
        assert _parse_episode_from_label("s02e10 title") == (2, 10)

    def test_sxxexx_high_numbers(self) -> None:
        assert _parse_episode_from_label("S21E1042") == (21, 1042)

    def test_sxxexx_with_surrounding_text(self) -> None:
        assert _parse_episode_from_label("Show S03E12 - The Final") == (3, 12)


# ---------------------------------------------------------------------------
# _filter_links_by_episode
# ---------------------------------------------------------------------------


class TestFilterLinksByEpisode:
    def test_returns_none_when_no_labels_have_episodes(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/a", "label": "Stream HD+"},
            {"hoster": "Filemoon", "link": "https://fm.sx/e/b", "label": "Mirror"},
        ]
        assert _filter_links_by_episode(links, season=1, episode=5) is None

    def test_filters_to_matching_episode(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://voe.sx/e/1", "label": "1x1 Ep 1"},
            {"hoster": "VOE", "link": "https://voe.sx/e/2", "label": "1x2 Ep 2"},
            {"hoster": "VOE", "link": "https://voe.sx/e/3", "label": "1x3 Ep 3"},
        ]
        result = _filter_links_by_episode(links, season=1, episode=2)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "1x2 Ep 2"

    def test_filters_by_season(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x1"},
            {"hoster": "VOE", "link": "https://b", "label": "2x1"},
            {"hoster": "VOE", "link": "https://c", "label": "2x2"},
        ]
        result = _filter_links_by_episode(links, season=2, episode=None)
        assert result is not None
        assert len(result) == 2

    def test_empty_result_when_no_match(self) -> None:
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x1"},
            {"hoster": "VOE", "link": "https://b", "label": "1x2"},
        ]
        result = _filter_links_by_episode(links, season=1, episode=5)
        assert result is not None
        assert len(result) == 0

    def test_season_zero_not_treated_as_falsy(self) -> None:
        """Season 0 (Specials/OVAs) must filter correctly, not bypass."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "0x1 Special 1"},
            {"hoster": "VOE", "link": "https://b", "label": "1x1 Episode 1"},
            {"hoster": "VOE", "link": "https://c", "label": "0x2 Special 2"},
        ]
        result = _filter_links_by_episode(links, season=0, episode=1)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "0x1 Special 1"

    def test_episode_zero_not_treated_as_falsy(self) -> None:
        """Episode 0 must filter correctly, not bypass."""
        links = [
            {"hoster": "VOE", "link": "https://a", "label": "1x0 Pilot"},
            {"hoster": "VOE", "link": "https://b", "label": "1x1 Episode 1"},
        ]
        result = _filter_links_by_episode(links, season=1, episode=0)
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
        result = _filter_links_by_episode(links, season=1, episode=2)
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
        result = _filter_links_by_episode(links, season=1, episode=5)
        assert result is not None
        assert len(result) == 1
        assert result[0]["label"] == "1x5 Ep 5"


# ---------------------------------------------------------------------------
# _build_search_query
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def test_movie_query(self) -> None:
        assert _build_search_query("Iron Man") == "Iron Man"

    def test_series_returns_plain_title(self) -> None:
        """Season/episode are passed separately, not appended to the query."""
        assert _build_search_query("Breaking Bad") == "Breaking Bad"

    def test_series_season_only_returns_plain_title(self) -> None:
        assert _build_search_query("Breaking Bad") == "Breaking Bad"

    def test_series_no_season_returns_plain_title(self) -> None:
        assert _build_search_query("Show") == "Show"

    def test_strips_colons(self) -> None:
        """Colons break site searches (e.g. s.to)."""
        assert _build_search_query("Naruto: Shippuden") == "Naruto Shippuden"

    def test_normalizes_unicode_diacritics(self) -> None:
        """Unicode diacritics like ū must become plain ASCII."""
        assert _build_search_query("Naruto: Shippūden") == "Naruto Shippuden"

    def test_preserves_hyphens(self) -> None:
        """Hyphens should survive sanitization."""
        assert _build_search_query("Spider-Man") == "Spider-Man"

    def test_collapses_whitespace(self) -> None:
        assert _build_search_query("  Breaking   Bad  ") == "Breaking Bad"

    def test_german_umlauts_pass_through(self) -> None:
        """German umlauts (ä/ö/ü) decompose to ae/oe/ue-like forms via NFKD."""
        # NFKD decomposes ü → u + combining diaeresis, then combining mark is stripped
        assert _build_search_query("Türkisch für Anfänger") == "Turkisch fur Anfanger"

    def test_german_eszett(self) -> None:
        """ß must transliterate to 'ss' (NFKD cannot decompose it)."""
        assert _build_search_query("Die Straße") == "Die Strasse"

    def test_ligature_ae(self) -> None:
        """Æ must transliterate to 'AE' (unidecode preserves case)."""
        assert _build_search_query("Ælfred") == "AElfred"

    def test_scandinavian_oe(self) -> None:
        """œ must transliterate to 'oe'."""
        assert _build_search_query("Cœur") == "Coeur"

    def test_scandinavian_oslash(self) -> None:
        """ø must transliterate to 'o'."""
        assert _build_search_query("Ødegaard") == "Odegaard"

    def test_polish_l_stroke(self) -> None:
        """Ł must transliterate to 'L'."""
        assert _build_search_query("Łódź") == "Lodz"

    def test_full_pipeline_naruto(self) -> None:
        """End-to-end: Wikidata title with colon + macron → clean query."""
        assert _build_search_query("Naruto: Shippūden") == "Naruto Shippuden"

    def test_ampersand_removed(self) -> None:
        assert _build_search_query("Hänsel & Gretel") == "Hansel Gretel"

    def test_preserves_apostrophe(self) -> None:
        assert _build_search_query("Ocean's Eleven") == "Ocean's Eleven"


# ---------------------------------------------------------------------------
# _build_search_queries (subtitle fallback)
# ---------------------------------------------------------------------------


class TestBuildSearchQueries:
    def test_no_colon_single_query(self) -> None:
        assert _build_search_queries("Iron Man") == ["Iron Man"]

    def test_colon_adds_base_title_fallback(self) -> None:
        assert _build_search_queries("Dune: Part One") == [
            "Dune Part One",
            "Dune",
        ]

    def test_colon_spider_man(self) -> None:
        queries = _build_search_queries("Spider-Man: No Way Home")
        assert queries == ["Spider-Man No Way Home", "Spider-Man"]

    def test_colon_same_as_full_no_duplicate(self) -> None:
        """If base == full after cleaning, don't add a duplicate."""
        assert _build_search_queries("Dune:") == ["Dune"]

    def test_multiple_colons_uses_first(self) -> None:
        queries = _build_search_queries("Star Wars: Episode IV: A New Hope")
        assert queries[0] == "Star Wars Episode IV A New Hope"
        assert queries[1] == "Star Wars"


# ---------------------------------------------------------------------------
# _format_stream
# ---------------------------------------------------------------------------


class TestFormatStream:
    def test_reference_title_used_as_name(self) -> None:
        """Reference title (from TMDB) is preferred over ranked.title."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            title="Iron Man",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        result = _format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.name == "Iron Man (2008) HD 1080P"

    def test_series_format_with_season_episode(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
            source_plugin="aniworld",
        )
        result = _format_stream(
            ranked, reference_title="Breaking Bad", season=1, episode=5
        )
        assert result.name == "Breaking Bad S01E05 HD 720P"

    def test_release_name_fallback_without_reference(self) -> None:
        """Without reference_title, release_name is used as fallback."""
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            size="1.5 GB",
            release_name="Iron.Man.2008.1080p.WEB-DL",
            source_plugin="hdfilme",
            rank_score=1500,
        )
        result = _format_stream(ranked)
        assert result.url == "https://voe.sx/e/abc"
        assert result.name == "Iron.Man.2008.1080p.WEB-DL"
        assert "German Dub" in result.description
        assert "VOE" in result.description
        assert "1.5 GB" in result.description

    def test_title_fallback_without_reference(self) -> None:
        """Without reference_title or release_name, ranked.title is used."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            title="Iron Man",
            source_plugin="hdfilme",
        )
        result = _format_stream(ranked)
        assert result.name == "Iron Man HD 1080P"

    def test_fallback_to_plugin_quality(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
            source_plugin="hdfilme",
        )
        result = _format_stream(ranked)
        assert result.name == "hdfilme HD 720P"

    def test_fallback_no_source_plugin(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_720P,
        )
        result = _format_stream(ranked)
        assert result.name == "HD 720P"

    def test_unknown_quality_not_appended(self) -> None:
        """UNKNOWN quality is not appended to the name."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.UNKNOWN,
            title="Iron Man",
            source_plugin="hdfilme",
        )
        result = _format_stream(ranked, reference_title="Iron Man", year=2008)
        assert result.name == "Iron Man (2008)"
        assert "UNKNOWN" not in result.name

    def test_source_plugin_in_description(self) -> None:
        """source_plugin is always the first element of the description."""
        lang = StreamLanguage(code="de", label="German Dub", is_dubbed=True)
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            language=lang,
            source_plugin="hdfilme",
        )
        result = _format_stream(ranked, reference_title="Iron Man")
        assert result.description.startswith("hdfilme")
        assert "German Dub" in result.description
        assert "VOE" in result.description

    def test_description_without_source_plugin(self) -> None:
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.UNKNOWN,
        )
        result = _format_stream(ranked)
        # No source_plugin → description should still work
        assert "VOE" in result.description

    def test_empty_hoster(self) -> None:
        ranked = RankedStream(
            url="https://example.com",
            hoster="",
            quality=StreamQuality.SD,
        )
        result = _format_stream(ranked)
        assert (
            result.description == ""
            or "|" not in result.description
            or result.description.strip()
        )

    def test_reference_title_without_year(self) -> None:
        """Reference title without year omits the year parenthetical."""
        ranked = RankedStream(
            url="https://voe.sx/e/abc",
            hoster="voe",
            quality=StreamQuality.HD_1080P,
            source_plugin="hdfilme",
        )
        result = _format_stream(ranked, reference_title="Iron Man")
        assert result.name == "Iron Man HD 1080P"


# ---------------------------------------------------------------------------
# StremioStreamUseCase.execute
# ---------------------------------------------------------------------------


class TestExecute:
    async def test_title_not_found_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=None)
        uc = _make_use_case(tmdb=tmdb)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_no_stream_plugins_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        plugins = MagicMock()
        plugins.get_by_provides.return_value = []

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_happy_path_movie(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc", "quality": "1080p"},
            ],
        )

        # Python plugin (no scraping attribute) that returns search results
        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping  # Ensure no scraping attr → Python plugin path

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        # Name should include reference title from TMDB
        assert "Iron Man" in result[0].name
        assert "(2008)" in result[0].name
        tmdb.get_title_and_year.assert_awaited_once_with("tt1234567")
        mock_plugin.search.assert_awaited_once_with(
            "Iron Man", category=2000, season=None, episode=None
        )
        engine.validate_results.assert_awaited_once()

    async def test_series_query_passes_season_episode(self) -> None:
        """Season/episode are passed as kwargs, query is the plain title."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Breaking Bad", year=2008)
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["aniworld"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        req = _make_request(content_type="series", season=1, episode=5)
        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(req)

        mock_plugin.search.assert_awaited_once_with(
            "Breaking Bad", category=5000, season=1, episode=5
        )

    async def test_multiple_plugins_combined(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr1 = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/1"}],
        )
        sr2 = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://filemoon.sx/e/2"}],
        )

        plugin_a = AsyncMock()
        plugin_a.search = AsyncMock(return_value=[sr1])
        del plugin_a.scraping
        plugin_b = AsyncMock()
        plugin_b.search = AsyncMock(return_value=[sr2])
        del plugin_b.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["a", "b"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {"a": plugin_a, "b": plugin_b}[name]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) == 2
        urls = {s.url for s in result}
        assert "https://voe.sx/e/1" in urls
        assert "https://filemoon.sx/e/2" in urls

    async def test_plugin_error_does_not_crash(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/ok"}],
        )

        good_plugin = AsyncMock()
        good_plugin.search = AsyncMock(return_value=[sr])
        del good_plugin.scraping
        bad_plugin = AsyncMock()
        bad_plugin.search = AsyncMock(side_effect=RuntimeError("boom"))
        del bad_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["good", "bad"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {"good": good_plugin, "bad": bad_plugin}[
            name
        ]

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        # Should still return results from the good plugin
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/ok"

    async def test_plugin_not_found_skipped(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["missing"] if p == "stream" else []
        )
        plugins.get.side_effect = KeyError("not found")

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_empty_search_results_returns_empty(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_source_plugin_tagged_on_results(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["myplugin"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(_make_request())

        # The use case should tag source_plugin in metadata
        assert sr.metadata.get("source_plugin") == "myplugin"

    async def test_both_provides_plugins_included(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/both"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        # "stream" returns nothing, but "both" returns one plugin
        plugins.get_by_provides.side_effect = lambda p: (
            [] if p == "stream" else ["combo"]
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        assert len(result) == 1

    async def test_deduplication_of_plugin_names(self) -> None:
        """Plugin in both stream and both is searched once."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        # Same plugin name returned by both calls
        plugins.get_by_provides.side_effect = lambda p: (
            ["overlap"] if p in ("stream", "both") else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        await uc.execute(_make_request())

        # Should only search once despite appearing in both lists
        mock_plugin.search.assert_awaited_once()

    async def test_streams_sorted_by_score(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[
                {"url": "https://streamtape.com/v/low", "quality": "SD"},
                {"url": "https://voe.sx/e/high", "quality": "1080p"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        # Should have 2 streams (different hosters, order depends on sorter)
        assert len(result) == 2

    async def test_plugin_without_search_method_skipped(self) -> None:
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        # Plugin without search method
        no_search_plugin = MagicMock(spec=[])

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["nosearch"] if p == "stream" else []
        )
        plugins.get.return_value = no_search_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_concurrency_limited(self) -> None:
        """Verify semaphore limits concurrent plugin searches."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(return_value=[])

        plugins = MagicMock()
        many_names = [f"plugin_{i}" for i in range(10)]
        plugins.get_by_provides.side_effect = lambda p: (
            many_names if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        config = _make_config(max_concurrent_plugins=2)
        uc = _make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )

        # Should complete without error; semaphore internally limits to 2
        result = await uc.execute(_make_request())
        assert result == []
        assert mock_plugin.search.await_count == 10

    async def test_slow_plugin_cancelled_by_timeout(self) -> None:
        """A plugin exceeding plugin_timeout_seconds is cancelled."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(return_value=TitleMatchInfo(title="Movie"))

        sr = _make_search_result(
            title="Movie",
            download_links=[{"url": "https://voe.sx/e/fast"}],
        )

        fast_plugin = AsyncMock()
        fast_plugin.search = AsyncMock(return_value=[sr])
        del fast_plugin.scraping

        async def _slow_search(*_a: object, **_kw: object) -> list[SearchResult]:
            await asyncio.sleep(10)
            return []

        slow_plugin = AsyncMock()
        slow_plugin.search = AsyncMock(side_effect=_slow_search)
        del slow_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["fast", "slow"] if p == "stream" else []
        )
        plugins.get.side_effect = lambda name: {
            "fast": fast_plugin,
            "slow": slow_plugin,
        }[name]

        config = _make_config(plugin_timeout_seconds=0.1)
        uc = _make_use_case(
            tmdb=tmdb, plugins=plugins, search_engine=engine, config=config
        )
        result = await uc.execute(_make_request())

        # Fast plugin result should be present, slow plugin timed out
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/fast"


# ---------------------------------------------------------------------------
# Title-match filtering
# ---------------------------------------------------------------------------


class TestTitleMatchFiltering:
    async def test_wrong_titles_filtered(self) -> None:
        """Only results matching the reference title pass through."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr_good = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/good"}],
        )
        sr_sequel = _make_search_result(
            title="Iron Man 2",
            download_links=[{"url": "https://voe.sx/e/sequel"}],
        )
        sr_unrelated = _make_search_result(
            title="Avengers Endgame",
            download_links=[{"url": "https://voe.sx/e/unrelated"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr_good, sr_sequel, sr_unrelated])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        urls = {s.url for s in result}
        assert "https://voe.sx/e/good" in urls
        assert "https://voe.sx/e/sequel" not in urls
        assert "https://voe.sx/e/unrelated" not in urls

    async def test_all_filtered_returns_empty(self) -> None:
        """When all results are below threshold, return empty list."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Completely Unrelated Film",
            download_links=[{"url": "https://voe.sx/e/bad"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())
        assert result == []

    async def test_no_year_still_filters_by_title(self) -> None:
        """Even without year info, title similarity is applied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man")
        )

        sr_good = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/match"}],
        )
        sr_bad = _make_search_result(
            title="Spider Man",
            download_links=[{"url": "https://voe.sx/e/nomatch"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr_good, sr_bad])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["test"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request())

        urls = {s.url for s in result}
        assert "https://voe.sx/e/match" in urls
        assert "https://voe.sx/e/nomatch" not in urls


# ---------------------------------------------------------------------------
# Stream link caching + proxy URLs
# ---------------------------------------------------------------------------


class TestStreamLinkProxy:
    async def test_proxy_urls_generated_with_base_url(self) -> None:
        """When stream_link_repo and base_url are provided, URLs are proxied."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url.startswith("http://localhost:8080/api/v1/stremio/play/")
        assert "voe.sx" not in result[0].url
        repo.save.assert_awaited()

    async def test_no_proxy_without_base_url(self) -> None:
        """Without base_url, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
        )
        # No base_url → no proxying
        result = await uc.execute(_make_request())

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"
        repo.save.assert_not_awaited()

    async def test_no_proxy_without_repo(self) -> None:
        """Without stream_link_repo, original hoster URLs are returned."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[{"url": "https://voe.sx/e/abc"}],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # No repo → no proxying
        uc = _make_use_case(tmdb=tmdb, plugins=plugins, search_engine=engine)
        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert result[0].url == "https://voe.sx/e/abc"


# ---------------------------------------------------------------------------
# Resolver echo-URL filtering (skip unplayable streams)
# ---------------------------------------------------------------------------


class TestResolverEchoFiltering:
    """Streams whose resolver only validates (echoes the URL) must be skipped."""

    async def test_echo_url_streams_skipped(self) -> None:
        """XFS-style resolver returns same URL → stream excluded from output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        # One stream with an XFS embed URL (veev)
        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver echoes the URL back (XFS behaviour)
        async def _echo_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_echo_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Stream should be skipped — not included in output
        assert len(result) == 0

    async def test_direct_video_url_streams_kept(self) -> None:
        """Resolver extracting a real video URL → stream included."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://voe.sx/e/abc123", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver extracts a real HLS video URL
        async def _real_resolve(url: str, hoster: str = "") -> ResolvedStream:
            return ResolvedStream(
                video_url="https://cdn.voe.sx/hls/master.m3u8",
                is_hls=True,
                headers={"Referer": "https://voe.sx/"},
            )

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_real_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) >= 1
        assert "master.m3u8" in result[0].url

    async def test_mixed_streams_only_playable_kept(self) -> None:
        """Mix of echo and real resolvers → only playable streams in output."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://veev.to/e/abc123456789", "hoster": "VEEV"},
                {"url": "https://voe.sx/e/def456", "hoster": "VOE"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # VOE extracts real URL, Veev echoes
        async def _mixed_resolve(url: str, hoster: str = "") -> ResolvedStream:
            if "voe.sx" in url:
                return ResolvedStream(
                    video_url="https://cdn.voe.sx/hls/master.m3u8",
                    is_hls=True,
                    headers={"Referer": "https://voe.sx/"},
                )
            return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)

        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(side_effect=_mixed_resolve),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Only the VOE stream should remain
        assert len(result) == 1
        assert "master.m3u8" in result[0].url

    async def test_unresolved_streams_dropped_when_resolver_configured(self) -> None:
        """Streams that fail resolution (None) are dropped to avoid 502 proxy."""
        tmdb = AsyncMock()
        tmdb.get_title_and_year = AsyncMock(
            return_value=TitleMatchInfo(title="Iron Man", year=2008)
        )

        sr = _make_search_result(
            title="Iron Man",
            download_links=[
                {"url": "https://unknown-hoster.com/v/abc", "hoster": "UNKNOWN"},
            ],
        )

        mock_plugin = AsyncMock()
        mock_plugin.search = AsyncMock(return_value=[sr])
        del mock_plugin.scraping

        engine = AsyncMock()
        engine.validate_results = AsyncMock(side_effect=lambda r: r)

        plugins = MagicMock()
        plugins.get_by_provides.side_effect = lambda p: (
            ["hdfilme"] if p == "stream" else []
        )
        plugins.get.return_value = mock_plugin

        # Resolver returns None (no resolver found for hoster)
        repo = AsyncMock()
        uc = _make_use_case(
            tmdb=tmdb,
            plugins=plugins,
            search_engine=engine,
            stream_link_repo=repo,
            resolve_fn=AsyncMock(return_value=None),
        )

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Unresolvable streams are dropped — the /play/ proxy would always 502
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Stream probe at /stream time
# ---------------------------------------------------------------------------


_PROBE_HOSTERS = [
    "voe.sx",
    "streamtape.com",
    "dood.re",
    "filemoon.sx",
    "mixdrop.ag",
    "vidmoly.me",
    "streamwish.com",
    "vidoza.net",
    "upstream.to",
    "wolfstream.tv",
]


def _probe_test_setup(
    *,
    result_count: int = 3,
    probe_fn: AsyncMock | None = None,
    config: StremioConfig | None = None,
) -> tuple[StremioStreamUseCase, AsyncMock]:
    """Build a use case with probe callback for testing.

    Returns (use_case, repo_mock). The use case has a TMDB mock returning
    "Iron Man" (2008), a single plugin returning *result_count* streams,
    and a stream_link_repo mock for proxy URL generation.

    Each stream uses a different hoster domain to survive per-hoster
    deduplication.
    """
    tmdb = AsyncMock()
    tmdb.get_title_and_year = AsyncMock(
        return_value=TitleMatchInfo(title="Iron Man", year=2008)
    )

    srs = [
        _make_search_result(
            title="Iron Man",
            download_links=[
                {
                    "url": (
                        f"https://{_PROBE_HOSTERS[i % len(_PROBE_HOSTERS)]}/e/stream{i}"
                    )
                }
            ],
        )
        for i in range(result_count)
    ]

    mock_plugin = AsyncMock()
    mock_plugin.search = AsyncMock(return_value=srs)
    del mock_plugin.scraping

    engine = AsyncMock()
    engine.validate_results = AsyncMock(side_effect=lambda r: r)

    plugins = MagicMock()
    plugins.get_by_provides.side_effect = lambda p: ["hdfilme"] if p == "stream" else []
    plugins.get.return_value = mock_plugin

    repo = AsyncMock()
    uc = _make_use_case(
        tmdb=tmdb,
        plugins=plugins,
        search_engine=engine,
        config=config or _make_config(),
        stream_link_repo=repo,
        probe_fn=probe_fn,
    )
    return uc, repo


class TestStreamProbe:
    async def test_dead_links_filtered_by_probe(self) -> None:
        """Probe kills index 1 → only 2 streams returned."""
        probe_fn = AsyncMock(return_value={0, 2})
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 2
        probe_fn.assert_awaited_once()
        assert repo.save.await_count == 2

    async def test_probe_disabled_skips_filtering(self) -> None:
        """probe_at_stream_time=False → no filtering, all streams pass."""
        probe_fn = AsyncMock(return_value={0})
        config = _make_config(probe_at_stream_time=False)
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn, config=config)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 3
        probe_fn.assert_not_awaited()

    async def test_probe_without_fn_skips_filtering(self) -> None:
        """No probe_fn → all streams pass through unfiltered."""
        uc, repo = _probe_test_setup(result_count=3, probe_fn=None)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 3

    async def test_max_probe_count_limits_probing(self) -> None:
        """5 streams, max_probe_count=2 → only first 2 probed, rest pass."""
        # Probe kills index 0, keeps index 1
        probe_fn = AsyncMock(return_value={1})
        config = _make_config(max_probe_count=2)
        uc, repo = _probe_test_setup(result_count=5, probe_fn=probe_fn, config=config)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        # Index 0 killed, index 1 alive, indices 2-4 unprobed (pass through)
        assert len(result) == 4
        # Verify probe was called with only 2 targets
        call_args = probe_fn.call_args[0][0]
        assert len(call_args) == 2

    async def test_all_dead_returns_empty(self) -> None:
        """All streams fail probe → empty result."""
        probe_fn = AsyncMock(return_value=set())
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 0
        repo.save.assert_not_awaited()

    async def test_probe_preserves_stream_order(self) -> None:
        """Alive streams keep original sort order."""
        # Keep indices 0 and 2 → first and third stream
        probe_fn = AsyncMock(return_value={0, 2})
        uc, repo = _probe_test_setup(result_count=3, probe_fn=probe_fn)

        result = await uc.execute(_make_request(), base_url="http://localhost:8080")

        assert len(result) == 2
        # Both should be proxy URLs (order preserved)
        assert all(
            s.url.startswith("http://localhost:8080/api/v1/stremio/play/")
            for s in result
        )


# ---------------------------------------------------------------------------
# _deduplicate_by_hoster
# ---------------------------------------------------------------------------


class TestDeduplicateByHoster:
    def test_keeps_best_per_hoster(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(url="https://voe.sx/e/b", hoster="voe", rank_score=90),
            RankedStream(
                url="https://streamtape.com/v/c", hoster="streamtape", rank_score=80
            ),
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 2
        assert result[0].url == "https://voe.sx/e/a"
        assert result[1].url == "https://streamtape.com/v/c"

    def test_empty_hoster_always_kept(self) -> None:
        streams = [
            RankedStream(url="https://a.com/1", hoster="", rank_score=100),
            RankedStream(url="https://b.com/2", hoster="", rank_score=90),
            RankedStream(url="https://voe.sx/e/c", hoster="voe", rank_score=80),
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 3

    def test_single_stream_unchanged(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/a"

    def test_empty_list(self) -> None:
        assert _deduplicate_by_hoster([]) == []

    def test_all_unique_hosters(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(
                url="https://streamtape.com/v/b",
                hoster="streamtape",
                rank_score=90,
            ),
            RankedStream(url="https://dood.re/e/c", hoster="doodstream", rank_score=80),
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 3

    def test_preserves_sort_order(self) -> None:
        streams = [
            RankedStream(url="https://voe.sx/e/a", hoster="voe", rank_score=100),
            RankedStream(
                url="https://streamtape.com/v/b",
                hoster="streamtape",
                rank_score=90,
            ),
            RankedStream(url="https://voe.sx/e/c", hoster="voe", rank_score=80),
            RankedStream(url="https://dood.re/e/d", hoster="doodstream", rank_score=70),
            RankedStream(
                url="https://streamtape.com/v/e",
                hoster="streamtape",
                rank_score=60,
            ),
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 3
        assert [s.hoster for s in result] == ["voe", "streamtape", "doodstream"]

    def test_many_duplicates(self) -> None:
        streams = [
            RankedStream(url=f"https://voe.sx/e/{i}", hoster="voe", rank_score=100 - i)
            for i in range(10)
        ]
        result = _deduplicate_by_hoster(streams)
        assert len(result) == 1
        assert result[0].url == "https://voe.sx/e/0"


# ---------------------------------------------------------------------------
# _is_direct_video_url — detect actual video vs embed page URLs
# ---------------------------------------------------------------------------


class TestIsDirectVideoUrl:
    """Ensure only genuine video URLs are sent to Stremio."""

    def test_hls_m3u8_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.voe.sx/hls/master.m3u8",
            is_hls=True,
            headers={"Referer": "https://voe.sx/e/abc"},
        )
        assert _is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_mp4_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/video.mp4",
        )
        assert _is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_mkv_url(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/video.mkv",
        )
        assert _is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_hls_path_pattern(self) -> None:
        resolved = ResolvedStream(
            video_url="https://hfs.serversicuro.cc/hls/,token,.urlset/master.m3u8",
            is_hls=True,
        )
        assert _is_direct_video_url(resolved, "https://supervideo.cc/e/abc") is True

    def test_streamtape_get_video(self) -> None:
        resolved = ResolvedStream(
            video_url="https://streamtape.com/get_video?id=abc&stream=1",
            headers={"Referer": "https://streamtape.com/"},
        )
        assert _is_direct_video_url(resolved, "https://streamtape.com/e/abc") is True

    def test_xfs_embed_url_echoed_back(self) -> None:
        """XFS resolver returning the embed URL unchanged — NOT a video."""
        embed = "https://veev.to/e/2EwYsJS8frxAbWIzEhmWIJlqeGylzY9utsaUISu"
        resolved = ResolvedStream(video_url=embed)
        assert _is_direct_video_url(resolved, embed) is False

    def test_xfs_embed_html_extension(self) -> None:
        embed = "https://vidmoly.to/embed-bvhzy03fsrcx.html"
        resolved = ResolvedStream(video_url=embed)
        assert _is_direct_video_url(resolved, embed) is False

    def test_ddl_url_echoed_back(self) -> None:
        """DDL resolver returning the download page URL — NOT a video."""
        page = "https://dropload.tv/n2sostug0kwa"
        resolved = ResolvedStream(video_url=page)
        assert _is_direct_video_url(resolved, page) is False

    def test_mixdrop_embed_echoed_back(self) -> None:
        page = "https://mixdrop.co/e/1vlvk1pli1w98k"
        resolved = ResolvedStream(video_url=page)
        assert _is_direct_video_url(resolved, page) is False

    def test_different_url_with_headers(self) -> None:
        """Resolver returned a different URL + headers = actual extraction."""
        resolved = ResolvedStream(
            video_url="https://cdn.voe.sx/redirect/abc123",
            headers={"Referer": "https://voe.sx/e/abc"},
        )
        assert _is_direct_video_url(resolved, "https://voe.sx/e/abc") is True

    def test_different_url_without_headers_no_extension(self) -> None:
        """Different URL but no headers and no video extension — ambiguous, reject."""
        resolved = ResolvedStream(
            video_url="https://ddownload.com/abc123",
        )
        assert (
            _is_direct_video_url(resolved, "https://ddownload.com/abc123/file") is False
        )

    def test_is_hls_flag_overrides_all(self) -> None:
        """is_hls=True always means it's a video, regardless of URL."""
        resolved = ResolvedStream(
            video_url="https://weird-url.com/no-extension",
            is_hls=True,
        )
        assert _is_direct_video_url(resolved, "https://embed.com/e/abc") is True

    def test_webm_extension(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/clip.webm",
        )
        assert _is_direct_video_url(resolved, "https://example.com/e/abc") is True

    def test_ts_extension(self) -> None:
        resolved = ResolvedStream(
            video_url="https://cdn.example.com/segment.ts",
        )
        assert _is_direct_video_url(resolved, "https://example.com/e/abc") is True
