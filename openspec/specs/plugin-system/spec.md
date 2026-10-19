# plugin-system Specification

## Purpose
Find the site plugins in the plugin directory, load each file once, keep the ones that match the plugin contract, and hand them to the Torznab and Stremio paths by name, by what they provide and by their mode, with the configuration's per-plugin overrides applied at the start. The plugin system as a whole (base classes, search stages, categories, relevance, plugin selection): `docs/current/plugins.md`; the plugins: `docs/reference/plugins.md`; writing one: `docs/how-to/new-plugin.md`; the loader: `src/scavengarr/infrastructure/plugins/loader.py`, the registry: `src/scavengarr/infrastructure/plugins/registry.py`.

## Requirements

### Requirement: Plugin Discovery
`PluginRegistry.discover()` SHALL index the `.py` files directly in the plugin directory, in file-name order and without importing them, and SHALL ignore subdirectories and every other file. A plugin directory that is missing or not a directory SHALL leave the registry empty. A second call SHALL do nothing.

#### Scenario: Only Python files count
- **WHEN** the plugin directory holds `a.py`, `b.yaml`, `readme.txt` and a subdirectory with `c.py`
- **THEN** `discovered_count` is 1 and no file is imported
- **AND** the log has `plugins_discovered` with `count=1` and the directory

#### Scenario: Missing plugin directory
- **WHEN** the configured plugin directory does not exist
- **THEN** the log has `plugin_directory_not_found` with the directory
- **AND** `list_names()` returns an empty list

#### Scenario: Empty plugin directory
- **WHEN** the plugin directory holds no `.py` file
- **THEN** the log has `plugins_discovered` with `count=0` and then `no_plugins_found`
- **AND** the server starts without plugins

### Requirement: Every Plugin Loads Once
The first registry call other than `discover()` SHALL import every discovered file once, in discovery order, and keep the plugin instances; every later call SHALL answer from them without importing a file again. The startup SHALL make that call, so every plugin is imported before the server answers its first request.

#### Scenario: Discovery imports nothing
- **WHEN** `discover()` runs on a directory whose plugin file has a side effect at import
- **THEN** the side effect has not happened

#### Scenario: The first call loads every plugin
- **WHEN** `get("a")` is called for the first time
- **THEN** every discovered file is imported once
- **AND** the log has `plugin_loaded` with `plugin_name` and `plugin_type="python"` for each plugin

#### Scenario: Later calls import nothing
- **WHEN** `list_names()`, `get()`, `get_by_provides()`, `get_mode()` or `get_languages()` is called after the first call
- **THEN** no plugin file is imported again

#### Scenario: The startup loads the plugins
- **WHEN** the server starts
- **THEN** every plugin is imported before the first request, at the latest when the startup hands the shared browser pool to the Playwright plugins

### Requirement: Python Plugin Contract
A plugin file SHALL export a module-level `plugin` with a non-empty string `name` and a `search` method, `async def search(query, category=None, season=None, episode=None) -> list[SearchResult]` (`PluginProtocol` in `src/scavengarr/domain/plugins/base.py`). A file that does not SHALL fail to load with `PluginLoadError`.

#### Scenario: A valid plugin
- **WHEN** a file exports `plugin` with the name `a` and a `search` method
- **THEN** `get("a")` returns that instance

#### Scenario: No plugin export
- **WHEN** a file defines no module-level `plugin`
- **THEN** loading it fails with `PluginLoadError` "Plugin must export 'plugin' variable"

#### Scenario: No search method
- **WHEN** the exported `plugin` has no `search` method
- **THEN** loading it fails with `PluginLoadError` "Plugin must have 'search' method"

#### Scenario: No name
- **WHEN** the exported `plugin` has no `name`, or an empty one, or one that is not a string
- **THEN** loading it fails with `PluginLoadError` "Plugin must have non-empty 'name' attribute"

### Requirement: A Broken Plugin Is Skipped
A plugin file whose import raises, a syntax error included, SHALL fail with a `PluginLoadError` that carries the traceback. Every load failure SHALL be logged as `plugin_load_failed` with `plugin_file`, `plugin_type`, `error_type` and `error_message`, and the registry SHALL skip the file and load the others.

#### Scenario: A syntax error
- **WHEN** a plugin file contains invalid Python syntax
- **THEN** loading it fails with `PluginLoadError`, whose message holds the `SyntaxError`'s traceback
- **AND** the log has `plugin_load_failed` with `plugin_type="python"` and `error_type="PluginLoadError"`

#### Scenario: The other plugins keep working
- **WHEN** `broken.py` fails to load beside `alpha.py` and `zeta.py`
- **THEN** `list_names()` returns `["alpha", "zeta"]`

### Requirement: Unique Plugin Names
Of two files that export the same plugin name, the first in file-name order SHALL win; the later one SHALL be logged as `plugin_name_duplicate` with `plugin_name` and `plugin_file` and skipped.

#### Scenario: Two files with one name
- **WHEN** `a_site.py` and `b_site.py` both export a plugin named `site`
- **THEN** `get("site")` returns the plugin of `a_site.py`
- **AND** the log has `plugin_name_duplicate` with `plugin_name="site"` and the path of `b_site.py`

### Requirement: Plugin Metadata
The registry SHALL read each plugin's `provides` (`stream`, `download` or `both`; `download` when unset), `mode` (`httpx` or `playwright`; `httpx` when unset) and `languages` (`["de"]` when unset) once, when the plugin loads. `get_mode()` and `get_languages()` SHALL answer `httpx` and `["de"]` for a name they do not know.

#### Scenario: A plugin without metadata
- **WHEN** a plugin sets none of `provides`, `mode` and `languages`
- **THEN** `get_by_provides("download")` lists it, `get_mode()` answers `httpx` and `get_languages()` answers `["de"]`

#### Scenario: Declared metadata
- **WHEN** a plugin sets `provides = "stream"`, `mode = "playwright"` and `languages = ["de", "en"]`
- **THEN** `get_by_provides("stream")` lists it, `get_mode()` answers `playwright` and `get_languages()` answers `["de", "en"]`

### Requirement: Registry Access
`list_names()` SHALL return the loaded plugin names sorted, `get(name)` the plugin instance, and `get_by_provides(provides)` the sorted names of the plugins that provide it or `both`. `remove(name)` SHALL drop a plugin from every later answer.

#### Scenario: What the plugins provide
- **WHEN** `alpha` provides `stream`, `site` provides `download` and `zeta` provides `both`
- **THEN** `list_names()` returns `["alpha", "site", "zeta"]`
- **AND** `get_by_provides("stream")` returns `["alpha", "zeta"]` and `get_by_provides("download")` returns `["site", "zeta"]`

#### Scenario: A removed plugin
- **WHEN** `remove("alpha")` has been called
- **THEN** neither `list_names()` nor `get_by_provides("stream")` names `alpha`
- **AND** `get("alpha")` raises `PluginNotFoundError`

### Requirement: Unknown Plugin Names
`get()` of a name the registry does not know SHALL raise `PluginNotFoundError` "Plugin '<name>' not found". A Torznab request for such a name (`GET /api/v1/torznab/<name>`, `t=caps` or `t=search`) SHALL answer 404 with an RSS feed without items, and `GET /api/v1/torznab/<name>/health` SHALL answer 404 with `"reachable": false` and `"error": "plugin not found"`.

#### Scenario: The registry
- **WHEN** `get("unknown-plugin")` is called
- **THEN** it raises `PluginNotFoundError` with the message "Plugin 'unknown-plugin' not found"

#### Scenario: A Torznab search
- **WHEN** `GET /api/v1/torznab/unknown?t=search&q=test` comes in with `environment: dev`
- **THEN** the answer is 404, an RSS feed without items whose description says "plugin not found"

#### Scenario: A Torznab search in production
- **WHEN** the same request comes in with `environment: prod`
- **THEN** the answer is 404, and the feed's description does not name the error

#### Scenario: The health check
- **WHEN** `GET /api/v1/torznab/unknown/health` comes in
- **THEN** the answer is 404 with `{"plugin": "unknown", "reachable": false, "error": "plugin not found"}`

### Requirement: Episode Location Is Declared
A plugin that places a series episode by the request's `EpisodeRef` SHALL set `locates_episodes = True` and take `episode_ref` as a keyword of `search()`. The Stremio plugin search SHALL pass the reference to such plugins only, and an attribute that is not `True` itself SHALL declare nothing. How the episode is placed: `openspec/specs/stremio-anime-episodes/spec.md`.

#### Scenario: A plugin that locates episodes
- **WHEN** a Stremio request for an episode with an `EpisodeRef` asks a plugin with `locates_episodes = True`
- **THEN** the plugin's search gets `episode_ref` beside the query, the category, the season and the episode

#### Scenario: Every other plugin
- **WHEN** the same request asks a plugin without the attribute, or with a value that is not `True` (a mock's attribute)
- **THEN** the plugin's search gets the query, the category, the season and the episode only

### Requirement: Grab-Time Links
A plugin whose real links cost a captcha or a download quota MAY implement `resolve_download(url) -> list[str]` (`GrabResolvingPlugin`). A Torznab result of such a plugin SHALL keep the page URLs in its CrawlJob with the plugin's name, and `GET /api/v1/download/<job_id>` SHALL turn them into links through `resolve_download()` when the job is grabbed, store the job again with the links and without the plugin's name, and answer 502 when no link comes back.

#### Scenario: A grab resolves the links
- **WHEN** an Arr app grabs a job of a plugin that resolves links at grab time
- **THEN** each page URL goes through the plugin's `resolve_download()`, and the job is stored with the links, duplicates dropped and the order kept
- **AND** the log has `crawljob_resolved` with the plugin, the job id and the link count

#### Scenario: A repeated grab
- **WHEN** the same job is grabbed again
- **THEN** the stored links answer without asking the plugin

#### Scenario: No link
- **WHEN** the plugin returns no link, or raises
- **THEN** the download answers 502
- **AND** the log has `crawljob_resolve_failed` with the job id

### Requirement: Startup Wiring
The startup SHALL create the registry with `plugins.plugin_dir` (`./plugins` by default), discover the plugins, log `plugins_discovered` with their count, and apply the per-plugin overrides. The httpx plugins SHALL share the app's HTTP client and cache, and the Playwright plugins of `PlaywrightPluginBase` SHALL get the shared browser pool.

#### Scenario: The default directory
- **WHEN** no layer of the configuration sets the plugin directory
- **THEN** the registry reads `./plugins`

#### Scenario: The directory from the environment
- **WHEN** `SCAVENGARR_PLUGIN_DIR=/custom/path` is set
- **THEN** the registry reads `/custom/path`

#### Scenario: The directory from the command line
- **WHEN** `SCAVENGARR_PLUGIN_DIR=/custom/path` is set and the server starts with `--plugin-dir /other/path`
- **THEN** the registry reads `/other/path`

### Requirement: Per-Plugin Overrides
At the start `plugins.overrides.<name>` SHALL apply to the loaded plugin of that name. `enabled: false` SHALL remove it from the registry, logged as `plugin_disabled_by_config`. On a plugin of `HttpxPluginBase` or `PlaywrightPluginBase`, `max_concurrent` and `max_results` SHALL replace its own limits (5 and 1000 unless the plugin sets others) and `timeout` an httpx plugin's request timeout (15 s by default), logged as `plugin_override_applied`.

#### Scenario: A disabled plugin
- **WHEN** the YAML file sets `plugins.overrides.cineby.enabled: false`
- **THEN** `list_names()` does not name `cineby`
- **AND** a Torznab request for it answers 404, and no Stremio request asks it

#### Scenario: New limits
- **WHEN** the YAML file sets `timeout: 7.5`, `max_concurrent: 2` and `max_results: 9` for an httpx plugin
- **THEN** the plugin's requests time out after 7.5 s, its detail-page semaphore (`_new_semaphore()`) admits 2 requests at once, and it pages through at most 9 results for Torznab

### Requirement: Override Problems Are Logged
An override SHALL NOT stop the start. A name no plugin has SHALL be logged as `plugin_override_unknown`, a `timeout` for a Playwright plugin as `plugin_timeout_override_unsupported` while its other values apply, and limits for a plugin outside the two base classes as `plugin_override_unsupported`, which leaves that plugin unchanged.

#### Scenario: A misspelled plugin name
- **WHEN** the YAML file disables `cinebyy`, which no plugin is named
- **THEN** the log has `plugin_override_unknown` with `plugin="cinebyy"`
- **AND** every plugin stays loaded

#### Scenario: A timeout for a Playwright plugin
- **WHEN** the YAML file sets `timeout: 7.5` and `max_concurrent: 2` for a Playwright plugin
- **THEN** the log has `plugin_timeout_override_unsupported`
- **AND** the plugin's semaphore (`_new_semaphore()`) admits 2 pages at once

### Requirement: Result Limit
A plugin of the two base classes SHALL page through the site's results up to `effective_max_results`: its `_max_results`, lowered to the caller's limit where the caller sets one (`search_max_results`). A Stremio search SHALL set `stremio.max_results_per_plugin` (100 by default); Torznab sets none.

#### Scenario: A Torznab search
- **WHEN** a Torznab search asks a plugin with the default limit
- **THEN** the plugin pages up to 1000 results

#### Scenario: A Stremio search
- **WHEN** a Stremio search asks the same plugin
- **THEN** the plugin pages up to 100 results

#### Scenario: A lower plugin limit wins
- **WHEN** `plugins.overrides.<name>.max_results` is 50 and a Stremio search asks the plugin
- **THEN** the plugin pages up to 50 results
