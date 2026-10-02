"""Tests for scripts/capture_pages.py (fixture storage, no live capture)."""

from __future__ import annotations

import gzip
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "capture_pages.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("capture_pages", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_mod = _load()


def test_scrub_removes_the_session_hash() -> None:
    html = "var dle_login_hash = '35f04e26480dea530964bb5dc435b912a9b1506f';"
    assert _mod.scrub(html) == "var dle_login_hash = '0';"


def test_scrub_removes_redirect_tokens() -> None:
    html = '<a href="/r?t=eyJpdiI6IkhJMVhOTEozcUN1NlFLVWNObzFXMHc9PSIs">VOE</a>'
    assert _mod.scrub(html) == '<a href="/r?t=scrubbed">VOE</a>'


def test_scrub_removes_the_csrf_token() -> None:
    html = '<meta name="csrf-token" content="Xy7rT0kq9WpLmZ3v">'
    assert _mod.scrub(html) == '<meta name="csrf-token" content="0">'


def test_store_fixture_writes_scrubbed_gzip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_mod, "_FIXTURE_DIR", tmp_path)
    page = tmp_path / "page.html"
    page.write_text("<p>Oppenheimer</p><script>dle_login_hash = 'abc123'</script>")

    target = _mod.store_fixture("kinoger", page, "detail-oppenheimer")

    assert target == tmp_path / "kinoger" / "detail-oppenheimer.html.gz"
    assert gzip.decompress(target.read_bytes()).decode() == (
        "<p>Oppenheimer</p><script>dle_login_hash = '0'</script>"
    )
