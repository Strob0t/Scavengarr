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

DEFAULT_MAX_CONCURRENT = 5
DEFAULT_MAX_RESULTS = 1000
DEFAULT_CLIENT_TIMEOUT = 15.0
DEFAULT_DOMAIN_CHECK_TIMEOUT = 5.0
