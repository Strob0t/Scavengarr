"""hd-world.cc Python plugin for Scavengarr.

Scrapes hd-world.cc (German DDL archive) via the WordPress REST API:
- GET /wp-json/wp/v2/posts?search={query}&per_page=100 for search
- GET /wp-json/wp/v2/posts?per_page=100 for browse (empty query)

Covers movies and TV series. Downloads go through filecrypt.cc containers.
Single active domain: hd-world.cc (hd-world.org/to/tv are dead).
No authentication required.
"""

from __future__ import annotations

import re
from html import unescape

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["hd-world.cc"]
_MAX_PAGES = 10  # 100 per page -> 1000 results max

# WP category IDs -> Torznab category
_MOVIE_CAT_IDS = frozenset({10, 63253, 63254, 11, 63255})
_TV_CAT_IDS = frozenset({13, 14, 15})

# WP category IDs for API filtering (comma-separated)
_MOVIE_CATS_PARAM = "10,63253,63254,11,63255"
_TV_CATS_PARAM = "13,14,15"

# ---------------------------------------------------------------------------
# HTML content parsing helpers
# ---------------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")

_SIZE_RE = re.compile(
    r"Gr\S+e:</strong>\s*([\d.,]+\s*[KMGT]?B)",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"Dauer:.*?(\d+)\s*Min\.", re.IGNORECASE)
_IMDB_ID_RE = re.compile(r"imdb\.com/title/(tt\d+)", re.IGNORECASE)
_IMDB_RATING_RE = re.compile(r"IMDb:\s*([\d.]+)")
_DOWNLOAD_LINK_RE = re.compile(
    r'<a\s+href="(https?://filecrypt\.cc/[^"]+)"[^>]*>\s*([^<]+?)\s*</a>',
    re.IGNORECASE,
)
_POSTER_RE = re.compile(r'<img\s[^>]*src="([^"]+)"', re.IGNORECASE)


def _strip_html(html: str) -> str:
    """Remove HTML tags and unescape entities."""
    return unescape(_TAG_RE.sub("", html)).strip()


def _extract_description(content: str) -> str:
    """Extract first paragraph as description (before blockquote)."""
    bq_pos = content.find("<blockquote")
    text = content[:bq_pos] if bq_pos != -1 else content

    m = re.search(r"<p>(.*?)</p>", text, re.DOTALL)
    if not m:
        return ""
    desc = _strip_html(m.group(1))
    if len(desc) > 300:
        desc = desc[:297] + "..."
    return desc


def _extract_size(content: str) -> str | None:
    """Extract filesize like '10269 MB' from content HTML."""
    m = _SIZE_RE.search(content)
    return m.group(1).strip() if m else None


def _extract_duration(content: str) -> str:
    """Extract duration in minutes from content HTML."""
    m = _DURATION_RE.search(content)
    return m.group(1) if m else ""


def _extract_imdb_id(content: str) -> str:
    """Extract IMDb ID like 'tt0118566' from content HTML."""
    m = _IMDB_ID_RE.search(content)
    return m.group(1) if m else ""


def _extract_imdb_rating(content: str) -> str:
    """Extract IMDb rating like '6.0' from content HTML."""
    m = _IMDB_RATING_RE.search(content)
    return m.group(1) if m else ""


def _extract_download_links(content: str) -> list[dict[str, str]]:
    """Extract filecrypt.cc download links with hoster names."""
    links = []
    for m in _DOWNLOAD_LINK_RE.finditer(content):
        url = m.group(1)
        hoster = m.group(2).strip()
        if url and hoster:
            links.append({"hoster": hoster, "link": url})
    return links


def _extract_poster(content: str) -> str:
    """Extract poster image URL from content HTML."""
    m = _POSTER_RE.search(content)
    return m.group(1) if m else ""


def _determine_category(wp_categories: list[int], link: str) -> int:
    """Determine Torznab category from WP category IDs and post link."""
    cat_set = set(wp_categories)
    if cat_set & _TV_CAT_IDS:
        return 5000
    if cat_set & _MOVIE_CAT_IDS:
        return 2000
    # Fallback: check link path
    if "/serien/" in link:
        return 5000
    return 2000


class HdWorldPlugin(HttpxPluginBase):
    """Python plugin for hd-world.cc using the WordPress REST API."""

    name = "hdworld"
    provides = "download"
    default_language = "de"
    _domains = _DOMAINS

    # ------------------------------------------------------------------
    # WP REST API
    # ------------------------------------------------------------------

    async def _fetch_posts(
        self,
        *,
        search: str = "",
        wp_categories: str = "",
        page: int = 1,
    ) -> tuple[list[dict], int]:
        """Fetch posts from WP REST API.

        Returns (posts_list, total_pages).
        """
        params: dict[str, str | int] = {
            "per_page": 100,
            "page": page,
        }
        if search:
            params["search"] = search
        if wp_categories:
            params["categories"] = wp_categories

        resp = await self._safe_fetch(
            f"{self.base_url}/wp-json/wp/v2/posts",
            context="posts",
            params=params,
        )
        if resp is None:
            return [], 0

        data = self._safe_parse_json(resp, context="posts")
        if not isinstance(data, list):
            return [], 0

        total_pages = 1
        tp_header = resp.headers.get("X-WP-TotalPages", "")
        if tp_header.isdigit():
            total_pages = int(tp_header)

        return data, total_pages

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------

    def _build_result(self, post: dict) -> SearchResult | None:
        """Convert a WP post JSON object into a SearchResult."""
        title_raw = post.get("title", {})
        title = (
            _strip_html(title_raw.get("rendered", ""))
            if isinstance(title_raw, dict)
            else ""
        )
        if not title:
            return None

        content_raw = post.get("content", {})
        content = (
            content_raw.get("rendered", "") if isinstance(content_raw, dict) else ""
        )

        link = post.get("link", "")
        wp_cats = post.get("categories", [])
        if not isinstance(wp_cats, list):
            wp_cats = []

        category = _determine_category(wp_cats, link)
        description = _extract_description(content)
        size = _extract_size(content)
        duration = _extract_duration(content)
        imdb_id = _extract_imdb_id(content)
        imdb_rating = _extract_imdb_rating(content)
        download_links = _extract_download_links(content)
        poster = _extract_poster(content)

        # Download link = first filecrypt link, or post URL
        download_link = download_links[0]["link"] if download_links else link
        if not download_link:
            return None

        date_str = post.get("date", "")
        published_date = date_str[:10] if date_str else None
        source_url = link or download_link

        return SearchResult(
            title=title,
            download_link=download_link,
            download_links=download_links or None,
            source_url=source_url,
            release_name=title,
            size=size,
            published_date=published_date,
            category=category,
            description=description or None,
            metadata={
                "imdb_id": imdb_id,
                "rating": imdb_rating,
                "runtime": duration,
                "poster": poster,
            },
        )

    # ------------------------------------------------------------------
    # Main search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        """Search hd-world.cc via WordPress REST API.

        Supports movie (2000) and TV (5000) categories.
        """
        wp_cats_param = ""
        if category is not None:
            if 2000 <= category < 3000:
                wp_cats_param = _MOVIE_CATS_PARAM
            elif 5000 <= category < 6000:
                wp_cats_param = _TV_CATS_PARAM
            else:
                return []

        await self._ensure_client()
        await self._verify_domain()

        results: list[SearchResult] = []
        for page in range(1, _MAX_PAGES + 1):
            posts, total_pages = await self._fetch_posts(
                search=query,
                wp_categories=wp_cats_param,
                page=page,
            )

            if not posts:
                break

            for post in posts:
                sr = self._build_result(post)
                if sr is not None:
                    results.append(sr)
                    if len(results) >= self.effective_max_results:
                        return results

            if page >= total_pages:
                break

        self._log.info(
            "hdworld_search",
            query=query,
            count=len(results),
        )

        return results


plugin = HdWorldPlugin()
