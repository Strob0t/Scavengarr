"""Shared constants for Python plugins."""

from __future__ import annotations

from contextvars import ContextVar

# Stremio sets this to limit pagination (e.g. 100 instead of 1000).
# Plugins use ``effective_max_results`` which respects this ContextVar.
search_max_results: ContextVar[int | None] = ContextVar(
    "search_max_results", default=None
)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

DEFAULT_MAX_CONCURRENT = 3
DEFAULT_MAX_RESULTS = 1000
DEFAULT_CLIENT_TIMEOUT = 15.0
DEFAULT_DOMAIN_CHECK_TIMEOUT = 5.0

# Torznab category ranges
TV_CATEGORY_RANGE = range(5000, 6000)
MOVIE_CATEGORY_RANGE = range(2000, 3000)


def is_tv_category(cat: int) -> bool:
    """Check if a Torznab category is in the TV range (5000-5999)."""
    return cat in TV_CATEGORY_RANGE


def is_movie_category(cat: int) -> bool:
    """Check if a Torznab category is in the Movie range (2000-2999)."""
    return cat in MOVIE_CATEGORY_RANGE
