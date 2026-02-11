"""Title-match scoring for Stremio stream results.

Pure transformation logic — no I/O, no framework dependencies.
Compares plugin SearchResult titles against a reference TitleMatchInfo
to filter out wrong titles (sequels, spin-offs, unrelated results).
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

import structlog

from scavengarr.domain.entities.stremio import TitleMatchInfo
from scavengarr.domain.plugins.base import SearchResult

log = structlog.get_logger(__name__)

# Regex: 4-digit year starting with 19xx or 20xx
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Regex: trailing sequel number — 1-2 digits only (e.g. "Iron Man 2", "Taken 3")
# Excludes 4-digit years like "2008".
_SEQUEL_RE = re.compile(r"\s+(\d{1,2})\s*$")


def _normalize(text: str) -> str:
    """Lowercase + collapse whitespace."""
    return " ".join(text.lower().split())


def _strip_year(text: str) -> str:
    """Remove 4-digit year tokens from text for cleaner title comparison."""
    return _YEAR_RE.sub("", text).strip()


def _extract_year(text: str) -> int | None:
    """Extract the last 4-digit year from text, or None."""
    matches = _YEAR_RE.findall(text)
    return int(matches[-1]) if matches else None


def _sequel_number(title: str) -> int | None:
    """Extract trailing sequel number from a normalised title, or None."""
    m = _SEQUEL_RE.search(title)
    return int(m.group(1)) if m else None


def score_title_match(
    result: SearchResult,
    reference: TitleMatchInfo,
) -> float:
    """Score how well *result* matches *reference* (0.0–~1.2).

    Components:
    - Base: ``SequenceMatcher.ratio()`` on normalised titles (0.0–1.0)
    - Year bonus: +0.2 if year matches (±1 tolerance)
    - Year penalty: −0.3 if year present but wrong
    - Sequel penalty: −0.3 if result has sequel number that reference lacks
    """
    norm_ref = _normalize(_strip_year(reference.title))
    norm_res = _normalize(_strip_year(result.title))

    if not norm_ref or not norm_res:
        return 0.0

    # --- base similarity ---
    score = SequenceMatcher(None, norm_ref, norm_res).ratio()

    # --- year handling ---
    result_year = _extract_year(result.title)
    if result_year is None and result.release_name:
        result_year = _extract_year(result.release_name)

    if reference.year is not None and result_year is not None:
        if abs(reference.year - result_year) <= 1:
            score += 0.2
        else:
            score -= 0.3

    # --- sequel detection (on year-stripped normalised titles) ---
    ref_sequel = _sequel_number(norm_ref)
    res_sequel = _sequel_number(norm_res)
    if res_sequel is not None and ref_sequel is None:
        score -= 0.3

    return score


def filter_by_title_match(
    results: list[SearchResult],
    reference: TitleMatchInfo | None,
    threshold: float = 0.7,
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
        s = score_title_match(r, reference)
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
