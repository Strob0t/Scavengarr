# Plan: Additional Plugins

**Status:** Planned
**Priority:** Medium
**Related:** `plugins/`, `docs/features/plugin-system.md`

## Current State

Scavengarr ships with two plugins:

| Plugin | Type | Engine | Site |
|---|---|---|---|
| `filmpalast.to.yaml` | YAML (declarative) | Scrapy | filmpalast.to (streaming) |
| `boerse.py` | Python (imperative) | Playwright | boerse.sx (forum/DDL) |

These two plugins demonstrate both plugin types and both scraping engines, but more
plugins are needed to validate the architecture across diverse site structures and to
provide real value to users.

## Plugin Candidates

### YAML Plugins (static HTML, Scrapy engine)

- [ ] **kinox.to** - Streaming site with simple HTML structure. Two-stage: search results
      list followed by detail page with hoster links. Good candidate for validating the
      standard search-to-detail pipeline.

- [ ] **movie4k / movie2k successors** - Streaming aggregators with straightforward
      HTML tables. Tests pagination handling in stage selectors.

- [ ] **scnlog.me** - Scene release log with download links. Single-stage plugin
      (search results contain direct links). Validates that single-stage pipelines
      work correctly alongside multi-stage ones.

### Python Plugins (JS-heavy or complex auth)

- [ ] **nima4k.org** - Requires session management and has Cloudflare protection.
      Good test for the planned PlaywrightAdapter (see `docs/plans/playwright-engine.md`).

- [ ] **serienjunkies.org** - Captcha-protected download links requiring multi-step
      extraction. Tests the boundary of what can be automated.

## Plugin Authoring Guide

To lower the barrier for contributors, a plugin authoring guide should cover:

- [ ] YAML plugin tutorial with annotated `filmpalast.to.yaml` walkthrough
- [ ] Python plugin tutorial with simplified boerse.py example
- [ ] Common selector patterns (CSS selectors for tables, lists, nested containers)
- [ ] Testing plugins locally with the CLI (`poetry run start`)
- [ ] Debugging tips: structlog output, stage-by-stage tracing
- [ ] Category mapping reference (Torznab category IDs to site categories)

## Plugin Quality Checklist

Every new plugin (YAML or Python) must meet these criteria before inclusion:

- [ ] Has at least one integration test with fixture HTML
- [ ] Handles missing fields gracefully (partial results, not crashes)
- [ ] Respects rate limits (`delay_seconds` for YAML, semaphore for Python)
- [ ] Uses `urljoin` for URL construction (no string concatenation)
- [ ] Documents required auth (env vars, credentials) if applicable
- [ ] Maps at least one Torznab category

## Architecture Validation Goals

Each new plugin should test at least one architectural assumption:

| Plugin | Validates |
|---|---|
| kinox.to | Standard two-stage YAML pipeline |
| scnlog.me | Single-stage YAML (no detail page) |
| nima4k.org | PlaywrightAdapter with YAML config |
| serienjunkies.org | Complex Python plugin with captcha handling |

## Timeline

Plugins should be added incrementally as the core stabilizes:

1. After PlaywrightAdapter lands (see `docs/plans/playwright-engine.md`)
2. After integration test infrastructure is ready (see `docs/plans/integration-tests.md`)
3. After search result caching is implemented (see `docs/plans/search-caching.md`)
