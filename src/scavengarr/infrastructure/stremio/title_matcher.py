"""Title-match scoring for Stremio stream results.

Pure transformation logic — no I/O, no framework dependencies.
Compares plugin SearchResult titles against a reference TitleMatchInfo
to filter out wrong titles (sequels, spin-offs, unrelated results), and
decides by identity where a result shows one: an IMDb id in its
metadata, a year outside the tolerance, or a category against the
reference's kind (``TitleMatchInfo.animation``). Every verdict names the
rule that decided it (``TitleScore.reason``).

Uses **rapidfuzz** for fast, robust fuzzy matching (C++ backend).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Literal

import structlog
from rapidfuzz import fuzz
from unidecode import unidecode as _unidecode

from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.stremio.release_guess import guess_release

log = structlog.get_logger(__name__)

# Regex: 4-digit year starting with 19xx or 20xx
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Regex: trailing sequel number — 1-2 digits only (e.g. "Iron Man 2", "Taken 3")
# Excludes 4-digit years like "2008".
_SEQUEL_RE = re.compile(r"\s+(\d{1,2})\s*$")

# Matches any character that is NOT a word character or whitespace.
# Used to strip punctuation (colons, hyphens, apostrophes, etc.) so that
# token matching is not broken by e.g. "dune:" vs "dune".
_PUNCT_RE = re.compile(r"[^\w\s]")

# An IMDb title id: "tt0388629", or the digits alone (a site's API may store
# the number, which loses the leading zeros)
_IMDB_RE = re.compile(r"(?:tt)?(\d+)")

# The labels the category rule reads: a plain series and an anime
_CATEGORY_SERIES = 5000
_CATEGORY_ANIME = 5070

TitleReason = Literal["score", "year", "imdb", "category"]


@dataclass(frozen=True)
class TitleScore:
    """A result's score and the rule that decided it: ``imdb`` (the ids
    agree, 1.2, or differ, 0.0), ``category`` (the result's label against
    the reference's kind, 0.0), ``year`` (the result's year outside the
    tolerance, 0.0) or ``score`` (the text rules)."""

    score: float
    reason: TitleReason


def _normalize(text: str) -> str:
    """Lowercase, transliterate Unicode→ASCII, strip punctuation, collapse ws."""
    text = _unidecode(text.lower())
    text = _PUNCT_RE.sub(" ", text)
    return " ".join(text.split())


def _strip_year(text: str) -> str:
    """Remove 4-digit year tokens from text for cleaner title comparison."""
    text = _YEAR_RE.sub("", text)
    # Clean up empty parentheses left after removing year
    text = re.sub(r"\s*\(\s*\)\s*", " ", text)
    return text.strip()


def _extract_year(text: str) -> int | None:
    """Extract the last 4-digit year from text, or None."""
    matches = _YEAR_RE.findall(text)
    return int(matches[-1]) if matches else None


def _sequel_number(title: str) -> int | None:
    """Extract trailing sequel number from a normalised title, or None."""
    m = _SEQUEL_RE.search(title)
    return int(m.group(1)) if m else None


def _score_single_title(
    norm_ref: str,
    norm_res: str,
    *,
    reference_year: int | None,
    result_year: int | None,
    year_tolerance: int = 1,
    year_bonus: float = 0.2,
    sequel_penalty: float = 0.35,
    extra_words_penalty: float = 0.35,
) -> float:
    """Score one normalised reference title against a normalised result.

    Uses ``rapidfuzz.fuzz.token_sort_ratio`` (handles reordering) and
    ``rapidfuzz.fuzz.token_set_ratio`` (handles subsets like "Dune" vs
    "Dune Part One") — whichever is higher becomes the base score.
    The subset score loses *extra_words_penalty* when the result adds
    words to the reference: token_set_ratio rates "Dark Matter" 100
    against "Dark", while a result that only drops words ("Dune" for
    "Dune: Part One") keeps it. A result year within the tolerance adds
    *year_bonus*; one outside it never gets here (``score_title`` drops
    the result).
    """
    if not norm_ref or not norm_res:
        return 0.0

    # rapidfuzz returns 0–100; normalise to 0.0–1.0.
    # processor=None because we already normalised the strings.
    sort_score = (
        fuzz.token_sort_ratio(
            norm_ref,
            norm_res,
            processor=None,
        )
        / 100.0
    )
    set_score = (
        fuzz.token_set_ratio(
            norm_ref,
            norm_res,
            processor=None,
        )
        / 100.0
    )
    if set(norm_res.split()) - set(norm_ref.split()):
        set_score -= extra_words_penalty
    score = max(sort_score, set_score)

    # --- year bonus ---
    if (
        reference_year is not None
        and result_year is not None
        and abs(reference_year - result_year) <= year_tolerance
    ):
        score += year_bonus

    # --- sequel detection ---
    # Penalise ANY mismatch: "Iron Man" vs "Iron Man 2",
    # "Iron Man 2" vs "Iron Man 3", or "Iron Man 2" vs "Iron Man".
    ref_sequel = _sequel_number(norm_ref)
    res_sequel = _sequel_number(norm_res)
    if ref_sequel != res_sequel:
        score -= sequel_penalty

    return score


def _extract_title_candidates(result: SearchResult) -> list[str]:
    """Build normalised title candidates from title and release_name.

    Uses ``guessit`` to extract clean titles from release-name-style
    strings (e.g. ``"Iron.Man.2008.German.DL.1080p.BluRay.x264"`` →
    ``"iron man"``).  Returns deduplicated, non-empty candidates.
    """
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(text: str | None) -> None:
        if not text:
            return
        norm = _normalize(_strip_year(text))
        if norm and norm not in seen:
            seen.add(norm)
            candidates.append(norm)

    # 1. raw title (normalised)
    _add(result.title)

    # 2. guessit-parsed title from result.title
    if result.title:
        _add(guess_release(result.title).get("title"))

    # 3. guessit-parsed title from release_name
    if result.release_name:
        _add(guess_release(result.release_name).get("title"))

    # 4. raw release_name (normalised) as fallback
    _add(result.release_name)

    return candidates


def _extract_result_year(
    result: SearchResult, *, ignore: frozenset[int] = frozenset()
) -> int | None:
    """The result's year: from its title, its release name, guessit on
    either, or ``metadata["year"]`` (an int, or a string of digits), in
    that order. A year in *ignore* (one the reference title itself carries:
    "Blade Runner 2049" is the 2017 film) is a title word, not a year."""
    for text in (result.title, result.release_name):
        years = [int(y) for y in _YEAR_RE.findall(text or "") if int(y) not in ignore]
        if years:
            return years[-1]

    for text in (result.title, result.release_name):
        if text:
            guess_year = guess_release(text).get("year")
            if guess_year and int(guess_year) not in ignore:
                return int(guess_year)

    return _metadata_year(result.metadata.get("year"), ignore)


def _metadata_year(value: object, ignore: frozenset[int]) -> int | None:
    """``metadata["year"]`` as a year: an int, or a string of digits, in
    the years the title regex accepts; anything else counts as none."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    text = str(value).strip()
    if not text.isdigit():
        return None
    year = int(text)
    if not 1900 <= year <= 2099 or year in ignore:
        return None
    return year


def _title_years(reference: TitleMatchInfo) -> frozenset[int]:
    """The years that are words of the reference's titles."""
    titles = [reference.title, *reference.alt_titles]
    return frozenset(int(y) for title in titles for y in _YEAR_RE.findall(title))


def _imdb_key(value: object) -> str | None:
    """``tt`` plus the digits of an IMDb title id, the leading zeros dropped
    ("tt0388629", "0388629" and 388629 give "tt388629"); ``None`` for
    anything else."""
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    found = _IMDB_RE.fullmatch(str(value).strip().lower())
    return f"tt{int(found.group(1))}" if found else None


def _result_imdb(result: SearchResult) -> str | None:
    """The IMDb id the result's metadata names (``imdb`` or ``imdb_id``)."""
    for key in ("imdb", "imdb_id"):
        found = _imdb_key(result.metadata.get(key))
        if found is not None:
            return found
    return None


def _wrong_kind(result: SearchResult, animation: bool | None) -> bool:
    """Whether the result's label contradicts the reference's kind: an anime
    label (5070) for a reference that is not animation, or a plain series
    label (5000) with genres of its own for an animation reference (a site
    that lists genres and did not call the title anime means another
    series). Nothing is contradicted while the kind is unknown."""
    if animation is None:
        return False
    if not animation:
        return result.category == _CATEGORY_ANIME
    return result.category == _CATEGORY_SERIES and bool(result.metadata.get("genres"))


def _identity_verdict(
    result: SearchResult, reference: TitleMatchInfo
) -> TitleScore | None:
    """The verdict the result's identity gives before any text is compared,
    or ``None`` when the text rules decide: the IMDb id alone when both
    sides name one, else the category against the reference's kind."""
    wanted = _imdb_key(reference.imdb_id)
    found = _result_imdb(result)
    if wanted is not None and found is not None:
        return TitleScore(1.2 if found == wanted else 0.0, "imdb")
    if _wrong_kind(result, reference.animation):
        return TitleScore(0.0, "category")
    return None


def score_title(
    result: SearchResult,
    reference: TitleMatchInfo,
    *,
    year_bonus: float = 0.2,
    sequel_penalty: float = 0.35,
    extra_words_penalty: float = 0.35,
    year_tolerance_movie: int = 1,
    year_tolerance_series: int = 3,
) -> TitleScore:
    """Score how well *result* matches *reference* (0.0–~1.2), with the
    rule that decided it.

    The identity rules come first: a result that names an IMDb id is kept
    (1.2) or dropped by the id alone; a label that contradicts the
    reference's kind drops it; a known year outside the tolerance (by type:
    1 year for a movie, 3 for a series) drops it. Then the text rules:
    multiple title candidates from ``result.title`` and
    ``result.release_name`` (including ``guessit``-parsed clean titles),
    each scored against all reference titles (primary + alt_titles); the
    best score across all combinations is returned.

    Components per title variant:

    - Base: ``max(token_sort_ratio, token_set_ratio)`` via rapidfuzz,
      the set ratio minus *extra_words_penalty* if the result adds words
    - Year bonus: +*year_bonus* if the year matches (tolerance by type)
    - Sequel penalty: −*sequel_penalty* if sequel numbers differ
    """
    verdict = _identity_verdict(result, reference)
    if verdict is not None:
        return verdict

    year_tolerance = (
        year_tolerance_series
        if reference.content_type == "series"
        else year_tolerance_movie
    )
    result_year = _extract_result_year(result, ignore=_title_years(reference))
    if (
        reference.year is not None
        and result_year is not None
        and abs(reference.year - result_year) > year_tolerance
    ):
        return TitleScore(0.0, "year")

    candidates = _extract_title_candidates(result)
    if not candidates:
        return TitleScore(0.0, "score")

    all_ref_titles = [reference.title] + list(reference.alt_titles)
    best = 0.0
    for norm_res in candidates:
        for ref_title in all_ref_titles:
            norm_ref = _normalize(_strip_year(ref_title))
            s = _score_single_title(
                norm_ref,
                norm_res,
                reference_year=reference.year,
                result_year=result_year,
                year_tolerance=year_tolerance,
                year_bonus=year_bonus,
                sequel_penalty=sequel_penalty,
                extra_words_penalty=extra_words_penalty,
            )
            if s > best:
                best = s

    return TitleScore(best, "score")


def score_title_match(
    result: SearchResult,
    reference: TitleMatchInfo,
    *,
    year_bonus: float = 0.2,
    sequel_penalty: float = 0.35,
    extra_words_penalty: float = 0.35,
    year_tolerance_movie: int = 1,
    year_tolerance_series: int = 3,
) -> float:
    """The score of ``score_title`` alone."""
    return score_title(
        result,
        reference,
        year_bonus=year_bonus,
        sequel_penalty=sequel_penalty,
        extra_words_penalty=extra_words_penalty,
        year_tolerance_movie=year_tolerance_movie,
        year_tolerance_series=year_tolerance_series,
    ).score


def filter_by_title_match(
    results: list[SearchResult],
    reference: TitleMatchInfo | None,
    threshold: float = 0.7,
    *,
    year_bonus: float = 0.2,
    sequel_penalty: float = 0.35,
    extra_words_penalty: float = 0.35,
    year_tolerance_movie: int = 1,
    year_tolerance_series: int = 3,
) -> list[SearchResult]:
    """Keep only results whose title score meets *threshold*.

    If *reference* is ``None`` (title lookup failed), all results pass
    through unchanged — better to return unfiltered than nothing. Every
    drop logs ``title_match_filtered`` with its ``reason``, and the
    summary counts the drops per reason.
    """
    if reference is None:
        return results

    kept: list[SearchResult] = []
    reasons: Counter[str] = Counter()

    for r in results:
        verdict = score_title(
            r,
            reference,
            year_bonus=year_bonus,
            sequel_penalty=sequel_penalty,
            extra_words_penalty=extra_words_penalty,
            year_tolerance_movie=year_tolerance_movie,
            year_tolerance_series=year_tolerance_series,
        )
        if verdict.score >= threshold:
            kept.append(r)
        else:
            reasons[verdict.reason] += 1
            log.debug(
                "title_match_filtered",
                result_title=r.title,
                score=round(verdict.score, 3),
                threshold=threshold,
                reason=verdict.reason,
            )

    log.info(
        "title_match_summary",
        reference=reference.title,
        total=len(results),
        kept=len(kept),
        dropped=sum(reasons.values()),
        reasons=dict(reasons),
        threshold=threshold,
    )
    return kept
