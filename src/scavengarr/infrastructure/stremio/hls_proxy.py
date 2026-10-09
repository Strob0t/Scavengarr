"""HLS proxy helpers — manifest rewriting and CDN fetching.

When a CDN requires ``Referer`` (or other headers) on **all** HLS
sub-requests (variant playlists, ``.ts`` segments), Stremio's built-in
``proxyHeaders`` is insufficient because it only applies the header to
the initial manifest fetch.  The HLS proxy endpoint solves this by
fetching each resource server-side with the correct headers and
rewriting absolute CDN URLs in manifests so the HLS player fetches
subsequent resources through the proxy as well.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from dataclasses import dataclass
from urllib.parse import ParseResult, SplitResult, urljoin, urlparse, urlsplit

import httpx
import structlog

from scavengarr.infrastructure.hoster_resolvers._domain import extract_domain
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT

log = structlog.get_logger(__name__)

# Short-TTL manifest cache (60 seconds) — prevents re-fetching the
# same master/variant playlist on rapid segment requests.
_MANIFEST_CACHE_TTL = 60
# Entries expire but are only removed when full: rotating manifest URLs
# (tokens in the query) would otherwise pile up forever
_MANIFEST_CACHE_MAX = 512
_manifest_cache: dict[str, tuple[bytes, str, float]] = {}

# Global semaphore for CDN proxy fetches (prevents stampede).
_CDN_SEMAPHORE = asyncio.Semaphore(50)

# Segments and files go out in pieces of this size (``_body``)
_SEGMENT_CHUNK = 65536

# The first byte of a direct file can take a while (Vinovo: about 30 s):
# the read timeout of a file request (``stream_file``)
_FILE_READ_TIMEOUT_S = 60.0
# The CDN's answer headers a proxied file passes to the player
# (``_file_headers``: Content-Length only when it frames the body)
_FILE_HEADERS = (
    "content-type",
    "content-length",
    "content-range",
    "accept-ranges",
    "content-encoding",
)
# A 206 answer's span: ``bytes <first>-<last>/<size or *>``
_CONTENT_RANGE_RE = re.compile(r"bytes (\d+)-(\d+)/(?:\d+|\*)")
# The player's request headers a proxied file forwards to the CDN
_RANGE_HEADERS = ("Range", "If-Range")

# URI attribute of an HLS tag (EXT-X-MEDIA, EXT-X-KEY, EXT-X-MAP, …)
_URI_ATTR_RE = re.compile(r'URI="([^"]*)"')


def cdn_base_from_url(video_url: str) -> str:
    """Extract the CDN base directory from a video URL.

    >>> cdn_base_from_url("https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/master.m3u8?t=abc")
    'https://ds7.dropcdn.io/hls2/01/00017/yw6c47u0v5nb_h/'
    """
    parsed = urlparse(video_url)
    # Everything up to and including the last '/' in the path
    path = parsed.path
    last_slash = path.rfind("/")
    if last_slash >= 0:
        base_path = path[: last_slash + 1]
    else:
        base_path = "/"
    return f"{parsed.scheme}://{parsed.netloc}{base_path}"


def _same_origin(a: SplitResult | ParseResult, b: SplitResult | ParseResult) -> bool:
    """Whether two URLs name the same scheme and host; the host without case.

    VidHide names its CDN host in mixed case in the stream URL and in lower
    case in its playlists: compared as written, its segments stayed
    unproxied and failed in the player (403, a token bound to the server).
    """
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def _proxy_uri(uri: str, cdn_base: str, proxy_base: str, playlist_dir: str) -> str:
    """*uri* as a proxy URL when it points at the stream's CDN, else as is.

    A relative URI becomes ``<proxy_base><playlist_dir><uri>``: resolved
    against the request URL instead, it went to whatever link that URL
    named, not to the one *proxy_base* names (the playlist's own, which a
    later resolution leaves alone). A URI from the CDN's root
    (``/secure/…``, ``//host/…`` or an absolute URL outside *cdn_base*)
    becomes ``<proxy_base>/<path>``; the proxy joins that absolute path
    with the CDN origin (``build_cdn_url``). Other origins and schemes
    (``data:``, ``skd:``) stay: the proxy only fetches from the stream's own
    CDN.
    """
    if uri.startswith(cdn_base):
        return proxy_base + uri[len(cdn_base) :]
    base = urlsplit(cdn_base)
    if uri.startswith("//"):
        target = urlsplit(f"{base.scheme}:{uri}")
    elif uri.startswith("/"):
        target = urlsplit(f"{base.scheme}://{base.netloc}{uri}")
    else:
        target = urlsplit(uri)
        if not target.scheme:
            return f"{proxy_base}{playlist_dir}{uri}"
        if target.scheme not in ("http", "https"):
            return uri
    if not _same_origin(target, base):
        return uri
    query = f"?{target.query}" if target.query else ""
    return f"{proxy_base}{target.path}{query}"


def rewrite_manifest(
    content: str, cdn_base: str, proxy_base: str, playlist_dir: str = ""
) -> str:
    """Point the CDN URIs of an HLS manifest at the proxy.

    Rewrites URI lines and the ``URI="…"`` attributes of tags (audio
    renditions, keys, init segments) with ``_proxy_uri``; *playlist_dir* is
    the manifest's directory below *proxy_base* (``720p/``; empty for the
    stream's own playlist). Query parameters (auth tokens) and line endings
    are preserved.
    """

    def proxied(uri: str) -> str:
        return _proxy_uri(uri, cdn_base, proxy_base, playlist_dir)

    lines: list[str] = []
    for line in content.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#"):
            line = _URI_ATTR_RE.sub(lambda m: f'URI="{proxied(m.group(1))}"', line)
        elif stripped:
            line = line.replace(stripped, proxied(stripped), 1)
        lines.append(line)
    return "".join(lines)


def _cache_manifest(url: str, body: bytes, content_type: str) -> None:
    """Cache a manifest; when full, drop expired entries, then the oldest."""
    now = time.monotonic()
    if len(_manifest_cache) >= _MANIFEST_CACHE_MAX:
        expired = [k for k, (_, _, exp) in _manifest_cache.items() if exp <= now]
        for key in expired:
            del _manifest_cache[key]
        while len(_manifest_cache) >= _MANIFEST_CACHE_MAX:
            del _manifest_cache[next(iter(_manifest_cache))]
    _manifest_cache[url] = (body, content_type, now + _MANIFEST_CACHE_TTL)


def _player_headers(headers: dict[str, str]) -> dict[str, str]:
    """The stream's headers with the player's (browser) User-Agent.

    The proxy fetches on the player's behalf; CDNs such as mixdrop's answer
    other agents (the app's own) with 403.
    """
    return {"User-Agent": DEFAULT_USER_AGENT, **headers}


async def fetch_hls_resource(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> tuple[bytes, str]:
    """Fetch an HLS resource (manifest or segment) with headers.

    Returns ``(body_bytes, content_type)``.
    Raises ``httpx.HTTPStatusError`` on non-2xx responses.

    Manifests are cached for 60 seconds to avoid re-fetching on
    rapid segment requests.  Acquires the global CDN semaphore to
    prevent connection stampedes from concurrent viewers.
    """
    # Check manifest cache for .m3u8 URLs
    cached = _manifest_cache.get(url)
    if cached is not None:
        body, ct, expires = cached
        if time.monotonic() < expires:
            return body, ct
        del _manifest_cache[url]

    async with _CDN_SEMAPHORE:
        resp = await http_client.get(
            url,
            headers=_player_headers(headers),
            follow_redirects=True,
            timeout=15.0,
        )
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "application/octet-stream")
    body = resp.content

    # Cache manifests (small text files, not segments)
    if ".m3u8" in url or "mpegurl" in ct.lower():
        _cache_manifest(url, body, ct)

    return body, ct


async def stream_hls_segment(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    head: bool = False,
    on_sent: Callable[[int], None] | None = None,
) -> tuple[AsyncGenerator[bytes], str]:
    """Stream an HLS segment from CDN without buffering full body.

    Returns ``(byte_iterator, content_type)``.
    Raises ``httpx.HTTPStatusError`` on non-2xx responses.

    Uses ``httpx.stream()`` so that segment bytes flow through the
    proxy without loading the entire 2-10 MB segment into memory. They go
    out in pieces of ``_SEGMENT_CHUNK``: VOE's CDN sends 4 KiB TLS
    records, and passed through one by one, each a response write, they
    cost the proxy 39-46 ms of CPU per MB against 25 ms in 64 KiB pieces
    (dev-server end-to-end run, 2026-10-05). An encoded body
    (``Content-Encoding``, which the proxy does not forward) is decoded.

    For a HEAD request (*head*) the iterator is empty: the CDN's answer
    is closed after its status and headers, before its bytes. *on_sent*
    is told the bytes that went out when the body ends, an aborted
    transfer's included.
    """
    async with _CDN_SEMAPHORE:
        resp = await http_client.send(
            http_client.build_request(
                "GET",
                url,
                headers=_player_headers(headers),
            ),
            stream=True,
            follow_redirects=True,
        )
        if resp.is_error:
            # Streamed responses hold their pooled connection until closed
            await resp.aclose()
        resp.raise_for_status()

    ct = resp.headers.get("content-type", "application/octet-stream")
    return _body(resp, head=head, on_sent=on_sent), ct


async def _body(
    resp: httpx.Response,
    *,
    head: bool,
    on_sent: Callable[[int], None] | None = None,
) -> AsyncGenerator[bytes]:
    """*resp*'s body in ``_SEGMENT_CHUNK`` pieces, the answer closed at the
    end; empty for HEAD (*head*), the answer closed before its bytes.
    *on_sent* is told the bytes that went out when the body ends, an
    aborted transfer's included.

    The one generator of a proxied body, closing the CDN's answer itself.
    A counting wrapper that closed it from its own ``finally`` raced the
    event loop's finalizer: a body the player dropped was collected with
    its wrapper, both closed in one pass ("aclose(): asynchronous
    generator is already running", production 2026-10-07).
    """
    sent = 0
    try:
        if head:
            return
        async for chunk in resp.aiter_bytes(chunk_size=_SEGMENT_CHUNK):
            sent += len(chunk)
            yield chunk
    finally:
        await resp.aclose()
        if on_sent is not None:
            on_sent(sent)


@dataclass(frozen=True)
class FileAnswer:
    """A direct file's answer from its CDN: the status (200, 206, or 416 for
    a range it cannot serve), the headers of ``_FILE_HEADERS`` it carried
    and its body in pieces."""

    status: int
    headers: dict[str, str]
    chunks: AsyncGenerator[bytes]


async def stream_file(
    http_client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    *,
    player: Mapping[str, str] | None = None,
    head: bool = False,
    on_sent: Callable[[int], None] | None = None,
) -> FileAnswer:
    """A direct file streamed from its CDN for the player, byte range included.

    The request carries the stored *headers* over the player's User-Agent,
    ``Accept-Encoding: identity`` (the byte offsets must hold, so nothing
    is decoded) and the ``Range`` and ``If-Range`` headers of *player*'s
    request when it sent them, and reads with ``_FILE_READ_TIMEOUT_S``.
    The CDN's status passes through with the headers of ``_FILE_HEADERS``
    (``_file_headers``: Content-Length only when it frames the body); the
    body goes out undecoded in ``_SEGMENT_CHUNK`` pieces without buffering
    the file, resumed once when the CDN ends it before the promised length
    (``_file_body``). For HEAD (*head*) the body is empty and the CDN's answer is
    closed after its headers. *on_sent* is told the bytes that went out
    when the body ends, an aborted transfer's included.

    Raises ``httpx.HTTPStatusError`` for an error answer other than 416
    (closed first) and ``httpx.HTTPError`` when the CDN cannot be reached.
    """
    request_headers = {**_player_headers(headers), "Accept-Encoding": "identity"}
    for name in _RANGE_HEADERS:
        value = player.get(name) if player is not None else None
        if value:
            request_headers[name] = value
    client_timeout = http_client.timeout
    timeout = httpx.Timeout(
        connect=client_timeout.connect,
        read=_FILE_READ_TIMEOUT_S,
        write=client_timeout.write,
        pool=client_timeout.pool,
    )
    async with _CDN_SEMAPHORE:
        resp = await http_client.send(
            http_client.build_request(
                "GET", url, headers=request_headers, timeout=timeout
            ),
            stream=True,
            follow_redirects=True,
        )
        if resp.is_error and resp.status_code != 416:
            await resp.aclose()
            resp.raise_for_status()
    body = _file_body(
        http_client, url, request_headers, timeout, resp, head=head, on_sent=on_sent
    )
    return FileAnswer(resp.status_code, _file_headers(resp), body)


class FileCut(Exception):
    """The CDN ended a file's body before the promised length, the one
    resume included. Raised out of the body: the player's connection
    aborts instead of ending as if the file were complete, and the player
    asks again with a range of its own."""


def _file_headers(resp: httpx.Response) -> dict[str, str]:
    """The headers of ``_FILE_HEADERS`` the CDN's answer carried,
    Content-Length only when it frames the body: with ``Transfer-Encoding``
    the body is chunked and the header is not its length (RFC 9112 section
    6.3 has an intermediary drop it), and uvicorn refuses a body shorter
    than the header."""
    passed = {
        name: resp.headers[name] for name in _FILE_HEADERS if name in resp.headers
    }
    if "transfer-encoding" in resp.headers:
        passed.pop("content-length", None)
    return passed


def _file_span(resp: httpx.Response) -> tuple[int, int | None]:
    """The first byte's offset in the file and the bytes the answer promises:
    from ``Content-Range`` for a 206, from a framing ``Content-Length`` for
    a 200; ``(0, None)`` when the answer promises no length (chunked, 416)."""
    match = _CONTENT_RANGE_RE.fullmatch(resp.headers.get("content-range", ""))
    if resp.status_code == 206 and match is not None:
        first, last = int(match.group(1)), int(match.group(2))
        return first, last - first + 1
    length = resp.headers.get("content-length", "")
    if (
        resp.status_code == 200
        and "transfer-encoding" not in resp.headers
        and length.isdigit()
    ):
        return 0, int(length)
    return 0, None


@dataclass
class _FileTransfer:
    """A proxied file's progress: the first byte's offset in the file, the
    bytes the answer promised (``None``: no length) and the bytes sent."""

    url: str
    first: int
    expected: int | None
    sent: int = 0
    resumed: bool = False

    @property
    def done(self) -> bool:
        return self.expected is not None and self.sent >= self.expected

    def take(self, chunk: bytes) -> bytes:
        """The part of *chunk* within the promised length, counted as sent."""
        if self.expected is not None:
            chunk = chunk[: self.expected - self.sent]
        self.sent += len(chunk)
        return chunk

    def cut(self) -> FileCut:
        """The transfer's end short of the promise, logged once."""
        log.warning(
            "hls_proxy_file_cut",
            cdn=extract_domain(self.url),
            sent=self.sent,
            expected=self.expected,
            resumed=self.resumed,
        )
        return FileCut(
            f"{self.sent} of {self.expected} bytes sent, resumed: {self.resumed}"
        )


async def _range_answer(
    http_client: httpx.AsyncClient,
    url: str,
    request_headers: dict[str, str],
    timeout: httpx.Timeout,
    *,
    first: int,
    last: int,
) -> httpx.Response | None:
    """The file asked for again from byte *first* to *last*, the request's
    other headers kept (``If-Range`` included: a changed file answers 200):
    the CDN's answer when it is a 206 from *first*, else ``None`` (closed)."""
    headers = {**request_headers, "Range": f"bytes={first}-{last}"}
    async with _CDN_SEMAPHORE:
        resp = await http_client.send(
            http_client.build_request("GET", url, headers=headers, timeout=timeout),
            stream=True,
            follow_redirects=True,
        )
    if resp.status_code != 206 or _file_span(resp)[0] != first:
        await resp.aclose()
        return None
    return resp


async def _resume(
    http_client: httpx.AsyncClient,
    request_headers: dict[str, str],
    timeout: httpx.Timeout,
    resp: httpx.Response,
    transfer: _FileTransfer,
    error: httpx.TransportError | None,
) -> httpx.Response:
    """The CDN's answer for the rest of the file after *resp* ended short
    of the promise (*error*: the transport error that ended it, if any),
    once per transfer; ``FileCut`` when the transfer cannot go on."""
    if transfer.expected is None or transfer.resumed:
        raise transfer.cut() from error
    await resp.aclose()
    try:
        fresh = await _range_answer(
            http_client,
            transfer.url,
            request_headers,
            timeout,
            first=transfer.first + transfer.sent,
            last=transfer.first + transfer.expected - 1,
        )
    except httpx.HTTPError as exc:
        raise transfer.cut() from exc
    if fresh is None:
        raise transfer.cut() from error
    transfer.resumed = True
    log.info(
        "hls_proxy_file_resumed",
        cdn=extract_domain(transfer.url),
        at=transfer.sent,
        expected=transfer.expected,
    )
    return fresh


async def _file_body(
    http_client: httpx.AsyncClient,
    url: str,
    request_headers: dict[str, str],
    timeout: httpx.Timeout,
    resp: httpx.Response,
    *,
    head: bool,
    on_sent: Callable[[int], None] | None,
) -> AsyncGenerator[bytes]:
    """A file's body in ``_SEGMENT_CHUNK`` pieces, undecoded (the byte
    offsets must hold), the CDN's answer closed at the end; empty for HEAD
    (*head*). *on_sent* is told the bytes that went out when the body ends.

    A body the CDN ends before the promised length (``_file_span``; a
    transport error mid-body, or a clean end short of it, which ended the
    player's answer early: uvicorn's "Response content shorter than
    Content-Length", 9 times in 48 h of production, 2026-10-07) is resumed
    once with a ``Range`` request from the next byte, continuing the same
    answer (``_resume``, ``hls_proxy_file_resumed``). When the resume fails
    or ends short too, ``FileCut`` goes out of the body with one
    ``hls_proxy_file_cut`` warning. An answer without a promised length
    (chunked) ends where the CDN ends it; a transport error cuts it.
    """
    transfer = _FileTransfer(url, *_file_span(resp))
    try:
        if head:
            return
        while not transfer.done:
            error: httpx.TransportError | None = None
            try:
                async for chunk in resp.aiter_raw(chunk_size=_SEGMENT_CHUNK):
                    piece = transfer.take(chunk)
                    if piece:
                        yield piece
                    if transfer.done:
                        break
            except httpx.TransportError as exc:
                error = exc
            if transfer.done or (transfer.expected is None and error is None):
                return
            resp = await _resume(
                http_client, request_headers, timeout, resp, transfer, error
            )
    finally:
        await resp.aclose()
        if on_sent is not None:
            on_sent(transfer.sent)


def build_cdn_url(cdn_base: str, path: str, query_string: str = "") -> str:
    """Build a full CDN URL from the base, a sub-path, and optional query.

    Uses ``urljoin`` so that absolute paths (``/foo/bar``) and relative
    paths (``seg-1-v1-a1.ts``) both resolve correctly.

    Raises ``ValueError`` when the result leaves the CDN (other scheme or
    host): *path* comes from the client, and ``urljoin`` lets an absolute
    or ``//host`` path replace the host, which would turn the proxy into
    an open proxy into the server's network.
    """
    url = urljoin(cdn_base, path)
    base, target = urlparse(cdn_base), urlparse(url)
    if not _same_origin(target, base):
        raise ValueError(f"path leaves the stream's CDN: {path[:80]}")
    if query_string:
        url = f"{url}?{query_string}"
    return url
