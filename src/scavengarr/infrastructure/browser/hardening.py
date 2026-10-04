"""What keeps the app's Chromium light: launch flags and blocked resources.

On the Raspberry Pi Chromium held 1.7 GB in 15 processes and used 4-9 s of
CPU per stream request (``docs/plans/pi-performance.md``). Contexts also
block service workers (``service_workers="block"`` where they are created):
a site the app visits once would keep one running and caching.
"""

from __future__ import annotations

from patchright.async_api import Route

# Main frames share at most 2 renderer processes; frames of other sites
# still get their own (site isolation): 6 instead of 7 renderers with 5
# open pages in production. Without site isolation it would be 2 (600 MB
# less), but then s.to's Turnstile gate failed (2 of 2 tries, 2026-10-04).
CHROMIUM_ARGS = ("--renderer-process-limit=2",)

BLOCKED_RESOURCE_TYPES = frozenset(
    {"image", "font", "stylesheet", "media", "texttrack"}
)


async def block_heavy_resources(route: Route) -> None:
    """Abort heavy resource types: scraping reads the DOM, not the layout."""
    if route.request.resource_type in BLOCKED_RESOURCE_TYPES:
        await route.abort()
    else:
        await route.continue_()
