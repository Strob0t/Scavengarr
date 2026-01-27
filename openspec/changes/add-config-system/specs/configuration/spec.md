## ADDED Requirements

### Requirement: Deterministic Config Precedence
The system SHALL load configuration using this precedence (highest to lowest):
1) CLI arguments
2) Environment variables
3) YAML config file
4) Built-in defaults

#### Scenario: Defaults only
- **WHEN** no CLI args, env vars, YAML config, or `.env` file are provided
- **THEN** the system uses built-in defaults for all config fields

#### Scenario: YAML overrides defaults
- **WHEN** a YAML config sets `plugin_dir: "./plugins-custom"`
- **THEN** the effective config uses `./plugins-custom`
- **AND** fields not present in YAML fall back to defaults

#### Scenario: Env overrides YAML
- **WHEN** YAML config sets `http_timeout_seconds: 30`
- **AND** `SCAVENGARR_HTTP_TIMEOUT_SECONDS=10` is set
- **THEN** the effective config uses `http_timeout_seconds=10`

#### Scenario: CLI overrides env
- **WHEN** `SCAVENGARR_LOG_LEVEL=INFO` is set
- **AND** CLI is executed with `--log-level DEBUG`
- **THEN** the effective config uses `log_level=DEBUG`

### Requirement: .env Support
The system SHALL support loading environment variables from a `.env` file.

#### Scenario: .env provides values
- **WHEN** `.env` contains `SCAVENGARR_PLUGIN_DIR=./plugins-from-dotenv`
- **AND** the CLI is executed with `--dotenv ./.env`
- **THEN** the effective config uses `plugin_dir=./plugins-from-dotenv`

#### Scenario: Missing .env file
- **WHEN** `--dotenv ./missing.env` is provided
- **THEN** the system fails with a clear error message indicating the file does not exist

### Requirement: Safe YAML Loading
The system SHALL parse YAML configuration using safe loading and SHALL reject invalid YAML.

#### Scenario: Invalid YAML rejected
- **WHEN** the YAML file contains invalid syntax
- **THEN** config loading fails with a `PluginLoadError`-equivalent config error (project-specific)
- **AND** the error message indicates the YAML parse problem and file path

### Requirement: Strict Validation
The system SHALL validate configuration values and reject invalid ones.

#### Scenario: Timeout must be positive
- **WHEN** `http_timeout_seconds` is set to `0` (via any source)
- **THEN** config loading fails with a validation error indicating it must be greater than zero

#### Scenario: Playwright timeout must be positive
- **WHEN** `playwright_timeout_ms` is set to `-1`
- **THEN** config loading fails with a validation error

#### Scenario: Log level must be valid
- **WHEN** `log_level` is set to `VERBOSE`
- **THEN** config loading fails with a validation error listing allowed values

### Requirement: Plugin Directory Configuration
The system SHALL allow configuring plugin discovery directory.

#### Scenario: Default plugin directory
- **WHEN** no overrides are provided
- **THEN** `plugin_dir` defaults to `./plugins`

#### Scenario: Environment overrides plugin directory
- **WHEN** `SCAVENGARR_PLUGIN_DIR=/data/plugins` is set
- **THEN** the effective config uses `/data/plugins`

### Requirement: Logging Configuration
The system SHALL expose config options controlling logging format and level.

#### Scenario: Console logs in dev by default
- **WHEN** `environment=dev` and no log format override is provided
- **THEN** `log_format` defaults to `console`

#### Scenario: JSON logs in prod by default
- **WHEN** `environment=prod` and no log format override is provided
- **THEN** `log_format` defaults to `json`

### Requirement: Redaction
The system SHALL never log secrets in plaintext.

#### Scenario: Redaction helper omits secrets
- **WHEN** a config contains secret-like fields (e.g., future `password`, `cookie_value`)
- **THEN** `redact_config_for_logging()` returns a structure where those fields are replaced with `***`

### Requirement: Single Load Per Process
The system SHALL load configuration exactly once per process and reuse it.

#### Scenario: CLI loads config once
- **WHEN** the CLI starts
- **THEN** it calls `load_config()` once
- **AND** it passes the resulting config object to subsequent components (plugin registry, engines, API)
