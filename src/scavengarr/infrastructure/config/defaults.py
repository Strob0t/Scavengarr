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
        "user_agent": "Scavengarr/0.1.0 (+https://github.com/Strob0t/Scavengarr)",
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
}
