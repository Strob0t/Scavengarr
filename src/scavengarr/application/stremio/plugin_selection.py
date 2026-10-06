"""The plugins a Stremio request searches.

Every plugin that provides streams, or, with scored selection on, the best
scored of them plus now and then one more (the exploration slot). See
``docs/features/plugin-scoring-and-probing.md``.
"""

from __future__ import annotations

import random
from typing import Protocol

import structlog

from scavengarr.application.stremio.plugin_search import current_snapshots
from scavengarr.domain.ports.plugin_registry import PluginRegistryPort
from scavengarr.domain.ports.plugin_score_store import PluginScoreStorePort

log = structlog.get_logger(__name__)


class PluginSelectionConfig(Protocol):
    """The scored selection's switch and limits."""

    scoring_enabled: bool
    max_plugins_scored: int
    exploration_probability: float


class PluginSelector:
    """The stream plugins, and which of them a request searches."""

    def __init__(
        self,
        *,
        plugins: PluginRegistryPort,
        score_store: PluginScoreStorePort | None,
        config: PluginSelectionConfig,
    ) -> None:
        self._plugins = plugins
        self._score_store = score_store
        self._scoring_enabled = config.scoring_enabled
        self._max_plugins_scored = config.max_plugins_scored
        self._exploration_probability = config.exploration_probability

    def stream_plugins(self) -> list[str]:
        """Every plugin that provides streams (``stream`` or ``both``), sorted."""
        plugin_names = self._plugins.get_by_provides("stream")
        both_names = self._plugins.get_by_provides("both")
        return sorted(set(plugin_names + both_names))

    async def select(self, all_names: list[str], category: int) -> list[str]:
        """Select plugins to search, using scores when available.

        When scoring is disabled or no scores exist yet, returns all
        plugins (graceful cold-start fallback).

        When scoring is active, selects the top-N plugins by
        ``final_score`` and optionally adds one random exploration slot.
        """
        if not self._scoring_enabled or self._score_store is None:
            return all_names

        # Collect scores for each plugin (using "current" bucket as proxy)
        snapshots = await current_snapshots(self._score_store, all_names, category)
        scored: list[tuple[str, float, float]] = [
            (name, snap.final_score, snap.confidence)
            if (snap := snapshots.get(name)) is not None
            else (name, 0.5, 0.0)
            for name in all_names
        ]

        # Cold-start guard: need at least 50% of plugins with confidence > 0.1
        confident_count = sum(1 for _, _, c in scored if c > 0.1)
        if confident_count < len(all_names) * 0.5:
            log.debug(
                "scored_selection_cold_start",
                confident=confident_count,
                total=len(all_names),
            )
            return all_names

        # Sort by final_score descending, pick top-N
        scored.sort(key=lambda x: x[1], reverse=True)
        top_n = scored[: self._max_plugins_scored]
        selected_names = [name for name, _, _ in top_n]

        # Exploration slot: with probability, add one mid-score plugin
        remaining = [
            (name, score, conf)
            for name, score, conf in scored[self._max_plugins_scored :]
            if conf >= 0.1
        ]
        if remaining and random.random() < self._exploration_probability:
            explorer = random.choice(remaining)
            selected_names.append(explorer[0])

        log.info(
            "scored_plugin_selection",
            top_n=[f"{n}:{s:.2f}" for n, s, _ in top_n],
            exploration=len(selected_names) > self._max_plugins_scored,
            total_available=len(all_names),
        )
        return selected_names
