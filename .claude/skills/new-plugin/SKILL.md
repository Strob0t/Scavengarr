---
name: new-plugin
description: Create a new Scavengarr scraping plugin for a website (site analysis with playwright-mcp, httpx or Playwright base class, category filtering, pagination, tests). Use when asked to add or build a plugin for a site.
argument-hint: "<site name or URL>"
---

Target site: $ARGUMENTS

Read `docs/features/python-plugins.md` → "Adding a New Plugin" and follow it. The non-negotiables:

1. **Site analysis first** with `playwright-mcp`: search, category, detail and download pages; selectors, URL patterns, pagination, JS/Cloudflare, auth. Keep tool output small: prefer `browser_evaluate`/`browser_find` with targeted selectors over full `browser_snapshot` dumps.
2. **Base class**: `HttpxPluginBase` for static HTML/JSON, `PlaywrightPluginBase` only for JS-heavy sites. Never duplicate base-class boilerplate. State the choice and why.
3. **Search standards**: category filtering mapped to the site's filters, pagination up to `self.effective_max_results`, detail pages bounded by `self._new_semaphore()`.
4. **TDD**: write `tests/unit/infrastructure/test_<name>_plugin.py` first; use an existing plugin + test with the same base class as reference.
5. Regenerate `docs/plugins.md`: `poetry run python scripts/generate_plugin_list.py`.
6. Finish with the `commit` skill.
