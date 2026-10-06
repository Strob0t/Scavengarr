## ADDED Requirements

### Requirement: Plugin Discovery
The system SHALL automatically discover all Python files in the configured plugin directory. Status: implemented for `.py` files (`PluginRegistry.discover()`); YAML discovery is superseded (removed in `42fced9`).

#### Scenario: Mixed plugin types discovered
- **WHEN** the plugin directory contains `plugin-a.yaml`, `plugin-b.py`, and `readme.txt`
- **THEN** the system discovers exactly 1 plugin (`plugin-b.py`; originally 2 including YAML, superseded)
- **AND** the `.yaml` and `.txt` files are ignored

#### Scenario: Empty plugin directory
- **WHEN** the plugin directory exists but contains no `.py` files
- **THEN** the system logs a warning event `no_plugins_found` with field `directory`
- **AND** the application starts successfully without errors

#### Scenario: Plugin directory does not exist
- **WHEN** the configured plugin directory path does not exist
- **THEN** the system logs a warning event `plugin_directory_not_found` with field `directory`
- **AND** `PluginRegistry.list_names()` returns an empty list

### Requirement: YAML Plugin Schema Validation (superseded)
The system SHALL validate YAML plugins against the Pydantic schema and reject invalid files. Status: superseded — YAML plugins were removed in `42fced9`; `PluginValidationError` was removed in `03454a8`.

#### Scenario: Valid Scrapy plugin loaded
- **WHEN** a YAML file conforms to the schema with `scraping.mode: "scrapy"`
- **THEN** the plugin is loaded into the registry
- **AND** a log entry `plugin_loaded` is created with `plugin_name` and `plugin_type: "yaml"`

#### Scenario: Valid Playwright plugin loaded
- **WHEN** a YAML file has `scraping.mode: "playwright"` with all required fields
- **THEN** the plugin is loaded successfully

#### Scenario: Missing required field
- **WHEN** a YAML plugin is missing the `scraping.mode` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "Field required: scraping.mode"
- **AND** the plugin is NOT added to the registry

#### Scenario: Invalid scraping mode
- **WHEN** a YAML plugin has `scraping.mode: "invalid"`
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "Input should be 'scrapy' or 'playwright'"

#### Scenario: Invalid base URL
- **WHEN** a YAML plugin has `base_url: "not-a-url"`
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "URL scheme should be 'http' or 'https'"

#### Scenario: Invalid plugin name format
- **WHEN** a YAML plugin has `name: "Invalid Name"` (contains uppercase and spaces)
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "String should match pattern '^[a-z0-9-]+$'"

#### Scenario: Invalid version format
- **WHEN** a YAML plugin has `version: "v1.0"` (not semver)
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "String should match pattern '^\d+\.\d+\.\d+$'"

### Requirement: Scrapy Mode Configuration (superseded)
YAML plugins with `scraping.mode: "scrapy"` SHALL define CSS selectors and search path. Status: superseded — Scrapy and YAML plugins were removed in `42fced9`.

#### Scenario: Scrapy plugin with valid selectors
- **WHEN** a plugin has mode "scrapy" and defines `search_path`, `selectors.row`, `selectors.title`, `selectors.download_link`
- **THEN** the plugin is loaded successfully

#### Scenario: Scrapy plugin missing selectors
- **WHEN** a plugin has `scraping.mode: "scrapy"` but no `selectors` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "scrapy mode requires 'selectors' field"

#### Scenario: Scrapy plugin missing search_path
- **WHEN** a plugin has `scraping.mode: "scrapy"` but no `search_path` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "scrapy mode requires 'search_path' field"

### Requirement: Playwright Mode Configuration (superseded)
YAML plugins with `scraping.mode: "playwright"` SHALL define locators and wait selector. Status: superseded — Playwright plugins are Python classes inheriting `PlaywrightPluginBase`.

#### Scenario: Playwright plugin with valid locators
- **WHEN** a plugin has mode "playwright" and defines `search_url_template`, `wait_for_selector`, `locators.row`, `locators.title`, `locators.download_link`
- **THEN** the plugin is loaded successfully

#### Scenario: Playwright plugin missing wait_for_selector
- **WHEN** a plugin has `scraping.mode: "playwright"` but no `wait_for_selector` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "playwright mode requires 'wait_for_selector' field"

#### Scenario: Playwright plugin missing locators
- **WHEN** a plugin has `scraping.mode: "playwright"` but no `locators` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "playwright mode requires 'locators' field"

### Requirement: Python Plugin Protocol Validation
The system SHALL validate Python plugins implement the required protocol. Status: implemented in `load_python_plugin()` (`src/scavengarr/infrastructure/plugins/loader.py`), which also requires a non-empty `name` attribute ("Plugin must have non-empty 'name' attribute").

#### Scenario: Valid Python plugin loaded
- **WHEN** a `.py` file exports `plugin` variable with a `search` method
- **THEN** the plugin is loaded into the registry
- **AND** a log entry `plugin_loaded` is created with `plugin_type: "python"`

#### Scenario: Python file without plugin export
- **WHEN** a `.py` file does not export a `plugin` variable
- **THEN** a `PluginLoadError` is raised
- **AND** the error message includes "Plugin must export 'plugin' variable"

#### Scenario: Python plugin without search method
- **WHEN** a `.py` file exports `plugin` but it lacks a `search` method
- **THEN** a `PluginLoadError` is raised
- **AND** the error message includes "Plugin must have 'search' method"

#### Scenario: Python file with syntax error
- **WHEN** a `.py` file contains invalid Python syntax
- **THEN** a `PluginLoadError` is raised
- **AND** the error message includes the syntax error traceback

### Requirement: Authentication Configuration (superseded)
The system SHALL support authentication configuration for YAML plugins via the `auth` section. Status: superseded — Python plugins implement authentication themselves (e.g. boerse, mygully, dataload); the plain `AuthConfig` dataclass that remained in `src/scavengarr/domain/plugins/plugin_schema.py` was removed unused (2026-10-06).

#### Scenario: No authentication
- **WHEN** a plugin has `auth.type: "none"` or omits the `auth` field
- **THEN** the plugin is loaded with authentication disabled

#### Scenario: Basic authentication configured
- **WHEN** a plugin has `auth.type: "basic"` with `username` and `password` fields
- **THEN** the plugin is loaded successfully

#### Scenario: Basic authentication missing credentials
- **WHEN** a plugin has `auth.type: "basic"` but missing `username` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "basic auth requires 'username' and 'password'"

#### Scenario: Form authentication configured
- **WHEN** a plugin has `auth.type: "form"` with `login_url`, `username_field`, `password_field`, `submit_selector`, `username`, `password`
- **THEN** the plugin is loaded successfully

#### Scenario: Form authentication missing login_url
- **WHEN** a plugin has `auth.type: "form"` but no `login_url` field
- **THEN** a `PluginValidationError` is raised
- **AND** the error message includes "form auth requires 'login_url' field"

### Requirement: Plugin Registry Access
The system SHALL provide a registry to retrieve loaded plugins by name or filter by criteria. Status: implemented as `PluginRegistry.get()`, `list_names()`, `get_mode(name)`, `get_by_provides(provides)` and `get_languages(name)`; the YAML scenarios and `get_by_mode()` are superseded.

#### Scenario: Get plugin by name (YAML) (superseded)
- **WHEN** a YAML plugin named "1337x" is loaded
- **AND** `PluginRegistry.get("1337x")` is called
- **THEN** a `PluginDefinition` object is returned

#### Scenario: Get plugin by name (Python)
- **WHEN** a Python plugin is loaded
- **AND** `PluginRegistry.get("my-plugin")` is called
- **THEN** the plugin instance object is returned

#### Scenario: Get non-existent plugin
- **WHEN** `PluginRegistry.get("unknown-plugin")` is called
- **THEN** a `PluginNotFoundError` is raised with message "Plugin 'unknown-plugin' not found"

#### Scenario: List all plugin names
- **WHEN** plugins "1337x", "rarbg", "my-gully" are loaded
- **AND** `PluginRegistry.list_names()` is called
- **THEN** a list `["1337x", "my-gully", "rarbg"]` is returned (alphabetically sorted)

#### Scenario: Filter plugins by mode (Scrapy) (superseded)
- **WHEN** YAML plugins "1337x" (scrapy) and "yggtorrent" (playwright) are loaded
- **AND** `PluginRegistry.get_by_mode("scrapy")` is called
- **THEN** only the "1337x" plugin is returned

#### Scenario: Filter plugins by mode (Playwright) (superseded)
- **WHEN** YAML plugins "1337x" (scrapy) and "yggtorrent" (playwright) are loaded
- **AND** `PluginRegistry.get_by_mode("playwright")` is called
- **THEN** only the "yggtorrent" plugin is returned

#### Scenario: Filter plugins ignores Python plugins (superseded)
- **WHEN** mixed YAML and Python plugins are loaded
- **AND** `PluginRegistry.get_by_mode("scrapy")` is called
- **THEN** only YAML plugins with `scraping.mode: "scrapy"` are returned
- **AND** Python plugins are excluded from results

### Requirement: Plugin Name Uniqueness
The system SHALL enforce unique plugin names across all loaded plugins. Status: partially implemented — `DuplicatePluginError` exists in `src/scavengarr/domain/plugins/exceptions.py` but is never raised; `list_names()` skips duplicate names and the first-loaded plugin wins.

#### Scenario: Duplicate plugin names rejected
- **WHEN** two files `duplicate.yaml` and `duplicate.py` both define `name: "duplicate"`
- **THEN** the second plugin raises a `DuplicatePluginError` with message "Plugin name 'duplicate' already exists" (not implemented: the duplicate is silently skipped)
- **AND** only the first-loaded plugin is retained in the registry

### Requirement: Lazy Loading
The system SHALL defer parsing plugin files until first access to improve startup performance.

#### Scenario: Plugin not parsed during discovery
- **WHEN** `PluginRegistry.discover()` is called
- **THEN** plugin files are indexed but not parsed
- **AND** no Python execution occurs

#### Scenario: Plugin parsed on first access
- **WHEN** `PluginRegistry.get("filmpalast")` is called for the first time
- **THEN** the matching `.py` file is imported and validated
- **AND** the plugin is cached in memory

#### Scenario: Subsequent access uses cache
- **WHEN** a plugin has been accessed once
- **AND** `PluginRegistry.get("same-plugin")` is called again
- **THEN** no file I/O or parsing occurs
- **AND** the cached plugin instance is returned

#### Scenario: Force-load all plugins (superseded)
- **WHEN** `PluginRegistry.load_all()` is called (method removed; `get_by_provides()` loads all plugins once to build its metadata cache)
- **THEN** all discovered plugins are parsed and validated
- **AND** all plugins are cached in memory

### Requirement: Error Logging
The system SHALL log detailed errors for plugin load and validation failures.

#### Scenario: YAML validation error logged (superseded)
- **WHEN** a YAML plugin fails Pydantic validation
- **THEN** a log entry is created with level ERROR
- **AND** the log includes fields: `event="plugin_validation_failed"`, `plugin_file="path/to/file.yaml"`, `error_type="ValidationError"`, `error_details=[...]`

#### Scenario: Python import error logged
- **WHEN** a Python plugin fails to import due to syntax error
- **THEN** a log entry is created with level ERROR
- **AND** the log includes fields: `event="plugin_load_failed"`, `plugin_file="path/to/file.py"`, `plugin_type="python"`, `error_type="PluginLoadError"` (the `SyntaxError` is wrapped), `error_message="..."` (contains the traceback)

#### Scenario: Plugin discovery logged
- **WHEN** `PluginRegistry.discover()` is called
- **THEN** a log entry is created with level INFO
- **AND** the log includes fields: `event="plugins_discovered"`, `count=<number>`, `directory="<path>"`

#### Scenario: Plugin loaded logged
- **WHEN** a plugin is successfully loaded
- **THEN** a log entry is created with level INFO
- **AND** the log includes fields: `event="plugin_loaded"`, `plugin_name="<name>"`, `plugin_type="python"`

### Requirement: Plugin Integration with Application
The system SHALL initialize the plugin registry on application startup.

#### Scenario: Registry initialized on FastAPI startup
- **WHEN** the FastAPI application starts
- **THEN** a `PluginRegistry` instance is created with the configured plugin directory (in `src/scavengarr/interfaces/composition.py`)
- **AND** `PluginRegistry.discover()` is called
- **AND** the total count of discovered plugins is logged

#### Scenario: Plugin directory configurable via environment
- **WHEN** the environment variable `SCAVENGARR_PLUGIN_DIR=/custom/path` is set
- **THEN** the registry uses `/custom/path` as the plugin directory

#### Scenario: Default plugin directory used
- **WHEN** `SCAVENGARR_PLUGIN_DIR` is not set
- **THEN** the registry uses `./plugins` as the default directory
