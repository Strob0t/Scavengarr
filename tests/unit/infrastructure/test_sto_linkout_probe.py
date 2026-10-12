"""Tests for scripts/probes/sto_linkout.py: what a page without a redirect is."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_PROBE = Path(__file__).resolve().parents[3] / "scripts/probes/sto_linkout.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sto_linkout", _PROBE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


probe = _load()

# The page the site answers a link-out with outside its player (2026-10-07)
_IFRAME_ONLY = (
    "<html><head></head><body><script>"
    "if (window.top === window.self) { document.body.innerHTML = 'x'; }"
    "</script></body></html>"
)


def test_gates_finds_turnstile_and_challenges() -> None:
    page = '<div class="cf-turnstile"></div><title>Just a moment...</title>'
    assert probe.gates(page) == ["turnstile", "cloudflare"]
    assert probe.gates(_IFRAME_ONLY) == []


def test_summary_names_title_hosts_and_words_without_paths() -> None:
    page = (
        "<title>Weiterleitung</title>"
        '<script src="https://cdn.example/t/abc?token=secret"></script>'
        '<iframe src="/r?t=secret"></iframe>'
    )
    text = probe.summary(page)
    assert text == (
        "title 'Weiterleitung' | loads (relative), cdn.example | words iframe"
    )
    assert "secret" not in text
    assert probe.summary(_IFRAME_ONLY) == "title '–' | loads nothing | words window.top"
