"""Configuration loading with layered precedence (defaults < YAML < ENV < CLI)."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import AliasChoices, AliasPath, BaseModel, SecretStr, ValidationError

from .defaults import DEFAULT_CONFIG
from .schema import AppConfig, ConfigSource, EnvOverrides

_SECTION_KEYS: set[str] = {
    "plugins",
    "http",
    "playwright",
    "logging",
    "cache",
    "stremio",
    "scoring",
    "telemetry",
}

# Top-level scalar keys passed through as they are
_TOP_LEVEL_KEYS: set[str] = {
    "app_name",
    "environment",
    "tmdb_api_key",
    "validate_download_links",
    "validation_timeout_seconds",
    "validation_max_concurrent",
}

# Flat keys (env/CLI style, YAML too) and the sectioned key each one sets
_FLAT_KEYS: dict[str, tuple[str, str]] = {
    "plugin_dir": ("plugins", "plugin_dir"),
    "http_timeout_seconds": ("http", "timeout_seconds"),
    "http_timeout_resolve_seconds": ("http", "timeout_resolve_seconds"),
    "http_follow_redirects": ("http", "follow_redirects"),
    "http_user_agent": ("http", "user_agent"),
    "http_http2": ("http", "http2"),
    "rate_limit_requests_per_second": ("http", "rate_limit_rps"),
    "rate_limit_adaptive": ("http", "rate_limit_adaptive"),
    "rate_limit_min_rps": ("http", "rate_limit_min_rps"),
    "rate_limit_max_rps": ("http", "rate_limit_max_rps"),
    "http_retry_max_attempts": ("http", "retry_max_attempts"),
    "http_retry_backoff_base": ("http", "retry_backoff_base"),
    "http_retry_max_backoff": ("http", "retry_max_backoff"),
    "api_rate_limit_rpm": ("http", "api_rate_limit_rpm"),
    "playwright_headless": ("playwright", "headless"),
    "playwright_browser_fallback": ("playwright", "browser_fallback"),
    "playwright_solver_url": ("playwright", "solver_url"),
    "playwright_timeout_ms": ("playwright", "timeout_ms"),
    "log_level": ("logging", "level"),
    "log_format": ("logging", "format"),
    "cache_dir": ("cache", "dir"),
    "cache_ttl_seconds": ("cache", "ttl_seconds"),
    "cache_backend": ("cache", "backend"),
    "cache_redis_url": ("cache", "redis_url"),
    "cache_max_concurrent": ("cache", "max_concurrent"),
    "telemetry_tracing_endpoint": ("telemetry", "tracing_endpoint"),
}

_ENV_PREFIX = "SCAVENGARR_"

# Field names whose values the startup log masks
_SECRET_WORDS = ("password", "token", "key", "secret")


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """
    Recursively merge `override` into `base` and return `base`.

    Rules:
    - dict + dict => deep merge
    - otherwise => override wins
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, Mapping):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _normalize_layer(data: Mapping[str, Any]) -> dict[str, Any]:
    """
    Normalize a layer (defaults/YAML/ENV/CLI) into the canonical *sectioned* shape.

    Canonical top-level keys:
    - app_name, environment
    - plugins.plugin_dir
    - http.timeout_seconds, http.follow_redirects, http.user_agent
    - playwright.headless, playwright.browser_fallback, playwright.solver_url,
      playwright.timeout_ms
    - logging.level, logging.format
    - cache.dir, cache.ttl_seconds
    """
    out: dict[str, Any] = {}

    for section in _SECTION_KEYS:
        if section in data and isinstance(data[section], Mapping):
            out[section] = dict(data[section])

    for key in _TOP_LEVEL_KEYS:
        if key in data:
            out[key] = data[key]

    for flat_key, (section, section_key) in _FLAT_KEYS.items():
        if flat_key in data:
            out.setdefault(section, {})
            out[section][section_key] = data[flat_key]

    return out


# The keys a layer may hold: a section's or nested model's own keys, ``None``
# for a value (a dict-typed field takes any keys)
type _Keys = dict[str, _Keys | None]


def _model_keys(model: type[BaseModel]) -> _Keys:
    """The input keys of a model (an alias replaces the field name)."""
    keys: _Keys = {}
    for name, field in model.model_fields.items():
        nested = field.annotation
        if isinstance(nested, type) and issubclass(nested, BaseModel):
            keys[field.alias or name] = _model_keys(nested)
        else:
            keys[field.alias or name] = None
    return keys


def _accepted_keys() -> _Keys:
    """The keys ``_normalize_layer`` passes on and a field then accepts."""
    fields = _model_keys(AppConfig)
    sections: dict[str, _Keys] = {s: dict(fields.get(s) or {}) for s in _SECTION_KEYS}
    # http, playwright and logging have no model: flat AppConfig fields
    # read their keys through an AliasPath (cache.dir is read twice)
    for field in AppConfig.model_fields.values():
        if isinstance(field.validation_alias, AliasChoices):
            for choice in field.validation_alias.choices:
                if isinstance(choice, AliasPath):
                    section, key = choice.path
                    sections[str(section)][str(key)] = None
    return {**dict.fromkeys(_TOP_LEVEL_KEYS | _FLAT_KEYS.keys()), **sections}


_ACCEPTED_KEYS = _accepted_keys()


def _unknown_keys(data: Mapping[str, Any]) -> tuple[str, ...]:
    """Dotted paths of a YAML layer that no field accepts, sorted.

    Loading ignores them: ``_normalize_layer`` drops unknown top-level keys,
    pydantic unknown nested ones (the ``cache`` section rejects them).
    """
    return tuple(sorted(_unknown_paths(data, _ACCEPTED_KEYS, "")))


def _unknown_paths(
    data: Mapping[str, Any], accepted: _Keys, prefix: str
) -> Iterator[str]:
    for key, value in data.items():
        path = f"{prefix}{key}"
        if key not in accepted:
            yield path
        elif (keys := accepted[key]) is not None and isinstance(value, Mapping):
            yield from _unknown_paths(value, keys, f"{path}.")


def changed_values(config: AppConfig) -> dict[str, Any]:
    """Dotted paths and values of the fields that differ from the defaults.

    For the startup log: a secret shows as ``***``, a dict-typed field
    (``plugins.overrides``) as its keys.
    """
    return dict(_changes(config, AppConfig(), ""))


def _changes(
    model: BaseModel, default: BaseModel, prefix: str
) -> Iterator[tuple[str, Any]]:
    for name in type(model).model_fields:
        value, base = getattr(model, name), getattr(default, name)
        if isinstance(value, BaseModel) and isinstance(base, BaseModel):
            yield from _changes(value, base, f"{prefix}{name}.")
        elif value != base:
            yield f"{prefix}{name}", _shown(name, value)


def _shown(name: str, value: Any) -> Any:
    if isinstance(value, SecretStr) or any(word in name for word in _SECRET_WORDS):
        return "***"
    if isinstance(value, dict):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    return value


def _env_value(raw: str) -> Any:
    """A variable's value for the model: JSON for a dict or list (``{...}``,
    ``[...]``), else the string, which the field's type parses."""
    if raw.lstrip().startswith(("{", "[")):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _env_layer(
    environ: Mapping[str, str],
) -> tuple[dict[str, Any], tuple[str, ...], tuple[tuple[str, str], ...]]:
    """The environment's layer, its unknown names and its alias conflicts.

    Every key of a section reads ``SCAVENGARR_<SECTION>_<KEY>``, every
    top-level key ``SCAVENGARR_<KEY>`` (case-insensitive). The explicit flat
    names of ``EnvOverrides`` go on top: where a flat alias and the sectioned
    name of one key disagree, the alias wins and the pair is returned. A name
    with a section's prefix but no key of that section is returned as
    unknown; other ``SCAVENGARR_*`` names (plugin credentials, the build
    identity, ``SCAVENGARR_CONFIG``) are not settings and stay unreported.
    """
    env = {
        name.upper(): value
        for name, value in environ.items()
        if name.upper().startswith(_ENV_PREFIX)
    }
    sectioned: dict[str, Any] = {}
    unknown: list[str] = []
    for name, raw in sorted(env.items()):
        rest = name.removeprefix(_ENV_PREFIX).lower()
        if rest in _TOP_LEVEL_KEYS:
            sectioned[rest] = _env_value(raw)
            continue
        section = next((s for s in _SECTION_KEYS if rest.startswith(f"{s}_")), None)
        if section is None:
            continue
        key, keys = rest.removeprefix(f"{section}_"), _ACCEPTED_KEYS[section]
        if keys is not None and key in keys and keys[key] is None:
            sectioned.setdefault(section, {})[key] = _env_value(raw)
        else:
            unknown.append(name)

    conflicts: list[tuple[str, str]] = []
    for flat, (section, key) in _FLAT_KEYS.items():
        alias, name = (
            f"{_ENV_PREFIX}{flat}".upper(),
            f"{_ENV_PREFIX}{section}_{key}".upper(),
        )
        if alias != name and alias in env and name in env:
            if env[alias].strip() != env[name].strip():
                conflicts.append((alias, name))

    layer = _deep_merge(
        _normalize_layer(sectioned),
        _normalize_layer(EnvOverrides().to_update_dict()),
    )
    return layer, tuple(unknown), tuple(conflicts)


def _validated(data: dict[str, Any]) -> AppConfig | None:
    """A layer stage as a config, or ``None`` when it is not valid on its own
    (a later layer completes it; its values then count for that layer)."""
    try:
        return AppConfig.model_validate(data)
    except ValidationError:
        return None


def _value_sources(
    stages: list[tuple[str, dict[str, Any]]], config: AppConfig
) -> dict[str, str]:
    """The layer that set each value of *config* that differs from the defaults.

    *stages* holds the merged data after each layer, the last one being
    *config*'s; a value counts for the first stage that gave it its final value.
    """
    sources: dict[str, str] = {}
    previous = AppConfig()
    for index, (layer, data) in enumerate(stages):
        model = config if index == len(stages) - 1 else _validated(data)
        if model is None:
            continue
        for path, _ in _changes(model, previous, ""):
            sources[path] = layer
        previous = model
    return {path: sources[path] for path in changed_values(config) if path in sources}


def _read_yaml_config(config_path: Path) -> dict[str, Any]:
    raw = config_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(f"Config YAML must be a mapping, got: {type(parsed)!r}")
    return parsed


def load_config(
    *,
    config_path: Path | None = None,
    dotenv_path: Path | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> AppConfig:
    """
    Load configuration with strict precedence:
    defaults < YAML file < env vars < cli overrides

    The config records its file, the file's unknown keys, the layer of each
    changed value and the environment's unknown names and alias conflicts
    (``source``).
    This function MUST NOT create files or directories (no filesystem side-effects).
    """
    cli_overrides = cli_overrides or {}

    if dotenv_path is not None:
        if not dotenv_path.exists():
            raise FileNotFoundError(dotenv_path)
        load_dotenv(dotenv_path, override=False)

    base = _normalize_layer(deepcopy(DEFAULT_CONFIG))
    # The merged data after each layer, for the value sources
    stages: list[tuple[str, dict[str, Any]]] = [("defaults", deepcopy(base))]

    unknown_keys: tuple[str, ...] = ()
    if config_path is not None:
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        yaml_data = _read_yaml_config(config_path)
        unknown_keys = _unknown_keys(yaml_data)
        _deep_merge(base, _normalize_layer(yaml_data))
        stages.append(("yaml", deepcopy(base)))

    env_layer, unknown_env, env_conflicts = _env_layer(os.environ)
    if env_layer:
        _deep_merge(base, env_layer)
        stages.append(("env", deepcopy(base)))

    cli_layer = _normalize_layer(cli_overrides)
    if cli_layer:
        _deep_merge(base, cli_layer)
        stages.append(("cli", deepcopy(base)))

    config = AppConfig.model_validate(base)
    config._source = ConfigSource(
        file=config_path,
        unknown_keys=unknown_keys,
        value_sources=_value_sources(stages, config),
        unknown_env=unknown_env,
        env_conflicts=env_conflicts,
    )
    return config
