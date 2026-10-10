"""Port for the long-term record of what happens to each plugin.

Whether a plugin whose site died stays, is disabled by default or is removed
is the maintainer's decision from months of evidence, not a fixed threshold:
per plugin and UTC day, how often it was searched, how many results it gave,
how often it ran into its timeout, how often its pages showed a challenge,
how often its site was checked and found unreachable.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Literal, Protocol, runtime_checkable

PluginCounter = Literal[
    "searches",
    "results",
    "timeouts",
    "dropped",
    "challenges",
    "checks",
    "unreachable",
    "unreachable_searches",
]


class ChallengeFlag:
    """Whether a page of the running search showed a challenge.

    The search runner sets a fresh flag for each plugin search; a plugin base
    marks it when a page answers with a challenge (``mark_challenge()``); the
    runner counts ``challenges`` once for the search when it ends, however
    many pages did.
    """

    __slots__ = ("seen",)

    def __init__(self) -> None:
        self.seen = False


challenge_flag: ContextVar[ChallengeFlag | None] = ContextVar(
    "challenge_flag", default=None
)


def mark_challenge() -> None:
    """Mark the running search's flag; nothing outside a search."""
    flag = challenge_flag.get()
    if flag is not None:
        flag.seen = True


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
