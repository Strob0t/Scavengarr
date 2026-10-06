## 1. Base Protocol and Models

- [x] 1.1 Create the package `src/scavengarr/domain/plugins/__init__.py` (planned: `src/scavengarr/plugins/__init__.py`)
- [x] 1.2 Create `src/scavengarr/domain/plugins/base.py` (planned: `src/scavengarr/plugins/base.py`):
  - [x] Define `SearchResult` with fields (implemented as a `@dataclass`, not `BaseModel`; more fields added since):
    - [x] `title: str`
    - [x] `download_link: str`
    - [x] `seeders: int | None = None`
    - [x] `leechers: int | None = None`
    - [x] `size: str | None = None`
    - [x] `published_date: str | None = None`
  - [x] Define `PluginProtocol(Protocol)` with method signature:
    - [x] `async def search(self, query: str, category: int | None = None, season: int | None = None, episode: int | None = None) -> list[SearchResult]` (plus `name` and `provides` attributes)
  - [x] Add comprehensive docstrings with examples

## 2. YAML Plugin Schema (superseded)

YAML plugins were implemented and later removed in `42fced9`. `AuthConfig` and `HttpOverrides` survived as plain dataclasses in `src/scavengarr/domain/plugins/plugin_schema.py` until 2026-10-06, when they were removed unused.

- [ ] 2.1 Create `src/scavengarr/plugins/schema.py` with Pydantic models (superseded):
  - [ ] `ScrapySelectors(BaseModel)`:
    - [ ] `row: str` (required)
    - [ ] `title: str` (required)
    - [ ] `download_link: str` (required)
    - [ ] `seeders: str | None = None`
    - [ ] `leechers: str | None = None`
    - [ ] `size: str | None = None`
  - [ ] `PlaywrightLocators(BaseModel)`:
    - [ ] `row: str` (required)
    - [ ] `title: str` (required)
    - [ ] `download_link: str` (required)
    - [ ] `seeders: str | None = None`
    - [ ] `leechers: str | None = None`
  - [ ] `ScrapingConfig(BaseModel)`:
    - [ ] `mode: Literal["scrapy", "playwright"]` (required)
    - [ ] `search_path: str | None = None` (Scrapy-specific)
    - [ ] `selectors: ScrapySelectors | None = None` (Scrapy)
    - [ ] `search_url_template: str | None = None` (Playwright)
    - [ ] `wait_for_selector: str | None = None` (Playwright)
    - [ ] `locators: PlaywrightLocators | None = None` (Playwright)
  - [ ] `AuthConfig(BaseModel)`:
    - [ ] `type: Literal["none", "basic", "form", "cookie"] = "none"`
    - [ ] `username: str | None = None`
    - [ ] `password: str | None = None`
    - [ ] `login_url: str | None = None`
    - [ ] `username_field: str | None = None`
    - [ ] `password_field: str | None = None`
    - [ ] `submit_selector: str | None = None`
    - [ ] `cookie_name: str | None = None`
    - [ ] `cookie_value: str | None = None`
  - [ ] `PluginDefinition(BaseModel)`:
    - [ ] `name: str` with pattern validator `^[a-z0-9-]+$`
    - [ ] `description: str`
    - [ ] `version: str` with pattern validator `^\d+\.\d+\.\d+$` (semver)
    - [ ] `author: str`
    - [ ] `base_url: HttpUrl`
    - [ ] `scraping: ScrapingConfig`
    - [ ] `auth: AuthConfig = Field(default_factory=lambda: AuthConfig())`
    - [ ] `categories: dict[int, str] = {}` (Torznab ID to site category)
- [ ] 2.2 Add field validators (superseded):
  - [ ] `@field_validator("selectors")` on `ScrapingConfig`:
    - [ ] Validate scrapy mode requires `selectors` field
    - [ ] Validate scrapy mode requires `search_path` field
  - [ ] `@field_validator("locators")` on `ScrapingConfig`:
    - [ ] Validate playwright mode requires `locators` field
    - [ ] Validate playwright mode requires `wait_for_selector` field
  - [ ] `@field_validator("username")` on `AuthConfig`:
    - [ ] Validate `type="basic"` requires `username` and `password`
  - [ ] `@field_validator("login_url")` on `AuthConfig`:
    - [ ] Validate `type="form"` requires all form fields

## 3. Exception Classes

- [x] 3.1 Create `src/scavengarr/domain/plugins/exceptions.py` (planned: `src/scavengarr/plugins/exceptions.py`; all derive from `PluginError`):
  - [x] `PluginLoadError` - Generic load failures
  - [ ] `PluginValidationError` - Schema/protocol validation failures (superseded: removed in `03454a8`)
  - [x] `PluginNotFoundError` - Plugin name doesn't exist in registry
  - [x] `DuplicatePluginError` - Two plugins with same name (defined, but never raised)

## 4. Plugin Loader

- [x] 4.1 Create `src/scavengarr/infrastructure/plugins/loader.py` (planned: `src/scavengarr/plugins/loader.py`):
  - [x] Import `importlib.util`, `Path`, base types (`yaml` and `PluginDefinition` superseded)
  - [ ] Function `load_yaml_plugin(path: Path) -> PluginDefinition` (superseded):
    - [ ] Read YAML file with `yaml.safe_load()`
    - [ ] Validate with `PluginDefinition(**data)`
    - [ ] Catch `yaml.YAMLError` and wrap in `PluginLoadError`
    - [ ] Catch `ValidationError` and wrap in `PluginValidationError`
  - [x] Function `load_python_plugin(path: Path) -> PluginProtocol`:
    - [x] Use `importlib.util.spec_from_file_location()` for dynamic import
    - [x] Execute module with `spec.loader.exec_module(module)`
    - [x] Validate module exports `plugin` variable
    - [x] Validate `plugin` has `search` method (`hasattr`) and a non-empty `name`
    - [x] Catch `SyntaxError` and other import errors and wrap in `PluginLoadError`
  - [ ] Function `load_plugin(path: Path) -> PluginDefinition | object` (superseded: only Python plugins exist):
    - [ ] Detect format by `path.suffix`
    - [ ] Delegate to `load_yaml_plugin()` if `.yaml`
    - [ ] Delegate to `load_python_plugin()` if `.py`
    - [ ] Raise `PluginLoadError` for unsupported extensions
  - [x] Discovery (implemented inside `PluginRegistry.discover()` instead of a `discover_plugins()` function):
    - [x] Index `*.py` files (`.yaml` superseded)
    - [x] Sorted by file name
    - [x] No error if directory doesn't exist (logs `plugin_directory_not_found`)
- [x] 4.2 Add logging with structlog:
  - [ ] Log `plugin_discovered` (level=DEBUG) with `plugin_file` field (not implemented; `plugins_discovered` INFO with `count`, `directory` instead)
  - [x] Log `plugin_loaded` (level=INFO) with `plugin_name`, `plugin_type` (always "python")
  - [x] Log `plugin_load_failed` (level=ERROR) with `plugin_file`, `plugin_type`, `error_type`, `error_message`

## 5. Plugin Registry

- [x] 5.1 Create `src/scavengarr/infrastructure/plugins/registry.py` (planned: `src/scavengarr/plugins/registry.py`):
  - [x] Class `PluginRegistry`:
    - [x] `__init__(self, plugin_dir: Path)`
    - [x] In-memory cache (implemented as `_python_cache: dict[str, PluginProtocol]`; planned `_plugins`)
    - [x] File index (implemented as `_refs: list[_PluginRef]`; planned `_plugin_files` name → path mapping)
    - [x] Method `discover(self) -> None`:
      - [x] Index plugin files in `self.plugin_dir`
      - [ ] Build name → file mapping at discovery time (names are resolved lazily via `_peek_name()`, which imports the module)
      - [x] Don't load plugins yet (lazy-loading)
    - [x] Loading helper (`_load_python()`; planned `_load_plugin()`):
      - [x] Check if already cached
      - [x] If not, call `load_python_plugin(path)`
      - [x] Extract name from the loaded plugin's `name` attribute (planned fallback `plugin.__class__.__name__.lower()` not implemented)
      - [ ] Validate name uniqueness (raise `DuplicatePluginError` if exists) — duplicates are skipped instead (first wins)
      - [x] Cache the plugin
      - [x] Return plugin
    - [x] Method `get(self, name: str) -> PluginProtocol`:
      - [x] Load the plugin if not cached
      - [x] Raise `PluginNotFoundError` if the name is unknown
      - [x] Return cached plugin
    - [x] Method `list_names(self) -> list[str]` (returns sorted, de-duplicated names)
    - [ ] Method `get_by_mode(self, mode: str) -> list[PluginDefinition]` (superseded: replaced by `get_mode(name) -> str` and `get_by_provides(provides) -> list[str]`)
    - [ ] Method `load_all(self) -> None` (superseded: removed as dead code, see `CHANGELOG.md` → Dead Code Cleanup)

## 6. Package Exports

- [x] 6.1 Package exports (planned: `src/scavengarr/plugins/__init__.py`):
  - [x] Export `PluginRegistry` (and `load_python_plugin`) from `src/scavengarr/infrastructure/plugins/__init__.py`
  - [x] Export `SearchResult`, `PluginProtocol`, `PluginProvides` from `src/scavengarr/domain/plugins/__init__.py` (`PluginDefinition` superseded)
  - [x] Export exceptions (`PluginLoadError`, `PluginNotFoundError`, `DuplicatePluginError`)

## 7. Integration with Main App

- [x] 7.1 Wire the registry at startup (planned: `src/scavengarr/main.py`; implemented in the FastAPI lifespan in `src/scavengarr/interfaces/composition.py`):
  - [x] Import `PluginRegistry`
  - [x] Store the registry on the app state (`state.plugins`; planned: module-level `plugin_registry` variable)
  - [x] Create FastAPI lifespan context manager (`lifespan` in `composition.py`):
    - [x] Read plugin directory from config (`plugins.plugin_dir`, env `SCAVENGARR_PLUGIN_DIR`, default `./plugins`)
    - [x] Initialize `PluginRegistry(plugin_dir=config.plugin_dir)`
    - [x] Call `discover()`
    - [x] Log total count: `log.info("plugins_discovered", count=state.plugins.discovered_count)`
  - [ ] Add getter function `def get_plugin_registry() -> PluginRegistry` for dependency injection (not needed: routers read `request.app.state`)

## 8. Testing

- [ ] 8.1 Create `tests/fixtures/plugins/` directory with example files (not created; registry tests write plugin files to `tmp_path`):
  - [ ] `valid-scrapy.yaml` (superseded):

```yaml
name: "test-scrapy"
description: "Test Scrapy plugin"
version: "1.0.0"
author: "test"
base_url: "https://example.com"
scraping:
  mode: "scrapy"
  search_path: "/search/{query}"
  selectors:
    row: "tr.result"
    title: "td.title::text"
    download_link: "td.link a::attr(href)"
    seeders: "td.seeds::text"
auth:
  type: "none"
```

  - [ ] `valid-playwright.yaml` (superseded):

```yaml
name: "test-playwright"
description: "Test Playwright plugin"
version: "1.0.0"
author: "test"
base_url: "https://example.com"
scraping:
  mode: "playwright"
  search_url_template: "https://example.com/search?q={query}"
  wait_for_selector: ".results"
  locators:
    row: ".result-row"
    title: ".title"
    download_link: ".download-btn"
auth:
  type: "none"
```

  - [ ] `valid-python.py`:

```python
from scavengarr.domain.plugins.base import SearchResult

class TestPythonPlugin:
    name = "test-python"

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Result for {query}",
                download_link="https://example.com/download",
                seeders=10,
                leechers=5,
            )
        ]

plugin = TestPythonPlugin()
```

  - [ ] `invalid-missing-mode.yaml` - YAML without `scraping.mode` (superseded)
  - [ ] `invalid-scrapy-no-selectors.yaml` - Scrapy mode without `selectors` (superseded)
  - [ ] `invalid-bad-url.yaml` - `base_url: "not-a-url"` (superseded)
  - [ ] `invalid-python-no-export.py` - Python file without `plugin` variable
  - [ ] `invalid-python-no-search.py` - Python plugin without `search` method
- [ ] 8.2 Create `tests/unit/plugins/test_schema.py` (superseded: YAML schema removed):
  - [ ] Test valid Scrapy YAML parses to `PluginDefinition`
  - [ ] Test valid Playwright YAML parses to `PluginDefinition`
  - [ ] Test missing `scraping.mode` raises `ValidationError`
  - [ ] Test Scrapy mode without `selectors` raises `ValidationError`
  - [ ] Test Playwright mode without `wait_for_selector` raises `ValidationError`
  - [ ] Test invalid `base_url` raises `ValidationError` with "URL scheme" message
  - [ ] Test `auth.type="basic"` without `username` raises `ValidationError`
  - [ ] Test invalid plugin name (uppercase, spaces) raises `ValidationError`
  - [ ] Test invalid version (not semver) raises `ValidationError`
- [ ] 8.3 Create `tests/unit/plugins/test_loader.py` (not created; no loader tests exist):
  - [ ] Test `load_yaml_plugin()` with valid YAML returns `PluginDefinition` (superseded)
  - [ ] Test `load_yaml_plugin()` with invalid YAML raises `PluginLoadError` (superseded)
  - [ ] Test `load_yaml_plugin()` with schema violation raises `PluginValidationError` (superseded)
  - [ ] Test `load_python_plugin()` with valid `.py` returns plugin instance
  - [ ] Test `load_python_plugin()` with missing `plugin` variable raises `PluginLoadError`
  - [ ] Test `load_python_plugin()` with missing `search` method raises `PluginLoadError`
  - [ ] Test `load_python_plugin()` with syntax error raises `PluginLoadError`
  - [ ] Test `load_plugin()` delegates to correct loader based on extension (superseded)
  - [ ] Test `load_plugin()` with `.txt` file raises `PluginLoadError` (superseded)
  - [ ] Test `discover_plugins()` finds both `.yaml` and `.py` files (superseded)
  - [ ] Test discovery ignores other file types
  - [ ] Test discovery of a non-existent directory yields no plugins
- [ ] 8.4 Registry tests (implemented as `tests/unit/infrastructure/test_plugin_registry.py`, planned `tests/unit/plugins/test_registry.py`; it covers `get_by_provides()`, metadata caching, `get_languages()` and `get_mode()`):
  - [ ] Test `discover()` populates the file index
  - [ ] Test `get()` lazy-loads plugin on first access
  - [ ] Test `get()` returns cached plugin on subsequent calls
  - [ ] Test `get()` with unknown name raises `PluginNotFoundError`
  - [ ] Test `list_names()` returns alphabetically sorted list
  - [ ] Test `get_by_mode("scrapy")` filters correctly (superseded; `get_mode()` is tested)
  - [ ] Test `get_by_mode("playwright")` filters correctly (superseded; `get_mode()` is tested)
  - [ ] Test duplicate plugin names raise `DuplicatePluginError`
  - [ ] Test `load_all()` forces loading of all plugins (superseded)
- [ ] 8.5 Create `tests/integration/test_plugin_loading.py` (not created)
  - [ ] Test loading all fixture plugins via registry
  - [ ] Test mixing YAML and Python plugins in same directory (superseded)
  - [ ] Test plugin registry accessible from FastAPI app startup

## 9. Documentation

- [x] 9.1 Plugin documentation (planned: `docs/plugin-schema.md`; implemented as `docs/features/python-plugins.md` and `docs/features/plugin-system.md`)
  - [ ] YAML schema reference (all fields, types, constraints) (superseded)
  - [x] Python plugin protocol specification
  - [x] Example plugins (Python, httpx and Playwright)
  - [ ] Common validation errors and fixes
  - [ ] Migration guide from Cardigann format (if applicable)
- [x] 9.2 Update `README.md`
  - [x] Add plugins section ("Plugins")
  - [ ] Link to `docs/plugin-schema.md` (superseded: plugin docs live in `docs/features/`)
  - [ ] Add example: "List all loaded plugins" CLI command (the CLI has no such command; `GET /api/v1/torznab/indexers` lists plugins)
