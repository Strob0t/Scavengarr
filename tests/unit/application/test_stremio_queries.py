"""Tests for Stremio search query building helpers."""

from __future__ import annotations

from scavengarr.application.stremio.queries import (
    build_multi_lang_reference,
    build_search_queries,
    build_search_query,
)
from scavengarr.domain.entities.stremio import TitleMatchInfo

# ---------------------------------------------------------------------------
# build_search_query
# ---------------------------------------------------------------------------


class TestBuildSearchQuery:
    def test_movie_query(self) -> None:
        assert build_search_query("Iron Man") == "Iron Man"

    def test_series_returns_plain_title(self) -> None:
        """Season/episode are passed separately, not appended to the query."""
        assert build_search_query("Breaking Bad") == "Breaking Bad"

    def test_series_season_only_returns_plain_title(self) -> None:
        assert build_search_query("Breaking Bad") == "Breaking Bad"

    def test_series_no_season_returns_plain_title(self) -> None:
        assert build_search_query("Show") == "Show"

    def test_strips_colons(self) -> None:
        """Colons break site searches (e.g. s.to)."""
        assert build_search_query("Naruto: Shippuden") == "Naruto Shippuden"

    def test_normalizes_unicode_diacritics(self) -> None:
        """Unicode diacritics like ū must become plain ASCII."""
        assert build_search_query("Naruto: Shippūden") == "Naruto Shippuden"

    def test_preserves_hyphens(self) -> None:
        """Hyphens should survive sanitization."""
        assert build_search_query("Spider-Man") == "Spider-Man"

    def test_collapses_whitespace(self) -> None:
        assert build_search_query("  Breaking   Bad  ") == "Breaking Bad"

    def test_german_umlauts_pass_through(self) -> None:
        """German umlauts (ä/ö/ü) decompose to ae/oe/ue-like forms via NFKD."""
        # NFKD decomposes ü → u + combining diaeresis, then combining mark is stripped
        assert build_search_query("Türkisch für Anfänger") == "Turkisch fur Anfanger"

    def test_german_eszett(self) -> None:
        """ß must transliterate to 'ss' (NFKD cannot decompose it)."""
        assert build_search_query("Die Straße") == "Die Strasse"

    def test_ligature_ae(self) -> None:
        """Æ must transliterate to 'AE' (unidecode preserves case)."""
        assert build_search_query("Ælfred") == "AElfred"

    def test_scandinavian_oe(self) -> None:
        """œ must transliterate to 'oe'."""
        assert build_search_query("Cœur") == "Coeur"

    def test_scandinavian_oslash(self) -> None:
        """ø must transliterate to 'o'."""
        assert build_search_query("Ødegaard") == "Odegaard"

    def test_polish_l_stroke(self) -> None:
        """Ł must transliterate to 'L'."""
        assert build_search_query("Łódź") == "Lodz"

    def test_full_pipeline_naruto(self) -> None:
        """End-to-end: Wikidata title with colon + macron → clean query."""
        assert build_search_query("Naruto: Shippūden") == "Naruto Shippuden"

    def test_ampersand_removed(self) -> None:
        assert build_search_query("Hänsel & Gretel") == "Hansel Gretel"

    def test_preserves_apostrophe(self) -> None:
        assert build_search_query("Ocean's Eleven") == "Ocean's Eleven"


# ---------------------------------------------------------------------------
# build_search_queries (subtitle fallback)
# ---------------------------------------------------------------------------


class TestBuildSearchQueries:
    def test_no_colon_single_query(self) -> None:
        assert build_search_queries("Iron Man") == ["Iron Man"]

    def test_colon_adds_base_title_fallback(self) -> None:
        assert build_search_queries("Dune: Part One") == [
            "Dune Part One",
            "Dune",
        ]

    def test_colon_spider_man(self) -> None:
        queries = build_search_queries("Spider-Man: No Way Home")
        assert queries == ["Spider-Man No Way Home", "Spider-Man"]

    def test_colon_same_as_full_no_duplicate(self) -> None:
        """If base == full after cleaning, don't add a duplicate."""
        assert build_search_queries("Dune:") == ["Dune"]

    def test_multiple_colons_uses_first(self) -> None:
        queries = build_search_queries("Star Wars: Episode IV: A New Hope")
        assert queries[0] == "Star Wars Episode IV A New Hope"
        assert queries[1] == "Star Wars"


# ---------------------------------------------------------------------------
# build_multi_lang_reference
# ---------------------------------------------------------------------------


class TestBuildMultiLangReference:
    """Tests for build_multi_lang_reference()."""

    def test_single_language(self) -> None:
        infos = {"de": TitleMatchInfo(title="Der Pate", year=1972)}
        ref = build_multi_lang_reference(infos, ["de"])
        assert ref is not None
        assert ref.title == "Der Pate"
        assert ref.year == 1972
        assert ref.alt_titles == []

    def test_two_languages_merge(self) -> None:
        infos = {
            "de": TitleMatchInfo(title="Der Pate", year=1972),
            "en": TitleMatchInfo(title="The Godfather", year=1972),
        }
        ref = build_multi_lang_reference(infos, ["de", "en"])
        assert ref is not None
        assert ref.title == "Der Pate"
        assert "The Godfather" in ref.alt_titles

    def test_first_lang_missing_falls_through(self) -> None:
        infos: dict[str, TitleMatchInfo | None] = {
            "de": None,
            "en": TitleMatchInfo(title="The Godfather", year=1972),
        }
        ref = build_multi_lang_reference(infos, ["de", "en"])
        assert ref is not None
        assert ref.title == "The Godfather"

    def test_all_langs_missing_returns_none(self) -> None:
        infos: dict[str, TitleMatchInfo | None] = {"de": None, "en": None}
        ref = build_multi_lang_reference(infos, ["de", "en"])
        assert ref is None

    def test_alt_titles_from_secondary_included(self) -> None:
        infos = {
            "de": TitleMatchInfo(title="Der Pate", year=1972),
            "en": TitleMatchInfo(
                title="The Godfather",
                year=1972,
                alt_titles=["Il Padrino"],
            ),
        }
        ref = build_multi_lang_reference(infos, ["de", "en"])
        assert ref is not None
        assert "The Godfather" in ref.alt_titles
        assert "Il Padrino" in ref.alt_titles

    def test_duplicate_titles_not_repeated(self) -> None:
        infos = {
            "de": TitleMatchInfo(title="Dune", year=2021),
            "en": TitleMatchInfo(title="Dune", year=2021),
        }
        ref = build_multi_lang_reference(infos, ["de", "en"])
        assert ref is not None
        assert ref.title == "Dune"
        # Same title shouldn't appear in alt_titles
        assert "Dune" not in ref.alt_titles

    def test_content_type_preserved(self) -> None:
        infos = {
            "de": TitleMatchInfo(title="Der Pate", year=1972, content_type="movie"),
        }
        ref = build_multi_lang_reference(infos, ["de"])
        assert ref is not None
        assert ref.content_type == "movie"
