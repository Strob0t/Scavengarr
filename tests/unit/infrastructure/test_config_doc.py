"""The environment table of docs/features/configuration.md is generated from
the schema and must stay current."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
_DOC = _ROOT / "docs" / "features" / "configuration.md"


def _generator() -> ModuleType:
    path = _ROOT / "scripts" / "generate_config_doc.py"
    spec = importlib.util.spec_from_file_location("generate_config_doc", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_env_table_is_current() -> None:
    generator = _generator()
    doc = _DOC.read_text(encoding="utf-8")
    assert generator.update(doc) == doc, (
        "the environment table in docs/features/configuration.md is outdated: "
        "run `poetry run python scripts/generate_config_doc.py`"
    )


def test_every_setting_has_a_row() -> None:
    table = _generator().render()
    rows = [line for line in table.splitlines() if line.startswith("| `SCAVENGARR_")]
    names = [row.split("`")[1] for row in rows]

    assert len(names) == len(set(names))
    assert "SCAVENGARR_STREMIO_PLUGIN_TIMEOUT_SECONDS" in names
    assert "SCAVENGARR_VALIDATION_MAX_CONCURRENT" in names
    assert "SCAVENGARR_LOGGING_LEVEL" in names
    # The section and top-level keys the model has; four fewer since the
    # unread stremio settings went (2026-10-10)
    assert len(names) == 76


def test_flat_aliases_are_named() -> None:
    table = _generator().render()
    row = next(
        line
        for line in table.splitlines()
        if line.startswith("| `SCAVENGARR_HTTP_RATE_LIMIT_RPS`")
    )
    assert "`SCAVENGARR_RATE_LIMIT_REQUESTS_PER_SECOND`" in row
