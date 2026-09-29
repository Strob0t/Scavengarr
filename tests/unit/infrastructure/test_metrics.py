"""Tests for the zero-impact MetricsCollector."""

from __future__ import annotations

from scavengarr.infrastructure.metrics import MetricsCollector, PluginStats


class TestPluginStats:
    def test_default_values(self) -> None:
        stats = PluginStats()
        assert stats.searches == 0
        assert stats.successes == 0
        assert stats.failures == 0
        assert stats.total_results == 0
        assert stats.total_duration_ns == 0

    def test_snapshot_no_searches(self) -> None:
        snap = PluginStats().snapshot()
        assert snap["searches"] == 0
        assert snap["avg_duration_ms"] == 0.0

    def test_snapshot_with_data(self) -> None:
        stats = PluginStats(
            searches=4,
            successes=3,
            failures=1,
            total_results=120,
            total_duration_ns=2_000_000_000,  # 2s total
        )
        snap = stats.snapshot()
        assert snap["searches"] == 4
        assert snap["successes"] == 3
        assert snap["failures"] == 1
        assert snap["total_results"] == 120
        assert snap["avg_duration_ms"] == 500.0  # 2000ms / 4


class TestMetricsCollector:
    def test_record_plugin_search_success(self) -> None:
        m = MetricsCollector()
        m.record_plugin_search("byte", 100_000_000, 42, success=True)

        snap = m.snapshot()
        plugins = snap["plugins"]
        assert "byte" in plugins
        assert plugins["byte"]["searches"] == 1
        assert plugins["byte"]["successes"] == 1
        assert plugins["byte"]["failures"] == 0
        assert plugins["byte"]["total_results"] == 42

    def test_record_plugin_search_failure(self) -> None:
        m = MetricsCollector()
        m.record_plugin_search("broken", 50_000_000, 0, success=False)

        snap = m.snapshot()
        assert snap["plugins"]["broken"]["failures"] == 1
        assert snap["plugins"]["broken"]["successes"] == 0

    def test_multiple_plugins(self) -> None:
        m = MetricsCollector()
        m.record_plugin_search("alpha", 10_000_000, 5, success=True)
        m.record_plugin_search("beta", 20_000_000, 10, success=True)
        m.record_plugin_search("alpha", 15_000_000, 3, success=True)

        snap = m.snapshot()
        assert snap["plugins"]["alpha"]["searches"] == 2
        assert snap["plugins"]["alpha"]["total_results"] == 8
        assert snap["plugins"]["beta"]["searches"] == 1

    def test_snapshot_includes_uptime(self) -> None:
        m = MetricsCollector()
        snap = m.snapshot()
        assert "uptime_seconds" in snap
        assert isinstance(snap["uptime_seconds"], float)
        assert snap["uptime_seconds"] >= 0

    def test_snapshot_empty_collector(self) -> None:
        m = MetricsCollector()
        snap = m.snapshot()
        assert snap["plugins"] == {}
        assert "probe" not in snap

    def test_plugins_sorted_alphabetically(self) -> None:
        m = MetricsCollector()
        m.record_plugin_search("zeta", 1, 0, success=True)
        m.record_plugin_search("alpha", 1, 0, success=True)
        m.record_plugin_search("mid", 1, 0, success=True)

        snap = m.snapshot()
        keys = list(snap["plugins"].keys())
        assert keys == ["alpha", "mid", "zeta"]
