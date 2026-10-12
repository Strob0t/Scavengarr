"""Tests for scripts/playback_under_load.py (no live requests)."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

_SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def _load() -> ModuleType:
    sys.path.insert(0, str(_SCRIPTS))  # the script imports portainer and prodctl
    try:
        return importlib.import_module("playback_under_load")
    finally:
        sys.path.remove(str(_SCRIPTS))


_mod = _load()

_BASE = "http://app.test"
_PROXY = f"{_BASE}/api/v1/stremio/proxy/abc"
_MASTER = (
    "#EXTM3U\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n"
    "low/index.m3u8\n"
    "#EXT-X-STREAM-INF:BANDWIDTH=2500000,RESOLUTION=1280x720\n"
    f"{_PROXY}/hd/index.m3u8\n"
)
_MEDIA = (
    "#EXTM3U\n"
    "#EXT-X-TARGETDURATION:6\n"
    "#EXTINF:6.006,\n"
    "seg0.ts\n"
    "\n"
    "#EXTINF:5.5,\n"
    f"{_PROXY}/hd/seg1.ts\n"
    "#EXT-X-ENDLIST\n"
)
_METRICS_BEFORE = """# HELP scavengarr_hls_proxy_seconds HLS proxy requests
# TYPE scavengarr_hls_proxy_seconds histogram
scavengarr_hls_proxy_seconds_bucket{kind="segment",le="0.25"} 2.0
scavengarr_hls_proxy_seconds_bucket{kind="segment",le="1.0"} 3.0
scavengarr_hls_proxy_seconds_bucket{kind="segment",le="+Inf"} 3.0
scavengarr_hls_proxy_seconds_count{kind="segment"} 3.0
scavengarr_hls_proxy_seconds_sum{kind="segment"} 0.9
scavengarr_hls_proxy_seconds_created{kind="segment"} 1.7e+09
scavengarr_hls_proxy_total{kind="segment",outcome="200"} 3.0
scavengarr_hls_readahead_total{outcome="ok"} 9.0
scavengarr_plugin_search_seconds_count{plugin="a"} 5.0
scavengarr_plugin_search_seconds_sum{plugin="a"} 1.0
"""
_METRICS_AFTER = """scavengarr_hls_proxy_seconds_bucket{kind="segment",le="0.25"} 10.0
scavengarr_hls_proxy_seconds_bucket{kind="segment",le="1.0"} 12.0
scavengarr_hls_proxy_seconds_bucket{kind="segment",le="+Inf"} 13.0
scavengarr_hls_proxy_seconds_count{kind="segment"} 13.0
scavengarr_hls_proxy_seconds_sum{kind="segment"} 4.9
scavengarr_hls_proxy_seconds_created{kind="segment"} 1.7e+09
scavengarr_hls_proxy_bucket{kind="master",le="+Inf"} 1.0
scavengarr_hls_proxy_total{kind="segment",outcome="200"} 13.0
scavengarr_hls_proxy_total{kind="master",outcome="200"} 1.0
scavengarr_hls_readahead_total{outcome="ok"} 17.0
scavengarr_hls_readahead_total{outcome="hit"} 8.0
scavengarr_plugin_search_seconds_count{plugin="a"} 6.0
scavengarr_plugin_search_seconds_sum{plugin="a"} 4.0
scavengarr_plugin_search_seconds_count{plugin="b"} 2.0
scavengarr_plugin_search_seconds_sum{plugin="b"} 9.0
scavengarr_plugin_search_seconds_bucket{plugin="a",le="2.0"} 6.0
scavengarr_plugin_search_seconds_bucket{plugin="a",le="+Inf"} 6.0
scavengarr_plugin_search_seconds_bucket{plugin="b",le="2.0"} 0.0
scavengarr_plugin_search_seconds_bucket{plugin="b",le="+Inf"} 2.0
odd line without a value
"""


class TestPlaylists:
    def test_the_variant_with_the_highest_bandwidth_wins(self) -> None:
        master_url = f"{_PROXY}/scavengarr.m3u8"
        assert _mod.best_variant(_MASTER, master_url) == f"{_PROXY}/hd/index.m3u8"

    def test_a_relative_variant_is_joined_to_the_master(self) -> None:
        master = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow/index.m3u8\n"
        assert (
            _mod.best_variant(master, f"{_PROXY}/scavengarr.m3u8")
            == f"{_PROXY}/low/index.m3u8"
        )

    def test_a_media_playlist_is_its_own_variant(self) -> None:
        url = f"{_PROXY}/hd/index.m3u8"
        assert _mod.best_variant(_MEDIA, url) == url

    def test_segments_carry_their_durations_and_absolute_urls(self) -> None:
        assert _mod.media_segments(_MEDIA, f"{_PROXY}/hd/index.m3u8") == [
            (6.006, f"{_PROXY}/hd/seg0.ts"),
            (5.5, f"{_PROXY}/hd/seg1.ts"),
        ]


class TestMetrics:
    def test_series_keep_their_labels_and_skip_comments(self) -> None:
        series = _mod.parse_metrics(_METRICS_BEFORE)
        assert series['scavengarr_hls_proxy_seconds_count{kind="segment"}'] == 3.0
        assert series['scavengarr_hls_readahead_total{outcome="ok"}'] == 9.0
        assert not any(key.startswith("#") for key in series)

    def test_a_line_without_a_value_is_skipped(self) -> None:
        assert "odd" not in " ".join(_mod.parse_metrics(_METRICS_AFTER))

    def test_the_deltas_count_new_series_from_zero(self) -> None:
        grown = _mod.deltas(
            _mod.parse_metrics(_METRICS_BEFORE), _mod.parse_metrics(_METRICS_AFTER)
        )
        assert grown['scavengarr_hls_proxy_seconds_count{kind="segment"}'] == 10.0
        assert grown['scavengarr_hls_readahead_total{outcome="hit"}'] == 8.0

    def test_a_histogram_is_summed_over_the_other_labels(self) -> None:
        grown = _mod.deltas(
            _mod.parse_metrics(_METRICS_BEFORE), _mod.parse_metrics(_METRICS_AFTER)
        )
        count, mean, buckets = _mod.histogram(grown, "scavengarr_plugin_search_seconds")
        assert count == 3
        assert mean == pytest.approx(4.0)
        assert buckets == {2.0: 6.0, float("inf"): 8.0}

    def test_a_label_filter_picks_one_series(self) -> None:
        grown = _mod.deltas(
            _mod.parse_metrics(_METRICS_BEFORE), _mod.parse_metrics(_METRICS_AFTER)
        )
        count, mean, buckets = _mod.histogram(
            grown, "scavengarr_hls_proxy_seconds", kind="segment"
        )
        assert (count, mean) == (10, pytest.approx(0.4))
        assert _mod.quantile(buckets, 0.5) == 0.25
        assert _mod.quantile(buckets, 0.9) == 1.0
        assert _mod.quantile(buckets, 1.0) == float("inf")
        assert (
            _mod.histogram(grown, "scavengarr_hls_proxy_seconds", kind="file")[0] == 0
        )

    def test_no_observations_give_no_quantile(self) -> None:
        assert _mod.quantile({}, 0.5) is None
        assert _mod.quantile({0.5: 0.0, float("inf"): 0.0}, 0.5) is None

    def test_outcomes_read_the_total_counter_only(self) -> None:
        grown = _mod.deltas(
            _mod.parse_metrics(_METRICS_BEFORE), _mod.parse_metrics(_METRICS_AFTER)
        )
        assert _mod.outcomes(grown, "scavengarr_hls_readahead") == {"ok": 8, "hit": 8}
        assert _mod.outcomes(grown, "scavengarr_hls_proxy", kind="segment") == {
            "200": 10
        }

    def test_pct_takes_the_nearest_rank(self) -> None:
        assert _mod.pct([3.0, 1.0, 2.0], 0.5) == 2.0
        assert _mod.pct([3.0, 1.0, 2.0], 0.9) == 3.0
        assert _mod.pct([1.0], 0.9) == 1.0


class TestRows:
    def _run(self) -> Any:
        run = _mod.Run("idle")
        run.segments.append(_mod.SegmentFetch(6.0, 0.2, 1.0, 2_000_000, 200))
        run.segments.append(_mod.SegmentFetch(6.0, 0.3, 7.0, 2_000_000, 200))
        run.cpu.extend([0.4, 0.9, 0.5])
        run.before = _mod.parse_metrics(_METRICS_BEFORE)
        run.after = _mod.parse_metrics(_METRICS_AFTER)
        return run

    def test_the_player_row_counts_the_stall_and_the_throughput(self) -> None:
        assert _mod.player_row(self._run()) == (
            "| idle | 2 (12 s) | 1 | 200 / 300 | 1000 / 7000 / 7000 | 4.0"
            " | 0.50 / 0.90 | none |"
        )

    def test_the_player_row_names_the_answers(self) -> None:
        run = self._run()
        run.answers.append(
            _mod.SearchAnswer("movie/tt1", 31.25, 200, 4, "miss", "false")
        )
        assert _mod.player_row(run).endswith("| 31.2 s (4 streams, miss) |")

    def test_a_run_without_segments_is_an_empty_row(self) -> None:
        assert (
            _mod.player_row(_mod.Run("idle")) == "| idle | 0 | - | - | - | - | - | - |"
        )

    def test_the_server_row_reads_the_deltas(self) -> None:
        assert _mod.server_row(self._run()) == (
            "| idle | 3 / 4.0 s / inf s | 10 / 400 ms / ≤1 s | 0 / 0.00 s"
            " | 8 / 8 / 0 / 0 | - / - / - s |"
        )


def _fake_server(*, metrics: list[str] | None = None) -> None:
    texts = list(metrics or [_METRICS_BEFORE, _METRICS_AFTER])
    respx.get(f"{_BASE}/metrics").mock(
        side_effect=lambda request: httpx.Response(200, text=texts.pop(0))
    )
    respx.get(f"{_PROXY}/scavengarr.m3u8").respond(200, text=_MASTER)
    fast = _MEDIA.replace("6.006", "0.001").replace("5.5", "0.001")
    respx.get(f"{_PROXY}/hd/index.m3u8").respond(200, text=fast)
    respx.get(url__regex=rf"{_PROXY}/hd/seg\d\.ts").respond(
        200, content=b"\x47" + b"\x00" * 375
    )
    respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt1.json").respond(
        200,
        json={"streams": [{"url": "a"}, {"url": "b"}]},
        headers={"X-Cache": "miss", "X-Search-Complete": "true"},
    )


def _args(*extra: str) -> argparse.Namespace:
    return _mod._parser().parse_args(
        ["--base", _BASE, "--stream-id", "abc", "--segments", "1", *extra]
    )


class TestRun:
    @respx.mock
    def test_an_idle_run_plays_the_asked_segments(self) -> None:
        _fake_server()
        run = asyncio.run(_mod.run_once(_args()))
        assert run.label == "idle"
        assert [s.status for s in run.segments] == [200]
        assert run.segments[0].size == 376
        assert run.answers == []
        assert run.after != run.before

    @respx.mock
    def test_a_search_run_plays_on_until_the_answer(self) -> None:
        _fake_server()
        run = asyncio.run(
            asyncio.wait_for(
                _mod.run_once(
                    _args("--search", "movie/tt1", "--search-at", "0", "--tail", "0")
                ),
                timeout=10,
            )
        )
        assert run.label == "search"
        assert len(run.answers) == 1
        answer = run.answers[0]
        assert (answer.status, answer.streams, answer.cache, answer.complete) == (
            200,
            2,
            "miss",
            "true",
        )
        assert answer.wall > 0
        assert len(run.segments) >= 1

    @respx.mock
    def test_a_failed_search_is_an_answer_without_a_status(self) -> None:
        _fake_server()
        respx.get(f"{_BASE}/api/v1/stremio/stream/movie/tt2.json").mock(
            side_effect=httpx.ConnectError("boom")
        )
        run = asyncio.run(
            _mod.run_once(
                _args("--search", "movie/tt2", "--search-at", "0", "--tail", "0")
            )
        )
        assert [(a.status, a.cache) for a in run.answers] == [(0, "ConnectError")]

    @respx.mock
    def test_a_refused_master_playlist_ends_the_run(self) -> None:
        _fake_server()
        respx.get(f"{_PROXY}/scavengarr.m3u8").respond(404, json={"error": "gone"})
        with pytest.raises(SystemExit, match="master playlist: HTTP 404"):
            asyncio.run(_mod.run_once(_args()))

    def test_the_cpu_sampler_reads_until_stopped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Container:
            def __init__(self) -> None:
                self.calls = 0

            def stats(self) -> dict[str, object]:
                self.calls += 1
                return {
                    "cpu_stats": {
                        "cpu_usage": {"total_usage": 300},
                        "system_cpu_usage": 1_000,
                        "online_cpus": 4,
                    },
                    "precpu_stats": {
                        "cpu_usage": {"total_usage": 100},
                        "system_cpu_usage": 600,
                    },
                }

        async def scenario() -> tuple[int, list[float]]:
            monkeypatch.setattr(_mod, "_STATS_EVERY", 0.01)
            container = Container()
            run = _mod.Run("idle")
            stop = asyncio.Event()
            task = asyncio.create_task(_mod.sample_cpu(container, run, stop))
            await asyncio.sleep(0.05)
            stop.set()
            await task
            return container.calls, run.cpu

        calls, cpu = asyncio.run(scenario())
        assert calls >= 2
        assert cpu[0] == pytest.approx(2.0)


class TestParser:
    def test_defaults(self) -> None:
        args = _mod._parser().parse_args(["--stream-id", "abc"])
        assert (args.segments, args.search, args.search_at, args.tail) == (
            20,
            [],
            10.0,
            30.0,
        )
        assert not args.portainer and args.out is None

    def test_a_stream_id_is_required(self) -> None:
        with pytest.raises(SystemExit):
            _mod._parser().parse_args([])
