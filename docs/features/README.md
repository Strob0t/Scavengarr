# Scavengarr Feature Documentation

> Central index for all feature documentation — start here to navigate the system.

---

## At a Glance

Scavengarr is a self-hosted, container-ready **Torznab/Newznab indexer** and **Stremio addon** for Prowlarr and other Arr applications. It scrapes sources via Python plugins (httpx for static HTML, Playwright for JS-heavy sites) and delivers results through standard Torznab endpoints and a Stremio addon.

| Fact | Value |
|---|---|
| Version | 0.2.3 |
| Python | 3.12–3.14 (Docker image: 3.14) |
| Plugins | 41 (34 httpx + 7 Playwright) |
| Hoster resolvers | 59 (22 individual + 12 generic DDL + 25 XFS) |
| Tests | 5082 offline (4851 unit + 188 E2E + 43 integration) + 41 live |
| Architecture | Clean Architecture |

---

## Quick Navigation

### Feature Handbook

| Document | Description |
|---|---|
| [FEATURES.md](./FEATURES.md) | Compact feature handbook — all features at a glance |

### Core Features

| Document | Description |
|---|---|
| [Plugin System](./plugin-system.md) | Python plugin authoring, base classes, protocol, discovery, per-plugin overrides |
| [Plugin List](../plugins.md) | Generated list of all plugins: website, mirrors, content, engine, languages |
| [Python Plugins](./python-plugins.md) | Detailed Python plugin development, base class reference, examples |
| [Multi-Stage Scraping](./multi-stage-scraping.md) | Search → detail → links inside plugins, bounded parallel execution |
| [CrawlJob System](./crawljob-system.md) | Multi-link packaging for JDownloader integration |
| [Torznab API](./torznab-api.md) | Torznab/Newznab endpoint reference, XML format, Prowlarr compatibility |
| [Link Validation](./link-validation.md) | HEAD/GET validation strategy, parallel checking, status policies |
| [Configuration](./configuration.md) | YAML/ENV/CLI config, precedence rules, all settings |

### Streaming & Integration

| Document | Description |
|---|---|
| [Stremio Addon](./stremio-addon.md) | Stremio integration with catalog, streams, and hoster resolution |
| [Hoster Resolvers](./hoster-resolvers.md) | Streaming, DDL, and XFS hoster resolvers |
| [Plugin Scoring & Probing](./plugin-scoring-and-probing.md) | EWMA-based plugin ranking via background health and search probes |
| [Mirror URL Fallback](./mirror-url-fallback.md) | Automatic domain fallback when primary mirrors are unreachable |
| [Prowlarr Integration](./prowlarr-integration.md) | Step-by-step Prowlarr setup, endpoint mapping, category sync |
| [Observability](./observability.md) | Prometheus metrics of Stremio requests, plugin searches, hoster resolutions and the HLS proxy; scrape job, queries, cost |

### Architecture

| Document | Description |
|---|---|
| [Clean Architecture](../architecture/clean-architecture.md) | Layer diagram, dependency rules, module organization |
| [Codeplan](../architecture/codeplan.md) | Implementation roadmap and architectural decisions |

### Plans & Roadmap

| Document | Description |
|---|---|
| [Playwright Engine](../plans/playwright-engine.md) | Browser pool and resource management for Playwright plugins |
| [More Plugins](../plans/more-plugins.md) | Plugin inventory and remaining candidates |
| [Integration Tests](../plans/integration-tests.md) | Implemented: integration, E2E, and live smoke tests |
| [Search Caching](../plans/search-caching.md) | Implemented: search result cache with `X-Cache` header |
| [Pi Performance](../plans/pi-performance.md) | Done 2026-10-04: baseline, measures and their effect on a Raspberry Pi 4 (CPU, outbound requests, event-loop lag per stream request) |
| [Round 5 Measures](../plans/round5-measures.md) | Implemented 2026-10-05, checked on the dev server (A/B against the fifth round's code) and in the sixth round in production (kinoger's stealth-browser contention open): plugin health check, resolution during the search, cached answers without waiting, half-open probes that report, SuperVideo, HLS proxy CPU (asyncio network backend; ChaCha20 measured useless), selectolax for every plugin, answers at 5 streams or 60 s, re-resolvable stream links |
| [Optimization Options](../plans/optimization-options.md) | Proposed 2026-10-04, updated 2026-10-05 after the fifth end-to-end round: where a stream request's time goes now, research on Stremio clients, other addons, HTTP clients, Chromium and Cloudflare; 19 evaluated options |
| [Plugin Repair](../plans/plugin-repair.md) | Done: repaired plugins; hdfilme keyword search broken upstream (browsing works); streamworld removed (site gone) |
| [Anti-Bot Hardening](../plans/antibot-patchright.md) | Done: Patchright instead of playwright-stealth, Turnstile solver, browser fallback port for httpx plugins |
| [Stremio Latency](../plans/stremio-latency.md) | Done: answer deadline, per-hoster resolution, playback check, measured 10 s / 15 s budget; end-to-end rounds 1–6 and the dev-server A/B round of the round-5 measures |
| [Code Review Fixes](../plans/code-review-fixes.md) | Done: 79 findings of the full `staging` review fixed in four waves (security, results, robustness, plugin categories); open observations listed |
| [Captcha Solving](../plans/captcha-solving.md) | Done: shared challenge detector, grab-time link resolution (nox ALTCHA, animeloads image captcha), vinovo/DoodStream without captcha, clearance cookies across restarts, optional Byparr sidecar |
| [Next Steps](../plans/next-steps.md) | Proposed: status snapshot of 2026-09-29 (live run, release, CI) and prioritized next steps, incl. the stremio.lan live test setup |
| [Ideas Backlog](../plans/ideas-backlog.md) | Decided 2026-10-06: snapshot of production and repository, six new findings (httpx log noise, discarded stream quality and size, hosters without a resolver, no build identity, kinox without yield, hand-made deployment), 16 evaluated ideas, the decisions, and the handoff to the implementing agent (order, working rules, task specifications; the larger items as OpenSpec changes `add-anime-ids`, `persist-resolver-state`, `add-container-image`, `add-stream-media-quality`) |
| [Stremio Stream Split](../plans/stremio-stream-split.md) | Proposed 2026-10-06 (item I14): split the 939-line stream use case into phase collaborators under `application/stremio/` (title resolution, plugin selection, title search, resolve flow, answer assembly) in five behaviour-preserving commits; exclusive-access rule |

### Refactoring History

| Document | Description |
|---|---|
| [Clean Architecture Migration](../refactor/COMPLETED/clean-architecture-migration.md) | Migration from flat structure to layered architecture |
| [Pydantic Domain Removal](../refactor/COMPLETED/pydantic-domain-removal.md) | Removing Pydantic from the domain layer |
| [German-English Translation](../refactor/COMPLETED/german-english-translation.md) | Codebase localization from German to English |

### Developer Reference

| Document | Description |
|---|---|
| [Python Best Practices](../PYTHON-BEST-PRACTICES.md) | Performance rules: non-blocking async I/O, shared HTTP client, caching, scraping limits (typing and coding rules: `AGENTS.md` section 5) |

---

## Technology Stack

| Component | Technology | Purpose |
|---|---|---|
| Web framework | FastAPI + Uvicorn | HTTP API (Torznab, Stremio, stats, download) |
| Static scraping | httpx | HTTP client for httpx plugins and hoster resolvers |
| HTML parsing | `selectolax` (lexbor) | HTML extraction in plugins (CSS selectors on the lexbor tree; pages through `parse_page()`, big ones in a worker thread) |
| Dynamic scraping | Patchright (Playwright fork, Chromium) | JS-heavy sites, Cloudflare bypass |
| Title matching | rapidfuzz | Fuzzy title scoring for Stremio |
| Release parsing | guessit | Release name parsing for title matching |
| Configuration | pydantic-settings, PyYAML, python-dotenv | Typed config with env/YAML/CLI support |
| Caching | diskcache (+ optional Redis via YAML) | Search results, CrawlJobs, stream links, plugin scores |
| Logging | structlog | Structured JSON/console logging |
| CLI | argparse (stdlib) | Server startup with config overrides |
| Testing | pytest, respx | 4431 offline tests + 41 live smoke tests |

---

## Project Layout

```text
src/scavengarr/
  domain/                    # Enterprise business rules
    entities/                # Torznab, Stremio, CrawlJob, scoring entities
    plugins/                 # SearchResult, PluginProtocol, plugin schema, exceptions
    ports/                   # Abstract contracts (Protocol classes)
  application/               # Application business rules
    use_cases/               # TorznabSearch/Caps/Indexers, StremioCatalog, StremioStream
    factories/               # CrawlJob factory
    stremio/                 # Plugin search, query building, stream builder
  infrastructure/            # Interface adapters
    plugins/                 # Registry, loader, HttpxPluginBase, PlaywrightPluginBase, shared browser pool
    torznab/                 # HttpxSearchEngine + XML presenter
    validation/              # HttpLinkValidator (HEAD/GET)
    cache/                   # diskcache + Redis adapters, cache factory
    persistence/             # CrawlJob, stream link, and plugin score repositories
    stremio/                 # Stream converter/sorter, title matcher, release parser, episode filter, HLS proxy
    tmdb/                    # TMDB client + IMDB fallback
    browser/                 # Shared Chromium pool, stealth context, headful/headless, Cloudflare detection
    hoster_resolvers/        # Hoster resolvers (individual + generic DDL + XFS), probes
    scoring/                 # EWMA scoring, health/search probers, scheduler
    config/                  # Pydantic schema, loader, defaults
    logging/                 # structlog setup
    common/                  # Parsers, converters, rate limiter, retry transport
    circuit_breaker.py       # Per-plugin circuit breaker
    concurrency.py           # Global concurrency pool
    graceful_shutdown.py     # Drain in-flight requests
    metrics.py               # Metrics collector
    resource_detector.py     # cgroup v2/v1 CPU/memory detection
  interfaces/                # Frameworks & drivers
    api/                     # FastAPI routers (torznab, stremio, download, stats) + rate-limit middleware
    cli/                     # argparse CLI (`poetry run start`)
    app.py                   # FastAPI app factory, health endpoints
    app_state.py             # Typed application state
    composition.py           # Dependency injection (lifespan)

plugins/                     # Plugin directory (Python plugins)
  filmpalast_to.py           # Python plugin example (httpx)
  boerse.py                  # Python plugin example (Playwright)
  einschalten.py             # Python plugin example (httpx API)

tests/
  unit/
    domain/                  # Pure domain tests
    application/             # Use case tests (mocked ports)
    infrastructure/          # Adapter, parser, resolver, and plugin tests
    interfaces/              # Router tests
  e2e/                       # Torznab + Stremio endpoint tests
  integration/               # Config loading, CrawlJob lifecycle, link validation
  live/                      # Live smoke tests (plugins, resolvers, grab-time links, Stremio)
  benchmark/                 # Concurrency tuning benchmarks (run manually)
```

---

## How to Use These Docs

1. **New to Scavengarr?** Start with [FEATURES.md](./FEATURES.md) for a bird's-eye view.
1. **Writing a new plugin?** Read [Plugin System](./plugin-system.md) and [Python Plugins](./python-plugins.md).
1. **Understanding multi-stage scraping?** Read [Multi-Stage Scraping](./multi-stage-scraping.md).
1. **Setting up Prowlarr?** Read [Prowlarr Integration](./prowlarr-integration.md) and [Torznab API](./torznab-api.md).
1. **Understanding the architecture?** Read [Clean Architecture](../architecture/clean-architecture.md).
1. **Configuring the system?** Read [Configuration](./configuration.md).
