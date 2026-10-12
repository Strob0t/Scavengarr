"""The alert rules (`docker/prometheus-alerts.yml`) query what the app exports.

An alert on a renamed family never fires, and nothing says so.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from scavengarr.infrastructure.telemetry.collectors import BreakerCollector
from scavengarr.infrastructure.telemetry.metrics import Telemetry

_ROOT = Path(__file__).resolve().parents[3]

# The sample names of a family, by its type
_SUFFIXES = {
    "counter": ("_total",),
    "histogram": ("_bucket", "_sum", "_count"),
    "info": ("_info",),
}


def _rules() -> list[dict[str, Any]]:
    data = yaml.safe_load((_ROOT / "docker" / "prometheus-alerts.yml").read_text())
    return [rule for group in data["groups"] for rule in group["rules"]]


def _exported() -> set[str]:
    telemetry = Telemetry()
    # The composition root registers it with the app's breakers
    telemetry.registry.register(BreakerCollector({}))
    return {
        family.name + suffix
        for family in telemetry.registry.collect()
        for suffix in _SUFFIXES.get(family.type, ("",))
    }


def test_every_alert_waits_and_explains_itself() -> None:
    for rule in _rules():
        assert rule["alert"].startswith("Scavengarr"), rule
        assert rule["for"], rule["alert"]
        assert rule["labels"]["severity"] in ("critical", "warning"), rule["alert"]
        assert rule["annotations"]["summary"], rule["alert"]


def test_the_rules_query_exported_families() -> None:
    queried = {
        name
        for rule in _rules()
        for name in re.findall(r"\bscavengarr_\w+", rule["expr"])
    }

    assert len(queried) >= 5
    assert queried <= _exported()
