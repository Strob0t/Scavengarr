"""Generic DDL hoster resolver — consolidates 12 identical DDL-based resolvers.

Consolidates alfafile, alphaddl, fastpic, filecrypt, filefactory, fsst, go4up,
mixdrop, nitroflare, onefichier, turbobit, and uploaded into a single
parameterised implementation.  Each hoster is described by a
``GenericDDLConfig`` — name, domains, file-ID regex, offline markers, and
extraction options — while the resolution logic lives once in
``GenericDDLResolver``.

Adding a new DDL hoster = adding a new ``GenericDDLConfig`` constant +
appending it to ``ALL_DDL_CONFIGS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlparse

import httpx
import structlog

from scavengarr.domain.entities.stremio import ResolvedStream, StreamQuality
from scavengarr.infrastructure.hoster_resolvers import extract_domain

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GenericDDLConfig:
    """Immutable configuration for one DDL-based hoster resolver.

    Parameters
    ----------
    name:
        Hoster name used for registry dispatch (e.g. ``"alfafile"``).
    domains:
        Second-level domain names for URL matching.
    file_id_re:
        Regex with one capture group extracting the file ID.
    offline_markers:
        Strings whose presence in the response HTML indicates the file is gone.
    file_id_source:
        ``"path"`` extracts from URL path (default), ``"query"`` from the
        query string (used by 1fichier whose IDs live after ``?``).
    min_file_id_len:
        Optional minimum length for extracted file IDs.  IDs shorter than
        this are rejected (e.g. turbobit requires >= 6).
    """

    name: str
    domains: frozenset[str]
    file_id_re: re.Pattern[str]
    offline_markers: tuple[str, ...]
    file_id_source: Literal["path", "query"] = "path"
    min_file_id_len: int | None = None


# ---------------------------------------------------------------------------
# File-ID extraction (stateless, testable)
# ---------------------------------------------------------------------------


def extract_ddl_file_id(url: str, config: GenericDDLConfig) -> str | None:
    """Extract the file ID from *url* using *config*.

    Returns ``None`` when the URL does not match the config's domains or
    file-ID regex, or when the extracted ID is shorter than
    ``config.min_file_id_len``.
    """
    try:
        domain = extract_domain(url)
        if domain not in config.domains:
            return None

        parsed = urlparse(url)
        source = parsed.query if config.file_id_source == "query" else parsed.path
        match = config.file_id_re.search(source)
        if not match:
            return None

        file_id = match.group(1)
        if config.min_file_id_len is not None and len(file_id) < config.min_file_id_len:
            return None

        return file_id
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class GenericDDLResolver:
    """Generic resolver for DDL-based hosters.

    Satisfies ``HosterResolverPort``.
    """

    def __init__(
        self,
        config: GenericDDLConfig,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._config = config
        self._http = http_client

    @property
    def name(self) -> str:
        return self._config.name

    async def resolve(self, url: str) -> ResolvedStream | None:
        """Validate a DDL link by fetching the file page.

        Checks for offline markers to determine if the file is still
        available.  Returns a ``ResolvedStream`` with the original URL on
        success.
        """
        hoster = self._config.name
        file_id = extract_ddl_file_id(url, self._config)
        if not file_id:
            log.warning("ddl_invalid_url", hoster=hoster, url=url)
            return None

        try:
            resp = await self._http.get(url, follow_redirects=True, timeout=15)
        except httpx.HTTPError:
            log.warning("ddl_request_failed", hoster=hoster, url=url)
            return None

        if resp.status_code != 200:
            log.warning(
                "ddl_http_error",
                hoster=hoster,
                status=resp.status_code,
                url=url,
            )
            return None

        html = resp.text

        for marker in self._config.offline_markers:
            if marker in html:
                log.info(
                    "ddl_file_offline",
                    hoster=hoster,
                    file_id=file_id,
                    marker=marker,
                )
                return None

        final_url = str(resp.url)
        if "/404" in final_url or "error" in final_url:
            log.info(
                "ddl_error_redirect",
                hoster=hoster,
                file_id=file_id,
                url=final_url,
            )
            return None

        log.debug("ddl_resolved", hoster=hoster, file_id=file_id)
        return ResolvedStream(video_url=url, quality=StreamQuality.UNKNOWN)


# ---------------------------------------------------------------------------
# Hoster configurations
# ---------------------------------------------------------------------------

ALFAFILE = GenericDDLConfig(
    name="alfafile",
    domains=frozenset({"alfafile"}),
    file_id_re=re.compile(r"^/file/([A-Za-z0-9]+)$"),
    offline_markers=(
        "File Not Found",
        "file was removed",
        "doesn't exist",
    ),
)

ALPHADDL = GenericDDLConfig(
    name="alphaddl",
    domains=frozenset({"alphaddl"}),
    file_id_re=re.compile(r"^/([a-zA-Z0-9_-]+)"),
    offline_markers=(
        "Page not found",
        "404",
        "not available",
    ),
    min_file_id_len=3,
)

FASTPIC = GenericDDLConfig(
    name="fastpic",
    domains=frozenset({"fastpic"}),
    file_id_re=re.compile(r"/(?:full)?view/.+?([a-f0-9]{32}\.[A-Za-z]+)"),
    offline_markers=(
        "not_found",
        "Image not found",
        "404 Not Found",
    ),
)

FILECRYPT = GenericDDLConfig(
    name="filecrypt",
    domains=frozenset({"filecrypt"}),
    file_id_re=re.compile(r"^/Container/([A-Za-z0-9]+)"),
    offline_markers=(
        "File Not Found",
        "Container not found",
        "has been deleted",
    ),
)

FILEFACTORY = GenericDDLConfig(
    name="filefactory",
    domains=frozenset({"filefactory"}),
    file_id_re=re.compile(r"^/file/([a-z0-9]+)"),
    offline_markers=(
        "File Not Found",
        "This file is no longer available",
        "File has been removed",
    ),
)

FSST = GenericDDLConfig(
    name="fsst",
    domains=frozenset({"fsst"}),
    file_id_re=re.compile(r"^/(?:e/|d/)?([a-zA-Z0-9]+)$"),
    offline_markers=(
        "File Not Found",
        "file was removed",
        "Video not found",
    ),
)

GO4UP = GenericDDLConfig(
    name="go4up",
    domains=frozenset({"go4up"}),
    file_id_re=re.compile(r"^/(?:dl|link)/([a-zA-Z0-9]+)"),
    offline_markers=(
        "File Not Found",
        "Link not found",
        "has been removed",
    ),
)

MIXDROP = GenericDDLConfig(
    name="mixdrop",
    domains=frozenset({"mixdrop", "mxdrop", "m1xdrop", "mixdrop23"}),
    file_id_re=re.compile(r"^/(?:f|e|emb)/([a-z0-9]+)$"),
    offline_markers=(
        "/imgs/illustration-notfound.png",
        "File not found",
    ),
)

NITROFLARE = GenericDDLConfig(
    name="nitroflare",
    domains=frozenset({"nitroflare", "nitro"}),
    file_id_re=re.compile(r"^/(?:view|watch)/([A-Z0-9]+)$"),
    offline_markers=(
        "File Not Found",
        "This file has been removed",
        ">File doesn't exist",
    ),
)

ONEFICHIER = GenericDDLConfig(
    name="1fichier",
    domains=frozenset(
        {
            "1fichier",
            "alterupload",
            "cjoint",
            "desfichiers",
            "dfichiers",
            "megadl",
            "mesfichiers",
            "piecejointe",
            "pjointe",
            "tenvoi",
            "dl4free",
        }
    ),
    file_id_re=re.compile(r"^([a-z0-9]{5,20})$"),
    offline_markers=(
        "not found",
        "has been deleted",
        "File not found",
        "The requested file could not be found",
        "The requested file has been deleted",
    ),
    file_id_source="query",
)

TURBOBIT = GenericDDLConfig(
    name="turbobit",
    domains=frozenset({"turbobit", "turb", "turbo"}),
    file_id_re=re.compile(r"^/(?:download/free/)?([A-Za-z0-9]+?)(?:/|\.html|$)"),
    offline_markers=(
        "File Not Found",
        "file was removed",
        "File was not found",
        ">This document is not available",
    ),
    min_file_id_len=6,
)

UPLOADED = GenericDDLConfig(
    name="uploaded",
    domains=frozenset({"uploaded", "ul"}),
    file_id_re=re.compile(r"^/(?:file/)?([a-z0-9]+)$"),
    offline_markers=(
        "File Not Found",
        "File was deleted",
        "The requested file isn't available anymore",
        "File not found",
    ),
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

ALL_DDL_CONFIGS: tuple[GenericDDLConfig, ...] = (
    ALFAFILE,
    ALPHADDL,
    FASTPIC,
    FILECRYPT,
    FILEFACTORY,
    FSST,
    GO4UP,
    MIXDROP,
    NITROFLARE,
    ONEFICHIER,
    TURBOBIT,
    UPLOADED,
)


def create_all_ddl_resolvers(
    http_client: httpx.AsyncClient,
) -> list[GenericDDLResolver]:
    """Create ``GenericDDLResolver`` instances for all known DDL hosters."""
    return [
        GenericDDLResolver(config=cfg, http_client=http_client)
        for cfg in ALL_DDL_CONFIGS
    ]
