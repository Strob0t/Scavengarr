"""httpcore network backend on asyncio streams.

httpx connects through httpcore, whose default backend on asyncio is anyio:
it runs TLS in Python (``anyio.streams.tls.TLSStream`` around an
``ssl.SSLObject``) and passes every chunk through anyio's stream layers.
asyncio streams leave TLS to the event loop, which in the app is uvloop's
compiled implementation. Measured on the Raspberry Pi with 1 MB HLS
segments through the proxy (2026-10-05, three runs): 103-112 ms of CPU per
MB with anyio, 75-88 ms with this backend.

The behaviour follows httpcore's anyio backend: the same exceptions, the
same ``get_extra_info`` keys, and closing a connection does not wait for
the server's TLS close_notify.
"""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Iterable
from typing import Any

import httpcore

_Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]


class AsyncioNetworkStream(httpcore.AsyncNetworkStream):
    """One connection: an asyncio reader and writer, plain or TLS."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._reader = reader
        self._writer = writer

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            async with asyncio.timeout(timeout):
                return await self._reader.read(max_bytes)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout("read timed out") from exc
        except OSError as exc:  # ssl.SSLError included
            raise httpcore.ReadError(str(exc)) from exc

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        if not buffer:
            return
        if self._writer.transport.is_closing():
            # asyncio would drop the data, uvloop raise a RuntimeError
            raise httpcore.WriteError("connection closed")
        try:
            async with asyncio.timeout(timeout):
                self._writer.write(buffer)
                await self._writer.drain()
        except TimeoutError as exc:
            raise httpcore.WriteTimeout("write timed out") from exc
        except OSError as exc:
            raise httpcore.WriteError(str(exc)) from exc

    async def aclose(self) -> None:
        # close() on a TLS transport sends close_notify and keeps the
        # connection until the server answers (up to 30 s); anyio's backend
        # closes the socket at once, and so does abort()
        self._writer.transport.abort()

    async def start_tls(
        self,
        ssl_context: ssl.SSLContext,
        server_hostname: str | None = None,
        timeout: float | None = None,
    ) -> httpcore.AsyncNetworkStream:
        # A failed handshake closes the connection (the event loop does)
        try:
            async with asyncio.timeout(timeout):
                await self._writer.start_tls(
                    ssl_context, server_hostname=server_hostname
                )
        except TimeoutError as exc:
            raise httpcore.ConnectTimeout("TLS handshake timed out") from exc
        except OSError as exc:  # ssl.SSLError included
            raise httpcore.ConnectError(str(exc)) from exc
        return self

    def get_extra_info(self, info: str) -> Any:
        if info == "is_readable":
            return self._is_readable()
        key = {"client_addr": "sockname", "server_addr": "peername"}.get(info, info)
        if key in ("ssl_object", "sockname", "peername", "socket"):
            return self._writer.get_extra_info(key)
        return None

    def _is_readable(self) -> bool:
        """Whether a read would return at once.

        httpcore retires an idle connection that is: the server closed it or
        sent something unasked (a 408 before closing), which must not be
        read as the answer to the next request. asyncio reads the socket
        ahead into the reader's buffer, so the socket itself (what anyio's
        backend polls) does not show it.
        """
        reader = self._reader
        return (
            # No public accessor; the attribute is untyped in typeshed
            bool(reader._buffer)  # type: ignore[attr-defined]
            or reader.at_eof()
            or reader.exception() is not None
            or self._writer.transport.is_closing()
        )


class AsyncioNetworkBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend opening :class:`AsyncioNetworkStream` connections."""

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        local_addr = None if local_address is None else (local_address, 0)
        return await _connect(
            asyncio.open_connection(host, port, local_addr=local_addr),
            timeout,
            socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await _connect(
            asyncio.open_unix_connection(path), timeout, socket_options
        )

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


async def _connect(
    opening: Awaitable[_Streams],
    timeout: float | None,
    socket_options: Iterable[httpcore.SOCKET_OPTION] | None,
) -> AsyncioNetworkStream:
    try:
        async with asyncio.timeout(timeout):
            reader, writer = await opening
    except TimeoutError as exc:
        raise httpcore.ConnectTimeout("connection timed out") from exc
    except OSError as exc:
        raise httpcore.ConnectError(str(exc)) from exc
    try:
        for option in socket_options or ():
            writer.get_extra_info("socket").setsockopt(*option)
    except OSError as exc:
        writer.transport.abort()
        raise httpcore.ConnectError(str(exc)) from exc
    return AsyncioNetworkStream(reader, writer)
