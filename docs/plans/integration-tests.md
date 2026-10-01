[← Back to Index](../features/README.md)

# Plan: Integration Test Suite

**Status:** Partially implemented (2026-09-28) — config, CrawlJob and link-validation integration tests plus Torznab/Stremio E2E tests exist; plugin-pipeline tests, CI, HTTP download-endpoint and TTL-expiry tests are open
**Priority:** High
**Related:** `tests/`, `AGENTS.md` section 6 (Testing)

## Implementation Summary

The test suite now includes 232 non-unit tests across three categories:

| Category | Location | Count | Description |
|---|---|---|---|
| Integration | `tests/integration/` | 25 | Config loading, CrawlJob lifecycle, link validation |
| E2E | `tests/e2e/` | 169 | 45 Torznab endpoint + 73 Stremio endpoint + 20 Stremio series + 31 streamable link verification |
| Live smoke | `tests/live/` | 38 | Plugin smoke tests + resolver contract tests |

Total test suite: **4151 tests** (3919 unit + 169 E2E + 25 integration + 38 live). The default `poetry run pytest` runs 4113 of them; the 38 live tests are opt-in (`poetry run pytest -m live`).

## Original Problem

Scavengarr had a solid unit test suite (235 tests across domain, application, and infrastructure layers), but no integration tests. Unit tests mock all I/O boundaries, which means the following were never tested together:

- HTTP router receives a Torznab request and returns valid XML
- Use case loads a plugin, executes scraping, validates links, returns results
- Multi-stage pipeline processes real (fixture) HTML through all stages
- CrawlJob creation from search results and download via HTTP endpoint
- Configuration loading from YAML + env vars + defaults in combination

Integration tests catch wiring bugs, serialization mismatches, and protocol violations that unit tests cannot detect.

## Design

### Test Categories

#### 1. Router-to-UseCase Integration

Tests that the FastAPI router correctly invokes use cases and returns valid responses. Implemented as E2E tests in `tests/e2e/test_torznab_endpoint.py` and `tests/e2e/test_stremio_endpoint.py`.

```text
HTTP Request → Router → Use Case → Mock Adapters → XML Response
```

- Use FastAPI's `TestClient` (or `httpx.AsyncClient(transport=httpx.ASGITransport(app=app))`; httpx 0.28 removed the `app=` argument)
- Mock only external I/O (HTTP requests to sites), not internal wiring
- Validate response XML against Torznab DTD/schema

```python
def test_search_returns_valid_torznab_xml(client: TestClient) -> None:
    response = client.get("/api/v1/torznab/filmpalast", params={"t": "search", "q": "test"})
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    root = ET.fromstring(response.text)
    assert root.tag == "rss"
```

#### 2. Plugin Pipeline Integration (open)

Tests that execute a full multi-stage Python plugin search against fixture HTML. Not implemented yet: the fixtures in `tests/fixtures/html/testsite/` and the `fixtures_dir` fixture in `tests/integration/conftest.py` exist but are unused.

```text
Python plugin (HttpxPluginBase) search() → Stage 1 (fixture HTML) → Stage 2 (fixture HTML) → SearchResult[]
```

- Serve fixture HTML files via `respx` (mock HTTP responses)
- Load the plugin through the real `PluginRegistry` (Python loader; YAML plugins were removed in `42fced9`)
- Verify stage chaining: Stage 1 URLs feed into Stage 2
- Verify deduplication and field extraction

#### 3. CrawlJob Lifecycle Integration

Tests the full lifecycle: search results create a CrawlJob, which is retrievable via HTTP.

```text
SearchResult[] → CrawlJobFactory → Cache → HTTP GET /api/v1/download/{job_id} → .crawljob file
```

- Use real cache adapter (diskcache with temp directory)
- Verify `.crawljob` file content matches validated links
- Verify TTL expiration behavior (open)

#### 4. Link Validation Integration

Tests the link validator with mocked HTTP responses (not real sites).

```text
SearchResult[].download_links → HttpLinkValidator → filtered SearchResult[]
```

- Use `respx` to simulate various HTTP responses (200, 403, 404, timeout, redirect)
- Verify HEAD-first-then-GET fallback strategy
- Verify parallel execution (timing assertions)

#### 5. Configuration Integration

Tests that configuration loads correctly from multiple sources with proper precedence.

```text
YAML file + ENV vars + CLI args → AppConfig (merged)
```

- Use `tmp_path` for YAML files, `monkeypatch` for env vars
- Verify precedence: CLI > ENV > YAML > defaults
- Secret masking in log output: not applicable yet (no redaction helper exists)

### Fixture Strategy

Fixtures are static HTML files stored in `tests/fixtures/`. Actual layout:

```text
tests/
  fixtures/
    html/
      hdfilme/, kinoger/, megakino/, streamcloud/, streamkiste/,
      filmpalast/, movie2k/, aniworld/, sto/, kinoking/
        search-oppenheimer.html.gz, detail-oppenheimer.html.gz, ...
                                # real pages, parsed in tests/unit/infrastructure/test_real_pages.py
      testsite/
        search_results.html     # Stage 1 response (unused so far)
        movie_detail.html       # Stage 2 response (unused so far)
        movie_detail_2.html
        empty_search.html
  integration/
    conftest.py                 # http_client, diskcache, respx_mock, fixtures_dir
    test_config_loading.py      # Category 5
    test_crawljob_lifecycle.py  # Category 3
    test_link_validation.py     # Category 4
  e2e/
    test_torznab_endpoint.py    # Category 1 (Torznab)
    test_stremio_endpoint.py    # Category 1 (Stremio)
```

Fixtures should be captured from real sites once and committed as static files. Never make real HTTP requests to external sites in CI.

**Real pages (2026-10-01):** search and detail pages of the five DataLife Engine sites, captured from a live run of the plugins (Oppenheimer, The Last of Us), the per-visitor `dle_login_hash` scrubbed, gzipped (230 KB for 13 pages). `test_real_pages.py` runs the plugins' parsers on them with values read off the pages. They found what the hand-written fixtures could not: kinoger's series pages hand out the first episode for every request, streamkiste reads the wrong year, kinoger's detail metadata is empty. Recapture when a site changes its theme: `scripts/capture_pages.py <plugin> "<query>"` records a live search's pages, `--fixture <page> <name>` stores one (scrubbed, gzipped).

### Test Infrastructure

#### conftest.py (integration)

```python
@pytest.fixture
def app_client() -> Iterator[TestClient]:
    """Full application with mocked HTTP but real wiring."""
    app = create_app(test_config)
    with TestClient(app) as client:
        yield client

@pytest.fixture
def fixtures_dir() -> Path:
    """Path to HTML fixtures directory."""
    return Path(__file__).parent.parent / "fixtures" / "html"
```

#### pytest markers

Defined in `pyproject.toml`:

```toml
[tool.pytest.ini_options]
markers = [
    "integration: Integration tests (real infrastructure, mocked HTTP)",
    "live: Live smoke tests against real websites (require network)",
    "benchmark: Concurrency tuning benchmarks (slow, run manually)",
]
```

## Checklist

### Phase 1: Infrastructure Setup

- [x] Create `tests/integration/` directory structure
- [ ] Create `tests/fixtures/` with initial HTML fixtures (files exist in `tests/fixtures/html/testsite/` but no test uses them)
- [x] Add integration conftest with fixture loader (`fixtures_dir`; no app client — router tests use `TestClient` in `tests/e2e/`)
- [x] Add `integration` pytest marker
- [ ] Verify integration tests run in CI (separate from unit tests) — no CI configuration exists

### Phase 2: Core Integration Tests

- [x] Router integration: `GET /api/v1/torznab/{plugin}?t=caps` returns valid XML
- [x] Router integration: `GET /api/v1/torznab/{plugin}?t=search&q=test` with mocked plugin
- [x] Router integration: error responses (missing params, unknown plugin)
- [ ] Plugin pipeline: filmpalast two-stage with fixture HTML
- [ ] Plugin pipeline: verify stage chaining (Stage 1 URLs used in Stage 2)

### Phase 3: Lifecycle Tests

- [x] CrawlJob creation from search results
- [ ] CrawlJob retrieval via HTTP endpoint (`GET /api/v1/download/{job_id}`)
- [ ] CrawlJob TTL expiration
- [x] Link validation with mixed HTTP responses
- [x] Link validation HEAD-then-GET fallback

### Phase 4: Configuration and Edge Cases

- [x] Configuration precedence (YAML + ENV + defaults)
- [ ] Plugin loading errors (missing `plugin` export, missing `search`, syntax error)
- [x] Empty search results handling
- [ ] Malformed HTML graceful degradation

## Dependencies

- `respx` (already in dev dependencies) for HTTP mocking
- `httpx` (already in dependencies) and FastAPI's `TestClient`
- No additional packages required
