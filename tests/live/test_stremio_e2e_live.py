"""Live end-to-end use case: Stremio lists streams for a title and one plays.

Runs the real app in-process (composition lifespan: plugins, resolvers,
stealth browser) and walks the path a Stremio client takes: stream list →
stream URL (HLS proxy or direct CDN, with ``proxyHeaders``) → playlist →
first media segment. Links are scraped fresh on every run, so the test
tracks plugins and resolvers as the sites change.

Opt-in: ``poetry run pytest -m live tests/live/test_stremio_e2e_live.py``
(headful browser: wrap in ``xvfb-run -a`` without a display).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
import pytest

from scavengarr.infrastructure.config.load import load_config
from scavengarr.interfaces.app import create_app

pytestmark = pytest.mark.live

_BASE = "http://scavengarr.test"
_REPO = Path(__file__).resolve().parents[2]

# (Stremio stream path, label): a popular film and a series episode with
# German streams on several plugins
_TITLES = [
    ("movie/tt15398776.json", "Oppenheimer (2023)"),
    ("series/tt0903747:1:1.json", "Breaking Bad S01E01"),
]


@pytest.fixture
async def app_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    config = load_config(
        cli_overrides={
            "plugin_dir": _REPO / "plugins",
            "cache_dir": tmp_path / "cache",
            # no background plugin searches next to the use case
            "scoring": {"enabled": False},
        }
    )
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url=_BASE, timeout=300
        ) as client:
            yield client


async def _get(
    app_client: httpx.AsyncClient,
    cdn: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    """Stream URLs point at the app's HLS proxy or straight at a CDN."""
    client = app_client if url.startswith(_BASE) else cdn
    # Range: a direct MP4 must not be downloaded whole
    return await client.get(url, headers={**headers, "Range": "bytes=0-65535"})


async def _play(
    app_client: httpx.AsyncClient, cdn: httpx.AsyncClient, stream: dict[str, Any]
) -> str | None:
    """Fetch the stream like a player: playlist(s), then the first segment.

    Returns None when media bytes arrive, else the reason it failed.
    """
    hints = stream.get("behaviorHints", {})
    headers = dict(hints.get("proxyHeaders", {}).get("request", {}))
    resp = await _get(app_client, cdn, stream["url"], headers)
    for _ in range(2):  # master playlist → media playlist → segment
        if resp.status_code not in (200, 206):
            return f"HTTP {resp.status_code}"
        if not _is_playlist(resp):
            break
        uris = [ln for ln in resp.text.splitlines() if ln and not ln.startswith("#")]
        if not uris:
            return "empty playlist"
        resp = await _get(app_client, cdn, urljoin(str(resp.url), uris[0]), headers)
    if resp.status_code not in (200, 206) or not resp.content:
        return f"segment HTTP {resp.status_code}"
    if _is_text(resp) and "<html" in resp.text[:200].lower():
        return "HTML instead of media"
    return None


def _is_text(resp: httpx.Response) -> bool:
    ctype = resp.headers.get("content-type", "")
    return "mpegurl" in ctype or ctype.startswith("text/") or ctype == ""


def _is_playlist(resp: httpx.Response) -> bool:
    return _is_text(resp) and resp.text.startswith("#EXTM3U")


@pytest.mark.parametrize(("path", "label"), _TITLES, ids=[t[1] for t in _TITLES])
async def test_stremio_stream_plays(
    app_client: httpx.AsyncClient, path: str, label: str
) -> None:
    resp = await app_client.get(f"/api/v1/stremio/stream/{path}")
    assert resp.status_code == 200
    streams = resp.json().get("streams", [])
    if not streams:
        pytest.skip(f"{label}: no streams (plugins blocked or sites down)")

    failures: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=60) as cdn:
        for stream in streams:
            reason = await _play(app_client, cdn, stream)
            if reason is None:
                return  # one playable stream is the use case
            failures.append(f"{stream.get('description', '?')}: {reason}")

    pytest.fail(f"{label}: none of {len(streams)} streams played: {failures}")
