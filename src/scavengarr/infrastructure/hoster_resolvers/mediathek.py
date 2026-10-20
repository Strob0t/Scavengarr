"""Mediathek (ard-mcdn.de, the broadcasters' CDN hosts) hoster resolver —
answers the public broadcasters' own MP4 and HLS files.

The mediathek plugin's links are the files of the Filmliste themselves, so
nothing is to be resolved: the file is checked (``verify_video_url``: a
HEAD that answers 200 or 206 without HTML, KiKA's files come as
``application/octet-stream``) and answered as it is, without headers and
not bound to the resolving address. A geo-blocked file (ORF, SRF abroad)
answers 403 and is no stream.

The hosts are the ones the Filmliste's files are served from, read from
the API's answers: the ARD CDN (``*.ard-mcdn.de``) by its name, the
broadcasters' zones on Akamai and their own hosts by their full name
(``akamaihd.net`` and ``akamaized.net`` serve other hosters too).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from scavengarr.domain.entities.stremio import ResolvedStream
from scavengarr.infrastructure.hoster_resolvers._verify import verify_video_url

# hr-, kika-, mdr-, ndr-, radiobremen-, rbb-, sportschau-, tagesschau- and
# wdr-progressive, swr-pd and the live and event hosts
_DOMAINS = frozenset({"ard-mcdn"})
_HOSTS = frozenset(
    {
        "apasfiis.sf.apa.at",
        "arteconcert-a.akamaihd.net",
        "arteptweb-a.akamaihd.net",
        "avdlswr-a.akamaihd.net",
        "avod.ard.de",
        "brevent.akamaized.net",
        "cdn-storage.br.de",
        "ctv-videos.daserste.de",
        "event11cloudhls.akamaized.net",
        "funk-02.akamaized.net",
        "funk-02dd.akamaized.net",
        "hrardmediathek-a.akamaihd.net",
        "kikaeventhls.akamaized.net",
        "mcdn.ndr.de",
        "mdrevent3wwhls.akamaized.net",
        "mdrevent5wwhls.akamaized.net",
        "media.tagesschau.de",
        "mediandr-a.akamaihd.net",
        "ndrevent.akamaized.net",
        "ndrspezial.akamaized.net",
        "nrodl.zdf.de",
        "nrodlzdf-a.akamaihd.net",
        "odgeomdr-a.akamaihd.net",
        "odmdr-a.akamaihd.net",
        "ondemand.unified.mdr.de",
        "pd-videos.daserste.de",
        "pdodswr-a.akamaihd.net",
        "pdvideosdaserste-a.akamaihd.net",
        "rbbmediapmdp-a.akamaihd.net",
        "rbbvod-rwrtr.akamaized.net",
        "rbprogressivedl-a.akamaihd.net",
        "rodl.zdf.de",
        "rodlzdf-a.akamaihd.net",
        "sportschau-dd.akamaized.net",
        "srde.akamaized.net",
        "srf-vod-amd.akamaized.net",
        "srfs.akamaized.net",
        "srstorage01-a.akamaihd.net",
        "swrvod-rwrtr.akamaized.net",
        "tv-download.dw.com",
        "tvdlzdf-a.akamaihd.net",
        "tvdownloaddw-a.akamaihd.net",
        "wdrevent2.akamaized.net",
        "wdrmedien-a.akamaihd.net",
        "wdrvod-rwrtr.akamaized.net",
    }
)


class MediathekResolver:
    """Answers the public broadcasters' files that answer as media."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    @property
    def name(self) -> str:
        # The mediathek plugin's hoster label
        return "mediathek"

    @property
    def supported_domains(self) -> frozenset[str]:
        """The ARD CDN, every host of which serves the broadcasters' files."""
        return _DOMAINS

    @property
    def supported_hosts(self) -> frozenset[str]:
        """Hosts dispatched here whose second-level name other hosts share."""
        return _HOSTS

    async def resolve(self, url: str) -> ResolvedStream | None:
        """The file itself when it answers as media, else ``None``."""
        if not await verify_video_url(self._http, url, {}, self.name):
            return None
        is_hls = urlparse(url).path.lower().endswith(".m3u8")
        return ResolvedStream(video_url=url, is_hls=is_hls)
