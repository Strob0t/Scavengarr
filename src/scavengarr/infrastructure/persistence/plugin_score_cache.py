"""Plugin score persistence backed by CachePort (diskcache/redis)."""

from __future__ import annotations

import json
from datetime import datetime

import structlog

from scavengarr.domain.entities.scoring import (
    EwmaState,
    PluginScoreSnapshot,
)
from scavengarr.domain.ports.cache import CachePort

log = structlog.get_logger(__name__)

# Default TTL: 30 days in seconds.
_DEFAULT_TTL: int = 30 * 86_400

# Cache key for the snapshot index (list of all stored triples).
_INDEX_KEY: str = "score:_index"


def _snapshot_key(plugin: str, category: int, bucket: str) -> str:
    return f"score:{plugin}:{category}:{bucket}"


def _lastrun_key(
    probe_type: str,
    plugin: str,
    category: int | None = None,
    bucket: str | None = None,
) -> str:
    parts = [f"lastrun:{probe_type}:{plugin}"]
    if category is not None:
        parts.append(str(category))
    if bucket is not None:
        parts.append(bucket)
    return ":".join(parts)


def _serialize_ewma(state: EwmaState) -> dict:
    return {
        "value": state.value,
        "last_ts": state.last_ts.isoformat(),
        "n_samples": state.n_samples,
    }


def _deserialize_ewma(data: dict) -> EwmaState:
    return EwmaState(
        value=data["value"],
        last_ts=datetime.fromisoformat(data["last_ts"]),
        n_samples=data["n_samples"],
    )


def _serialize_snapshot(snap: PluginScoreSnapshot) -> str:
    return json.dumps(
        {
            "plugin": snap.plugin,
            "category": snap.category,
            "bucket": snap.bucket,
            "health_score": _serialize_ewma(snap.health_score),
            "search_score": _serialize_ewma(snap.search_score),
            "final_score": snap.final_score,
            "confidence": snap.confidence,
            "updated_at": snap.updated_at.isoformat(),
        }
    )


def _deserialize_snapshot(data: str) -> PluginScoreSnapshot:
    d = json.loads(data)
    return PluginScoreSnapshot(
        plugin=d["plugin"],
        category=d["category"],
        bucket=d["bucket"],
        health_score=_deserialize_ewma(d["health_score"]),
        search_score=_deserialize_ewma(d["search_score"]),
        final_score=d["final_score"],
        confidence=d["confidence"],
        updated_at=datetime.fromisoformat(d["updated_at"]),
    )


class CachePluginScoreStore:
    """Stores plugin score snapshots via CachePort.

    Key schema:
    - ``score:{plugin}:{category}:{bucket}`` → JSON PluginScoreSnapshot
    - ``score:_index`` → JSON list of ``[plugin, category, bucket]`` triples
    - ``lastrun:{type}:{plugin}[:{category}:{bucket}]`` → ISO timestamp
    """

    def __init__(self, cache: CachePort, ttl_days: int = 30) -> None:
        self.cache = cache
        self.ttl = ttl_days * 86_400

    async def get_snapshot(
        self, plugin: str, category: int, bucket: str
    ) -> PluginScoreSnapshot | None:
        key = _snapshot_key(plugin, category, bucket)
        data = await self.cache.get(key)
        if data is None:
            return None
        try:
            return _deserialize_snapshot(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            log.error("snapshot_deserialize_error", key=key, error=str(e))
            return None

    async def put_snapshot(self, snapshot: PluginScoreSnapshot) -> None:
        key = _snapshot_key(snapshot.plugin, snapshot.category, snapshot.bucket)
        await self.cache.set(key, _serialize_snapshot(snapshot), ttl=self.ttl)

        # Update the index.
        triple = [snapshot.plugin, snapshot.category, snapshot.bucket]
        index = await self._load_index()
        if triple not in index:
            index.append(triple)
            await self._save_index(index)

        log.debug(
            "snapshot_saved",
            plugin=snapshot.plugin,
            category=snapshot.category,
            bucket=snapshot.bucket,
        )

    async def list_snapshots(
        self, plugin: str | None = None
    ) -> list[PluginScoreSnapshot]:
        index = await self._load_index()
        results: list[PluginScoreSnapshot] = []
        for p, cat, bkt in index:
            if plugin is not None and p != plugin:
                continue
            snap = await self.get_snapshot(p, cat, bkt)
            if snap is not None:
                results.append(snap)
        return results

    async def get_last_run(
        self,
        probe_type: str,
        plugin: str,
        category: int | None = None,
        bucket: str | None = None,
    ) -> datetime | None:
        key = _lastrun_key(probe_type, plugin, category, bucket)
        data = await self.cache.get(key)
        if data is None:
            return None
        try:
            return datetime.fromisoformat(data)
        except (ValueError, TypeError):
            return None

    async def set_last_run(
        self,
        probe_type: str,
        plugin: str,
        ts: datetime,
        category: int | None = None,
        bucket: str | None = None,
    ) -> None:
        key = _lastrun_key(probe_type, plugin, category, bucket)
        await self.cache.set(key, ts.isoformat(), ttl=self.ttl)

    # -- internal helpers --------------------------------------------------

    async def _load_index(self) -> list[list]:
        data = await self.cache.get(_INDEX_KEY)
        if data is None:
            return []
        try:
            return json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return []

    async def _save_index(self, index: list[list]) -> None:
        await self.cache.set(_INDEX_KEY, json.dumps(index), ttl=self.ttl)
