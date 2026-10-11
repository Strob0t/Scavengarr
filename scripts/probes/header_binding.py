"""Stored resolutions from the player's side: which CDNs need the playback headers.

Usage (dev container; ``-P`` because this directory shadows the redis package):
    PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/header_binding.py
    ... --source dev|production|both --hosters voe,vidoza --since 3600
    ... --ids ID ... --urls .cache/live/resolver-urls.json --out FILE

A file that is neither HLS nor address-bound reaches Stremio through
``/play``, a redirect to the CDN: the player fetches the video URL itself,
with its own User-Agent and without the playback headers the resolver
stored (``Referer``, ``Origin``), while the registry's checks
(``verify_video_url()``, ``check_playable()``) send them. The probe asks the
CDN of each stored resolution for the first 2 KiB (a ``Range`` GET,
redirects followed, a fresh client per request, so no cookie carries over),
in up to four ways:

- ``check``: the playback check's headers (the stored ones over the browser
  User-Agent), which the file pass-through ``/proxy/<id>/file`` sends too;
- ``bare``: a player, a Firefox User-Agent and nothing else;
- ``player UA``: the stored headers with the Firefox User-Agent, and
- ``check UA``: the check's User-Agent without the stored headers, both only
  when headers other than the User-Agent are stored.

The verdict names what the bare player lacks: ``free`` (it gets media),
``user-agent``, ``headers``, ``user-agent + headers`` or ``user-agent or
headers``; ``dead here`` when the check itself gets no media from this
network (bound to another address, expired or dead), ``no answer`` when it
gets no answer. Media is an HLS playlist or the first bytes of MPEG-TS, MP4
or Matroska (``media_kind()`` of ``scripts/stremio_playcheck.py``).

Sources: ``dev`` resolves the live suite's hoster links (``--urls``, written
by ``scripts/probes/resolver_urls.py``) with the app's registry here, so a
file bound to the resolving address plays from here and only header binding
shows; ``production`` reads production's links resolved in the last
``--since`` seconds (or the ``--ids``) through Portainer (``PORTAINER_URL``
and ``PORTAINER_API_KEY`` as in ``scripts/portainer.py``): resolved from the
Pi's address and fetched from here, as a player on the LAN fetches them.
Prints hosters, titles, CDN domains, header names, statuses and content
kinds, never a URL, a header value or a stream id; changes nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx
import structlog

from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.plugins.constants import DEFAULT_USER_AGENT
from scavengarr.interfaces.app import create_app
from scavengarr.interfaces.app_state import AppState

# scripts/ holds the Portainer client and the play check's media test:
# appended, so that nothing there shadows an installed package
sys.path.append(str(Path(__file__).resolve().parents[1]))
from prodctl import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    portainer_client,
)
from stremio_playcheck import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    is_media,
    media_kind,
)

REPO = Path(__file__).resolve().parents[2]
URLS = REPO / ".cache" / "live" / "resolver-urls.json"
SNIFF_BYTES = 2048
FETCH_TIMEOUT = 10.0
RESOLVE_TIMEOUT = 90.0
# A player other than the app: Firefox, the browser of the reports
PLAYER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"
)
WAYS = ("check", "bare", "player UA", "check UA")
# Not what the bare player lacks: the probe's other verdicts
UNBOUND = frozenset({"free", "dead here", "no answer"})

# Runs inside the container (``python -c <DUMP> <since> [ids]``): the stored
# links as JSON lines, read by this probe and never printed
DUMP = r"""
import asyncio, json, logging, os, sys, time
from pathlib import Path

import structlog

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.ERROR)
)

from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
)


async def main(since, ids):
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    if not ids:
        if config.cache.backend != "redis":
            sys.exit("pass stream ids: only the Redis backend can list links")
        import redis.asyncio as aioredis

        client = aioredis.from_url(config.cache.redis_url)
        try:
            ids = sorted(
                [
                    key.decode().rsplit("streamlink:", 1)[1]
                    async for key in client.scan_iter(
                        match="*streamlink:*", count=500
                    )
                ]
            )
        finally:
            await client.aclose()
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    async with cache:
        repo = CacheStreamLinkRepository(cache)
        for stream_id in ids:
            link = await repo.get(stream_id)
            if link is None or not link.video_url:
                continue
            if since > 0 and time.time() - link.resolved_at > since:
                continue
            print(
                json.dumps(
                    {
                        "hoster": link.hoster,
                        "title": link.title,
                        "video_url": link.video_url,
                        "video_headers": link.video_headers,
                        "is_hls": link.is_hls,
                        "address_bound": link.address_bound,
                    }
                )
            )


asyncio.run(main(float(sys.argv[1]), sys.argv[2:]))
"""


@dataclass(frozen=True)
class Link:
    """A stored resolution: where it came from and what the player gets."""

    source: str
    hoster: str
    title: str = ""
    video_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    is_hls: bool = False
    address_bound: bool = False
    # Why there is no video URL (a dev link that did not resolve)
    note: str = ""

    @property
    def route(self) -> str:
        """How Stremio reaches it: ``proxy`` (HLS), ``file`` (an
        address-bound file's pass-through) or ``play`` (a redirect)."""
        if self.is_hls:
            return "proxy"
        if self.address_bound:
            return "file"
        return "play"


@dataclass(frozen=True)
class Answer:
    """What the CDN answered one way of asking: the status (0 without an
    answer) and what the first bytes are (the exception's name without)."""

    status: int
    kind: str

    @property
    def media(self) -> bool:
        return self.status in (200, 206) and (
            self.kind == "playlist" or is_media(self.kind)
        )

    def __str__(self) -> str:
        return f"{self.status} {self.kind}" if self.status else self.kind


def sniff(content_type: str, head: bytes) -> str:
    """``playlist``, ``empty`` and ``html`` (by type) before the play
    check's kinds (``mpegts``, ``mp4``, ``mkv``, ``html``, ``bytes:<hex>``)."""
    if head.lstrip().startswith(b"#EXTM3U"):
        return "playlist"
    if not head:
        return "empty"
    if "text/html" in content_type:
        return "html"
    return media_kind(head)


def ways(headers: dict[str, str]) -> dict[str, dict[str, str]]:
    """The request headers per way of asking (``WAYS``); a stored
    User-Agent is the check's, as in ``check_playable()``."""
    stored_ua = next(
        (value for key, value in headers.items() if key.lower() == "user-agent"),
        None,
    )
    others = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
    check_ua = stored_ua or DEFAULT_USER_AGENT
    asked = {
        "check": {"User-Agent": check_ua, **others},
        "bare": {"User-Agent": PLAYER_UA},
    }
    if others:
        asked["player UA"] = {"User-Agent": PLAYER_UA, **others}
        asked["check UA"] = {"User-Agent": check_ua}
    return asked


def verdict(answers: dict[str, Answer]) -> str:
    """What the bare player lacks, from the answers per way of asking."""
    check = answers["check"]
    if not check.media:
        return "dead here" if check.status else "no answer"
    if answers["bare"].media:
        return "free"
    if "player UA" not in answers:
        return "user-agent"
    user_agent = not answers["player UA"].media
    headers = not answers["check UA"].media
    if user_agent and headers:
        return "user-agent + headers"
    if user_agent:
        return "user-agent"
    if headers:
        return "headers"
    return "user-agent or headers"


async def fetch(url: str, headers: dict[str, str]) -> Answer:
    """The first ``SNIFF_BYTES`` of *url*, asked with *headers* only: a fresh
    client, so no cookie of an earlier answer goes along."""
    asked = {**headers, "Range": f"bytes=0-{SNIFF_BYTES - 1}"}
    try:
        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=FETCH_TIMEOUT) as client,
            client.stream("GET", url, headers=asked) as resp,
        ):
            head = b""
            async for chunk in resp.aiter_bytes():
                head += chunk
                if len(head) >= SNIFF_BYTES:
                    break
            content_type = resp.headers.get("content-type", "").lower()
            return Answer(resp.status_code, sniff(content_type, head[:SNIFF_BYTES]))
    except httpx.HTTPError as exc:
        return Answer(0, type(exc).__name__)


async def ask(link: Link, pause: float) -> dict[str, Answer]:
    """*link*'s CDN asked every way, one after another, *pause* apart."""
    answers: dict[str, Answer] = {}
    for position, (way, headers) in enumerate(ways(link.headers).items()):
        if position:
            await asyncio.sleep(pause)
        answers[way] = await fetch(link.video_url, headers)
    return answers


def parse_dump(output: str) -> list[Link]:
    """The container's JSON lines as links, one per video URL (a pinned copy
    of a link repeats it); other lines (warnings) are skipped."""
    links: list[Link] = []
    seen: set[str] = set()
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            continue
        url = str(data.get("video_url") or "")
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            headers = json.loads(data.get("video_headers") or "{}")
        except ValueError:
            headers = {}
        if not isinstance(headers, dict):
            headers = {}
        links.append(
            Link(
                "production",
                str(data.get("hoster") or "?"),
                str(data.get("title") or ""),
                url,
                {str(k): str(v) for k, v in headers.items()},
                bool(data.get("is_hls")),
                bool(data.get("address_bound")),
            )
        )
    return links


def production_links(container: str, since: float, ids: list[str]) -> list[Link]:
    """Production's stored links through Portainer: resolved in the last
    *since* seconds, or the *ids*. A failed dump reports its exit code only:
    its output may hold URLs."""
    output, code = portainer_client(container).run(
        ["python", "-c", DUMP, str(0 if ids else since), *ids]
    )
    if code != 0:
        raise SystemExit(f"the container's link dump failed (exit {code})")
    return parse_dump(output)


async def dev_links(urls: dict[str, str]) -> list[Link]:
    """The live suite's hoster links resolved here by the app's registry."""
    links: list[Link] = []
    with tempfile.TemporaryDirectory() as tmp:
        config = load_config(
            cli_overrides={
                "plugin_dir": REPO / "plugins",
                "cache_dir": Path(tmp) / "cache",
                # no background plugin searches next to the probe
                "scoring": {"enabled": False},
            }
        )
        app = create_app(config)
        async with app.router.lifespan_context(app):
            registry = cast(AppState, app.state).hoster_resolver_registry
            assert registry is not None
            for name, url in sorted(urls.items()):
                try:
                    stream = await asyncio.wait_for(
                        registry.resolve(url), RESOLVE_TIMEOUT
                    )
                except (TimeoutError, httpx.HTTPError, OSError) as exc:
                    links.append(
                        Link("dev", name, note=f"not resolved ({type(exc).__name__})")
                    )
                    continue
                if stream is None:
                    links.append(Link("dev", name, note="not resolved"))
                    continue
                links.append(
                    Link(
                        "dev",
                        name,
                        video_url=stream.video_url,
                        headers=dict(stream.headers),
                        is_hls=stream.is_hls,
                        address_bound=stream.address_bound,
                    )
                )
    return links


def cdn(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return ".".join(host.split(".")[-2:])


def _cell(text: str, width: int = 40) -> str:
    return text.replace("|", "/")[:width] or "–"


def result(link: Link, answers: dict[str, Answer]) -> str:
    """The link's verdict, or why it has none."""
    return verdict(answers) if answers else link.note or "not asked"


TABLE_HEAD = (
    "| Source | Hoster | Title | Route | CDN | Stored headers | check | bare"
    " | player UA | check UA | Verdict |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|"
)


def row(link: Link, answers: dict[str, Answer]) -> str:
    """One link's line: header names, never their values."""
    names = ", ".join(sorted(link.headers)) or "–"
    cells = " | ".join(str(answers[w]) if w in answers else "–" for w in WAYS)
    return (
        f"| {link.source} | {link.hoster} | {_cell(link.title)} | {link.route}"
        f" | {cdn(link.video_url) or '–'} | {_cell(names, 60)} | {cells}"
        f" | {result(link, answers)} |"
    )


def summary(results: list[tuple[Link, dict[str, Answer]]]) -> str:
    """The verdicts per hoster and route, and the hosters the bare player
    cannot play on the ``play`` route: the report's candidates."""
    counts: defaultdict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for link, answers in results:
        counts[(link.hoster, link.route)][
            f"{link.source}: {result(link, answers)}"
        ] += 1
    lines = ["| Hoster | Route | Verdicts |", "|---|---|---|"]
    for (hoster, route), verdicts in sorted(counts.items()):
        listed = ", ".join(f"{v} ×{n}" for v, n in sorted(verdicts.items()))
        lines.append(f"| {hoster} | {route} | {listed} |")
    bound = sorted(
        {
            link.hoster
            for link, answers in results
            if answers and link.route == "play" and verdict(answers) not in UNBOUND
        }
    )
    lines.append("")
    lines.append(
        "Bound on the play route (what the player lacks there): "
        + (", ".join(bound) or "none")
    )
    return "\n".join(lines)


def render(results: list[tuple[Link, dict[str, Answer]]]) -> str:
    return "\n".join(
        [TABLE_HEAD, *(row(link, answers) for link, answers in results)]
    ) + ("\n\n" + summary(results))


async def run(args: argparse.Namespace) -> list[tuple[Link, dict[str, Answer]]]:
    """Each source's links, then each link asked every way."""
    only = set(args.hosters.split(",")) if args.hosters else None
    results: list[tuple[Link, dict[str, Answer]]] = []

    async def asked(links: list[Link]) -> None:
        for link in links:
            if only is not None and link.hoster not in only:
                continue
            answers = await ask(link, args.pause) if link.video_url else {}
            print(
                f"  {link.source} {link.hoster}: {result(link, answers)}",
                file=sys.stderr,
            )
            results.append((link, answers))

    if args.source in ("production", "both"):
        await asked(
            await asyncio.to_thread(
                production_links, args.container, args.since, args.ids
            )
        )
    if args.source in ("dev", "both"):
        urls = json.loads(args.urls.read_text()) if args.urls.is_file() else {}
        if only is not None:
            urls = {name: url for name, url in urls.items() if name in only}
        await asked(await dev_links({str(k): str(v) for k, v in urls.items()}))
    return results


def parser() -> argparse.ArgumentParser:
    parsed = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parsed.add_argument(
        "--source", choices=("dev", "production", "both"), default="both"
    )
    parsed.add_argument("--hosters", help="only these, comma-separated")
    parsed.add_argument(
        "--since", type=float, default=3600.0, help="production: resolved within"
    )
    parsed.add_argument(
        "--ids", nargs="*", default=[], help="production: these stream ids"
    )
    parsed.add_argument("--urls", type=Path, default=URLS, help="dev: hoster links")
    parsed.add_argument("--container", default="scavengarr")
    parsed.add_argument(
        "--pause", type=float, default=0.5, help="seconds between two requests"
    )
    parsed.add_argument("--out", type=Path, help="append the tables to this file")
    return parsed


def main(argv: list[str] | None = None) -> int:
    # The app's warnings name hoster URLs: the output must not
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL)
    )
    args = parser().parse_args(argv)
    text = render(asyncio.run(run(args)))
    print(text)
    if args.out is not None:
        with args.out.open("a", encoding="utf-8") as handle:
            handle.write(f"\n{text}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
