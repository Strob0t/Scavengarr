from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping

import yaml
from dotenv import load_dotenv

from .defaults import DEFAULT_CONFIG
from .schema import AppConfig, EnvOverrides


_SECTION_KEYS: set[str] = {"plugins", "http", "playwright", "logging", "cache"}


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """
    Recursively merge `override` into `base` and return `base`.

    Rules:
    - dict + dict => deep merge
    - otherwise => override wins
    """
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, Mapping)
        ):
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
    - playwright.headless, playwright.timeout_ms
    - logging.level, logging.format
    - cache.dir, cache.ttl_seconds
    """
    out: dict[str, Any] = {}

    # Pass through already sectioned blocks
    for section in _SECTION_KEYS:
        if section in data and isinstance(data[section], Mapping):
            out[section] = dict(data[section])

    # General
    if "app_name" in data:
        out["app_name"] = data["app_name"]
    if "environment" in data:
        out["environment"] = data["environment"]

    # Flat -> section mappings
    flat_map: dict[str, tuple[str, str]] = {
        "plugin_dir": ("plugins", "plugin_dir"),
        "http_timeout_seconds": ("http", "timeout_seconds"),
        "http_follow_redirects": ("http", "follow_redirects"),
        "http_user_agent": ("http", "user_agent"),
        "playwright_headless": ("playwright", "headless"),
        "playwright_timeout_ms": ("playwright", "timeout_ms"),
        "log_level": ("logging", "level"),
        "log_format": ("logging", "format"),
        "cache_dir": ("cache", "dir"),
        "cache_ttl_seconds": ("cache", "ttl_seconds"),
    }

    for flat_key, (section, section_key) in flat_map.items():
        if flat_key in data:
            out.setdefault(section, {})
            out[section][section_key] = data[flat_key]

    return out


def _read_yaml_config(config_path: Path) -> dict[str, Any]:
    raw = config_path.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise ValueError(
            f"Config YAML must be a mapping, got: {type(parsed)!r}")
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

    This function MUST NOT create files or directories (no filesystem side-effects).
    """
    cli_overrides = cli_overrides or {}

    # Load .env first so it participates as "env vars" layer.
    if dotenv_path is not None:
        if not dotenv_path.exists():
            raise FileNotFoundError(dotenv_path)
        load_dotenv(dotenv_path, override=False)

    base = _normalize_layer(deepcopy(DEFAULT_CONFIG))

    if config_path is not None:
        if not config_path.exists():
            raise FileNotFoundError(config_path)
        yaml_layer = _normalize_layer(_read_yaml_config(config_path))
        _deep_merge(base, yaml_layer)

    env_layer_flat = EnvOverrides().to_update_dict()
    env_layer = _normalize_layer(env_layer_flat)
    _deep_merge(base, env_layer)

    cli_layer = _normalize_layer(cli_overrides)
    _deep_merge(base, cli_layer)

    # Validate final merged config (single source of truth).
    return AppConfig.model_validate(base)
