# Plan: Integration Test Suite

**Status:** Implemented
**Priority:** High
**Related:** `tests/`, `CLAUDE.md` section 12

## Implementation Summary

The test suite now includes 221 non-unit tests across three categories:

| Category | Location | Count | Description |
|---|---|---|---|
| Integration | `tests/integration/` | 25 | Config loading, crawljob lifecycle, link validation, plugin pipeline |
| E2E | `tests/e2e/` | 158 | 46 Torznab endpoint + 81 Stremio endpoint + 31 streamable link verification |
| Live smoke | `tests/live/` | 38 | Plugin smoke tests + resolver contract tests |

Total test suite: **3997 tests** (3776 unit + 158 E2E + 25 integration + 38 live).

## Original Problem

Scavengarr had a solid unit test suite (235 tests across domain, application, and
infrastructure layers), but no integration tests. Unit tests mock all I/O boundaries,
which means the following were never tested together:

- HTTP router receives a Torznab request and returns valid XML
- Use case loads a plugin, executes scraping, validates links, returns results
- Multi-stage pipeline processes real (fixture) HTML through all stages
- CrawlJob creation from search results and download via HTTP endpoint
- Configuration loading from YAML + env vars + defaults in combination

Integration tests catch wiring bugs, serialization mismatches, and protocol
violations that unit tests cannot detect.

## Design

### Test Categories

#### 1. Router-to-UseCase Integration
Tests that the FastAPI router correctly invokes use cases and returns valid responses.

```
HTTP Request → Router → Use Case → Mock Adapters → XML Response
```

- Use `httpx.AsyncClient` with `app` (TestClient pattern)
- Mock only external I/O (HTTP requests to sites), not internal wiring
- Validate response XML against Torznab DTD/schema

```python
async def test_search_returns_valid_torznab_xml(client: httpx.AsyncClient):
    response = await client.get("/api", params={"t": "search", "q": "test"})
    assert response.status_code == 200
    assert "application/xml" in response.headers["content-type"]
    root = ET.fromstring(response.text)
    assert root.tag == "rss"
```

#### 2. Plugin Pipeline Integration
Tests that execute a full multi-stage plugin pipeline against fixture HTML.

```
Plugin Config → SearchEngine → Stage 1 (fixture HTML) → Stage 2 (fixture HTML) → SearchResult[]
```

- Serve fixture HTML files via `respx` (mock HTTP responses)
- Use real plugin loading (YAML parser + registry)
- Verify stage chaining: Stage 1 URLs feed into Stage 2
- Verify deduplication and field extraction

#### 3. CrawlJob Lifecycle Integration
Tests the full lifecycle: search results create a CrawlJob, which is retrievable via HTTP.

```
SearchResult[] → CrawlJobFactory → Cache → HTTP GET /crawljob/{id} → .crawljob file
```

- Use real cache adapter (diskcache with temp directory)
- Verify `.crawljob` file content matches validated links
- Verify TTL expiration behavior

#### 4. Link Validation Integration
Tests the link validator with mocked HTTP responses (not real sites).

```
SearchResult[].download_links → HttpLinkValidator → filtered SearchResult[]
```

- Use `respx` to simulate various HTTP responses (200, 403, 404, timeout, redirect)
- Verify HEAD-first-then-GET fallback strategy
- Verify parallel execution (timing assertions)

#### 5. Configuration Integration
Tests that configuration loads correctly from multiple sources with proper precedence.

```
YAML file + ENV vars + CLI args → AppConfig (merged)
```

- Use `tmp_path` for YAML files, `monkeypatch` for env vars
- Verify precedence: CLI > ENV > YAML > defaults
- Verify secret masking in log output

### Fixture Strategy

Fixtures are static HTML files stored in `tests/fixtures/`:

```
tests/
  fixtures/
    filmpalast/
      search_results.html      # Stage 1 response
      movie_detail.html         # Stage 2 response
    boerse/
      search_results.html       # Thread listing
      thread_page.html          # Thread with download links
  integration/
    test_router.py              # Category 1
    test_plugin_pipeline.py     # Category 2
    test_crawljob_lifecycle.py  # Category 3
    test_link_validation.py     # Category 4
    test_configuration.py       # Category 5
    conftest.py                 # Integration fixtures
```

Fixtures should be captured from real sites once and committed as static files.
Never make real HTTP requests to external sites in CI.

### Test Infrastructure

#### conftest.py (integration)

```python
@pytest.fixture
async def app_client():
    """Full application with mocked HTTP but real wiring."""
    app = create_app(config=test_config)
    async with httpx.AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.fixture
def fixture_html():
    """Load fixture HTML by path."""
    def _load(name: str) -> str:
        path = Path(__file__).parent.parent / "fixtures" / name
        return path.read_text()
    return _load
```

#### pytest markers

```ini
[tool.pytest.ini_options]
markers = [
    "integration: marks integration tests (deselect with '-m not integration')",
]
```

## Checklist

### Phase 1: Infrastructure Setup
- [ ] Create `tests/integration/` directory structure
- [ ] Create `tests/fixtures/` with initial HTML fixtures
- [ ] Add integration conftest with app client and fixture loader
- [ ] Add `integration` pytest marker
- [ ] Verify integration tests run in CI (separate from unit tests)

### Phase 2: Core Integration Tests
- [ ] Router integration: `GET /api?t=caps` returns valid XML
- [ ] Router integration: `GET /api?t=search&q=test` with mocked plugin
- [ ] Router integration: error responses (missing params, unknown plugin)
- [ ] Plugin pipeline: filmpalast two-stage with fixture HTML
- [ ] Plugin pipeline: verify stage chaining (Stage 1 URLs used in Stage 2)

### Phase 3: Lifecycle Tests
- [ ] CrawlJob creation from search results
- [ ] CrawlJob retrieval via HTTP endpoint
- [ ] CrawlJob TTL expiration
- [ ] Link validation with mixed HTTP responses
- [ ] Link validation HEAD-then-GET fallback

### Phase 4: Configuration and Edge Cases
- [ ] Configuration precedence (YAML + ENV + defaults)
- [ ] Plugin loading errors (missing file, invalid YAML)
- [ ] Empty search results handling
- [ ] Malformed HTML graceful degradation

## Dependencies

- `respx` (already in dev dependencies) for HTTP mocking
- `httpx` (already in dependencies) for TestClient
- No additional packages required
