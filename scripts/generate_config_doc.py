"""Generate the environment table of docs/features/configuration.md from the schema.

Usage:
    poetry run python scripts/generate_config_doc.py           # rewrite the table
    poetry run python scripts/generate_config_doc.py --check   # exit 1 if outdated

Every setting reads ``SCAVENGARR_<SECTION>_<KEY>`` (a top-level one
``SCAVENGARR_<KEY>``), so the table lists every key of every section with its
type, default and description, generated between the markers
``<!-- config-env:start -->`` and ``<!-- config-env:end -->``.
``tests/unit/infrastructure/test_config_doc.py`` fails when the doc differs
from the output of :func:`update`.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
import typing
from pathlib import Path
from typing import Any

from pydantic import AliasChoices, AliasPath, BaseModel
from pydantic.fields import FieldInfo

from scavengarr.infrastructure.config.load import (
    _ACCEPTED_KEYS,
    _ENV_PREFIX,
    _FLAT_KEYS,
    _TOP_LEVEL_KEYS,
)
from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.infrastructure.version import APP_VERSION

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "features" / "configuration.md"
_START = "<!-- config-env:start -->"
_END = "<!-- config-env:end -->"

# The sections in the order of the YAML file and the docs
_SECTIONS = (
    "plugins",
    "http",
    "playwright",
    "logging",
    "cache",
    "stremio",
    "scoring",
    "telemetry",
)


def _section_field(section: str, key: str) -> FieldInfo:
    """The field a sectioned key sets: the section model's own field (its alias
    included), else the flat AppConfig field that reads it through an AliasPath."""
    section_field = AppConfig.model_fields.get(section)
    model = section_field.annotation if section_field else None
    if isinstance(model, type) and issubclass(model, BaseModel):
        for name, field in model.model_fields.items():
            if key in (name, field.alias):
                return field
    for field in AppConfig.model_fields.values():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices) and AliasPath(section, key) in alias.choices:
            return field
    raise KeyError(f"{section}.{key}")


def _type(annotation: Any) -> str:
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return ", ".join(f"`{value}`" for value in typing.get_args(annotation))
    if origin in (typing.Union, types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        return " or ".join(_type(a) for a in args) + " (optional)"
    if origin in (dict, list):
        return "JSON object" if origin is dict else "JSON list"
    if annotation is Path:
        return "path"
    return getattr(annotation, "__name__", str(annotation))


def _default(field: FieldInfo) -> str:
    value = field.get_default(call_default_factory=True)
    if value is None:
        return "(unset)"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, dict):
        return f"`{json.dumps(value, default=str)}`" if value else "`{}`"
    text = str(value).replace(APP_VERSION, "<version>")
    return f"`{text}`"


def _description(field: FieldInfo) -> str:
    text = " ".join((field.description or "").split())
    return text.replace("|", "\\|")


def _aliases() -> dict[tuple[str, str], str]:
    """The flat names of the sectioned keys, where they differ."""
    return {
        target: f"{_ENV_PREFIX}{flat.upper()}"
        for flat, target in _FLAT_KEYS.items()
        if flat != f"{target[0]}_{target[1]}"
    }


def _row(name: str, field: FieldInfo, alias: str | None) -> str:
    variable = f"`{name}`" + (f" (or `{alias}`)" if alias else "")
    return (
        f"| {variable} | {_type(field.annotation)} | {_default(field)} "
        f"| {_description(field)} |"
    )


def render() -> str:
    """The generated block: one table per section, the top-level keys first."""
    header = ["| Variable | Type | Default | Description |", "|---|---|---|---|"]
    lines = ["#### Top level", "", *header]
    for key in sorted(_TOP_LEVEL_KEYS):
        lines.append(
            _row(f"{_ENV_PREFIX}{key.upper()}", AppConfig.model_fields[key], None)
        )
    aliases = _aliases()
    for section in _SECTIONS:
        keys = _ACCEPTED_KEYS[section] or {}
        lines += ["", f"#### `{section}`", "", *header]
        for key in sorted(k for k, nested in keys.items() if nested is None):
            name = f"{_ENV_PREFIX}{section.upper()}_{key.upper()}"
            lines.append(
                _row(name, _section_field(section, key), aliases.get((section, key)))
            )
    return "\n".join(lines) + "\n"


def update(doc: str) -> str:
    """*doc* with the block between the markers replaced by :func:`render`."""
    before, rest = doc.split(_START, 1)
    _, after = rest.split(_END, 1)
    note = "<!-- Generated by scripts/generate_config_doc.py, do not edit by hand. -->"
    return f"{before}{_START}\n{note}\n\n{render()}\n{_END}{after}"


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--check", action="store_true", help="exit 1 if the table is outdated"
    )
    args = parser.parse_args()

    current = _DOC.read_text(encoding="utf-8")
    text = update(current)
    if args.check:
        if current != text:
            print(f"{_DOC.relative_to(_ROOT)} is outdated", file=sys.stderr)
            return 1
        return 0
    _DOC.write_text(text, encoding="utf-8")
    print(f"wrote {_DOC.relative_to(_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
