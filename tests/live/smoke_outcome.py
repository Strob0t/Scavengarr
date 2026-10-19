"""The live smoke's reading of a plugin run that found nothing.

Free of browser and plugin imports, so its unit test loads it alone.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

# The lines of the base's fetch helpers (``_fetch_text()``, ``_safe_fetch()``)
# for a fetch that gave ``None``: a timeout, a transport error, an error answer
_FAILED_FETCH = ("timeout", "fetch_error", "http_error")
_DETAILS = ("context", "status")


def failed_fetch(events: Iterable[Mapping[str, object]], plugin: str) -> str | None:
    """The first failed fetch *plugin* logged among *events*, with its context
    and status, or ``None`` when every fetch answered."""
    lines = {f"{plugin}_{suffix}" for suffix in _FAILED_FETCH}
    for event in events:
        if event.get("event") in lines:
            details = ", ".join(f"{k}={event[k]}" for k in _DETAILS if k in event)
            return f"{event['event']} ({details})" if details else str(event["event"])
    return None
