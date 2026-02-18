"""Title-match scoring for Stremio stream results.

Pure transformation logic — no I/O, no framework dependencies.
Compares plugin SearchResult titles against a reference TitleMatchInfo
to filter out wrong titles (sequels, spin-offs, unrelated results).

Uses **rapidfuzz** for fast, robust fuzzy matching (C++ backend).
"""

from __future__ import annotations

import re

import structlog
from guessit import guessit
from rapidfuzz import fuzz
from unidecode import unidecode as _unidecode

from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult

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
    year_penalty: float = 0.3,
    sequel_penalty: float = 0.35,
) -> float:
    """Score one normalised reference title against a normalised result.

    Uses ``rapidfuzz.fuzz.token_sort_ratio`` (handles reordering) and
    ``rapidfuzz.fuzz.token_set_ratio`` (handles subsets like "Dune" vs
    "Dune Part One") — whichever is higher becomes the base score.
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
    score = max(sort_score, set_score)

    # --- year handling ---
    if reference_year is not None and result_year is not None:
        if abs(reference_year - result_year) <= year_tolerance:
            score += year_bonus
        else:
            score -= year_penalty

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
        _add(guessit(result.title).get("title"))

    # 3. guessit-parsed title from release_name
    if result.release_name:
        _add(guessit(result.release_name).get("title"))

    # 4. raw release_name (normalised) as fallback
    _add(result.release_name)

    return candidates


def _extract_result_year(result: SearchResult) -> int | None:
    """Extract a year from title, release_name, or guessit parsing."""
    year = _extract_year(result.title)
    if year is not None:
        return year

    if result.release_name:
        year = _extract_year(result.release_name)
        if year is not None:
            return year

    for src in (result.title, result.release_name):
        if src:
            guess_year = guessit(src).get("year")
            if guess_year:
                return int(guess_year)

    return None


def score_title_match(
    result: SearchResult,
    reference: TitleMatchInfo,
    *,
    year_bonus: float = 0.2,
    year_penalty: float = 0.3,
    sequel_penalty: float = 0.35,
    year_tolerance_movie: int = 1,
    year_tolerance_series: int = 3,
) -> float:
    """Score how well *result* matches *reference* (0.0–~1.2).

    Generates multiple title candidates from ``result.title`` and
    ``result.release_name`` (including ``guessit``-parsed clean titles)
    and scores each against all reference titles (primary + alt_titles).
    The best score across all combinations is returned.

    Components per title variant:

    - Base: ``max(token_sort_ratio, token_set_ratio)`` via rapidfuzz
    - Year bonus: +*year_bonus* if year matches (tolerance by type)
    - Year penalty: −*year_penalty* if year present but wrong
    - Sequel penalty: −*sequel_penalty* if sequel numbers differ
    """
    candidates = _extract_title_candidates(result)
    if not candidates:
        return 0.0

    result_year = _extract_result_year(result)

    year_tolerance = (
        year_tolerance_series
        if reference.content_type == "series"
        else year_tolerance_movie
    )

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
                year_penalty=year_penalty,
                sequel_penalty=sequel_penalty,
            )
            if s > best:
                best = s

    return best


def filter_by_title_match(
    results: list[SearchResult],
    reference: TitleMatchInfo | None,
    threshold: float = 0.7,
    *,
    year_bonus: float = 0.2,
    year_penalty: float = 0.3,
    sequel_penalty: float = 0.35,
    year_tolerance_movie: int = 1,
    year_tolerance_series: int = 3,
) -> list[SearchResult]:
    """Keep only results whose title score meets *threshold*.

    If *reference* is ``None`` (title lookup failed), all results pass
    through unchanged — better to return unfiltered than nothing.
    """
    if reference is None:
        return results

    kept: list[SearchResult] = []
    dropped = 0

    for r in results:
        s = score_title_match(
            r,
            reference,
            year_bonus=year_bonus,
            year_penalty=year_penalty,
            sequel_penalty=sequel_penalty,
            year_tolerance_movie=year_tolerance_movie,
            year_tolerance_series=year_tolerance_series,
        )
        if s >= threshold:
            kept.append(r)
        else:
            dropped += 1
            log.debug(
                "title_match_filtered",
                result_title=r.title,
                score=round(s, 3),
                threshold=threshold,
            )

    log.info(
        "title_match_summary",
        reference=reference.title,
        total=len(results),
        kept=len(kept),
        dropped=dropped,
        threshold=threshold,
    )
    return kept
