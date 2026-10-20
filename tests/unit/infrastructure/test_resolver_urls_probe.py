"""Tests for scripts/probes/resolver_urls.py (the live resolver tests' links):
``--avoid`` keeps each resolver's next link."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

from scavengarr.domain.plugins.base import SearchResult

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"


def _load() -> ModuleType:
    # By path: scripts/probes/ is no package
    spec = importlib.util.spec_from_file_location(
        "resolver_urls_probe", _PROBES / "resolver_urls.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()

_FIRST = "https://veev.to/e/first"
_SECOND = "https://veev.to/e/second"


def _registry() -> MagicMock:
    registry = MagicMock()
    registry.canonical_hoster.side_effect = lambda host: (
        "veev" if host.startswith("veev") else None
    )
    return registry


def _result(*links: str) -> SearchResult:
    return SearchResult(
        title="Iron Man",
        download_link=links[0],
        download_links=[{"link": link} for link in links[1:]],
    )


class TestAvoid:
    def test_without_avoid_the_first_link_is_kept(self) -> None:
        found = _mod.Found()
        found.take(_registry(), "moflix", _result(_FIRST, _SECOND))

        assert found.urls == {"veev": _FIRST}

    def test_an_avoided_link_gives_way_to_the_next(self) -> None:
        found = _mod.Found(avoid={_FIRST})
        found.take(_registry(), "moflix", _result(_FIRST))
        found.take(_registry(), "filmpalast", _result(_SECOND))

        assert found.urls == {"veev": _SECOND}
        assert found.plugin == {"veev": "filmpalast"}
        assert found.links["veev"] == 2  # the avoided link still counts

    def test_avoided_links_come_from_an_earlier_output(self, tmp_path: Path) -> None:
        earlier = tmp_path / "resolver-urls.json"
        earlier.write_text(json.dumps({"veev": _FIRST, "voe": "https://voe.sx/e/1"}))

        assert _mod.read_avoid(earlier) == {_FIRST, "https://voe.sx/e/1"}
        assert _mod.read_avoid(None) == set()
