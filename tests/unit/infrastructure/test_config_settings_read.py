"""Every setting of the config model is read somewhere in ``src/`` outside the
config package, or its description says it is unused (step 51).

A setting nobody reads misleads the operator: ``playwright.timeout_ms`` and
``stremio.probe_stealth_timeout_seconds`` were loaded, validated and
documented for months without an effect. The declaration goes into the
field's ``description`` (the generated env table shows it), so the schema
says what the code does not.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel
from pydantic.fields import FieldInfo

from scavengarr.infrastructure.config.schema import AppConfig

_SRC = Path(__file__).resolve().parents[3] / "src" / "scavengarr"
_CONFIG_PACKAGE = _SRC / "infrastructure" / "config"

# Kept so existing configs stay valid; removal candidates
_DECLARED_UNUSED = (
    "cache_dir",
    "cache_ttl_seconds",
    "stremio.max_items_per_plugin",
    "stremio.max_items_total",
    "stremio.preferred_language",
    "stremio.stremio_deadline_ms",
)
_WIRED_IN_STEP_51 = ("playwright_timeout_ms", "stremio.probe_stealth_timeout_seconds")


def _leaves(
    model: type[BaseModel], prefix: str = ""
) -> list[tuple[str, str, FieldInfo]]:
    """``(dotted name, attribute name, field)`` of every setting, sections
    descended; a dict-typed field is one setting."""
    out: list[tuple[str, str, FieldInfo]] = []
    for name, field in model.model_fields.items():
        annotation = field.annotation
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            out.extend(_leaves(annotation, f"{prefix}{name}."))
        else:
            out.append((f"{prefix}{name}", name, field))
    return out


def _sources() -> list[str]:
    return [
        path.read_text(encoding="utf-8")
        for path in _SRC.rglob("*.py")
        if _CONFIG_PACKAGE not in path.parents
    ]


def _is_read(attribute: str, sources: list[str]) -> bool:
    """Whether a module reads the setting as an attribute (``.name``)."""
    pattern = re.compile(rf"\.{re.escape(attribute)}\b")
    return any(pattern.search(text) for text in sources)


def _declared_unused(field: FieldInfo) -> bool:
    return "unused" in (field.description or "").lower()


def test_every_setting_is_read_or_declared_unused() -> None:
    sources = _sources()

    unread = [
        dotted
        for dotted, attribute, field in _leaves(AppConfig)
        if not _is_read(attribute, sources) and not _declared_unused(field)
    ]

    assert unread == [], (
        "settings loaded but read nowhere in src/ outside the config package:"
        f" {unread}; wire each where its name says, or declare it unused in its"
        " description (and in docs/features/configuration.md)"
    )


def test_the_declared_unused_settings_are_known() -> None:
    """A new declaration is a conscious decision, not a way past the guard."""
    declared = sorted(
        dotted for dotted, _, field in _leaves(AppConfig) if _declared_unused(field)
    )

    assert declared == sorted(_DECLARED_UNUSED)


def test_the_settings_of_step_51_are_read() -> None:
    sources = _sources()
    by_name = {dotted: (attr, field) for dotted, attr, field in _leaves(AppConfig)}

    for dotted in _WIRED_IN_STEP_51:
        attribute, field = by_name[dotted]
        assert not _declared_unused(field), dotted
        assert _is_read(attribute, sources), dotted
