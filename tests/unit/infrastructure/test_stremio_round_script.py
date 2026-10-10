"""Tests for scripts/stremio_round.py (no live server)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
_BASE = "http://scavengarr.test"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports its sibling scripts
    try:
        spec = importlib.util.spec_from_file_location(
            "stremio_round", _SCRIPTS / "stremio_round.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        # @dataclass resolves the module's annotations through sys.modules
        sys.modules["stremio_round"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()


def _row(**kwargs: object) -> object:
    values: dict[str, object] = {
        "sid": "movie/tt1",
        "title": "One",
        "first_s": 4.0,
        "first_streams": 3,
        "cache": "–",
        "complete": "–",
        "cached_s": 0.1,
        "cached_streams": 3,
        "playable": 2,
    }
    values.update(kwargs)
    return _mod.Row(**values)


def test_read_ids_takes_titles_from_comments(tmp_path: Path) -> None:
    ids = tmp_path / "ids.txt"
    ids.write_text(
        "# round titles\n\nmovie/tt0130827  # Lola rennt\nseries/tt0903747:1:2\n"
    )
    assert _mod.read_ids(ids) == [
        ("movie/tt0130827", "Lola rennt"),
        ("series/tt0903747:1:2", "series/tt0903747:1:2"),
    ]


def test_default_ids_file_lists_round_titles() -> None:
    ids = _mod.read_ids(_mod.DEFAULT_IDS_FILE)
    sids = [sid for sid, _ in ids]
    assert len(sids) == len(set(sids)) == 19
    assert {"movie/tt0816692", "series/tt0903747:1:2", "movie/tt0133093"} <= set(sids)


@respx.mock
async def test_measure_requests_twice_and_checks_first_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").mock(
        side_effect=[
            httpx.Response(200, json={"streams": [{"url": "a"}, {"url": "b"}]}),
            httpx.Response(200, json={"streams": [{"url": "a"}, {"url": "b"}, {}]}),
        ]
    )
    checked: list[str] = []

    async def fake_check(client: httpx.AsyncClient, stream: dict) -> str:
        checked.append(stream["url"])
        return "OK mp4" if stream["url"] == "a" else "FAIL html"

    monkeypatch.setattr(_mod, "check_stream", fake_check)
    async with httpx.AsyncClient() as client:
        row = await _mod.measure(client, _BASE, "movie/tt1", "One", playcheck=True)

    assert route.call_count == 2
    assert checked == ["a", "b"]
    assert (row.first_streams, row.cached_streams, row.playable) == (2, 3, 1)
    assert (row.cache, row.complete) == ("–", "–")


def _stream(url: str, hoster: str) -> dict[str, str]:
    return {"url": url, "description": f"Title 1080p\nGerman\n{hoster} · site"}


@respx.mock
async def test_measure_collects_the_failing_verdicts_and_the_200_findings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing stream is a finding ``hoster: verdict`` (the verdict cut to
    60 characters, the hoster from the description, never the URL); a Range
    request answered 200 counts even on a playable stream."""
    streams = [
        _stream("https://cdn.a/v.mp4", "VOE"),
        _stream("https://cdn.b/v.mp4", "DOOD"),
        _stream("https://cdn.c/v.m3u8", "FSST"),
    ]
    respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"streams": streams})
    )
    verdicts = {
        "https://cdn.a/v.mp4": "OK mp4 3.00 GB, head 200 instead of 206, seek 206",
        "https://cdn.b/v.mp4": "FAIL html b'<html><body>403</body></html>'",
        "https://cdn.c/v.m3u8": "FAIL hls 3 segments: " + "502, " * 20,
    }

    async def fake_check(client: httpx.AsyncClient, stream: dict) -> str:
        return verdicts[stream["url"]]

    monkeypatch.setattr(_mod, "check_stream", fake_check)
    async with httpx.AsyncClient() as client:
        row = await _mod.measure(client, _BASE, "movie/tt1", "One", playcheck=True)

    assert row.playable == 1
    assert row.findings == (
        "DOOD: FAIL html b'<html><body>403</body></html>'",
        "FSST: " + ("FAIL hls 3 segments: " + "502, " * 20)[:60],
    )
    assert row.full_bodies == 1
    assert all("https://" not in f for f in row.findings)


def test_findings_cell_names_the_hosters_and_the_200_count() -> None:
    assert _mod._findings(_row()) == "–"
    assert _mod._findings(_row(full_bodies=2)) == "2× 200 instead of 206"
    assert (
        _mod._findings(
            _row(findings=("DOOD: FAIL html", "DOOD: FAIL html", "FSST: FAIL 502"))
        )
        == "DOOD, FSST"
    )
    assert (
        _mod._findings(_row(findings=("DOOD: FAIL html",), full_bodies=1))
        == "DOOD; 1× 200 instead of 206"
    )


@respx.mock
async def test_measure_without_playcheck_reads_x_cache() -> None:
    respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").mock(
        return_value=httpx.Response(
            200,
            json={"streams": []},
            headers={"X-Cache": "MISS", "X-Search-Complete": "false"},
        )
    )
    async with httpx.AsyncClient() as client:
        row = await _mod.measure(client, _BASE, "movie/tt1", "One", playcheck=False)
    assert (row.first_streams, row.cache, row.complete, row.playable) == (
        0,
        "MISS",
        "false",
        None,
    )


def test_render_has_a_row_per_title_and_a_summary() -> None:
    rows = [
        _row(),
        _row(
            sid="movie/tt2",
            title="Two",
            first_s=10.0,
            first_streams=0,
            cached_s=0.3,
            cached_streams=1,
            playable=0,
        ),
        _row(
            sid="movie/tt3",
            title="Three",
            first_s=6.0,
            first_streams=5,
            cached_s=0.2,
            cached_streams=5,
            playable=5,
        ),
    ]
    rows[0] = _row(findings=("DOOD: FAIL html",), full_bodies=1)
    table = _mod.render(rows, {"python": 12.5, "chrome": 40.0})
    lines = table.splitlines()
    assert lines[0] == (
        "| Title | First answer | Streams | X-Cache | Complete "
        "| Cached answer | Streams | Playable | Findings |"
    )
    assert (
        "| One (`movie/tt1`) | 4.0 s | 3 | – | – | 0.10 s | 3 | 2 of 3 "
        "| DOOD; 1× 200 instead of 206 |"
    ) in lines
    assert (
        "| Three (`movie/tt3`) | 6.0 s | 5 | – | – | 0.20 s | 5 | 5 of 5 | – |" in lines
    )
    assert (
        "| **Median / total** (3 titles) | 6.0 s | 8 | – | – | 0.20 s | 9 "
        "| 7 of 8 | – |"
    ) in lines
    assert "1 of 3 (first answer), 0 of 3 (cached answer)" in table
    assert "max first answer 10.0 s" in table
    assert "Python 12.5 s, Chromium 40.0 s" in table


def test_render_without_playcheck_or_cpu() -> None:
    table = _mod.render(
        [
            _row(playable=None, cache="HIT", complete="true"),
            _row(playable=None, cache="MISS", complete="false"),
        ]
    )
    assert "| 4.0 s | 3 | HIT | true | 0.10 s | 3 | – | – |" in table
    assert "| 4.0 s | 6 | 1 HIT | 1 of 2 complete | 0.10 s | 6 | – | – |" in table
    assert "CPU" not in table


@respx.mock
def test_main_prints_the_findings_under_the_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "streams": [
                    _stream("https://cdn.a/v.mp4", "VOE"),
                    _stream("https://cdn.b/v.mp4", "DOOD"),
                ]
            },
        )
    )

    async def fake_check(client: httpx.AsyncClient, stream: dict) -> str:
        if stream["url"].startswith("https://cdn.a/"):
            return "OK mp4 1.00 GB, head 200 instead of 206, seek 206"
        return "FAIL html b'<html>'"

    monkeypatch.setattr(_mod, "check_stream", fake_check)
    ids = tmp_path / "ids.txt"
    ids.write_text("movie/tt1  # One\n")

    _mod.main(["--base", _BASE, "--ids-file", str(ids)])

    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("movie/tt1: first ")
    assert out[0].endswith("playable 1 of 2")
    assert out[1:3] == ["   DOOD: FAIL html b'<html>'", "   1× 200 instead of 206"]
    assert "https://" not in "\n".join(out[:3])


@respx.mock
def test_main_appends_the_table_to_out(tmp_path: Path) -> None:
    respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").mock(
        return_value=httpx.Response(200, json={"streams": [{"url": "a"}]})
    )
    ids = tmp_path / "ids.txt"
    ids.write_text("movie/tt1  # One\n")
    out = tmp_path / "round.md"
    out.write_text("# Rounds\n")

    _mod.main(
        ["--base", _BASE, "--ids-file", str(ids), "--out", str(out), "--no-playcheck"]
    )

    text = out.read_text()
    assert text.startswith("# Rounds\n")
    assert "`scripts/stremio_round.py`" in text
    assert "| One (`movie/tt1`) |" in text
