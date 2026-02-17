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
  └── MiniSearchProber (2×/week)         ├── Search plugins (bounded concurrency)
        query per (category, bucket)     ├── Exploration slot (random mid-score)
        → EwmaState (search)             └── Return ranked streams
                │
                ▼
         PluginScoreStore (diskcache / Redis)
```

### Key Files

| Component | Path |
|-----------|------|
| Domain entities | `src/scavengarr/domain/entities/scoring.py` |
| Score store port | `src/scavengarr/domain/ports/plugin_score_store.py` |
| EWMA functions | `src/scavengarr/infrastructure/scoring/ewma.py` |
| Health prober | `src/scavengarr/infrastructure/scoring/health_prober.py` |
| Search prober | `src/scavengarr/infrastructure/scoring/search_prober.py` |
| Query pool | `src/scavengarr/infrastructure/scoring/query_pool.py` |
| Scheduler | `src/scavengarr/infrastructure/scoring/scheduler.py` |
| Cache store | `src/scavengarr/infrastructure/persistence/plugin_score_cache.py` |
| Config | `src/scavengarr/infrastructure/config/schema.py` (ScoringConfig) |
| Composition | `src/scavengarr/interfaces/composition.py` (_wire_scoring) |
| Use case | `src/scavengarr/application/use_cases/stremio_stream.py` (_select_plugins) |
| Debug API | `src/scavengarr/interfaces/api/stats/router.py` |

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
| `hoster_checked` | `int` | Number of supported hoster URLs HEAD-checked |
| `hoster_reachable` | `int` | Reachable hosters from sample |
| `hoster_supported` | `int` | Links pointing to hosters with registered resolvers |
| `hoster_total` | `int` | Total links with extractable hoster names |

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
| `plugin` | `str` | Plugin name |
| `category` | `int` | Torznab category (2000=movies, 5000=TV) |
| `bucket` | `AgeBucket` | Age bucket ("current", "y1_2", "y5_10") |
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
raw = wH * health_score + wS * search_score
final_score = raw * (0.5 + 0.5 * confidence)
```

Default weights: `w_health = 0.4`, `w_search = 0.6`.

**Health observation** (0.0–1.0):
- `1.0` if ok, `0.0` if not
- Latency penalty: `max(0, 1 - duration_ms / 5000)`
- Combined: `0.5 * reachability + 0.5 * speed`

**Search observation** (0.0–1.0, 5 components):
- Success rate (binary ok/fail) — weight 0.20
- Latency penalty (clamped `duration_ms / 10000`) — weight 0.15
- Result quality: `min(items_found, limit) / limit` — weight 0.20
- Hoster reachability ratio (only supported hosters are HEAD-checked) — weight 0.20
- Supported-hoster ratio: `hoster_supported / hoster_total` — weight 0.25

---

## Probers

### HealthProber (daily)

Lightweight availability check for each plugin's origin domain.

| Aspect | Details |
|---|---|
| Method | HEAD request; fallback GET with `Range: bytes=0-0` on 405/501 |
| Target | Plugin base URL origin (scheme + host) |
| Timeout | Configurable (default 5s) |
| Concurrency | Semaphore-bounded (default 5) |
| Output | `ok`, `http_status`, `duration_ms`, `error_kind` |

### MiniSearchProber (2× per week)

Shallow search probe per (plugin, category, age bucket).

| Aspect | Details |
|---|---|
| Pipeline | Existing plugin search path, limited scope |
| Max items | 20 per plugin (configurable) |
| Timeout | 10 seconds per plugin (configurable) |
| Hoster sampling | HEAD-check up to 3 result links (only supported hosters) |
| Supported-hoster filtering | Links are classified by whether their hoster has a registered resolver; only supported links are HEAD-checked |
| Output | Full `ProbeResult` with items, latency, hoster reachability, supported-hoster counts |

---

## Query Planning & Rotation

### Age Buckets

| Bucket | Description | Year Range |
|---|---|---|
| `current` | Recent releases | Current year ± 1 |
| `y1_2` | 1–2 years old | 1–2 years ago |
| `y5_10` | 5–10 years old | 5–10 years ago |

### Dynamic Query Pools (IMDB Suggest API)

Query pools are **automatically generated from the free IMDB Suggest API** — no API key needed. The `QueryPoolBuilder` uses:

- Queries `v2.sg.media-imdb.com/suggestion/{letter}/{query}.json` with a set of letter prefixes and keywords
- Filters results by content type (`movie`/`tvSeries`) and year range per bucket
- Results are cached for 24h. Rotation is deterministic per ISO week number (seeded shuffle) to ensure reproducibility.

**Fallback**: if the IMDB Suggest API is unavailable, bundled lists of well-known German titles are used (~10 titles per media type).

---

## Persistence (CachePluginScoreStore)

### Port Interface

```python
class PluginScoreStorePort(Protocol):
    async def get_snapshot(self, plugin: str, category: int, bucket: str) -> PluginScoreSnapshot | None: ...
    async def put_snapshot(self, snapshot: PluginScoreSnapshot) -> None: ...
    async def list_snapshots(self, plugin: str | None = None) -> list[PluginScoreSnapshot]: ...
    async def get_last_run(self, probe_type: str, plugin: str, category: int | None = None, bucket: str | None = None) -> datetime | None: ...
    async def set_last_run(self, probe_type: str, plugin: str, ts: datetime, category: int | None = None, bucket: str | None = None) -> None: ...
```

### Key Schema

| Key Pattern | Purpose |
|---|---|
| `score:{plugin}:{category}:{bucket}` | JSON-serialized PluginScoreSnapshot |
| `score:_index` | JSON list of all (plugin, category, bucket) triples |
| `lastrun:{probe_type}:{plugin}` | Last health probe timestamp |
| `lastrun:{probe_type}:{plugin}:{category}:{bucket}` | Last search probe timestamp |

Default TTL: 30 days (scores expire if probes stop running).

---

## Background Scheduler

An asyncio background task runs during app lifespan with a 5-minute tick loop:

| Probe | Due Condition |
|---|---|
| Health | `last_run is None` or `now - last_run >= health_interval_hours` |
| Search | Per (plugin, category, bucket): `7 / search_runs_per_week` days since last run |

Safeguards:
- 10-second initial delay after startup
- Per-probe-type concurrency limits (semaphore)
- Error logging with full traceback (no crash on individual probe failure)
- Clean cancellation on app shutdown via `asyncio.CancelledError`

---

## Live Integration (StremioStreamUseCase)

When `stremio.scoring_enabled` is True and a `score_store` is provided, the use case applies scored plugin selection:

| Parameter | Default | Description |
|---|---|---|
| `scoring_enabled` | `false` | Use scores to limit plugin selection |
| `stremio_deadline_ms` | `2000` | Overall deadline for stream search |
| `max_plugins_scored` | `5` | Top-N plugins when scoring is active |
| `max_items_total` | `50` | Global result cap across all plugins |
| `max_items_per_plugin` | `20` | Per-plugin result cap |
| `exploration_probability` | `0.15` | Chance to include a mid-score plugin |

Behavior:
1. Load `PluginScoreSnapshot` for all streaming plugins (category, "current" bucket)
2. **Cold-start guard**: if <50% of plugins have `confidence > 0.1`, fall back to all plugins
3. Select top-N by `final_score` (descending)
4. **Exploration slot**: with configured probability, add one random mid-score plugin (must have `confidence >= 0.1`)
5. Search selected plugins in parallel with per-plugin timeout
6. Apply title matching, sorting, probing, and resolution as normal

---

## Per-Plugin Configuration

Users can override plugin defaults from YAML:

```yaml
plugins:
  plugin_dir: ./plugins
  overrides:
    kinoger:
      timeout: 20.0
      max_concurrent: 5
      max_results: 500
      enabled: false
    filmpalast:
      timeout: 30.0
    sto:
      max_concurrent: 2
```

| Attribute | YAML key | Plugin attribute | Default |
|-----------|----------|------------------|---------|
| Timeout | `timeout` | `_timeout` | 15.0 |
| Concurrency | `max_concurrent` | `_max_concurrent` | 3 |
| Max results | `max_results` | `_max_results` | 1000 |
| Enabled | `enabled` | _(registry removal)_ | true |

Applied after `plugins.discover()` in the composition root. Unknown plugin names are logged as warnings.

---

## Debug Endpoint

```
GET /api/v1/stats/plugin-scores?plugin=sto&category=5000&bucket=current
```

Returns JSON with all scoring state:

```json
{
  "scores": [
    {
      "plugin": "sto",
      "category": 5000,
      "bucket": "current",
      "health_score": {"value": 0.85, "n_samples": 12, "last_ts": "..."},
      "search_score": {"value": 0.72, "n_samples": 8, "last_ts": "..."},
      "final_score": 0.78,
      "confidence": 0.65,
      "updated_at": "..."
    }
  ],
  "count": 1
}
```

Returns `503` when scoring is not enabled.

---

## Configuration

### Scoring section (`scoring:` in YAML)

| Setting | YAML Key | Env Override | Default | Description |
|---|---|---|---|---|
| Enable scoring | `enabled` | `SCAVENGARR_SCORING_ENABLED` | `false` | Enable background probing |
| Health half-life | `health_halflife_days` | — | `2.0` | Health EWMA half-life (days) |
| Search half-life | `search_halflife_weeks` | — | `2.0` | Search EWMA half-life (weeks) |
| Health interval | `health_interval_hours` | — | `24.0` | Hours between health probes |
| Search frequency | `search_runs_per_week` | — | `2` | Search probes per week |
| Health timeout | `health_timeout_seconds` | — | `5.0` | Health probe timeout |
| Search timeout | `search_timeout_seconds` | — | `10.0` | Search probe timeout |
| Search max items | `search_max_items` | — | `20` | Max items per search probe |
| Health concurrency | `health_concurrency` | — | `5` | Parallel health probes |
| Search concurrency | `search_concurrency` | — | `3` | Parallel search probes |
| Score TTL | `score_ttl_days` | — | `30` | Score expiry (days) |
| Health weight | `w_health` | `SCAVENGARR_SCORING_W_HEALTH` | `0.4` | Health weight in composite |
| Search weight | `w_search` | `SCAVENGARR_SCORING_W_SEARCH` | `0.6` | Search weight in composite |

### Stremio budget section (`stremio:` in YAML)

| Setting | YAML Key | Env Override | Default | Description |
|---|---|---|---|---|
| Use scores | `scoring_enabled` | `SCAVENGARR_STREMIO_SCORING_ENABLED` | `false` | Enable scored plugin selection |
| Deadline | `stremio_deadline_ms` | — | `2000` | Overall search deadline (ms) |
| Max plugins | `max_plugins_scored` | — | `5` | Top-N plugins per request |
| Max items total | `max_items_total` | — | `50` | Global result cap |
| Max per plugin | `max_items_per_plugin` | — | `20` | Per-plugin result cap |
| Exploration | `exploration_probability` | — | `0.15` | Mid-score plugin inclusion chance |

### Example YAML

```yaml
scoring:
  enabled: true
  health_halflife_days: 2.0
  search_halflife_weeks: 2.0
  w_health: 0.4
  w_search: 0.6

stremio:
  scoring_enabled: true
  stremio_deadline_ms: 2000
  max_plugins_scored: 5
  exploration_probability: 0.15

plugins:
  overrides:
    kinoger:
      timeout: 20.0
      enabled: false
```

---

## Testing

### Unit Tests

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `test_ewma.py` | 31 | All pure scoring functions |
| `test_plugin_score_cache.py` | 19 | Cache persistence + index management |
| `test_query_pool.py` | 14 | TMDB query generation + fallback |
| `test_health_prober.py` | 11 | HEAD/GET probing with respx mocks |
| `test_search_prober.py` | 8 | Plugin search + hoster checks |
| `test_scoring_scheduler.py` | 14 | Health/search cycles + tick |

### Running Tests

```bash
# All scoring tests:
poetry run pytest tests/unit/infrastructure/test_ewma.py \
  tests/unit/infrastructure/test_plugin_score_cache.py \
  tests/unit/infrastructure/test_query_pool.py \
  tests/unit/infrastructure/test_health_prober.py \
  tests/unit/infrastructure/test_search_prober.py \
  tests/unit/infrastructure/test_scoring_scheduler.py -v

# Full suite:
poetry run pytest
```
