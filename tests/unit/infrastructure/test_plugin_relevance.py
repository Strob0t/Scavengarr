"""Tests for picking the search hits worth scraping (plugins/relevance.py)."""

from __future__ import annotations

from scavengarr.infrastructure.plugins.relevance import (
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

    def test_hit_title_of_parsed_hits(self) -> None:
        hits = [{"title": "Iron Man", "url": "u1"}, {"url": "u2"}]
        assert relevant_hits(hits, "iron man", hit_title) == hits[:1]
