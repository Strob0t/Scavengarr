"""Tests for picking the search hits worth scraping (plugins/relevance.py)."""

from __future__ import annotations

from scavengarr.infrastructure.plugins.relevance import (
    SINGLE_TITLE_HITS,
    hit_title,
    query_words,
    relevant_hits,
)


def _titles(hits: list[dict[str, str]]) -> list[str]:
    return [hit["title"] for hit in hits]


class TestQueryWords:
    def test_folds_case_accents_and_punctuation(self) -> None:
        assert query_words("Pokémon: Die Serie!") == {"pokemon", "die", "serie"}

    def test_empty(self) -> None:
        assert query_words("  ") == set()


class TestRelevantHits:
    """Site searches also list loose matches ("Breaking Bad" finds "Better
    Call Saul"); scraping each one's detail pages costs seconds."""

    def test_keeps_hits_containing_every_query_word(self) -> None:
        hits = [
            {"title": "Better Call Saul"},
            {"title": "Breaking Bad"},
            {"title": "El Camino: Ein Breaking Bad Film"},
        ]
        result = relevant_hits(hits, "Breaking Bad", lambda h: h["title"])
        assert _titles(result) == ["Breaking Bad", "El Camino: Ein Breaking Bad Film"]

    def test_falls_back_to_the_sites_top_hits(self) -> None:
        # Other-language titles: "Money Heist" is "Haus des Geldes" there
        hits = [{"title": f"Serie {i}"} for i in range(6)]
        result = relevant_hits(hits, "Money Heist", lambda h: h["title"], fallback=3)
        assert result == hits[:3]

    def test_empty_query_keeps_everything(self) -> None:
        hits = [{"title": "A"}, {"title": "B"}]
        assert relevant_hits(hits, "", lambda h: h["title"]) == hits

    def test_closest_titles_first(self) -> None:
        # "Dark" also finds "Dark Matter" and "The Dark Crystal"
        hits = [
            {"title": "The Dark Crystal: Age of Resistance"},
            {"title": "Dark Matter"},
            {"title": "Dark"},
        ]
        result = relevant_hits(hits, "Dark", hit_title)
        assert _titles(result) == [
            "Dark",
            "Dark Matter",
            "The Dark Crystal: Age of Resistance",
        ]

    def test_limit_for_requests_of_one_title(self) -> None:
        hits = [{"title": f"Dark {i}"} for i in range(6)]
        result = relevant_hits(hits, "Dark", hit_title, limit=SINGLE_TITLE_HITS)
        assert _titles(result) == ["Dark 0", "Dark 1", "Dark 2"]

    def test_exact_hit_alone_for_requests_of_one_title(self) -> None:
        """ "Dark" S01E01: "Dark Matter" and "Dark Winds" are other series, and
        each costs a series, a season and an episode page plus link-outs."""
        hits = [{"title": "Dark Matter"}, {"title": "dark"}, {"title": "Dark Winds"}]
        result = relevant_hits(hits, "Dark", hit_title, limit=SINGLE_TITLE_HITS)
        assert _titles(result) == ["dark"]

    def test_exact_hit_keeps_the_others_without_limit(self) -> None:
        # Films and Torznab searches: the caller filters the titles itself
        hits = [{"title": "Dark Matter"}, {"title": "Dark"}]
        assert _titles(relevant_hits(hits, "Dark", hit_title)) == [
            "Dark",
            "Dark Matter",
        ]

    def test_a_year_suffix_is_no_title_word(self) -> None:
        """ "One Piece (2023)" next to "One Piece": both are exact hits for the
        one-title request, and the title matcher decides by the year."""
        hits = [
            {"title": "One Piece (2023)"},
            {"title": "LEGO One Piece"},
            {"title": "One Piece"},
        ]
        result = relevant_hits(hits, "One Piece", hit_title, limit=SINGLE_TITLE_HITS)
        assert _titles(result) == ["One Piece (2023)", "One Piece"]

    def test_a_year_inside_the_title_stays_a_word(self) -> None:
        hits = [{"title": "Blade Runner 2049"}, {"title": "Blade Runner"}]
        result = relevant_hits(hits, "Blade Runner", hit_title, limit=SINGLE_TITLE_HITS)
        assert _titles(result) == ["Blade Runner"]
        assert _titles(relevant_hits(hits, "Blade Runner 2049", hit_title)) == [
            "Blade Runner 2049"
        ]

    def test_hit_title_of_parsed_hits(self) -> None:
        hits = [{"title": "Iron Man", "url": "u1"}, {"url": "u2"}]
        assert relevant_hits(hits, "iron man", hit_title) == hits[:1]
