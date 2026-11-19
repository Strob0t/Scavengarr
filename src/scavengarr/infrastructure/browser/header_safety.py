"""Which request headers a browser fetch may send with every request.

A plugin's headers can carry its site session (moflix's ``X-XSRF-TOKEN``).
A browser applies page-wide extra headers to every request the page makes —
subresources and redirect targets included — and a routed header override
follows a redirect too. Only the headers that are harmless off-site go
therefore with the page; the rest go with the in-page fetch, which stays on
the target's origin and refuses redirects (``_FETCH_RAW_JS``).
"""

from __future__ import annotations

from collections.abc import Mapping

# No credential, token or cookie: safe on any request the page makes.
SAFE_HEADERS = frozenset(
    {"accept", "accept-language", "referer", "user-agent", "x-requested-with"}
)


def safe_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """The headers of *headers* that are safe on every request of a page."""
    if not headers:
        return {}
    return {
        name: value for name, value in headers.items() if name.lower() in SAFE_HEADERS
    }
