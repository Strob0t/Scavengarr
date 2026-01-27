## 1. Base Protocol and Models
- [ ] 1.1 Create `src/scavengarr/plugins/__init__.py` (empty, for package)
- [ ] 1.2 Create `src/scavengarr/plugins/base.py`:
  - [ ] Define `SearchResult(BaseModel)` with fields:
    - [ ] `title: str`
    - [ ] `download_link: str`
    - [ ] `seeders: int | None = None`
    - [ ] `leechers: int | None = None`
    - [ ] `size: str | None = None`
    - [ ] `published_date: str | None = None`
  - [ ] Define `PluginProtocol(Protocol)` with method signature:
    - [ ] `async def search(self, query: str, category: int | None = None) -> list[SearchResult]`
  - [ ] Add comprehensive docstrings with examples

## 2. YAML Plugin Schema
- [ ] 2.1 Create `src/scavengarr/plugins/schema.py` with Pydantic models:
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

- [ ] 2.2 Add field validators:
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
- [ ] 3.1 Create `src/scavengarr/plugins/exceptions.py`:
  - [ ] `PluginLoadError(Exception)` - Generic load failures
  - [ ] `PluginValidationError(Exception)` - Schema/protocol validation failures
  - [ ] `PluginNotFoundError(Exception)` - Plugin name doesn't exist in registry
  - [ ] `DuplicatePluginError(Exception)` - Two plugins with same name

## 4. Plugin Loader
- [ ] 4.1 Create `src/scavengarr/plugins/loader.py`:
  - [ ] Import `yaml`, `importlib.util`, `Path`, `PluginDefinition`, base types
  - [ ] Function `load_yaml_plugin(path: Path) -> PluginDefinition`:
    - [ ] Read YAML file with `yaml.safe_load()`
    - [ ] Validate with `PluginDefinition(**data)`
    - [ ] Catch `yaml.YAMLError` and wrap in `PluginLoadError`
    - [ ] Catch `ValidationError` and wrap in `PluginValidationError`
  - [ ] Function `load_python_plugin(path: Path) -> object`:
    - [ ] Use `importlib.util.spec_from_file_location()` for dynamic import
    - [ ] Execute module with `spec.loader.exec_module(module)`
    - [ ] Validate module exports `plugin` variable
    - [ ] Validate `plugin` has `search` method (use `hasattr` or `isinstance(PluginProtocol)`)
    - [ ] Catch `ImportError`, `AttributeError`, `SyntaxError` and wrap in `PluginLoadError`
  - [ ] Function `load_plugin(path: Path) -> PluginDefinition | object`:
    - [ ] Detect format by `path.suffix`
    - [ ] Delegate to `load_yaml_plugin()` if `.yaml`
    - [ ] Delegate to `load_python_plugin()` if `.py`
    - [ ] Raise `PluginLoadError` for unsupported extensions
  - [ ] Function `discover_plugins(directory: Path) -> list[Path]`:
    - [ ] Use `Path.glob("*.yaml")` and `Path.glob("*.py")`
    - [ ] Return sorted list of absolute paths
    - [ ] Return empty list if directory doesn't exist (don't raise)

- [ ] 4.2 Add logging with structlog:
  - [ ] Log `plugin_discovered` (level=DEBUG) with `plugin_file` field
  - [ ] Log `plugin_loaded` (level=INFO) with `plugin_name`, `plugin_type` ("yaml" or "python")
  - [ ] Log `plugin_load_failed` (level=ERROR) with `plugin_file`, `error_type`, `error_message`

## 5. Plugin Registry
- [ ] 5.1 Create `src/scavengarr/plugins/registry.py`:
  - [ ] Class `PluginRegistry`:
    - [ ] `__init__(self, plugin_dir: Path)`
    - [ ] `_plugins: dict[str, PluginDefinition | object] = {}` (in-memory cache)
    - [ ] `_plugin_files: dict[str, Path] = {}` (name → file path mapping)
    - [ ] Method `discover(self) -> None`:
      - [ ] Call `discover_plugins(self.plugin_dir)`
      - [ ] Build `_plugin_files` mapping (extract name from YAML or Python module)
      - [ ] Don't load plugins yet (lazy-loading)
    - [ ] Method `_load_plugin(self, name: str) -> PluginDefinition | object`:
      - [ ] Check if already cached in `_plugins`
      - [ ] If not, call `load_plugin(self._plugin_files[name])`
      - [ ] Extract name from loaded plugin (YAML: `plugin.name`, Python: `plugin.__class__.__name__.lower()` or manual attribute)
      - [ ] Validate name uniqueness (raise `DuplicatePluginError` if exists)
      - [ ] Cache in `_plugins`
      - [ ] Return plugin
    - [ ] Method `get(self, name: str) -> PluginDefinition | object`:
      - [ ] Call `_load_plugin(name)` if not cached
      - [ ] Raise `PluginNotFoundError` if name not in `_plugin_files`
      - [ ] Return cached plugin
    - [ ] Method `list_names(self) -> list[str]`:
      - [ ] Return `sorted(self._plugin_files.keys())`
    - [ ] Method `get_by_mode(self, mode: str) -> list[PluginDefinition]`:
      - [ ] Load all plugins (iterate `_plugin_files`)
      - [ ] Filter YAML plugins where `plugin.scraping.mode == mode`
      - [ ] Python plugins have no mode (skip or return empty for Python)
      - [ ] Return filtered list
    - [ ] Method `load_all(self) -> None`:
      - [ ] Force-load all discovered plugins (for validation/testing)
      - [ ] Iterate `_plugin_files` and call `_load_plugin(name)`

## 6. Package Exports
- [ ] 6.1 Update `src/scavengarr/plugins/__init__.py`:
  - [ ] Export `PluginRegistry`
  - [ ] Export `PluginDefinition`, `SearchResult`, `PluginProtocol`
  - [ ] Export all exceptions (`PluginLoadError`, `PluginNotFoundError`, etc.)

## 7. Integration with Main App
- [ ] 7.1 Update `src/scavengarr/main.py`:
  - [ ] Import `PluginRegistry`
  - [ ] Add module-level variable `plugin_registry: PluginRegistry | None = None`
  - [ ] Create FastAPI lifespan context manager or `@app.on_event("startup")`:
    - [ ] Read plugin directory from env var `SCAVENGARR_PLUGIN_DIR` (default: `./plugins`)
    - [ ] Initialize `plugin_registry = PluginRegistry(Path(plugin_dir))`
    - [ ] Call `plugin_registry.discover()`
    - [ ] Log total count: `structlog.info("plugins_discovered", count=len(plugin_registry.list_names()))`
  - [ ] Add getter function `def get_plugin_registry() -> PluginRegistry` for dependency injection

## 8. Testing
- [ ] 8.1 Create `tests/fixtures/plugins/` directory with example files:
  - [ ] `valid-scrapy.yaml`:
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
  - [ ] `valid-playwright.yaml`:
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
from scavengarr.plugins.base import SearchResult

class TestPythonPlugin:
    async def search(self, query: str, category: int | None = None) -> list[SearchResult]:
        return [
            SearchResult(
                title=f"Result for {query}",
                download_link="https://example.com/download",
                seeders=10,
                leechers=5
            )
        ]

plugin = TestPythonPlugin()

```
  - [ ] `invalid-missing-mode.yaml` - YAML without `scraping.mode`
  - [ ] `invalid-scrapy-no-selectors.yaml` - Scrapy mode without `selectors`
  - [ ] `invalid-bad-url.yaml` - `base_url: "not-a-url"`
  - [ ] `invalid-python-no-export.py` - Python file without `plugin` variable
  - [ ] `iinvalid-python-no-search.py` - Python plugin without `search` method
- [ ] 8.2 Create `tests/unit/plugins/test_schema.py`:
  - [ ] Test valid Scrapy YAML parses to `PluginDefinition`
  - [ ] Test valid Playwright YAML parses to `PluginDefinition`
  - [ ] Test missing `scraping.mode` raises `ValidationError`
  - [ ] Test Scrapy mode without `selectors` raises `ValidationError`
  - [ ] Test Playwright mode without `wait_for_selector` raises `ValidationError`
  - [ ] Test invalid `base_url` raises `ValidationError` with "URL scheme" message
  - [ ] Test `auth.type="basic"` without `username` raises `ValidationError`
  - [ ] Test invalid plugin name (uppercase, spaces) raises `ValidationError`
  - [ ] Test invalid version (not semver) raises `ValidationError`
- [ ] 8.3 Create `tests/unit/plugins/test_schema.py`:
  - [ ] Test `load_yaml_plugin()` with valid YAML returns `PluginDefinition`
  - [ ] Test `load_yaml_plugin()` with invalid YAML raises `PluginLoadError`
  - [ ] Test `load_yaml_plugin()` with schema violation raises `PluginValidationError`
  - [ ] Test `load_python_plugin()` with valid `.py` returns plugin instance
  - [ ] Test `load_python_plugin()` with missing `plugin` variable raises `PluginLoadError`
  - [ ] Test `load_python_plugin()` with missing `search` method raises `PluginLoadError`
  - [ ] Test `load_python_plugin()` with syntax error raises `PluginLoadError`
  - [ ] Test `load_plugin()` delegates to correct loader based on extension
  - [ ] Test `load_plugin()` with `.txt` file raises `PluginLoadError`
  - [ ] Test `discover_plugins()` finds both `.yaml` and `.py` files
  - [ ] Test `discover_plugins()` ignores other file types
  - [ ] Test `discover_plugins()` returns empty list for non-existent directory
- [ ] 8.4 Create `tests/unit/plugins/test_registry.py`:
  - [ ] Test `discover()` populates `_plugin_files` mapping
  - [ ] Test `get()` lazy-loads plugin on first access
  - [ ] Test `get()` returns cached plugin on subsequent calls
  - [ ] Test `get()` with unknown name raises `PluginNotFoundError`
  - [ ] Test `list_names()` returns alphabetically sorted list
  - [ ] Test `get_by_mode("scrapy")` filters correctly (YAML plugins only)
  - [ ] Test `get_by_mode("playwright")` filters correctly
  - [ ] Test duplicate plugin names raise `DuplicatePluginError`
  - [ ] Test `load_all()` forces loading of all plugins
- [ ] 8.5 Create `tests/integration/test_plugin_loading.py`
  - [ ] Test loading all fixture plugins via registry
  - [ ] Test mixing YAML and Python plugins in same directory
  - [ ] Test plugin registry accessible from FastAPI app startup

## 9. Documentation
- [ ] 9.1 Create `docs/plugin-schema.md`
  - [ ] YAML schema reference (all fields, types, constraints)
  - [ ] Python plugin protocol specification
  - [ ] Example plugins for each mode
  - [ ] Common validation errors and fixes
  - [ ] Migration guide from Cardigann format (if applicable)

- [ ] 9.2 Update `README.md`
  - [ ] Add "Creating Plugins" section
  - [ ] Link to `docs/plugin-schema.md`
  - [ ] Add example: "List all loaded plugins" CLI command
