"""The live smoke's reading of a plugin run that found nothing.

The base's ``_fetch_text()`` and ``_safe_fetch()`` turn a timeout, a transport
error and an error answer into ``None`` with the line ``<plugin>_timeout``,
``_fetch_error`` or ``_http_error``, and the plugin answers ``[]``: the smoke
skips such a run as a slow or failing site instead of failing it as a broken
parser. The module is loaded from its file, so the live package (and its
browser imports) is not.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_MODULE = Path(__file__).resolve().parents[2] / "live" / "smoke_outcome.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_outcome", _MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


failed_fetch = _load().failed_fetch


def test_a_run_without_a_failed_fetch_keeps_the_assertion() -> None:
    events = [{"event": "jjs_search_page", "results": 0, "log_level": "info"}]

    assert failed_fetch(events, "jjs") is None


@pytest.mark.parametrize(
    ("event", "reason"),
    [
        (
            {"event": "jjs_timeout", "context": "search_page_1"},
            "jjs_timeout (context=search_page_1)",
        ),
        (
            {"event": "kinox_http_error", "context": "search", "status": 503},
            "kinox_http_error (context=search, status=503)",
        ),
        ({"event": "kinox_fetch_error"}, "kinox_fetch_error"),
    ],
)
def test_a_failed_fetch_names_its_line(event: dict[str, object], reason: str) -> None:
    plugin = str(event["event"]).split("_", 1)[0]

    assert failed_fetch([event], plugin) == reason


def test_another_plugins_line_does_not_count() -> None:
    events = [{"event": "kinox_timeout", "context": "search"}]

    assert failed_fetch(events, "jjs") is None


def test_the_first_failed_fetch_is_named() -> None:
    events = [
        {"event": "jjs_search_page", "results": 3},
        {"event": "jjs_http_error", "context": "detail", "status": 502},
        {"event": "jjs_timeout", "context": "detail"},
    ]

    assert failed_fetch(events, "jjs") == "jjs_http_error (context=detail, status=502)"
