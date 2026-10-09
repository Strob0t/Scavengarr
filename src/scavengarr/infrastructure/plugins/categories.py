"""Torznab category helpers shared by the plugins.

Torznab categories follow a ``X000`` parent / ``X0YY`` child scheme
(2000 Movies, 2040 Movies/HD, 5000 TV, 5070 TV/Anime, ...). A plugin labels
each result with the category the site gives it and answers a request with
the results whose label the requested category covers:

- ``served_category()`` turns the request into the category to match. A child
  the site does not tell apart (2040 where every film is 2000) becomes its
  parent; a family the site does not serve at all gives ``None``, and the
  plugin returns ``[]`` without a request.
- ``category_matches()`` / ``filter_by_category()`` then keep the results.

Streaming sites (films and series) label with ``stream_category()``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from scavengarr.domain.plugins.base import SearchResult

# The labels ``stream_category()`` gives
STREAM_CATEGORIES = (2000, 5000, 5070)

_ANIME_GENRES = frozenset({"anime", "animation"})
# "S01E02", "S03" (season pack) as a token; "Staffel 3"
_SERIES_TITLE_RE = re.compile(
    r"(?:^|[\s._\-(\[])S\d{1,2}(?:E\d{1,4})?(?=$|[\s._\-)\]])|\bStaffel\b",
    re.IGNORECASE,
)


def category_matches(requested: int | None, accepted: int) -> bool:
    """Whether a result labelled *accepted* answers a request for *requested*.

    A parent request (5000 = any TV) covers its children (5000-5999); a
    child request (5070) only matches itself; no request matches all.
    """
    if requested is None or requested == accepted:
        return True
    return requested % 1000 == 0 and accepted // 1000 == requested // 1000


def served_category(requested: int, offered: Iterable[int]) -> int | None:
    """The category to match results against when *requested* comes in.

    *offered* are the labels the site can give. *requested* itself when it
    covers one of them; else its parent (a child the site does not tell
    apart); ``None`` when the site has nothing of the family.
    """
    labels = tuple(offered)
    for wanted in (requested, requested - requested % 1000):
        if any(category_matches(wanted, label) for label in labels):
            return wanted
    return None


def filter_by_category(
    results: list[SearchResult], category: int
) -> list[SearchResult]:
    """The results whose label *category* covers."""
    return [r for r in results if category_matches(category, r.category)]


def is_series_title(title: str) -> bool:
    """Whether a release title names episodes or a season.

    Scene naming (``S01E02``, season packs ``S03``) and German "Staffel".
    """
    return _SERIES_TITLE_RE.search(title) is not None


def names_anime(genres: Iterable[str]) -> bool:
    """Whether a genre list calls the title anime or animation: the words
    the streaming plugins label 5070 by, and the words the Stremio title
    matcher reads a result's genres with."""
    return bool(_ANIME_GENRES & {g.strip().lower() for g in genres})


def stream_category(genres: Iterable[str], *, is_series: bool) -> int:
    """Category of a streaming-site title.

    Films are 2000 whatever their genre (animated films and documentaries
    included); series are 5000, anime and animation series 5070.
    """
    if not is_series:
        return 2000
    if names_anime(genres):
        return 5070
    return 5000
