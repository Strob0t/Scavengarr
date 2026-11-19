---
name: new-plugin
description: Create a new Scavengarr scraping plugin for a website (site analysis with playwright-mcp, httpx or Playwright base class, category filtering, pagination, tests). Use when asked to add or build a plugin for a site.
argument-hint: "<site name or URL>"
---

Target site: $ARGUMENTS

Read `docs/how-to/new-plugin.md` and follow it. The non-negotiables (AGENTS.md section 7):

1. **Site analysis first** with `playwright-mcp`: search, category, detail and download pages; selectors, URL patterns, pagination, JS/Cloudflare, auth. Keep tool output small: prefer `browser_evaluate`/`browser_find` with targeted selectors over full `browser_snapshot` dumps. Without a browser (the sandbox blocks the Chromium download hosts, `.devdata/agent-notes/mcp.md`), stop and say so: no plugin without the site analysis.
2. **Base class**: `HttpxPluginBase` for static HTML/JSON, `PlaywrightPluginBase` only for JS-heavy sites. Never duplicate base-class boilerplate; sites on the same backend share one base in `src/scavengarr/infrastructure/plugins/`. State the choice and why.
3. **Search standards**:
   - Categories: label results from site data, never with the requested category; answer with `served_category()` / `filter_by_category()` of `infrastructure/plugins/categories.py` (`[]` without a request for a category the site lacks).
   - Relevance: keep only the relevant hits with `relevant_hits()` of `infrastructure/plugins/relevance.py` (site searches also list loose matches).
   - Pagination up to `self.effective_max_results`, detail pages bounded by `self._new_semaphore()`.
   - Parsing: `selectolax`, every page through `await parse_page(parser, html)` of `infrastructure/plugins/dom.py`.
   - Grab-time links: a site that lets only a few link-outs through per second resolves them at the grab (`GrabResolvingPlugin`, the how-to's validation section).
   - Episodes: a plugin that places an episode on the site's own season pages sets `locates_episodes = True` and answers nothing when no row matches, never the request's numbers.
4. **Domains**: `_domains` holds genuine domains only (check the JDownloader plugin's dead and fake/scam domain notes); a site with an official address list gets `domain_list_url` and a `domain_marker`; sites that front one database share a `mirror_group` (`docs/current/plugin-domains.md`).
5. **TDD**: write `tests/unit/infrastructure/test_<name>_plugin.py` first; use an existing plugin + test with the same base class as reference. Test the parsers on the site's own pages too (`scripts/capture_pages.py`, `tests/unit/infrastructure/test_real_pages.py`).
6. Regenerate `docs/reference/plugins.md`: `poetry run python scripts/generate_plugin_list.py` (no poetry: `.venv/bin/python`; in a worktree prefix `PYTHONPATH=src`). Raise the plugin count in `README.md` and `AGENTS.md` (`tests/unit/infrastructure/test_readme.py` checks the README's).
7. Finish with the `commit` skill.
