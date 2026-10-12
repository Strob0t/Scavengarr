"""Port for the catalog's record of a title (Cinemeta).

The record names the title's kind (its genres) and, for a series, the
episodes in IMDb's numbering: the identity a request is matched against
and the reference for sites that count episodes their own way.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from scavengarr.domain.entities.stremio import SeriesMeta, StremioContentType

# ``found``: the record; ``not_found``: the catalog does not know the id;
# ``error``: the catalog did not answer
MetaOutcome = Literal["found", "not_found", "error"]


@runtime_checkable
class SeriesMetaPort(Protocol):
    """Looks a title up in the catalog."""

    async def lookup(
        self, content_type: StremioContentType, imdb_id: str
    ) -> tuple[SeriesMeta | None, MetaOutcome]:
        """The title's record with the lookup's outcome; the record is
        ``None`` unless the outcome is ``found``."""
        ...


class _NoSeriesMeta:
    """Knows no title: the default where no catalog client is wired in."""

    async def lookup(
        self, content_type: StremioContentType, imdb_id: str
    ) -> tuple[SeriesMeta | None, MetaOutcome]:
        del content_type, imdb_id
        return None, "not_found"


NO_SERIES_META: SeriesMetaPort = _NoSeriesMeta()
