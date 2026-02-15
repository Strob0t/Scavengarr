"""Hardcoded default configuration values."""

from __future__ import annotations

from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    "app_name": "scavengarr",
    "environment": "dev",
    "plugins": {
        "plugin_dir": "./plugins",
    },
    "http": {
        "timeout_seconds": 30.0,
        "follow_redirects": True,
        "user_agent": "Scavengarr/0.1.0",
    },
    "playwright": {
        "headless": True,
        "timeout_ms": 30_000,
    },
    "logging": {
        "level": "INFO",
        "format": None,  # Derived from environment in schema.py
    },
    "cache": {
        "dir": "./.cache/scavengarr",
        "backend": "diskcache",
        "ttl_seconds": 3600,
    },
    "scoring": {
        "enabled": True,
        "health_halflife_days": 2.0,
        "search_halflife_weeks": 2.0,
        "health_interval_hours": 24.0,
        "search_runs_per_week": 2,
        "w_health": 0.4,
        "w_search": 0.6,
    },
    "stremio": {
        "preferred_language": "de",
        "max_concurrent_plugins": 5,
        "plugin_timeout_seconds": 30.0,
        "title_match_threshold": 0.7,
    },
}
