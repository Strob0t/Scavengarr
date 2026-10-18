"""The metric table of docs/reference/metrics.md is generated from the app's
registry and must stay current."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parents[3]
_DOC = _ROOT / "docs" / "reference" / "metrics.md"


def _generator() -> ModuleType:
    path = _ROOT / "scripts" / "generate_metrics_doc.py"
    spec = importlib.util.spec_from_file_location("generate_metrics_doc", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rows() -> dict[str, list[str]]:
    rows = [
        line for line in _generator().render().splitlines() if line.startswith("| `")
    ]
    return {
        row.split("`")[1]: [cell.strip() for cell in row.split("|")[2:-1]]
        for row in rows
    }


def test_metric_table_is_current() -> None:
    generator = _generator()
    doc = _DOC.read_text(encoding="utf-8")
    assert generator.update(doc) == doc, (
        "the metric table in docs/reference/metrics.md is outdated: "
        "run `PYTHONPATH=src poetry run python scripts/generate_metrics_doc.py`"
    )


def test_a_stage_has_a_duration_and_an_outcome_counter() -> None:
    rows = _rows()

    assert rows["scavengarr_plugin_search_seconds"] == [
        "histogram",
        "`plugin`",
        "1, 2, 4, 7, 10, 15, 30",
        "Plugin searches of Stremio requests: duration",
    ]
    assert rows["scavengarr_plugin_search_total"][:2] == [
        "counter",
        "`plugin`, `outcome`",
    ]


def test_the_scrape_time_collectors_are_listed() -> None:
    rows = _rows()

    assert rows["scavengarr_circuit_breaker_open"][:2] == [
        "gauge",
        "`breaker`, `name`, `state`",
    ]
    assert rows["scavengarr_browser_pages"][:2] == ["gauge", "`state`"]
    assert rows["scavengarr_container_cpu_seconds_total"][0] == "counter"
    assert rows["scavengarr_container_memory_bytes"][0] == "gauge"
    assert rows["scavengarr_build_info"][:2] == ["info", "`version`, `commit`, `built`"]


def test_prometheus_client_defaults_stay_out() -> None:
    assert all(name.startswith("scavengarr_") for name in _rows())
