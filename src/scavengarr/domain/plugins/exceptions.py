"""Plugin system exceptions."""

from __future__ import annotations


class PluginError(Exception):
    """Base class for all plugin-related errors."""


class PluginValidationError(PluginError):
    """Raised when a YAML plugin fails schema validation."""


class PluginLoadError(PluginError):
    """Raised when a Python plugin fails to import or does not match the protocol."""


class PluginNotFoundError(PluginError):
    """Raised when a plugin name is not known to the registry."""


class DuplicatePluginError(PluginError):
    """Raised when two plugins resolve to the same name."""
