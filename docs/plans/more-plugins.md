[← Back to Index](../features/README.md)

# Plan: Additional Plugins

**Status:** Mostly complete — 41 plugins implemented (2026-09-28: streamworld removed, site gone)
**Priority:** Low (ongoing)
**Related:** `plugins/`, `docs/features/python-plugins.md`

## Current State

Scavengarr ships with **41 plugins** covering German and English streaming, DDL and anime sites: 35 on httpx bases and 6 on `PlaywrightPluginBase`. The current list, with base class, domains and languages, is the generated [`docs/plugins.md`](../plugins.md) (`scripts/generate_plugin_list.py`). streamworld was removed on 2026-09-28 (site gone).

## Remaining Candidates

Sites not yet covered that could benefit from plugins:

- **dokustream.de** — Documentary streaming
- **filmkiste.to** — Movie/TV streaming
- **xcine.me** — German movie streaming
- **goldstreamtv.com** — German streaming aggregator

## Plugin Quality Checklist

Every new plugin must meet these standards:

- [ ] Uses `HttpxPluginBase` or `PlaywrightPluginBase` (no raw client setup)
- [ ] Configurable settings at top of file with section headers (`_DOMAINS`, `_MAX_PAGES`, etc.)
- [ ] Category filtering via site's filter system mapped to Torznab categories
- [ ] Pagination up to 1000 items (`_MAX_PAGES` based on results-per-page)
- [ ] Bounded concurrency via `self._new_semaphore()` (default 5, `_max_concurrent`)
- [ ] `season`/`episode` params in `search()` signature
- [ ] `provides` attribute set to `"stream"` or `"download"`
- [ ] `languages` attribute set (default `["de"]`, e.g. `["en"]` for English sites; `default_language` is a derived property)
- [ ] Unit tests with mocked HTTP responses
- [ ] Live smoke test entry in `tests/live/`
- [ ] Handles missing fields gracefully (partial results, not crashes)
- [ ] Uses `self._safe_fetch()` / `self._safe_parse_json()` for error handling
