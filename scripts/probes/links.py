"""Stored Stremio stream links: hoster, release title, HLS, CDN domain, age.

``probe links [ID ...]`` prints the given stream ids; without ids, the links
resolved in the last hour (Redis backend only: the probe scans the keys).
Read-only; prints no video URLs, only the CDN's domain.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import structlog

from scavengarr.infrastructure.cache.cache_factory import create_cache
from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.persistence.stream_link_cache import (
    CacheStreamLinkRepository,
)

RECENT_S = 3600
# The cache and repository log every read at debug level
structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(logging.WARNING)
)


def cdn(url: str) -> str:
    host = urlsplit(url).hostname or ""
    return ".".join(host.split(".")[-2:])


async def recent_ids(redis_url: str) -> list[str]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(redis_url)
    try:
        return sorted(
            [
                key.decode().rsplit("streamlink:", 1)[1]
                async for key in client.scan_iter(match="*streamlink:*", count=500)
            ]
        )
    finally:
        await client.aclose()


async def main(ids: list[str]) -> None:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    recent_only = not ids
    if recent_only:
        if config.cache.backend != "redis":
            sys.exit("pass stream ids: only the Redis backend can list links")
        ids = await recent_ids(config.cache.redis_url)
    cache = create_cache(
        backend=config.cache.backend,
        directory=str(config.cache.directory),
        redis_url=config.cache.redis_url,
    )
    shown = 0
    async with cache:
        repo = CacheStreamLinkRepository(cache)
        for stream_id in ids:
            link = await repo.get(stream_id)
            if link is None:
                print(stream_id, "missing")
                continue
            age = time.time() - link.resolved_at if link.resolved_at else None
            if recent_only and age is not None and age > RECENT_S:
                continue
            minutes = f"{age / 60:.0f} min" if age is not None else "?"
            print(
                f"{stream_id} | {link.hoster} | {link.title} | hls {link.is_hls}"
                f" | cdn {cdn(link.video_url or '')} | age {minutes}"
            )
            shown += 1
    print(f"[{shown} links]")


asyncio.run(main(sys.argv[1:]))
