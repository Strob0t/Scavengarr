[← Back to Index](./README.md)

# Plugin System

> How Scavengarr discovers, loads, configures and calls its 41 Python plugins. For writing a plugin, see [Python Plugins](./python-plugins.md).

---

## Overview

Scavengarr is plugin-driven. Each plugin knows how to search one source site and returns normalized `SearchResult` objects. Plugins implement the multi-stage pipeline (search page → detail pages → links), authentication, pagination and domain fallback inside their `search()` method.

Key characteristics:
- **Python-only:** every plugin is a `.py` file in the plugin directory that exports a module-level `plugin` instance.
- **Two base classes:** `HttpxPluginBase` (33 plugins) and `PlaywrightPluginBase` (9 plugins), see [Python Plugins](./python-plugins.md#plugin-base-classes).
- **Loaded at startup:** discovery only indexes files, but startup wiring imports every plugin once and caches the instances for the process lifetime.
- **Shared resources:** httpx plugins share one rate-limited, retrying HTTP client; Playwright plugins share one Chromium process.

---

## Discovery & Loading

### Startup Flow

```text
Server startup (composition root)
  |
  v
PluginRegistry.discover()
  |-- Scans plugin_dir for .py files (sorted by file name)
  |-- Indexes paths only, no import
  |-- Logs: "plugins_discovered count=N"
  |
  v
_apply_plugin_overrides()            (plugins.overrides from YAML)
  |-- first registry call imports every plugin file once
  |-- enabled: false -> registry.remove(name)
  |-- otherwise get(name) and set _timeout / _max_concurrent / _max_results
  |
  v
_inject_shared_browser_pool()
  |-- list_names() + get_mode(name) for every plugin (cached)
  |-- Playwright plugins receive the SharedBrowserPool
```

### Key Behaviors

1. **Discovery is file-only:** `discover()` reads no file contents and runs once.
2. **One import per file, at startup:** the first call of any other method imports every discovered file once via `load_python_plugin()` (`importlib`) and caches the instances and their metadata (`provides`, `mode`, `languages`); the plugin name is only known after the import. The startup wiring makes that first call, so all plugins are imported before the first request, and `list_names()` (called by `/healthz` on every probe), `get()` and `get_by_provides()` only read the cache.
3. **Validation on import:** the module must export `plugin`, which needs a non-empty `name: str` and a `search` method; otherwise `PluginLoadError`. A file that fails to import is logged (`plugin_load_failed`) and skipped; the other plugins keep working.
4. **Duplicate names:** the first file in alphabetical order wins, later ones are logged (`plugin_name_duplicate`) and skipped.

### Registry API

| Method | Returns |
|---|---|
| `discover()` | Indexes `.py` files (idempotent) |
| `list_names()` | Sorted plugin names |
| `get(name)` | Cached plugin instance, raises `PluginNotFoundError` |
| `get_by_provides(provides)` | Names whose `provides` matches, plugins with `"both"` always included |
| `get_languages(name)` | Plugin `languages` (default `["de"]`) |
| `get_mode(name)` | `"httpx"` or `"playwright"` |
| `remove(name)` | Drops a plugin (used by `enabled: false`) |
| `discovered_count` | Number of discovered files |

### Plugin Directory

The default plugin directory is `./plugins`. Override it via:

```bash
# Environment variable
export SCAVENGARR_PLUGIN_DIR=/path/to/plugins

# CLI argument
poetry run start --plugin-dir /path/to/plugins
```

or in YAML as `plugins.plugin_dir` (a top-level `plugin_dir` key is also accepted). See [Configuration](./configuration.md).

---

## Per-Plugin Overrides

`plugins.overrides.<plugin-name>` in the YAML config adjusts individual plugins at startup:

```yaml
plugins:
  overrides:
    kinoking:
      timeout: 30          # seconds, sets _timeout (httpx plugins only)
      max_concurrent: 2    # sets _max_concurrent (semaphore size)
      max_results: 500     # sets _max_results
    kinox:
      enabled: false       # removes the plugin from the registry
```

Unknown plugin names are logged as `plugin_override_unknown` and ignored.

---

## PluginProtocol

Every plugin must satisfy the `PluginProtocol` defined in the domain layer:

```python
# src/scavengarr/domain/plugins/base.py
PluginProvides = Literal["stream", "download", "both"]


class PluginProtocol(Protocol):
    name: str
    provides: PluginProvides

    async def search(
        self,
        query: str,
        category: int | None = None,
        season: int | None = None,
        episode: int | None = None,
    ) -> list[SearchResult]: ...
```

Optional capability: a plugin whose links sit behind a captcha or a download quota also implements `GrabResolvingPlugin` (`async def resolve_download(self, url: str) -> list[str]`). Its search results keep page URLs; the links are resolved only when an Arr app grabs the result (see [Grab-Time Resolution](./crawljob-system.md#grab-time-resolution); example: `plugins/nox.py`).

### Contract Requirements

| Requirement | Details |
|---|---|
| Module-level `plugin` variable | The `.py` file must export a variable named `plugin` |
| `name: str` attribute | Non-empty string, used as the plugin identifier in URLs |
| `provides` attribute | `"stream"` (streaming links), `"download"` (DDL links) or `"both"`; base classes default to `"download"` |
| `async def search(...)` method | Returns `list[SearchResult]` |
| `query: str` parameter | The search term |
| `category: int \| None` parameter | Optional Torznab category ID for filtering |
| `season: int \| None` parameter | Optional season number (Stremio series requests) |
| `episode: int \| None` parameter | Optional episode number (Stremio series requests) |

---

## How Plugins Are Called

| Path | Plugins | Call |
|---|---|---|
| Torznab search (`TorznabSearchUseCase`) | the plugin named in the URL | `plugin.search(query, category=category)`; results are cached per query (`cache_ttl` on the plugin overrides the default TTL) and then link-validated |
| Stremio streams (`PluginSearchRunner`) | the plugins with `provides` `"stream"` or `"both"` whose site answered its last health check, one per mirror group (with `stremio.scoring_enabled`: the top `max_plugins_scored` by score plus an exploration slot) | `plugin.isolated_search(query, category, season=..., episode=...)` under the global concurrency pool, with per-plugin timeout and circuit breaker |
| Scoring probes (`MiniSearchProber`) | the stream plugins, in the background (`scoring.enabled`) | `plugin.search(query, category=category)` with `search_max_results` at `scoring.search_max_items` (20) and a `scoring.search_timeout_seconds` (10 s) timeout |

`isolated_search()` is a passthrough for httpx plugins. Playwright plugins run it in a per-request `BrowserContext` (or serialized behind a lock when `_serialize_search = True`), so concurrent Stremio requests do not share page state. Torznab requests and scoring probes call `search()` directly and share the plugin's persistent context.

During Stremio searches and scoring probes, the `search_max_results` context variable lowers the pagination limit; plugins read it through `effective_max_results`.

---

## Plugin Exceptions

```python
# src/scavengarr/domain/plugins/exceptions.py
class PluginError(Exception): ...            # Base class
class PluginLoadError(PluginError): ...      # Import or protocol failure
class PluginNotFoundError(PluginError): ...  # Name not in registry
```

| Exception | Trigger |
|---|---|
| `PluginLoadError` | Module has no `plugin` variable, no `search` method, or empty `name` |
| `PluginLoadError` | `SyntaxError` or `ImportError` during module import |
| `PluginNotFoundError` | `registry.get("unknown-name")`; the Torznab router maps it to a Torznab "plugin not found" error |

---

## Source Code References

| Component | Path |
|---|---|
| PluginProtocol, SearchResult | `src/scavengarr/domain/plugins/base.py` |
| Plugin exceptions | `src/scavengarr/domain/plugins/exceptions.py` |
| Plugin loader | `src/scavengarr/infrastructure/plugins/loader.py` |
| Plugin registry | `src/scavengarr/infrastructure/plugins/registry.py` |
| Plugin constants (defaults, `search_max_results`) | `src/scavengarr/infrastructure/plugins/constants.py` |
| Override config (`PluginOverride`) | `src/scavengarr/infrastructure/config/schema.py` |
| Startup wiring | `src/scavengarr/interfaces/composition.py` |
| Torznab dispatch | `src/scavengarr/application/use_cases/torznab_search.py` |
| Stremio dispatch | `src/scavengarr/application/stremio/plugin_search.py` |
