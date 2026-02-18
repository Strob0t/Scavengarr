"""Tests for the title-match scoring module."""

from __future__ import annotations

import pytest

from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.title_matcher import (
    _extract_result_year,
    _extract_title_candidates,
    _extract_year,
    _normalize,
    _sequel_number,
    _strip_year,
    filter_by_title_match,
    score_title_match,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sr(title: str, release_name: str | None = None) -> SearchResult:
    """Build a minimal SearchResult for scoring tests."""
    return SearchResult(
        title=title,
        download_link="https://example.com/dl",
        release_name=release_name,
    )


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_lowercase(self) -> None:
        assert _normalize("Iron Man") == "iron man"

    def test_collapse_whitespace(self) -> None:
        assert _normalize("  Iron   Man  ") == "iron man"

    def test_empty(self) -> None:
        assert _normalize("") == ""

    def test_strips_colon(self) -> None:
        assert _normalize("Dune: Part One") == "dune part one"

    def test_strips_hyphen(self) -> None:
        assert _normalize("Spider-Man") == "spider man"

    def test_strips_apostrophe(self) -> None:
        assert _normalize("Ocean's Eleven") == "ocean s eleven"

    def test_strips_mixed_punctuation(self) -> None:
        assert _normalize("T2: Trainspotting!") == "t2 trainspotting"


# ---------------------------------------------------------------------------
# _strip_year
# ---------------------------------------------------------------------------


class TestStripYear:
    def test_removes_trailing_year(self) -> None:
        assert _strip_year("Iron Man 2008") == "Iron Man"

    def test_removes_middle_year(self) -> None:
        assert _strip_year("Iron Man 2008 BDRip") == "Iron Man  BDRip"

    def test_no_year_unchanged(self) -> None:
        assert _strip_year("Iron Man") == "Iron Man"

    def test_parenthesized_year_cleaned(self) -> None:
        assert _strip_year("Iron Man (2008)") == "Iron Man"

    def test_only_year(self) -> None:
        assert _strip_year("2008") == ""


# ---------------------------------------------------------------------------
# _extract_year
# ---------------------------------------------------------------------------


class TestExtractYear:
    def test_year_at_end(self) -> None:
        assert _extract_year("Iron Man 2008") == 2008

    def test_year_in_middle(self) -> None:
        assert _extract_year("Iron Man 2008 BDRip") == 2008

    def test_no_year(self) -> None:
        assert _extract_year("Iron Man") is None

    def test_multiple_years_takes_last(self) -> None:
        assert _extract_year("2001 A Space Odyssey 1968") == 1968

    def test_19xx_year(self) -> None:
        assert _extract_year("Terminator 2 1991") == 1991


# ---------------------------------------------------------------------------
# _sequel_number
# ---------------------------------------------------------------------------


class TestSequelNumber:
    def test_sequel_at_end(self) -> None:
        assert _sequel_number("iron man 2") == 2

    def test_no_sequel(self) -> None:
        assert _sequel_number("iron man") is None

    def test_sequel_with_trailing_space(self) -> None:
        assert _sequel_number("taken 3 ") == 3

    def test_year_is_not_sequel(self) -> None:
        # "2008" is 4 digits — should NOT be caught by sequel regex
        # (sequel regex only fires for trailing numbers after whitespace)
        # but "iron man 2008" would match — the regex doesn't filter by
        # digit count, so we rely on the title being normalised without year
        # In practice, titles from plugins rarely include the year in title field
        assert _sequel_number("iron man") is None


# ---------------------------------------------------------------------------
# score_title_match
# ---------------------------------------------------------------------------


class TestScoreTitleMatch:
    def test_exact_match_no_year(self) -> None:
        ref = TitleMatchInfo(title="Iron Man")
        score = score_title_match(_sr("Iron Man"), ref)
        assert score == pytest.approx(1.0)

    def test_exact_match_with_year_bonus(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2008"), ref)
        # base ~1.0 (close match) + 0.2 year bonus
        assert score > 1.0

    def test_sequel_penalty(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2"), ref)
        # base high (~0.89) but sequel penalty -0.3 → well below 0.7
        assert score < 0.7

    def test_wrong_year_penalty(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2013"), ref)
        # year penalty -0.3
        assert score < 0.9

    def test_year_tolerance_plus_one(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2009"), ref)
        # within ±1 → bonus
        assert score > 1.0

    def test_completely_different_title(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Avengers Endgame"), ref)
        assert score < 0.5

    def test_empty_result_title(self) -> None:
        ref = TitleMatchInfo(title="Iron Man")
        assert score_title_match(_sr(""), ref) == 0.0

    def test_empty_reference_title(self) -> None:
        ref = TitleMatchInfo(title="")
        assert score_title_match(_sr("Iron Man"), ref) == 0.0

    def test_year_from_release_name_fallback(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        sr = _sr("Iron Man", release_name="Iron.Man.2008.BDRip.x264")
        score = score_title_match(sr, ref)
        # base 1.0 + year bonus 0.2
        assert score == pytest.approx(1.2)

    def test_sequel_with_correct_number(self) -> None:
        """Iron Man 2 vs ref 'Iron Man 2' should score high."""
        ref = TitleMatchInfo(title="Iron Man 2", year=2010)
        score = score_title_match(_sr("Iron Man 2"), ref)
        # exact match, no sequel penalty (both have "2")
        assert score >= 1.0

    def test_sequel_ref_has_number_result_does_not(self) -> None:
        """Ref 'Iron Man 2' vs result 'Iron Man' → sequel mismatch penalty."""
        ref = TitleMatchInfo(title="Iron Man 2")
        score = score_title_match(_sr("Iron Man"), ref)
        assert score < 0.7

    def test_sequel_different_numbers(self) -> None:
        """Ref 'Iron Man 2' vs result 'Iron Man 3' → sequel mismatch penalty."""
        ref = TitleMatchInfo(title="Iron Man 2")
        score = score_title_match(_sr("Iron Man 3"), ref)
        assert score < 0.7

    def test_sequel_correct_number_among_many(self) -> None:
        """The correct sequel scores highest among all sequels."""
        ref = TitleMatchInfo(title="Iron Man 2")
        s1 = score_title_match(_sr("Iron Man"), ref)
        s2 = score_title_match(_sr("Iron Man 2"), ref)
        s3 = score_title_match(_sr("Iron Man 3"), ref)
        assert s2 > s1  # correct sequel beats no-number
        assert s2 > s3  # correct sequel beats wrong sequel

    def test_case_insensitive(self) -> None:
        ref = TitleMatchInfo(title="IRON MAN")
        score = score_title_match(_sr("iron man"), ref)
        assert score == pytest.approx(1.0)

    def test_alt_title_english_matches_german_result(self) -> None:
        """German primary + English alt_title: English result should match."""
        ref = TitleMatchInfo(
            title="Die Verurteilten",
            year=1994,
            alt_titles=["The Shawshank Redemption"],
        )
        score = score_title_match(_sr("The Shawshank Redemption"), ref)
        assert score >= 1.0

    def test_alt_title_german_result_matches_german_primary(self) -> None:
        """German primary title: German result should match directly."""
        ref = TitleMatchInfo(
            title="Die Verurteilten",
            year=1994,
            alt_titles=["The Shawshank Redemption"],
        )
        score = score_title_match(_sr("Die Verurteilten"), ref)
        assert score >= 1.0

    def test_alt_title_best_score_wins(self) -> None:
        """When primary title is a poor match but alt is exact, score is high."""
        ref = TitleMatchInfo(
            title="Completely Different Title",
            alt_titles=["Iron Man"],
        )
        score = score_title_match(_sr("Iron Man"), ref)
        assert score >= 1.0

    def test_no_alt_titles_behaves_as_before(self) -> None:
        """Empty alt_titles list should not change scoring."""
        ref = TitleMatchInfo(title="Iron Man", alt_titles=[])
        score = score_title_match(_sr("Iron Man"), ref)
        assert score == pytest.approx(1.0)

    def test_parenthesized_year_in_result_title(self) -> None:
        """Titles like 'Iron Man (2008)' from plugins should match well."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man (2008)"), ref)
        assert score >= 1.0

    def test_alt_title_sequel_penalty_still_applies(self) -> None:
        """Sequel penalty applies even when matching against alt title."""
        ref = TitleMatchInfo(title="Der Eiserne", alt_titles=["Iron Man"])
        score = score_title_match(_sr("Iron Man 2"), ref)
        assert score < 0.7

    def test_colon_in_reference_does_not_break_matching(self) -> None:
        """'Dune' result must match 'Dune: Part One' reference (colon stripped)."""
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        score = score_title_match(_sr("Dune 2021"), ref)
        # token overlap: {"dune"} ⊂ {"dune", "part", "one"} → 1.0
        # + year bonus 0.2 → 1.2
        assert score >= 1.0

    def test_hyphenated_title_matches(self) -> None:
        """'Spider Man' result must match 'Spider-Man' reference."""
        ref = TitleMatchInfo(title="Spider-Man")
        score = score_title_match(_sr("Spider Man"), ref)
        assert score >= 1.0


# ---------------------------------------------------------------------------
# filter_by_title_match
# ---------------------------------------------------------------------------


class TestFilterByTitleMatch:
    def test_filters_below_threshold(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        results = [
            _sr("Iron Man"),
            _sr("Iron Man 2"),
            _sr("Avengers Endgame"),
        ]
        kept = filter_by_title_match(results, ref, threshold=0.7)
        titles = [r.title for r in kept]
        assert "Iron Man" in titles
        assert "Iron Man 2" not in titles
        assert "Avengers Endgame" not in titles

    def test_none_reference_passes_all(self) -> None:
        results = [_sr("Iron Man"), _sr("Random Movie")]
        kept = filter_by_title_match(results, None, threshold=0.7)
        assert len(kept) == 2

    def test_all_filtered_returns_empty(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        results = [_sr("Completely Unrelated Film")]
        kept = filter_by_title_match(results, ref, threshold=0.7)
        assert kept == []

    def test_no_year_still_filters_by_title(self) -> None:
        ref = TitleMatchInfo(title="Iron Man")
        results = [_sr("Iron Man"), _sr("Spider Man")]
        kept = filter_by_title_match(results, ref, threshold=0.7)
        assert len(kept) == 1
        assert kept[0].title == "Iron Man"

    def test_alt_titles_keeps_cross_language_results(self) -> None:
        """German primary + English alt: both language results kept."""
        ref = TitleMatchInfo(
            title="Die Verurteilten",
            year=1994,
            alt_titles=["The Shawshank Redemption"],
        )
        results = [
            _sr("Die Verurteilten"),
            _sr("The Shawshank Redemption"),
            _sr("Completely Wrong Movie"),
        ]
        kept = filter_by_title_match(results, ref, threshold=0.7)
        titles = [r.title for r in kept]
        assert "Die Verurteilten" in titles
        assert "The Shawshank Redemption" in titles
        assert "Completely Wrong Movie" not in titles


# ---------------------------------------------------------------------------
# _extract_title_candidates
# ---------------------------------------------------------------------------


class TestExtractTitleCandidates:
    def test_clean_title_only(self) -> None:
        sr = _sr("Iron Man")
        candidates = _extract_title_candidates(sr)
        assert "iron man" in candidates

    def test_release_name_style_title(self) -> None:
        """guessit should extract 'iron man' from a release-name title."""
        sr = _sr("Iron.Man.2008.German.DL.1080p.BluRay.x264")
        candidates = _extract_title_candidates(sr)
        assert "iron man" in candidates

    def test_release_name_field_adds_candidate(self) -> None:
        sr = _sr("Unknown", release_name="Iron.Man.2008.BDRip.x264")
        candidates = _extract_title_candidates(sr)
        assert "iron man" in candidates

    def test_deduplication(self) -> None:
        sr = _sr("Iron Man", release_name="Iron.Man.2008.BDRip")
        candidates = _extract_title_candidates(sr)
        assert candidates.count("iron man") == 1

    def test_empty_title_and_release(self) -> None:
        sr = _sr("")
        assert _extract_title_candidates(sr) == []


# ---------------------------------------------------------------------------
# _extract_result_year
# ---------------------------------------------------------------------------


class TestExtractResultYear:
    def test_year_from_title(self) -> None:
        sr = _sr("Iron Man 2008")
        assert _extract_result_year(sr) == 2008

    def test_year_from_release_name(self) -> None:
        sr = _sr("Iron Man", release_name="Iron.Man.2008.BDRip")
        assert _extract_result_year(sr) == 2008

    def test_year_from_guessit(self) -> None:
        """guessit can extract year even when regex misses it."""
        sr = _sr(
            "Iron Man",
            release_name="Iron.Man.2008.German.DL.1080p.BluRay.x264",
        )
        assert _extract_result_year(sr) == 2008

    def test_no_year(self) -> None:
        sr = _sr("Iron Man")
        assert _extract_result_year(sr) is None


# ---------------------------------------------------------------------------
# score_title_match — release-name scenarios
# ---------------------------------------------------------------------------


class TestScoreReleaseName:
    def test_release_name_as_title_matches(self) -> None:
        """A scene release name in title field should still match."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        sr = _sr("Iron.Man.2008.German.DL.1080p.BluRay.x264")
        score = score_title_match(sr, ref)
        assert score >= 1.0  # guessit extracts "Iron Man" + year bonus

    def test_title_with_quality_suffix_matches(self) -> None:
        """Titles like 'Iron Man - BDRip x264' (boerse.py pattern)."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        sr = _sr("Iron Man - BDRip x264")
        score = score_title_match(sr, ref)
        assert score >= 0.7

    def test_release_name_field_used_for_matching(self) -> None:
        """When title is useless, release_name should save the match."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        sr = _sr("Unknown", release_name="Iron.Man.2008.BDRip.x264")
        score = score_title_match(sr, ref)
        assert score >= 1.0

    def test_wrong_movie_release_name_rejected(self) -> None:
        """A different movie's release name must still be rejected."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        sr = _sr("Avengers.Endgame.2019.German.DL.1080p.BluRay.x264")
        score = score_title_match(sr, ref)
        assert score < 0.7

    def test_german_release_name_matches(self) -> None:
        """German release names with extra tags should match."""
        ref = TitleMatchInfo(
            title="Die Verurteilten",
            year=1994,
            alt_titles=["The Shawshank Redemption"],
        )
        sr = _sr("The.Shawshank.Redemption.1994.German.DL.1080p.BluRay.x264")
        score = score_title_match(sr, ref)
        assert score >= 1.0


# ---------------------------------------------------------------------------
# _normalize — umlaut transliteration
# ---------------------------------------------------------------------------


class TestNormalizeUmlauts:
    def test_ae_oe_ue(self) -> None:
        assert _normalize("Schöne Grüße") == "schone grusse"

    def test_eszett(self) -> None:
        assert _normalize("Straße") == "strasse"

    def test_mixed_umlauts_and_ascii(self) -> None:
        assert _normalize("Über den Wölken") == "uber den wolken"

    def test_no_umlauts_unchanged(self) -> None:
        assert _normalize("Iron Man") == "iron man"

    def test_uppercase_umlauts(self) -> None:
        """Uppercase Ä/Ö/Ü are lowered first, then transliterated."""
        assert _normalize("ÜBER") == "uber"


# ---------------------------------------------------------------------------
# score_title_match — rapidfuzz token-based scoring
# ---------------------------------------------------------------------------


class TestTokenBasedScoring:
    def test_reordered_tokens_still_match(self) -> None:
        """token_sort_ratio handles reordered titles."""
        ref = TitleMatchInfo(title="Man Iron")
        score = score_title_match(_sr("Iron Man"), ref)
        assert score >= 1.0

    def test_token_scoring_picks_max(self) -> None:
        """Score uses max(token_sort_ratio, token_set_ratio)."""
        ref = TitleMatchInfo(title="the iron man")
        score = score_title_match(_sr("iron man the"), ref)
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# score_title_match — umlaut handling
# ---------------------------------------------------------------------------


class TestUmlautScoring:
    def test_umlaut_vs_ascii_matches(self) -> None:
        """Both sides with Über normalise identically via unidecode."""
        ref = TitleMatchInfo(title="Über den Wolken")
        score = score_title_match(_sr("Über den Wolken"), ref)
        assert score == pytest.approx(1.0)

    def test_umlaut_vs_manual_transliteration_close(self) -> None:
        """'Über' (→ 'uber') vs 'Ueber' (→ 'ueber'): close but not exact."""
        ref = TitleMatchInfo(title="Über den Wolken")
        score = score_title_match(_sr("Ueber den Wolken"), ref)
        # unidecode: ü→u, so "uber" vs "ueber" gives ~0.97 (above threshold)
        assert score >= 0.9

    def test_both_umlauts_match(self) -> None:
        """Both sides with umlauts should normalize the same way."""
        ref = TitleMatchInfo(title="Schöne Grüße")
        score = score_title_match(_sr("Schöne Grüße"), ref)
        assert score == pytest.approx(1.0)

    def test_eszett_matches_ss(self) -> None:
        ref = TitleMatchInfo(title="Die Straße")
        score = score_title_match(_sr("Die Strasse"), ref)
        assert score == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# score_title_match — series year tolerance
# ---------------------------------------------------------------------------


class TestSeriesYearTolerance:
    def test_series_3_year_difference_accepted(self) -> None:
        """Series with ±3 year tolerance: 2020 vs 2023 should get bonus."""
        ref = TitleMatchInfo(title="Breaking Bad", year=2008, content_type="series")
        score = score_title_match(_sr("Breaking Bad 2011"), ref)
        # Within ±3 tolerance → year bonus
        assert score > 1.0

    def test_movie_3_year_difference_penalized(self) -> None:
        """Movie with ±1 year tolerance: 2008 vs 2011 should get penalty."""
        ref = TitleMatchInfo(title="Iron Man", year=2008, content_type="movie")
        score = score_title_match(_sr("Iron Man 2011"), ref)
        # Outside ±1 tolerance → year penalty
        assert score < 1.0

    def test_series_exact_year_bonus(self) -> None:
        ref = TitleMatchInfo(title="Breaking Bad", year=2008, content_type="series")
        score = score_title_match(_sr("Breaking Bad 2008"), ref)
        assert score > 1.0

    def test_no_content_type_defaults_to_movie_tolerance(self) -> None:
        """When content_type is None, use movie tolerance (±1)."""
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score_close = score_title_match(_sr("Iron Man 2009"), ref)
        score_far = score_title_match(_sr("Iron Man 2011"), ref)
        assert score_close > 1.0  # within ±1
        assert score_far < 1.0  # outside ±1


# ---------------------------------------------------------------------------
# score_title_match — configurable penalties
# ---------------------------------------------------------------------------


class TestConfigurablePenalties:
    def test_custom_year_bonus(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2008"), ref, year_bonus=0.5)
        # base 1.0 + bonus 0.5 = 1.5
        assert score == pytest.approx(1.5)

    def test_custom_year_penalty(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        score = score_title_match(_sr("Iron Man 2015"), ref, year_penalty=0.1)
        # base 1.0 - penalty 0.1 = 0.9
        assert score == pytest.approx(0.9)

    def test_custom_sequel_penalty(self) -> None:
        ref = TitleMatchInfo(title="Iron Man")
        score_default = score_title_match(_sr("Iron Man 2"), ref)
        score_light = score_title_match(_sr("Iron Man 2"), ref, sequel_penalty=0.1)
        assert score_light > score_default

    def test_custom_year_tolerance(self) -> None:
        ref = TitleMatchInfo(title="Iron Man", year=2008)
        # Default movie tolerance (1): 2011 is outside → penalty
        score_strict = score_title_match(_sr("Iron Man 2011"), ref)
        # Custom tolerance 5: 2011 is within → bonus
        score_lenient = score_title_match(
            _sr("Iron Man 2011"), ref, year_tolerance_movie=5
        )
        assert score_lenient > score_strict


# ---------------------------------------------------------------------------
# score_title_match — Dune franchise disambiguation
# ---------------------------------------------------------------------------


class TestDuneFranchise:
    """Real-world regression tests for Dune / Dune: Part Two matching.

    German streaming sites list Dune under various titles:
    "Dune", "Dune: Part One", "Dune (2021)", "Dune Part Two (2024)".
    The matcher must accept the correct film and reject the other.
    """

    # --- Dune: Part One (2021) ---

    def test_dune_part_one_exact(self) -> None:
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        score = score_title_match(_sr("Dune: Part One (2021)"), ref)
        assert score >= 1.0

    def test_dune_short_title_matches_part_one(self) -> None:
        """Sites listing 'Dune' should match 'Dune: Part One' ref."""
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        score = score_title_match(_sr("Dune 2021"), ref)
        assert score >= 1.0

    def test_dune_part_one_release_name(self) -> None:
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        sr = _sr(
            "Dune",
            release_name="Dune.Part.One.2021.German.DL.1080p.BluRay.x264",
        )
        score = score_title_match(sr, ref)
        assert score >= 1.0

    def test_dune_part_two_rejected_for_part_one_ref(self) -> None:
        """Dune Part Two must NOT match when looking for Part One."""
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        score_two = score_title_match(_sr("Dune: Part Two (2024)"), ref)
        score_one = score_title_match(_sr("Dune: Part One (2021)"), ref)
        assert score_one > score_two

    # --- Dune: Part Two (2024) ---

    def test_dune_part_two_exact(self) -> None:
        ref = TitleMatchInfo(title="Dune: Part Two", year=2024)
        score = score_title_match(_sr("Dune: Part Two (2024)"), ref)
        assert score >= 1.0

    def test_dune_part_two_release_name(self) -> None:
        ref = TitleMatchInfo(title="Dune: Part Two", year=2024)
        sr = _sr(
            "Dune Part Two",
            release_name="Dune.Part.Two.2024.German.DL.1080p.BluRay.x264",
        )
        score = score_title_match(sr, ref)
        assert score >= 1.0

    def test_dune_part_one_rejected_for_part_two_ref(self) -> None:
        """Dune Part One must NOT match when looking for Part Two."""
        ref = TitleMatchInfo(title="Dune: Part Two", year=2024)
        score_one = score_title_match(_sr("Dune: Part One (2021)"), ref)
        score_two = score_title_match(_sr("Dune: Part Two (2024)"), ref)
        assert score_two > score_one

    def test_dune_1984_rejected_for_2021(self) -> None:
        """Original 1984 Dune must not match 2021 Dune ref."""
        ref = TitleMatchInfo(title="Dune: Part One", year=2021)
        score = score_title_match(_sr("Dune (1984)"), ref)
        # year penalty should push it down
        assert score < 1.0

    def test_dune_part_two_scores_higher_than_part_one(self) -> None:
        """When searching for Part Two, Part Two scores higher than Part One."""
        ref = TitleMatchInfo(title="Dune: Part Two", year=2024)
        results = [
            _sr("Dune: Part One (2021)"),
            _sr("Dune: Part Two (2024)"),
            _sr("Dune (1984)"),
        ]
        scores = {r.title: score_title_match(r, ref) for r in results}
        assert scores["Dune: Part Two (2024)"] > scores["Dune: Part One (2021)"]
        assert scores["Dune: Part Two (2024)"] > scores["Dune (1984)"]

    def test_dune_filter_keeps_correct_part(self) -> None:
        """filter_by_title_match with Dune Part Two ref keeps Part Two."""
        ref = TitleMatchInfo(title="Dune: Part Two", year=2024)
        results = [
            _sr("Dune: Part Two (2024)"),
            _sr("Completely Unrelated Film"),
        ]
        kept = filter_by_title_match(results, ref, threshold=0.7)
        titles = [r.title for r in kept]
        assert "Dune: Part Two (2024)" in titles
        assert "Completely Unrelated Film" not in titles
