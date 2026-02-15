[← Back to Index](./README.md)

# Plugin Scoring & Probing

> Data-driven plugin prioritization for Stremio stream resolution via background health and search probes.

---

## Overview

Scavengarr searches all streaming plugins in parallel for every Stremio request. With 20+ plugins, this creates unnecessary load — many plugins are slow, unreliable, or return low-quality results for a given content category.

The **Plugin Scoring & Probing** system solves this by ranking plugins based on measured performance. Background probes continuously assess plugin health and search quality, feeding an EWMA-based scoring model that determines which plugins are queried in the live path.

---

## Architecture

```
Background Probes                    Live Stremio Path
  ├── HealthProber (daily)             GET /stream/{type}/{id}.json
  │     HEAD/GET origin → ok/fail        │
  │     → EwmaState (health)             ├── Load PluginScoreSnapshots
  │                                      ├── Select top-N by final_score
  └── MiniSearchProber (2×/week)         ├── Search plugins (deadline + early-stop)
        query per (category, bucket)     ├── Cancel remaining on budget
        → EwmaState (search)             └── Return ranked streams
                │
                ▼
         PluginScoreStore (diskcache / Redis)
```

---

## Data Model

### ProbeResult

Raw output from a single probe run.

| Field | Type | Description |
|---|---|---|
| `started_at` | `datetime` | Probe start timestamp |
| `duration_ms` | `float` | Wall-clock duration |
| `ok` | `bool` | Whether the probe succeeded |
| `error_kind` | `str \| None` | Error classification (timeout, captcha, http_error) |
| `http_status` | `int \| None` | HTTP response status |
| `captcha_detected` | `bool` | Cloudflare or CAPTCHA challenge detected |
| `items_found` | `int` | Raw search result count |
| `items_used` | `int` | Results after limit application |
| `hoster_checked` | `int` | Number of hoster URLs sampled |
| `hoster_reachable` | `int` | Reachable hosters from sample |

### EwmaState

Exponentially weighted moving average tracker.

| Field | Type | Description |
|---|---|---|
| `value` | `float` | Current EWMA value (0.0–1.0) |
| `last_ts` | `datetime` | Timestamp of last update |
| `n_samples` | `int` | Total samples incorporated |

### PluginScoreSnapshot

Composite score for a plugin within a (category, bucket) context.

| Field | Type | Description |
|---|---|---|
| `health_score` | `EwmaState` | Health probe EWMA |
| `search_score` | `EwmaState` | Search probe EWMA |
| `final_score` | `float` | Weighted composite score |
| `confidence` | `float` | How trustworthy the score is (0.0–1.0) |
| `updated_at` | `datetime` | Last snapshot update |

---

## EWMA Scoring

### Alpha Calculation

The smoothing factor `alpha` is derived from the probe interval and desired half-life:

```
alpha = 1 - 0.5 ^ (dt / half_life)
```

| Probe Type | Half-Life | Interval (dt) | Alpha |
|---|---|---|---|
| Health | 2 days | 1 day | ~0.2929 |
| Search | 2 weeks | 0.5 weeks (2×/week) | ~0.1591 |

Health scores react within 2 days; search scores adjust over ~2 weeks.

### Update Rule

```python
new_value = alpha * observation + (1 - alpha) * previous_value
```

### Confidence

Confidence measures how trustworthy a score is, based on sample count and recency:

```
sample_conf  = 1 - exp(-n_samples / k)          # k = 10
recency_conf = exp(-age_seconds / tau_conf)      # tau_conf = 4 weeks
confidence   = clamp(sample_conf * recency_conf, 0, 1)
```

### Final Score Composition

All sub-scores are normalized to 0.0–1.0:

```
final_score = wH * health_score + wS * search_score - wC * cost_penalty
```

Optionally discounted by confidence: `final_score *= (0.5 + 0.5 * confidence)`.

**Health score** combines:
- Reachability (binary: ok or not)
- Latency penalty (inverted speed score)

**Search score** combines:
- Success/timeout rate
- Latency (clamped `duration_ms`)
- Result quality: `min(items_found, limit) / limit`, hoster reachability ratio

---

## Probers

### HealthProber (daily)

Lightweight availability check for each plugin's origin domain.

| Aspect | Details |
|---|---|
| Method | HEAD request; fallback GET with `Range: bytes=0-0` on 405/501 |
| Target | Plugin base URL origin (scheme + host) |
| Timeout | 2–5 seconds |
| Concurrency | Semaphore-bounded to avoid system overload |
| Output | `ok`, `http_status`, `duration_ms`, `error_kind` |

### MiniSearchProber (2× per week)

Shallow search probe per (plugin, category, age bucket).

| Aspect | Details |
|---|---|
| Pipeline | Existing search path, limited to 1 page |
| Max items | 10–20 per plugin |
| Timeout | 5–10 seconds per plugin |
| Hoster sampling | HEAD-check up to 3–5 result links |
| Output | Full `ProbeResult` with items, latency, hoster reachability |
| Scope | Only categories the plugin declares; skip unknown |

---

## Query Planning & Rotation

### Age Buckets

| Bucket | Description |
|---|---|
| `current` | Recent releases (within ~1 year) |
| `y1_2` | 1–2 years old |
| `y5_10` | 5–10 years old |

### Query Pools

Static query lists per (category, bucket), e.g. 20–50 entries each. Rotation is deterministic per calendar week (seed = ISO week number) to ensure reproducibility.

Each probe selects 1–2 queries from the pool. Plugins are only probed for categories they declare; unknown categories use a fallback policy (skip or default set).

---

## Persistence (PluginScoreStore)

### Port Interface

```python
class PluginScoreStore(Protocol):
    async def get_snapshot(self, plugin: str, category: int, bucket: str) -> PluginScoreSnapshot | None: ...
    async def put_snapshot(self, snapshot: PluginScoreSnapshot) -> None: ...
    async def list_snapshots(self, plugin: str | None = None) -> list[PluginScoreSnapshot]: ...
    async def get_last_run(self, probe_key: ProbeKey) -> datetime | None: ...
    async def set_last_run(self, probe_key: ProbeKey, ts: datetime) -> None: ...
```

### Key Schema

| Key Pattern | Purpose |
|---|---|
| `score:{plugin}:{category}:{bucket}` | Score snapshot |
| `lastrun:health:{plugin}` | Last health probe timestamp |
| `lastrun:search:{plugin}:{category}:{bucket}` | Last search probe timestamp |
| `rotation:{bucket}:{category}` | Query rotation cursor |

Default backend: diskcache. Redis supported if already wired.

---

## Background Scheduler

An asyncio background task runs during app lifespan with a periodic "due?" check loop:

| Probe | Due Condition |
|---|---|
| Health | `last_run > 24 hours ago` |
| Search | 2× per week per (plugin, category, bucket), e.g. Mon/Thu |

Safeguards:
- Concurrency limits (semaphore) per probe type
- Backoff/cooldown on CAPTCHA or rate-limit responses
- Clean cancellation on app shutdown (lifespan integration)

---

## Live Integration (Stremio Router)

When scoring is enabled, the Stremio stream endpoint applies budget constraints:

| Parameter | Default | Description |
|---|---|---|
| `overall_deadline_ms` | 1500–3000 | Maximum wall-clock time for the entire search |
| `max_plugins` | 3–5 | Top-N plugins selected by `final_score` |
| `max_items_total` | 50 | Global result cap across all plugins |
| `max_items_per_plugin` | 20 | Per-plugin result cap |

Behavior:
1. Load `PluginScoreSnapshot` for all streaming plugins
2. Select top-N by `final_score` (with confidence weighting)
3. Search selected plugins in parallel with per-plugin timeout
4. **Early-stop** when enough items collected or deadline reached
5. Cancel remaining plugin tasks
6. Optional **exploration**: 10–20% chance to include a mid-score plugin

---

## Debug Endpoint

```
GET /api/v1/stats/plugin-scores
```

Returns current scoring state for all plugins:

| Field | Description |
|---|---|
| `final_score` | Composite score |
| `health_score` | Health EWMA value |
| `search_score` | Search EWMA value |
| `confidence` | Score trustworthiness |
| `last_probe_ts` | Last probe timestamp |
| `last_errors` | Recent error classifications |

Supports optional query filters: `?plugin=sto&category=5000&bucket=current`.

---

## Configuration

All settings are available via environment variables (pydantic-settings):

| Setting | Default | Description |
|---|---|---|
| `SCAVENGARR_SCORING_ENABLED` | `false` | Enable/disable the scoring system |
| `SCAVENGARR_SCORING_HEALTH_HALFLIFE_DAYS` | `2` | Health EWMA half-life |
| `SCAVENGARR_SCORING_SEARCH_HALFLIFE_WEEKS` | `2` | Search EWMA half-life |
| `SCAVENGARR_SCORING_HEALTH_INTERVAL_HOURS` | `24` | Hours between health probes |
| `SCAVENGARR_SCORING_SEARCH_RUNS_PER_WEEK` | `2` | Search probes per week |
| `SCAVENGARR_STREMIO_DEADLINE_MS` | `2000` | Stremio search deadline |
| `SCAVENGARR_STREMIO_MAX_PLUGINS` | `5` | Max plugins per Stremio request |
| `SCAVENGARR_STREMIO_MAX_ITEMS_TOTAL` | `50` | Global result cap |
| `SCAVENGARR_STREMIO_MAX_ITEMS_PER_PLUGIN` | `20` | Per-plugin result cap |

---

## Testing Strategy

### Unit Tests (pure logic)

- `alpha_from_halflife()` returns correct values for health (~0.2929) and search (~0.1591)
- `ewma_update()` increases/decreases correctly, stays clamped
- Confidence increases with samples, decays with age
- Score composition produces expected rankings

### Prober Tests (httpx mocked)

- HealthProber: HEAD success, HEAD 405 → GET fallback, timeout → `ok=False`
- MiniSearchProber: respects timeout and `max_items`, enforces hoster sampling limit

### Integration Tests (FastAPI)

- Stremio endpoint returns within deadline when plugins hang
- Plugin ranking follows `ScoreStore` ordering
- Early-stop triggers when enough items are collected

---

## Open Questions

- How should "hoster reachable" be validated — download-link domain, redirect target, or known hoster list?
- Should the Stremio endpoint return partial results when the deadline is reached?
- What are the ideal default deadlines for warm vs. cold requests?
- How should plugins without declared categories be handled (skip or probe with default set)?
