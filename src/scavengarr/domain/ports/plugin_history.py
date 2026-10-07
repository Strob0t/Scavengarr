"""Port for the long-term record of what happens to each plugin.

Whether a plugin whose site died stays, is disabled by default or is removed
is the maintainer's decision from months of evidence, not a fixed threshold:
per plugin and UTC day, how often it was searched, how many results it gave,
how often it ran into its timeout, how often its site was checked and found
unreachable.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

PluginCounter = Literal["searches", "results", "timeouts", "checks", "unreachable"]


@runtime_checkable
class PluginHistoryPort(Protocol):
    """Counts the plugins' searches and checks, per plugin and day."""

    def count(self, plugin: str, counter: PluginCounter, n: int = 1) -> None:
        """Add *n* to *plugin*'s *counter* of today."""
        ...


class _NoPluginHistory:
    """Counts nothing: the default where no record is wired in."""

    def count(self, plugin: str, counter: PluginCounter, n: int = 1) -> None:
        del plugin, counter, n


NO_PLUGIN_HISTORY: PluginHistoryPort = _NoPluginHistory()
