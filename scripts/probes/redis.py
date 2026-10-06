"""Read-only look into the app's Redis cache.

    probe redis keys PATTERN [LIMIT]   matching keys with type and TTL (SCAN)
    probe redis ttl KEY                seconds left (-1: none, -2: missing)
    probe redis get KEY                the value's type, size and first 2000 bytes

Only SCAN, TYPE, TTL, STRLEN and GET: nothing is written. prodctl masks the
output (values hold video URLs and the client's address).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import redis.asyncio as aioredis

from scavengarr.infrastructure.config import load_config

PREVIEW = 2000


async def main(argv: list[str]) -> None:
    env = os.getenv("SCAVENGARR_CONFIG")
    config = load_config(config_path=Path(env) if env else None)
    if config.cache.backend != "redis":
        sys.exit(f"the cache backend is {config.cache.backend}, not redis")
    if len(argv) < 2 or argv[0] not in ("keys", "ttl", "get"):
        sys.exit(__doc__)
    client = aioredis.from_url(config.cache.redis_url)
    try:
        command, target = argv[0], argv[1]
        if command == "keys":
            limit = int(argv[2]) if len(argv) > 2 else 50
            count = 0
            async for key in client.scan_iter(match=target, count=500):
                count += 1
                if count <= limit:
                    kind = (await client.type(key)).decode()
                    print(key.decode(errors="replace"), kind, await client.ttl(key))
            print(f"[{min(count, limit)} of {count} keys]")
        elif command == "ttl":
            print(await client.ttl(target))
        else:
            kind = (await client.type(target)).decode()
            if kind != "string":
                print(f"type {kind}: only string values are shown")
                return
            value = await client.get(target) or b""
            print(f"type string, {await client.strlen(target)} bytes")
            print(repr(value[:PREVIEW]))
    finally:
        await client.aclose()


asyncio.run(main(sys.argv[1:]))
