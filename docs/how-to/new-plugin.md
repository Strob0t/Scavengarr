# Adding a plugin

> **Status:** how-to
> **As of:** 5b4303e4

## Goal

A new site searchable through Torznab and, for a streaming site, through the Stremio addon: one file in `plugins/`, its tests, and its line in the generated plugin list.
The contract the plugin must meet and the base classes it builds on are described in [Plugins](../current/plugins.md).

## Prerequisites

- The dev container, with `playwright-mcp` available in Claude Code (it runs over stdio from `.mcp.json`; `.devcontainer/setup.sh` installs its Chromium).
- The site's JDownloader plugin in `.devdata/JDownloader2/`, for its domains and its notes on dead and fake domains.
- For a site with a login, an account of the maintainer's, passed as `SCAVENGARR_<PLUGIN>_USERNAME` and `SCAVENGARR_<PLUGIN>_PASSWORD`; never in code.

## Steps

### 1. Analyse the site first

Inspect every relevant page with `playwright-mcp` before writing code: the search, the categories, a detail page and the download or stream links.
Note the selectors, the link and URL patterns, the pagination and the page size.
Check for JavaScript dependencies (a Cloudflare or DDoS-Guard challenge, content loaded by script, a single-page app) and for authentication (login, cookies, tokens).
Prefer targeted `browser_evaluate` or `browser_find` calls over full snapshots.

### 2. Choose the base class

- `HttpxPluginBase` for server-rendered HTML and JSON APIs, a Cloudflare challenge included: fetch such a site's pages with `_fetch_text()`, which passes the challenge in the browser and goes on with its session.
- `PlaywrightPluginBase` for sites that need JavaScript, DDoS-Guard or a login in the browser.
- A shared base where the site runs a backend that already has one (`DataApiPluginBase`, `XenForoPluginBase`): the plugin then sets only `name`, `provides`, `_domains` and what the base asks for.

Never copy what a base already does: the client, the domain fallback, the cleanup, the semaphore and the User-Agent.
Playwright plugins import from `patchright.async_api`; `playwright` is not a dependency.

### 3. Lay out the file

A standalone plugin starts with a "Configurable settings" block, followed by its constants:

```python
from __future__ import annotations

from scavengarr.domain.plugins.base import SearchResult
from scavengarr.infrastructure.plugins.httpx_base import HttpxPluginBase

# ---------------------------------------------------------------------------
# Configurable settings
# ---------------------------------------------------------------------------
_DOMAINS = ["my-site.example", "my-site.example.org"]  # fallback order
_MAX_PAGES = 50  # 20 results per page -> 50 pages for 1000

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_CATEGORY_MAP = {2000: "movies", 5000: "series"}


class MySitePlugin(HttpxPluginBase):
    name = "my-site"
    provides = "stream"
    _domains = _DOMAINS

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        await self._verify_domain()
        ...


plugin = MySitePlugin()
```

Set `name`, `provides` and `_domains`; set `languages` for a site that is not German (`["en"]`), and override `_max_results`, `_max_concurrent` or `cache_ttl` only where the site needs it.
`_domains` lists genuine domains only: popular sites have look-alike clones that swap the player for ad or scam redirects.
A site that publishes an official list of its addresses also gets `domain_list_url` and a `domain_marker` (see [Plugin domains](../current/plugin-domains.md)).
Sites that front the same database as an existing plugin share its `mirror_group`.

### 4. Implement the search

Follow the search stages of [Plugins](../current/plugins.md#search-stages):

1. Answer the category with `served_category()`: a category the site lacks returns `[]` without a request; pass the served category to the site's own filter (a URL segment, a dropdown id, a forum id).
2. Page through the results until `self.effective_max_results` items or no further page; set `_MAX_PAGES` from the page size (200 per page gives 5, 50 per page 20, 10 per page 100).
3. Keep the relevant hits with `relevant_hits()`, with `limit=SINGLE_TITLE_HITS` when the request names a season; drop duplicate detail URLs.
4. Fetch the detail pages in parallel under `self._new_semaphore()`.
5. Build each `SearchResult` from the site's data: `title` and `download_link`, `download_links` for every hoster, `size`, `release_name`, `source_url`, the `category` the site gives (films and series through `stream_category()`), and `metadata["year"]` (the int start year) and `metadata["genres"]` (comma-joined) where the page has them.
6. Keep the requested category with `filter_by_category()`.

```python
all_results: list[SearchResult] = []
for page_num in range(1, _MAX_PAGES + 1):
    html = await self._fetch_text(f"{search_url}&page={page_num}", context="search")
    if html is None:
        break
    items = await self._parse_results(html)
    if not items:
        break
    all_results.extend(items)
    if len(all_results) >= self.effective_max_results:
        break

sem = self._new_semaphore()


async def _bounded(url: str) -> SearchResult | None:
    async with sem:
        return await self._scrape_detail(url)


gathered = await asyncio.gather(*(_bounded(u) for u in detail_urls), return_exceptions=True)
results = [r for r in gathered if isinstance(r, SearchResult)]
```

Encode search terms (`quote_plus`, or `quote(term, safe="")` in a path segment), resolve relative links with `urljoin(self.base_url, href)`, and read URLs from attributes (`href`, `data-url`, an embed's `src`), not from text.
On a missing optional field return a partial result and log a warning; skip an item without its required fields instead of aborting the search.
A series page that lists every episode labels its links with `episode_label()`, and the plugin keeps the requested one with `filter_episodes()`.
Links on the plugin's own host that redirect off-site go through `_resolve_result_links()`, on the capped result list only.

### 5. Parse pages with selectolax

Parsers read pages with selectolax (`LexborHTMLParser`) and CSS selectors.
A parser is a small class with `feed(html)` and public result attributes, and every page goes through `await parse_page(parser, html)` of `infrastructure/plugins/dom.py`, which parses pages from 32 KiB in a worker thread.

```python
from selectolax.lexbor import LexborHTMLParser

from scavengarr.infrastructure.plugins.dom import parse_page


class _SearchResultParser:
    def __init__(self, base_url: str) -> None:
        self.results: list[dict[str, str]] = []
        self._base_url = base_url

    def feed(self, html: str) -> None:
        for card in LexborHTMLParser(html).css("div.card"):
            links = card.css("h2 a[href]")
            if links:
                href = links[-1].attributes.get("href") or ""
                self.results.append(
                    {"title": links[-1].text().strip(), "url": urljoin(self._base_url, href)}
                )


parser = await parse_page(_SearchResultParser(self.base_url), html)
```

The pitfalls, also noted in `dom.py`:

- `node.css(selector)` searches the subtree including the node itself, and `node.css_matches(selector)` tests the whole subtree, not the node.
- Nested cards match twice: `outermost(tree.css(selector))` keeps the matches not nested in another match.
- A group selector returns a node once per part it matches: put alternatives into `:is()`.
- `LexborNode.__eq__` compares the nodes' HTML: compare `node.mem_id` for identity.
- Without a doctype, class and `#id` selectors ignore case, while attribute selectors stay exact.
- `node.text()` is the deep text, with `<script>` and `<style>` content included.
- lexbor builds the tree like a browser: it closes unclosed elements and inserts an implied `<tbody>`.

### 6. For a Playwright plugin

Take pages from `_new_page()` and close them in a `finally`, never through `self._context`: under `isolated_search()` the active context is a per-request one.
Navigate with `_navigate_and_wait()` or `_fetch_page_html()`, which pass a Cloudflare challenge on the way.
Wait on conditions (`wait_for_selector`, `wait_for_function`, `wait_for_load_state`) with explicit timeouts, never with `asyncio.sleep()`.
A plugin that logs in hands its session to the per-request context through `_prepare_context()`, as `plugins/boerse.py` does.
`page.evaluate()` runs in an isolated world by default; pass `isolated_context=False` to reach the site's own scripts.
Lower `_max_concurrent` for a heavy or fragile site.

### 7. Write the tests

Write `tests/unit/infrastructure/test_<name>_plugin.py` first, after an existing plugin's test with the same base class.
Plugin files are not a package: the tests load them by path with `importlib.util.spec_from_file_location()`.
Mock HTTP with `respx` (or the plugin's client), and patch `scavengarr.infrastructure.plugins.playwright_base.async_playwright` for a Playwright plugin.
A plugin test needs no ports; a test of the core around a plugin passes `NO_TELEMETRY` and a real `ConcurrencyPool`, and mocks the other ports as [AGENTS.md](../../AGENTS.md) (section 6, Testing) lists.

Test the parsers on the site's own pages too:

```bash
poetry run python scripts/capture_pages.py <name> "<query>" [--category N --season S --episode E]
poetry run python scripts/capture_pages.py <name> --fixture <page> <fixture-name>
```

The first command records every page a live search fetches under `.cache/pages/<name>/`; the second stores one of them scrubbed and gzipped under `tests/fixtures/html/<name>/`.
`tests/unit/infrastructure/test_real_pages.py` asserts values read off those pages.
Add a live smoke entry under `tests/live/`.

### 8. Regenerate the plugin list

```bash
poetry run python scripts/generate_plugin_list.py
```

The list in [the plugin reference](../reference/plugins.md) is generated from the plugin metadata; `tests/unit/infrastructure/test_plugin_list_doc.py` fails while it is outdated.
Run it again after renaming a plugin or changing its `provides`, `_domains`, `languages` or base class.

## Verify

- `poetry run pytest tests/unit/infrastructure/test_<name>_plugin.py` passes, and so do `poetry run pre-commit run --all-files` and `poetry run pytest -n auto`.
- `poetry run pytest -m live -k <name> -v` finds results on the real site.
- With the dev server running (`PYTHONPATH=src poetry run start` in a worktree), `curl "http://localhost:7979/api/v1/torznab/<name>?t=search&q=<query>"` answers results.

The plugin meets this checklist:

- It inherits from a plugin base and sets up no client of its own.
- Its configurable settings sit at the top of the file.
- It filters categories through the site's own filters, mapped to Torznab categories.
- It pages up to 1000 items, with `_MAX_PAGES` from the page size.
- It fetches detail pages under `self._new_semaphore()`, for the relevant hits only.
- Its `search()` takes `season` and `episode`.
- It sets `provides` and, for a site that is not German, `languages`.
- It has unit tests, real-page tests and a live smoke entry.
- It returns partial results on missing fields instead of failing.
- It fetches through the base's helpers (`_fetch_text()`, `_safe_fetch()`, `_safe_parse_json()`).

## Troubleshooting

A plugin that returns nothing usually means the site changed, not the parser's tests: the offline suite keeps passing on the old fixtures.
Triage in three steps:

1. Request each domain of `_domains` with a browser User-Agent and note the status, the redirect target and any Cloudflare or DDoS-Guard markers.
2. Run the plugin's `search()` directly with debug logging and read its warnings (the script below).
3. For zero results without an error, fetch the search URL and check whether the query term is in the HTML: if it is, the parser no longer matches; if not, the search itself moved (a new endpoint, a form token, a script).

```python
# poetry run python <job tmp>/triage.py <plugin> "<query>"
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys

import structlog

structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG))
spec = importlib.util.spec_from_file_location(sys.argv[1], f"plugins/{sys.argv[1]}.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


async def main() -> None:
    plugin = mod.plugin
    try:
        results = await asyncio.wait_for(plugin.search(sys.argv[2]), 90)
        print("RESULT", len(results), "base_url", plugin.base_url)
    except Exception as exc:
        print("EXC", type(exc).__name__, str(exc)[:300])
    finally:
        await plugin.cleanup()


asyncio.run(main())
```

Common causes and their fixes:

| Symptom | Cause | Fix |
|---|---|---|
| Every page answers 403 with a challenge page. | Cloudflare blocks plain httpx. | Fetch the pages with `_fetch_text()`; the browser passes the challenge. |
| The search endpoint answers 404 while the homepage answers. | The site moved its search or API. | Find the new endpoint in the site's network requests with `playwright-mcp`. |
| A form POST answers 400. | The form needs a new token or field. | Read the token from the page or the session, as `XenForoPluginBase` does with the CSRF token. |
| The query term is in the HTML, but the parser finds nothing. | The markup changed. | Capture the pages again and fix the selectors against them. |
| The domain answers with a redirect to another host. | The site moved. | The httpx base follows permanent moves by itself; put the new host into `_domains`. |
| No domain answers at all. | The site is down or gone. | The plugin stays and reports itself unreachable; check the JDownloader plugin and the site's official list for a new address. |
