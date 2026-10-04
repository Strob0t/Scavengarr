"""guessit's parse of a release name, once per name.

guessit was the biggest Python CPU cost of a stream request on a Raspberry
Pi: 25-33% (docs/plans/pi-performance.md). The title matcher, the release
parser and the episode filter each parsed the same names, and the next
request for the same title parsed them all again. The parse depends on the
name alone, so the result is cached; it is shared and therefore read-only.
"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType
from typing import Any

from guessit import guessit

# Release names of a few hundred stream requests (a request has 10-300)
_CACHE_SIZE = 4096


@lru_cache(maxsize=_CACHE_SIZE)
def guess_release(name: str) -> Mapping[str, Any]:
    """Return guessit's properties of *name* (cached, read-only)."""
    return MappingProxyType(dict(guessit(name)))


def clear_cache() -> None:
    """Drop the cached parses (tests)."""
    guess_release.cache_clear()
