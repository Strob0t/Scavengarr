"""Tests for scripts/probes/capture_state.py (the kept capture states and
clearance cookies: names, lengths and times, never a value)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_PROBES = Path(__file__).resolve().parents[3] / "scripts" / "probes"


def _load() -> ModuleType:
    # By path: scripts/probes/ is no package
    spec = importlib.util.spec_from_file_location(
        "capture_state_probe", _PROBES / "capture_state.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_mod = _load()

_NOW = 1_760_000_000.0  # 2025-10-09 08:53:20 UTC
_SECRETS = ("secret-1", "secret-2", "secret-3", "secret-4", "secret-5", "secret-6")
_CODES = ("Fq3ZmK8pXw2RtB", "k3j9x0q2w8zt")  # made-up file codes

_STATES: dict[str, Any] = {
    "bysezejataos.com": {
        "saved_at": _NOW - 2 * 3600 - 18 * 60,
        "cookies": [
            {
                "name": "byse_device_id",
                "value": "secret-1",
                "domain": "bysezejataos.com",
                "path": "/",
                "expires": _NOW + 364 * 86400,
            },
            {
                "name": "byse_session",
                "value": "secret-2",
                "domain": ".bysezejataos.com",
                "path": "/api",
                "expires": -1,
            },
            {
                "name": "byse_pass",
                "value": "secret-3",
                "domain": "bysezejataos.com",
                "path": "/e/k3j9x0q2w8zt",
                "expires": _NOW - 600,
            },
        ],
        "origins": [
            {
                "origin": "https://bysezejataos.com",
                "localStorage": [
                    {"name": "volume", "value": "secret-4"},
                    {
                        "name": "byse.jw8.subtitle.settings.n1mwq.org.Fq3ZmK8pXw2RtB",
                        "value": "",
                    },
                    {"name": "captcha_token", "value": "secret-5" * 4},
                ],
                "indexedDB": [{"name": "player-cache", "version": 1, "stores": []}],
            }
        ],
    },
    "n1mwq.org": {"saved_at": _NOW - 60, "cookies": [], "origins": []},
}
_CLEARANCE: list[dict[str, Any]] = [
    {
        "name": "cf_clearance",
        "value": "secret-6",
        "domain": ".kinoger.com",
        "path": "/",
        "expires": _NOW + 18 * 60,
    }
]


def _lines() -> list[str]:
    return _mod.report_lines(json.dumps(_STATES), json.dumps(_CLEARANCE), now=_NOW)


class TestReport:
    def test_each_host_with_its_age_cookies_and_origins(self) -> None:
        assert _lines() == [
            "capture states (browser:capture_state:v1): 2 hosts",
            "host bysezejataos.com: kept 06:35:20 UTC, 2 h 18 min ago",
            "- cookie byse_device_id | domain bysezejataos.com | path / | 364 d left",
            "- cookie byse_session | domain .bysezejataos.com | path /api | session",
            "- cookie byse_pass | domain bysezejataos.com | path /e/…"
            " | expired 10 min ago",
            "- origin bysezejataos.com: localStorage volume (8 chars),"
            " byse.jw8.subtitle.settings.n1mwq.org.… (0 chars),"
            " captcha_token (32 chars); IndexedDB player-cache",
            "host n1mwq.org: kept 08:52:20 UTC, 1 min ago",
            "- nothing kept",
            "clearance cookies (browser:clearance_cookies): 1",
            "- cookie cf_clearance | domain .kinoger.com | path / | 18 min left",
        ]

    def test_no_value_reaches_the_output(self) -> None:
        text = "\n".join(_lines())

        assert not any(secret in text for secret in _SECRETS)

    def test_no_file_code_reaches_the_output(self) -> None:
        text = "\n".join(_lines())

        assert not any(code in text for code in _CODES)


class TestMask:
    def test_a_file_code_segment_is_masked(self) -> None:
        assert _mod.mask("/e/k3j9x0q2w8zt") == "/e/…"
        assert _mod.mask("vcap18510794") == "…"

    def test_setting_names_stay_readable(self) -> None:
        for name in (
            "i18nextLng",
            "byse:rendering-diagnostics-options",
            "byse.languagePreference.v1",
            "byse_device_id",
            "/",
        ):
            assert _mod.mask(name) == name

    def test_missing_keys(self) -> None:
        assert _mod.report_lines(None, None, now=_NOW) == [
            "capture states (browser:capture_state:v1): none",
            "clearance cookies (browser:clearance_cookies): none",
        ]

    def test_a_corrupt_key_is_named_not_changed(self) -> None:
        assert _mod.report_lines("{not json", "[1, 2", now=_NOW) == [
            "capture states (browser:capture_state:v1): corrupt",
            "clearance cookies (browser:clearance_cookies): corrupt",
        ]


def _item_line(value: str) -> str:
    states = {
        "n1mwq.org": {
            "saved_at": _NOW - 60,
            "cookies": [],
            "origins": [
                {
                    "origin": "https://n1mwq.org",
                    "localStorage": [
                        {"name": "byse:captcha-clearance:embed", "value": value}
                    ],
                }
            ],
        }
    }
    return _mod.report_lines(json.dumps(states), None, now=_NOW)[2]


class TestTimes:
    def test_a_json_items_epoch_times_show_from_now(self) -> None:
        # Byse's captcha pass: a token and its expiry in epoch milliseconds
        value = json.dumps({"token": "secret-7", "expiresAt": (_NOW + 23 * 60) * 1000})

        assert _item_line(value) == (
            "- origin n1mwq.org: localStorage byse:captcha-clearance:embed"
            f" ({len(value)} chars, expiresAt in 23 min)"
        )

    def test_a_past_time_in_seconds(self) -> None:
        value = json.dumps({"issuedAt": _NOW - 90, "count": 3})

        assert _item_line(value).endswith(f"({len(value)} chars, issuedAt 1 min ago)")

    def test_no_other_field_or_value_shows(self) -> None:
        # An id-sized number, a string with digits, a nested time, a flag
        value = json.dumps(
            {
                "user": 123456789012345,
                "token": "secret-8",
                "inner": {"expiresAt": _NOW * 1000},
                "flag": True,
            }
        )

        line = _item_line(value)

        assert line.endswith(f"({len(value)} chars)")
        assert "123456789012345" not in line and "secret-8" not in line

    def test_a_value_that_is_no_json_object_shows_its_length(self) -> None:
        for value in ("not json", "[1760000000]", "1760000000"):
            assert _item_line(value).endswith(f"({len(value)} chars)")


class TestLifetime:
    def test_the_spans(self) -> None:
        assert _mod.span(45) == "45 s"
        assert _mod.span(18 * 60) == "18 min"
        assert _mod.span(2 * 3600 + 18 * 60) == "2 h 18 min"
        assert _mod.span(364 * 86400) == "364 d"
