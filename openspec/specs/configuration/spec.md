# configuration Specification

## Purpose
Load the server's settings once at the start from four layers with a fixed precedence, validate them, and report in the startup log what the server runs with and what loading ignored. Every setting with its YAML key, environment variable and default: `docs/reference/configuration.md`; the loader: `src/scavengarr/infrastructure/config/load.py`, the model: `src/scavengarr/infrastructure/config/schema.py`.

## Requirements

### Requirement: Deterministic Config Precedence
The system SHALL build the configuration from four layers, each one overriding the layers below it: the built-in defaults (`DEFAULT_CONFIG` in `defaults.py` and the field defaults of the model), the YAML file, the environment (the variables of a `--dotenv` file included), and the CLI arguments. A key a layer does not set SHALL keep the value of the layer below.

#### Scenario: Defaults only
- **WHEN** no YAML file, no `SCAVENGARR_*` variable and no CLI override is given
- **THEN** every setting has its default, and the startup log reports no changed value

#### Scenario: YAML overrides defaults
- **WHEN** the YAML file sets `http.timeout_seconds: 15`
- **THEN** the effective `http_timeout_seconds` is 15.0
- **AND** the settings the file does not name keep their defaults

#### Scenario: Environment overrides YAML
- **WHEN** the YAML file sets `http.timeout_seconds: 15`
- **AND** `SCAVENGARR_HTTP_TIMEOUT_SECONDS=60` is set
- **THEN** the effective `http_timeout_seconds` is 60.0

#### Scenario: CLI overrides the environment
- **WHEN** `SCAVENGARR_LOG_LEVEL=INFO` is set
- **AND** the server starts with `--log-level DEBUG`
- **THEN** the effective `log_level` is `DEBUG`

### Requirement: Config File Selection
The CLI SHALL read the YAML file that `--config` names, else the one `SCAVENGARR_CONFIG` names when that file exists. A `--config` file that does not exist SHALL stop the start with `FileNotFoundError`. A `SCAVENGARR_CONFIG` that names a missing file SHALL be logged as `config_file_not_found`, and the start SHALL go on without a YAML layer.

#### Scenario: The image's config path
- **WHEN** the production image starts with its `SCAVENGARR_CONFIG=/app/config/config.yaml` and no `--config`
- **THEN** the YAML layer is read from `/app/config/config.yaml`

#### Scenario: Missing file named by the environment
- **WHEN** `SCAVENGARR_CONFIG` names a file that does not exist
- **THEN** the log has `config_file_not_found` with the path
- **AND** the server starts with the defaults, the environment and the CLI arguments

#### Scenario: Missing file named on the command line
- **WHEN** the server starts with `--config ./missing.yaml` and the file does not exist
- **THEN** the start fails with `FileNotFoundError`

### Requirement: Dotenv File
`--dotenv <file>` SHALL load the file's variables into the environment before the environment layer is read, without replacing a variable that is already set. A dotenv file that does not exist SHALL stop the start with `FileNotFoundError`.

#### Scenario: The dotenv file provides a value
- **WHEN** the dotenv file contains `SCAVENGARR_PLUGIN_DIR=./plugins-from-dotenv`
- **AND** the server starts with `--dotenv` naming that file
- **THEN** the effective `plugin_dir` is `./plugins-from-dotenv`

#### Scenario: The real environment wins
- **WHEN** `SCAVENGARR_LOG_LEVEL=WARNING` is set in the environment
- **AND** the dotenv file contains `SCAVENGARR_LOG_LEVEL=DEBUG`
- **THEN** the effective `log_level` is `WARNING`

#### Scenario: Missing dotenv file
- **WHEN** the server starts with `--dotenv ./missing.env` and the file does not exist
- **THEN** the start fails with `FileNotFoundError`

### Requirement: YAML Layer
The YAML file SHALL hold the sections `plugins`, `http`, `playwright`, `logging`, `cache`, `stremio`, `scoring` and `telemetry` and the top-level keys, and it MAY use the flat names of the environment layer (`log_level`, `http_timeout_seconds`, `cache_dir`, …) as well.

#### Scenario: A flat name in the file
- **WHEN** the YAML file sets `log_level: DEBUG` at the top level
- **THEN** the effective `log_level` is `DEBUG`, as with `logging.level: DEBUG`

### Requirement: YAML Parsing
The YAML file SHALL be parsed with `yaml.safe_load`. An empty file SHALL count as a layer without settings, a document that is not a mapping SHALL fail with `ValueError`, and invalid syntax SHALL fail with the parser's `yaml.YAMLError`, which is not wrapped in an error of the project's own.

#### Scenario: Empty file
- **WHEN** the YAML file is empty
- **THEN** loading succeeds with the other layers

#### Scenario: Not a mapping
- **WHEN** the YAML file holds a list
- **THEN** loading fails with `ValueError`

#### Scenario: Invalid syntax
- **WHEN** the YAML file contains invalid syntax
- **THEN** loading fails with the `yaml.YAMLError` that `yaml.safe_load` raises, which names the position

### Requirement: Environment Variable Names
Every key of a section SHALL be read from `SCAVENGARR_<SECTION>_<KEY>` and every top-level key from `SCAVENGARR_<KEY>`, without regard to case. A value that starts with `{` or `[` SHALL be parsed as JSON, for the dict- and list-typed settings. The configuration SHALL NOT read variables without the `SCAVENGARR_` prefix, `CACHE_*` included; `HOST` and `PORT` are the CLI's bind address, not settings.

#### Scenario: A section key reaches its field
- **WHEN** `SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECONDS=20` is set
- **THEN** the effective `stremio.plugin_timeout_seconds` is 20.0

#### Scenario: A JSON value
- **WHEN** `SCAVENGARR_PLUGINS_OVERRIDES={"cineby": {"enabled": false}}` is set
- **THEN** the effective `plugins.overrides` disables the plugin `cineby`

#### Scenario: Unprefixed cache variables
- **WHEN** `CACHE_MAX_CONCURRENT=42` is set
- **THEN** the effective `cache.max_concurrent` keeps its default 10

### Requirement: Flat Environment Names
The flat names of `EnvOverrides` (`SCAVENGARR_LOG_LEVEL`, `SCAVENGARR_HTTP_TIMEOUT_SECONDS`, `SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND`, …) SHALL be read beside the sectioned names and SHALL win over the sectioned name of the same key. A flat name and a sectioned name set to different values SHALL be logged as `config_env_conflict` with the name used and the name ignored.

#### Scenario: The flat name wins
- **WHEN** `SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND=10` and `SCAVENGARR_HTTP_RATE_LIMIT_RPS=3` are set
- **THEN** the effective `rate_limit_requests_per_second` is 10.0
- **AND** the startup log has `config_env_conflict` with `used=SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND` and `ignored=SCAVENGARR_HTTP_RATE_LIMIT_RPS`

#### Scenario: Agreeing names
- **WHEN** `SCAVENGARR_LOG_LEVEL=DEBUG` and `SCAVENGARR_LOGGING_LEVEL=DEBUG` are set
- **THEN** the effective `log_level` is `DEBUG` and no conflict is logged

### Requirement: Ignored Keys Are Reported
Loading SHALL ignore a YAML key that no setting accepts and a `SCAVENGARR_<SECTION>_<KEY>` variable whose section has no such key, and the startup SHALL log them: `config_unknown_keys` with the config file and the dotted YAML paths, `config_unknown_env` with the variable names. Other `SCAVENGARR_*` variables (plugin credentials, the build identity, `SCAVENGARR_CONFIG`) are not settings and SHALL NOT be reported.

#### Scenario: A misspelled YAML key
- **WHEN** the YAML file sets `http.timeout_secs: 1`
- **THEN** `http_timeout_seconds` keeps its value from the other layers
- **AND** the startup log has `config_unknown_keys` with `http.timeout_secs`

#### Scenario: A removed setting
- **WHEN** an old YAML file still sets `stremio.probe_at_stream_time: true`
- **THEN** the file loads
- **AND** `config_unknown_keys` names `stremio.probe_at_stream_time`

#### Scenario: A misspelled variable
- **WHEN** `SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND=20` and `SCAVENGARR_BOERSE_USERNAME=user` are set
- **THEN** `stremio.plugin_timeout_seconds` keeps its default 30.0
- **AND** `config_unknown_env` names `SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECOND` only

### Requirement: Strict Validation
The merged layers SHALL be validated by the pydantic model, and an invalid value from any layer SHALL stop the start with a validation error that names the setting.

#### Scenario: Timeouts must be positive
- **WHEN** `http.timeout_seconds`, `http.timeout_resolve_seconds` or `playwright.timeout_ms` is 0 or less, from any layer
- **THEN** loading fails with a validation error for that setting

#### Scenario: Log level must be one of the allowed values
- **WHEN** `logging.level` is `VERBOSE`
- **THEN** loading fails with a validation error that lists `DEBUG`, `INFO`, `WARNING` and `ERROR`

#### Scenario: Cache lifetimes
- **WHEN** `cache.ttl_seconds` is negative or `cache.crawljob_ttl_seconds` is 0
- **THEN** loading fails with a validation error for that setting

#### Scenario: Answer budget of at least a second
- **WHEN** `stremio.answer_budget_seconds` is 0.5
- **THEN** loading fails with a validation error for `answer_budget_seconds`

#### Scenario: Tracing endpoint
- **WHEN** `telemetry.tracing_endpoint` is `ftp://collector:4318`
- **THEN** loading fails, since the endpoint must start with `http://` or `https://`
- **AND** an empty value turns tracing off instead of failing

### Requirement: Log Format Follows the Environment
When no layer sets `logging.format`, it SHALL be `json` for `environment: prod` and `console` for `dev` and `test`.

#### Scenario: Production
- **WHEN** `SCAVENGARR_ENVIRONMENT=prod` is set and no log format is given
- **THEN** the effective `log_format` is `json`

#### Scenario: Development
- **WHEN** `environment` is `dev` and no log format is given
- **THEN** the effective `log_format` is `console`

#### Scenario: An explicit format wins
- **WHEN** `environment` is `prod` and the server starts with `--log-format console`
- **THEN** the effective `log_format` is `console`

### Requirement: The Effective Configuration Is Logged
The startup SHALL log `config_effective` with the config file, every value that differs from its default under its dotted path, and in `sources` the layer that set each one (`defaults`, `yaml`, `env` or `cli`).

#### Scenario: Nothing changed
- **WHEN** the server runs with the defaults only
- **THEN** `config_effective` names no setting

#### Scenario: A changed value and its layer
- **WHEN** the YAML file sets `http.timeout_seconds: 15` and `SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECONDS=20` is set
- **THEN** `config_effective` has `http_timeout_seconds=15.0` and `stremio.plugin_timeout_seconds=20.0`
- **AND** `sources` names `yaml` for the first and `env` for the second

### Requirement: Secrets Are Masked in the Startup Log
In `config_effective`, a setting whose name contains `password`, `token`, `key` or `secret`, or whose type is `SecretStr`, SHALL show as `***`, a dict-typed setting as its keys, and a path as a string. The log processors SHALL mask a password inside a URL value (`redis://:***@host`) and secret query parameters in every log field.

#### Scenario: A secret
- **WHEN** `tmdb_api_key` is set
- **THEN** `config_effective` shows `tmdb_api_key="***"`

#### Scenario: A password in a URL
- **WHEN** `cache.redis_url` is `redis://:<password>@redis:6379/0`
- **THEN** the startup log shows `redis://:***@redis:6379/0`

#### Scenario: Per-plugin overrides
- **WHEN** the YAML file sets `plugins.overrides.cineby.enabled: false`
- **THEN** `config_effective` shows `plugins.overrides=["cineby"]`

### Requirement: One Load Without Side Effects
The CLI's `start()` SHALL call `load_config()` once and hand the result to the logging setup and the app. Loading SHALL NOT create files or directories and SHALL NOT start network activity.

#### Scenario: The CLI loads the configuration once
- **WHEN** the server starts
- **THEN** `load_config()` runs once
- **AND** the plugin registry, the engines and the API get that configuration object

#### Scenario: A configured directory is not created
- **WHEN** `cache.dir` names a directory that does not exist
- **THEN** loading succeeds and does not create it

### Requirement: Server Bind Address
`--host` and `--port` SHALL win over the `HOST` and `PORT` variables, which default to `0.0.0.0` and `7979` and are read after the dotenv file is loaded.

#### Scenario: Defaults
- **WHEN** neither the CLI nor the environment names a host or a port
- **THEN** the server listens on `0.0.0.0:7979`

#### Scenario: The port from the dotenv file
- **WHEN** the dotenv file contains `PORT=7981` and no `--port` is given
- **THEN** the server listens on port 7981

### Requirement: Every Setting Is Read and Documented
Every setting of the model SHALL be read in `src/` outside the config package or declare itself unused in its description (`tests/unit/infrastructure/test_config_settings_read.py`). `docs/reference/configuration.md` SHALL list every setting with its environment variable and every flat name, generated by `scripts/generate_config_doc.py` (`tests/unit/infrastructure/test_config_doc.py`).

#### Scenario: A setting nobody reads
- **WHEN** a setting is added to the model and nothing outside the config package reads it
- **THEN** `test_config_settings_read.py` fails and names it, unless its description declares it unused

#### Scenario: A setting without its row
- **WHEN** a setting is added and the reference is not regenerated
- **THEN** `test_config_doc.py` fails until `scripts/generate_config_doc.py` has rewritten the tables
