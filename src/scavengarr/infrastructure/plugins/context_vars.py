"""Context variables for per-request Playwright isolation.

The ``request_browser_context`` ContextVar carries a per-request
BrowserContext so that ``PlaywrightPluginBase._ensure_context()``
returns an isolated context instead of the singleton instance.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

request_browser_context: ContextVar[BrowserContext | None] = ContextVar(
    "request_browser_context",
    default=None,
)
