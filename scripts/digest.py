"""One report of a production window: requests, plugins, hosters, breakers,
log noise and errors (``prodctl.py digest``).

Pure functions over what ``prodctl.py`` fetches: the container log of the
window (JSON records), ``GET /metrics`` (since the process start) and the
plugins' long-term record (``GET /api/v1/stats/plugins``). ``prodctl.py``
masks everything it prints; this module carries no credentials and makes no
request.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from typing import Any

# A metric sample: ``name{label="value",...} 1.0``
_SAMPLE = re.compile(r"^([A-Za-z_:][A-Za-z0-9_:]*)(?:\{(.*)\})?\s+(\S+)")
_LABEL = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)="((?:[^"\\]|\\.)*)"')
# Liveness and readiness checks: two records every 30 s
_HEALTH_PATHS = ("/api/v1/healthz", "/api/v1/readyz")
_STREAM_PATH = "/stremio/stream/"
# The id schemes of a stream request (the catalogs the players use):
# /stremio/stream/<type>/tt123.json, kitsu:123:2.json, tmdb:603.json
_ID_SCHEMES = ("tt", "kitsu", "tmdb")
# Searches a request did not run itself end the request as ``joined``;
# these events decide the source before that (the first one wins)
_SOURCE_EVENTS = {
    "stremio_search_start": "search",
    "stremio_title_not_found": "none",
    "stremio_no_stream_plugins": "none",
}
_SOURCE_ORDER = ("search", "joined", "stale", "cache", "none")
_RECORD_WINDOWS = ("30", "90", "180")
_TOP_EVENTS = 5
_TOP_ERRORS = 3
_ERROR_LEVELS = frozenset({"error", "critical", "fatal"})

Record = dict[str, Any]
Sample = tuple[str, dict[str, str], float]


def parse_records(text: str) -> list[Record]:
    """The JSON records of a container log (``timestamp payload`` lines),
    without the health checks; other lines are skipped."""
    records: list[Record] = []
    for line in text.splitlines():
        _, _, payload = line.partition(" ")
        try:
            record = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("event") == "http_request" and record.get("path") in (
            _HEALTH_PATHS
        ):
            continue
        records.append(record)
    return records


def parse_metrics(text: str) -> list[Sample]:
    """The samples of a Prometheus text exposition."""
    samples: list[Sample] = []
    for line in text.splitlines():
        if line.startswith("#"):
            continue
        match = _SAMPLE.match(line)
        if match is None:
            continue
        name, raw_labels, raw_value = match.groups()
        try:
            value = float(raw_value)
        except ValueError:
            continue
        labels = {k: v for k, v in _LABEL.findall(raw_labels or "")}
        samples.append((name, labels, value))
    return samples


def _percentile(values: Sequence[float], share: float) -> float | None:
    """The nearest-rank percentile of *values*; ``None`` without values."""
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def _id_scheme(path: str) -> str:
    """The id scheme of a stream request path: ``tt``, ``kitsu``, ``tmdb``
    or ``other``."""
    stream_id = path.rpartition("/")[2].removesuffix(".json")
    if stream_id.startswith("tt"):
        return "tt"
    scheme = stream_id.partition(":")[0]
    return scheme if scheme in _ID_SCHEMES else "other"


def _stream_requests(records: Iterable[Record]) -> dict[str, dict[str, Any]]:
    """The stream requests of the window by ``request_id``: their source,
    answer time (the access record) and streams (the response record)."""
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        rid = record.get("request_id")
        event = record.get("event")
        if not isinstance(rid, str) or not isinstance(event, str):
            continue
        if event == "stremio_stream_request":
            by_id.setdefault(rid, {})
            continue
        request = by_id.get(rid)
        if request is None:
            continue
        if event == "stremio_stream_response":
            request["streams"] = record.get("streams_returned")
        elif event == "stremio_search_cache_hit":
            request.setdefault("source", "stale" if record.get("stale") else "cache")
        elif event in _SOURCE_EVENTS:
            request.setdefault("source", _SOURCE_EVENTS[event])
        elif event == "http_request" and _STREAM_PATH in str(record.get("path", "")):
            request["duration_ms"] = record.get("duration_ms")
            request["scheme"] = _id_scheme(str(record.get("path", "")))
    return by_id


def _source_stats(answered: Sequence[dict[str, Any]]) -> dict[str, Any]:
    durations = [
        float(r["duration_ms"])
        for r in answered
        if isinstance(r.get("duration_ms"), int | float)
    ]
    streams = [int(r["streams"]) for r in answered]
    return {
        "count": len(answered),
        "p50_ms": _percentile(durations, 0.5),
        "p90_ms": _percentile(durations, 0.9),
        "streams_per_answer": round(sum(streams) / len(streams), 1),
        "without_streams": sum(1 for s in streams if s == 0),
    }


def requests(records: Iterable[Record]) -> dict[str, Any]:
    """Stream requests by source: count, p50/p90 of the answer time and
    the streams per answer, joined over ``request_id``.

    The source: ``search`` ran the search, ``joined`` waited for one
    already running, ``cache`` and ``stale`` answered from the search
    cache, ``none`` found no title or no plugin.
    """
    by_id = _stream_requests(records)
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    schemes: Counter[str] = Counter()
    for request in by_id.values():
        if "streams" in request:
            by_source[request.get("source", "joined")].append(request)
        if "scheme" in request:
            schemes[request["scheme"]] += 1
    return {
        "answered": sum(len(v) for v in by_source.values()),
        # Still running at the end of the window, or an error
        "unanswered": sum(1 for r in by_id.values() if "streams" not in r),
        "by_source": {
            source: _source_stats(by_source[source])
            for source in sorted(by_source, key=_SOURCE_ORDER.index)
        },
        # Every request with an access record, answered or not; the known
        # schemes always, "other" only when seen
        "by_scheme": {
            scheme: schemes[scheme]
            for scheme in (*_ID_SCHEMES, "other")
            if scheme in _ID_SCHEMES or schemes[scheme]
        },
    }


def plugins(record: Any, first_day: date) -> dict[str, Any]:
    """Per plugin, the record's counters of the days from *first_day* on,
    the last day with a result and the unreachable shares; sorted by
    results, then searches."""
    if not isinstance(record, dict) or not isinstance(record.get("plugins"), dict):
        return {"available": False, "plugins": []}
    today = date.fromisoformat(record["today"]) if "today" in record else None
    rows: list[dict[str, Any]] = []
    for name, entry in record["plugins"].items():
        counters: Counter[str] = Counter()
        for day, day_counters in entry.get("days", {}).items():
            if day >= first_day.isoformat():
                counters.update(day_counters)
        last = entry.get("last_result_day")
        days_since = (
            (today - date.fromisoformat(last)).days
            if today is not None and isinstance(last, str)
            else None
        )
        rows.append(
            {
                "plugin": name,
                "searches": counters["searches"],
                "results": counters["results"],
                "timeouts": counters["timeouts"],
                "dropped": counters["dropped"],
                "challenges": counters["challenges"],
                "checks": counters["checks"],
                "unreachable": counters["unreachable"],
                "last_result_day": last,
                "days_since_result": days_since,
                "unreachable_share": {
                    w: entry.get("unreachable_share", {}).get(w)
                    for w in _RECORD_WINDOWS
                },
            }
        )
    rows.sort(key=lambda r: (-r["results"], -r["searches"], r["plugin"]))
    return {"available": True, "first_day": first_day.isoformat(), "plugins": rows}


def hosters(samples: Iterable[Sample]) -> list[dict[str, Any]]:
    """Per resolver: resolutions, their mean duration and the outcomes
    (``scavengarr_hoster_resolve_*``, since the process start)."""
    outcomes: dict[str, Counter[str]] = defaultdict(Counter)
    counts: Counter[str] = Counter()
    sums: dict[str, float] = defaultdict(float)
    for name, labels, value in samples:
        resolver = labels.get("resolver")
        if resolver is None:
            continue
        if name == "scavengarr_hoster_resolve_total":
            outcomes[resolver][labels.get("outcome", "")] += int(value)
        elif name == "scavengarr_hoster_resolve_seconds_count":
            counts[resolver] += int(value)
        elif name == "scavengarr_hoster_resolve_seconds_sum":
            sums[resolver] += value
    rows = [
        {
            "resolver": resolver,
            "resolutions": counts[resolver],
            "mean_s": round(sums[resolver] / counts[resolver], 2)
            if counts[resolver]
            else None,
            "outcomes": dict(outcomes[resolver].most_common()),
        }
        for resolver in set(counts) | set(outcomes)
    ]
    rows.sort(key=lambda r: (-r["resolutions"], r["resolver"]))
    return rows


def breakers(samples: Iterable[Sample], records: Iterable[Record]) -> dict[str, Any]:
    """The breakers not closed now (the ``circuit_breaker_open`` gauge),
    the openings in the window (``circuit_breaker_opened``) and how often
    an open one skipped a search or a resolution
    (``stremio_plugin_circuit_open``, ``hoster_resolve_circuit_open``)."""
    open_now = [
        {
            "breaker": labels.get("breaker", ""),
            "name": labels.get("name", ""),
            "state": labels.get("state", ""),
        }
        for name, labels, value in samples
        if name == "scavengarr_circuit_breaker_open" and value > 0
    ]
    open_now.sort(key=lambda b: (b["breaker"], b["name"]))
    opened: Counter[str] = Counter()
    skipped: Counter[tuple[str, str]] = Counter()
    for record in records:
        event = record.get("event")
        if event == "circuit_breaker_opened":
            opened[str(record.get("name", ""))] += 1
        elif event == "stremio_plugin_circuit_open":
            skipped["plugin", str(record.get("plugin", ""))] += 1
        elif event == "hoster_resolve_circuit_open":
            skipped["hoster", str(record.get("hoster", ""))] += 1
    return {
        "open": open_now,
        "opened": [
            {"name": name, "openings": count} for name, count in opened.most_common()
        ],
        "skipped": [
            {"breaker": breaker, "name": name, "skipped": count}
            for (breaker, name), count in skipped.most_common()
        ],
    }


def noise(records: Sequence[Record]) -> list[dict[str, Any]]:
    """The most frequent events and their share of the records."""
    counts = Counter(str(r.get("event", "")) for r in records)
    total = len(records) or 1
    return [
        {"event": event, "count": count, "share": round(count / total, 3)}
        for event, count in counts.most_common(_TOP_EVENTS)
    ]


def errors(records: Iterable[Record]) -> dict[str, Any]:
    """Records at ``error`` and above: their count and the most frequent
    messages (the event, its logger and the exception's last line)."""
    messages: Counter[str] = Counter()
    for record in records:
        if str(record.get("level", "")).lower() not in _ERROR_LEVELS:
            continue
        # The first line only: asyncio's "Task exception was never
        # retrieved" names the task on the second, which would split one
        # error into as many messages as tasks
        message = str(record.get("event", "")).strip().partition("\n")[0]
        logger = record.get("logger")
        if logger:
            message += f" ({logger})"
        exception = record.get("exception") or record.get("error")
        if exception:
            last = str(exception).strip().splitlines()[-1]
            message += f": {last[:120]}"
        messages[message] += 1
    return {
        "count": sum(messages.values()),
        "top": [
            {"message": message, "count": count}
            for message, count in messages.most_common(_TOP_ERRORS)
        ],
    }


def process(samples: Iterable[Sample], now: float) -> dict[str, Any]:
    """The build and the uptime (``scavengarr_build_info``,
    ``process_start_time_seconds``)."""
    info: dict[str, Any] = {"version": None, "commit": None, "uptime_s": None}
    for name, labels, value in samples:
        if name == "scavengarr_build_info":
            info["version"] = labels.get("version")
            info["commit"] = labels.get("commit")
        elif name == "process_start_time_seconds":
            info["uptime_s"] = max(0, round(now - value))
    return info


def digest(
    *,
    container: str,
    since_s: float,
    log_text: str,
    metrics_text: str,
    plugin_record: Any,
    now: float,
) -> dict[str, Any]:
    """The report's data: the sections over the window of *since_s*
    seconds before *now* (the log) and the process (the metrics)."""
    records = parse_records(log_text)
    samples = parse_metrics(metrics_text)
    first_day = datetime.fromtimestamp(now - since_s, UTC).date()
    return {
        "container": container,
        "since_s": since_s,
        "log_records": len(records),
        "process": process(samples, now),
        "requests": requests(records),
        "plugins": plugins(plugin_record, first_day),
        "hosters": hosters(samples),
        "breakers": breakers(samples, records),
        "noise": noise(records),
        "errors": errors(records),
    }


# --- Markdown -----------------------------------------------------------


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
    ]
    lines.extend("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)
    return lines


def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.1f}"
    return str(value)


def _duration(seconds: float) -> str:
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    if seconds < 2 * 86400:
        return f"{seconds / 3600:.1f} h"
    return f"{seconds / 86400:.1f} d"


def _share(value: float | None) -> str:
    return "–" if value is None else f"{value:.0%}"


def render(data: dict[str, Any]) -> str:
    """The report as Markdown."""
    process_ = data["process"]
    uptime = process_["uptime_s"]
    head = [f"{data['log_records']} log records in the window"]
    if process_["version"]:
        head.append(f"version {process_['version']}, commit {process_['commit']}")
    if uptime is not None:
        head.append(f"up {_duration(uptime)} (the metrics count since the start)")
    elif not process_["version"]:
        head.append("no metrics")
    lines = [
        f"# {data['container']}: the last {_duration(data['since_s'])}",
        "",
        "; ".join(head),
        "",
        "## Requests",
        "",
    ]
    req = data["requests"]
    lines.append(
        f"{req['answered']} answered"
        + (
            f", {req['unanswered']} without an answer in the window"
            if req["unanswered"]
            else ""
        )
    )
    lines.append(
        "By id scheme: "
        + ", ".join(f"{scheme} {count}" for scheme, count in req["by_scheme"].items())
    )
    lines.append("")
    lines.extend(
        _table(
            (
                "Source",
                "Answers",
                "p50",
                "p90",
                "Streams per answer",
                "Without streams",
            ),
            (
                (
                    source,
                    s["count"],
                    _ms(s["p50_ms"]),
                    _ms(s["p90_ms"]),
                    s["streams_per_answer"],
                    s["without_streams"],
                )
                for source, s in req["by_source"].items()
            ),
        )
    )
    lines.extend(["", "## Plugins", ""])
    plugins_ = data["plugins"]
    if not plugins_["available"]:
        lines.append(
            "The long-term record is not available (an older image, or the"
            " route answered an error)."
        )
    else:
        lines.append(
            f"The record's days from {plugins_['first_day']} on; the last result"
            " and the unreachable shares (30, 90, 180 days) from the whole record."
        )
        lines.append("")
        lines.extend(
            _table(
                (
                    "Plugin",
                    "Searches",
                    "Results",
                    "Timeouts",
                    "Dropped",
                    "Challenges",
                    "Checks",
                    "Unreachable",
                    "Last result",
                    "Unreachable 30/90/180 d",
                ),
                (
                    (
                        p["plugin"],
                        p["searches"],
                        p["results"],
                        p["timeouts"],
                        p["dropped"],
                        p["challenges"],
                        p["checks"],
                        p["unreachable"],
                        _last_result(p),
                        "/".join(
                            _share(p["unreachable_share"][w]) for w in _RECORD_WINDOWS
                        ),
                    )
                    for p in plugins_["plugins"]
                ),
            )
        )
        lines.append("")
        challenged = [p for p in plugins_["plugins"] if p["challenges"]]
        if challenged:
            lines.append(
                "Challenged in the window: "
                + ", ".join(f"{p['plugin']} {p['challenges']}" for p in challenged)
            )
        else:
            lines.append("No search met a challenge in the window.")
    lines.extend(["", "## Hosters", "", "Since the process start."])
    lines.append("")
    lines.extend(
        _table(
            ("Resolver", "Resolutions", "Mean", "Outcomes"),
            (
                (
                    h["resolver"],
                    h["resolutions"],
                    None if h["mean_s"] is None else f"{h['mean_s']:.1f} s",
                    ", ".join(f"{o} {n}" for o, n in h["outcomes"].items()),
                )
                for h in data["hosters"]
            ),
        )
    )
    lines.extend(["", "## Breakers", ""])
    br = data["breakers"]
    if br["open"]:
        lines.append(
            "Not closed now: "
            + ", ".join(
                f"{b['name']} ({b['breaker']}, {b['state']})" for b in br["open"]
            )
        )
    else:
        lines.append("Every breaker is closed now.")
    if br["opened"]:
        lines.append(
            "Opened in the window: "
            + ", ".join(f"{b['name']} ×{b['openings']}" for b in br["opened"])
        )
    if br["skipped"]:
        lines.append("")
        lines.extend(
            _table(
                ("Breaker", "Name", "Skipped in the window"),
                ((s["breaker"], s["name"], s["skipped"]) for s in br["skipped"]),
            )
        )
    lines.extend(["", "## Noise", ""])
    lines.extend(
        _table(
            ("Event", "Records", "Share"),
            ((n["event"], n["count"], _share(n["share"])) for n in data["noise"]),
        )
    )
    err = data["errors"]
    lines.extend(["", "## Errors", "", f"{err['count']} at error and above"])
    if err["top"]:
        lines.append("")
        lines.extend(
            _table(
                ("Message", "Count"), ((e["message"], e["count"]) for e in err["top"])
            )
        )
    return "\n".join(lines) + "\n"


def _ms(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{value / 1000:.2f} s" if value >= 1000 else f"{value:.0f} ms"


def _last_result(plugin: dict[str, Any]) -> str:
    if plugin["last_result_day"] is None:
        return "never"
    days = plugin["days_since_result"]
    if days is None:
        return str(plugin["last_result_day"])
    return "today" if days == 0 else f"{days} d ago"
