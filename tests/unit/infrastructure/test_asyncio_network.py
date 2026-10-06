"""Tests for the httpcore network backend on asyncio streams."""

from __future__ import annotations

import asyncio
import datetime
import ipaddress
import ssl
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import httpcore
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from scavengarr.infrastructure.common.asyncio_network import AsyncioNetworkBackend
from scavengarr.infrastructure.common.private_address_guard import (
    GuardedNetworkBackend,
    PrivateAddressGuard,
)

_OK = b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"
_TIMEOUT = b"HTTP/1.1 408 Request Timeout\r\nContent-Length: 0\r\n\r\n"

_Handler = Callable[[asyncio.StreamReader, asyncio.StreamWriter], Awaitable[None]]
_Serve = Callable[..., Awaitable[tuple["_Server", int]]]


class _Server:
    """A local HTTP server counting its connections."""

    def __init__(self, handler: _Handler) -> None:
        self.connections = 0
        self._handler = handler
        self._tasks: set[asyncio.Task[object]] = set()
        self._server: asyncio.Server | None = None

    async def start(self, ssl_context: ssl.SSLContext | None) -> int:
        self._server = await asyncio.start_server(
            self._serve, "127.0.0.1", 0, ssl=ssl_context
        )
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self._server is not None
        self._server.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _serve(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.connections += 1
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        try:
            await self._handler(reader, writer)
        finally:
            writer.close()


@pytest.fixture
async def serve() -> AsyncIterator[_Serve]:
    servers: list[_Server] = []

    async def _start(
        handler: _Handler, ssl_context: ssl.SSLContext | None = None
    ) -> tuple[_Server, int]:
        server = _Server(handler)
        servers.append(server)
        return server, await server.start(ssl_context)

    yield _start
    for server in servers:
        await server.stop()


def _client(ssl_context: ssl.SSLContext | None = None) -> httpx.AsyncClient:
    """An httpx client whose connections use the asyncio backend."""
    transport = httpx.AsyncHTTPTransport()
    transport._pool = httpcore.AsyncConnectionPool(
        ssl_context=ssl_context or httpx.create_ssl_context(),
        network_backend=AsyncioNetworkBackend(),
    )
    return httpx.AsyncClient(transport=transport, timeout=2)


async def _answer_every_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    while True:
        try:
            await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError:
            return
        writer.write(_OK)
        await writer.drain()


async def _answer_once(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Answer, then close the connection (an idle keep-alive timeout)."""
    await reader.readuntil(b"\r\n\r\n")
    writer.write(_OK)
    await writer.drain()


async def _answer_then_time_out(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    """Answer, then send a 408 unasked while the connection is idle."""
    await reader.readuntil(b"\r\n\r\n")
    writer.write(_OK)
    await writer.drain()
    await asyncio.sleep(0.02)
    writer.write(_TIMEOUT)
    await writer.drain()
    await asyncio.sleep(10)


async def _never_answer(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> None:
    await asyncio.sleep(10)


class TestPlainTcp:
    async def test_get(self, serve: _Serve) -> None:
        _, port = await serve(_answer_every_request)
        async with _client() as client:
            resp = await client.get(f"http://127.0.0.1:{port}/")
        assert resp.status_code == 200
        assert resp.text == "hello"
        stream = resp.extensions["network_stream"]
        assert stream.get_extra_info("server_addr") == ("127.0.0.1", port)
        assert stream.get_extra_info("ssl_object") is None

    async def test_keep_alive_reuses_the_connection(self, serve: _Serve) -> None:
        server, port = await serve(_answer_every_request)
        async with _client() as client:
            for _ in range(3):
                resp = await client.get(f"http://127.0.0.1:{port}/")
                assert resp.text == "hello"
        assert server.connections == 1

    async def test_a_connection_the_server_closed_is_not_reused(
        self, serve: _Serve
    ) -> None:
        server, port = await serve(_answer_once)
        async with _client() as client:
            assert (await client.get(f"http://127.0.0.1:{port}/")).text == "hello"
            await asyncio.sleep(0.1)
            assert (await client.get(f"http://127.0.0.1:{port}/")).text == "hello"
        assert server.connections == 2

    async def test_data_on_an_idle_connection_retires_it(self, serve: _Serve) -> None:
        """The 408 the server sent unasked is not the next request's answer."""
        server, port = await serve(_answer_then_time_out)
        async with _client() as client:
            assert (await client.get(f"http://127.0.0.1:{port}/")).text == "hello"
            await asyncio.sleep(0.1)
            assert (await client.get(f"http://127.0.0.1:{port}/")).status_code == 200
        assert server.connections == 2

    async def test_a_silent_server_is_a_read_timeout(self, serve: _Serve) -> None:
        _, port = await serve(_never_answer)
        async with _client() as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.get(f"http://127.0.0.1:{port}/", timeout=0.1)

    async def test_a_closed_port_is_a_connect_error(self, serve: _Serve) -> None:
        server, port = await serve(_never_answer)
        await server.stop()
        async with _client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(f"http://127.0.0.1:{port}/")


def _contexts(tmp_path: Path) -> tuple[ssl.SSLContext, ssl.SSLContext]:
    """Server and client TLS contexts for a self-signed 127.0.0.1 certificate."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(cafile=str(cert_path))


class TestTls:
    async def test_keep_alive_over_tls(self, serve: _Serve, tmp_path: Path) -> None:
        server_ctx, client_ctx = _contexts(tmp_path)
        server, port = await serve(_answer_every_request, server_ctx)
        async with _client(client_ctx) as client:
            for _ in range(2):
                resp = await client.get(f"https://127.0.0.1:{port}/")
                assert resp.text == "hello"
        ssl_object = resp.extensions["network_stream"].get_extra_info("ssl_object")
        assert ssl_object.version().startswith("TLS")
        assert server.connections == 1

    async def test_a_tls_connection_the_server_closed_is_not_reused(
        self, serve: _Serve, tmp_path: Path
    ) -> None:
        server_ctx, client_ctx = _contexts(tmp_path)
        server, port = await serve(_answer_once, server_ctx)
        async with _client(client_ctx) as client:
            assert (await client.get(f"https://127.0.0.1:{port}/")).text == "hello"
            await asyncio.sleep(0.1)
            assert (await client.get(f"https://127.0.0.1:{port}/")).text == "hello"
        assert server.connections == 2

    async def test_an_untrusted_certificate_is_a_connect_error(
        self, serve: _Serve, tmp_path: Path
    ) -> None:
        server_ctx, _ = _contexts(tmp_path)
        _, port = await serve(_answer_every_request, server_ctx)
        async with _client() as client:
            with pytest.raises(httpx.ConnectError):
                await client.get(f"https://127.0.0.1:{port}/")


class TestGuardedBackend:
    def test_the_guard_connects_with_asyncio_streams(self) -> None:
        """httpx's default backend (anyio) runs TLS in Python; asyncio streams
        saved the HLS proxy about a quarter of its CPU on the Raspberry Pi
        (2026-10-05)."""
        backend = GuardedNetworkBackend(PrivateAddressGuard())
        assert isinstance(backend._backend, AsyncioNetworkBackend)
