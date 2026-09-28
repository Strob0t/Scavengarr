[← Back to Index](./README.md)

# Plugin Scoring & Probing

> Data-driven plugin prioritization for Stremio stream resolution via background health and search probes.

---

## Overview

Without scoring, Scavengarr searches all streaming plugins for every Stremio request. Many plugins are slow, unreliable, or return few playable results for a given content category.

The **Plugin Scoring & Probing** system ranks plugins by measured performance. Background probes assess plugin health and search quality and feed an EWMA-based scoring model; when scored selection is enabled, the Stremio stream use case queries only the top-ranked plugins (plus an occasional exploration pick).

---

## Architecture

```text
Background Probes (ScoringScheduler)       Live Stremio Path
  ├── HealthProber (daily)                   GET /stream/{type}/{id}.json
  │     HEAD/GET plugin base_url → ok/fail     │
  │     → EwmaState (health)                   ├── Load PluginScoreSnapshots ("current" bucket)
  │                                            ├── Cold-start guard
  └── MiniSearchProber (2×/week)               ├── Select top-N by final_score
        query per (plugin, category, bucket)   ├── Exploration slot (random mid-score)
        → EwmaState (search)                   └── Search selected plugins → ranked streams
                │
                ▼
         PluginScoreStore (CachePort: diskcache / Redis)
```

The scheduler probes every plugin returned by `get_by_provides("stream")`, which includes `provides="both"` plugins. Probes cover the Torznab categories `2000` (movies) and `5000` (TV) and the three age buckets. A health probe updates the health EWMA of all six (category, bucket) snapshots of a plugin; a search probe updates one snapshot.

---

## Data Model

### ProbeResult

Raw output from a single probe run.

| Field | Type | Description |
|---|---|---|
| `started_at` | `datetime` | Probe start timestamp |
| `duration_ms` | `float` | Wall-clock duration |
| `ok` | `bool` | Whether the probe succeeded |
| `error_kind` | `str \| None` | `timeout`, `captcha`, `http_error` (health); `timeout`, `search_error`, `plugin_not_found` (search) |
| `http_status` | `int \| None` | HTTP response status |
| `captcha_detected` | `bool` | Cloudflare challenge detected |
| `items_found` | `int` | Raw search result count |
| `items_used` | `int` | `min(items_found, max_items)` |
| `hoster_checked` | `int` | Number of supported hoster URLs HEAD-checked |
| `hoster_reachable` | `int` | Reachable hosters from the sample |
| `hoster_supported` | `int` | Links pointing to supported hosters |
| `hoster_total` | `int` | Links with an extractable hoster name |

### EwmaState

Exponentially weighted moving average tracker.

| Field | Type | Description |
|---|---|---|
| `value` | `float` | Current EWMA value (0.0–1.0, initial 0.5) |
| `last_ts` | `datetime` | Timestamp of the last update |
| `n_samples` | `int` | Total samples incorporated |

### PluginScoreSnapshot

Composite score for a plugin within a (category, bucket) context.

| Field | Type | Description |
|---|---|---|
| `plugin` | `str` | Plugin name |
| `category` | `int` | Torznab category (`2000` = movies, `5000` = TV) |
| `bucket` | `AgeBucket` | `"current"`, `"y1_2"`, or `"y5_10"` |
| `health_score` | `EwmaState` | Health probe EWMA |
| `search_score` | `EwmaState` | Search probe EWMA |
| `final_score` | `float` | Weighted composite score (initial 0.5) |
| `confidence` | `float` | How trustworthy the score is (0.0–1.0, initial 0.0) |
| `updated_at` | `datetime` | Last snapshot update |

---

## EWMA Scoring

### Alpha Calculation

The smoothing factor `alpha` is derived from the probe interval and the half-life:

```text
alpha = 1 - 0.5 ^ (dt / half_life)
```

| Probe type | Half-life | Interval (`dt`) | Alpha |
|---|---|---|---|
| Health | 2 days (`health_halflife_days`) | `health_interval_hours / 24` = 1 day | ~0.2929 |
| Search | 2 weeks (`search_halflife_weeks`) | `7 / search_runs_per_week` = 3.5 days | ~0.1591 |

### Update Rule

```python
new_value = alpha * clamp(observation, 0, 1) + (1 - alpha) * previous_value
```

### Confidence

Confidence combines the sample count and recency of both EWMAs:

```text
n            = health.n_samples + search.n_samples
age_seconds  = now - max(health.last_ts, search.last_ts)
sample_conf  = 1 - exp(-n / k)                 # k = 10
recency_conf = exp(-age_seconds / tau)         # tau = 4 weeks
confidence   = clamp(sample_conf * recency_conf, 0, 1)
```

### Final Score Composition

All sub-scores are normalized to 0.0–1.0:

```text
raw         = w_health * health_score + w_search * search_score
final_score = clamp(raw * (0.5 + 0.5 * confidence), 0, 1)
```

Default weights: `w_health = 0.4`, `w_search = 0.6`.

**Health observation** (0.0–1.0):

- `0.0` if `captcha_detected` is `True`.
- Otherwise `0.7 * reachability + 0.3 * speed`, where reachability is `1.0` if `ok` else `0.0`, and speed is `1.0 - min(duration_ms / 10000, 1.0)`.

**Search observation** (0.0–1.0, 5 weighted components):

| Component | Formula | Weight |
|---|---|---|
| Success | `1.0` if `ok` else `0.0` | 0.20 |
| Speed | `1.0 - min(duration_ms / 10000, 1.0)` | 0.15 |
| Result quality | `min(items_found, limit) / limit` (`limit` = `search_max_items`) | 0.20 |
| Hoster reachability | `hoster_reachable / hoster_checked` (`1.0` if nothing was checked) | 0.20 |
| Supported-hoster ratio | `hoster_supported / hoster_total` (`0.0` if `hoster_total` is 0) | 0.25 |

---

## Probers

### HealthProber (daily)

Lightweight availability check for each plugin's `base_url`.

| Aspect | Details |
|---|---|
| Method | HEAD request (redirects followed); GET with `Range: bytes=0-0` on `405`/`501` |
| Target | The plugin's `base_url` as-is |
| Timeout | `health_timeout_seconds` (default 5 s) |
| Concurrency | Semaphore, `health_concurrency` (default 5) |
| Cloudflare detection | HEAD: `cf-ray` header + `403`/`503`; GET fallback: body-based `is_cloudflare_challenge()` |
| Success | Status `< 400` and no Cloudflare challenge |
| Output | `ok`, `http_status`, `duration_ms`, `error_kind`, `captcha_detected` |

When the prober encounters a Cloudflare challenge, `captcha_detected` is `True`, `ok` is `False`, and `error_kind` is `"captcha"`; `compute_health_observation()` then returns `0.0`.

### MiniSearchProber (2× per week)

Shallow search probe per (plugin, category, age bucket).

| Aspect | Details |
|---|---|
| Search | Direct `plugin.search(query, category=...)` call under `asyncio.wait_for`; no search engine or link validation |
| Timeout | `search_timeout_seconds` (default 10 s) |
| Items analysed | First `search_max_items` (default 20) results; the plugin's own pagination is not limited |
| Hoster classification | The second-level domain of each `download_link` is compared with `HosterResolverRegistry.supported_domains` (resolver names plus alias domains such as `filelions` for Vidhide) |
| Hoster sampling | HEAD-check (5 s timeout) up to 3 links to supported hosters; status `< 400` = reachable |
| Concurrency | Semaphore, `search_concurrency` (default 3) |
| Output | `ProbeResult` with items, latency, hoster reachability, supported-hoster counts |

---

## Query Planning & Rotation

### Age Buckets

| Bucket | Year range (inclusive) |
|---|---|
| `current` | current year − 1 … current year |
| `y1_2` | current year − 2 … current year − 1 |
| `y5_10` | current year − 10 … current year − 5 |

### Dynamic Query Pools (IMDB Suggest API)

`QueryPoolBuilder` builds query pools from the free IMDB Suggest API (no API key needed):

- Queries `https://v2.sg.media-imdb.com/suggestion/{letter}/{query}.json` for the letters `a`–`z` plus a fixed keyword list (e.g. `the`, `das`, `star`, `dark`), in a weekly shuffled order.
- Keeps entries of type `movie` (category `2000`) or `tvSeries`/`tvMiniSeries` (category `5000`) whose year falls into the bucket range.
- Pools are cached for 24 h under `querypool:{media}:{bucket}`. The query picked per probe is chosen by a shuffle seeded with the ISO week number, so rotation is deterministic within a week.

**Fallback:** if the IMDB Suggest API is unavailable, bundled lists of 10 popular movies and 10 popular series are used.

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

| Key pattern | Purpose |
|---|---|
| `score:{plugin}:{category}:{bucket}` | JSON-serialized `PluginScoreSnapshot` |
| `score:_index` | JSON list of all (plugin, category, bucket) triples |
| `lastrun:health:{plugin}` | Last health probe timestamp |
| `lastrun:search:{plugin}:{category}:{bucket}` | Last search probe timestamp |
| `querypool:{media}:{bucket}` | Cached IMDB query pool (24 h, written by `QueryPoolBuilder`) |

Score entries use `score_ttl_days` (default 30 days), so scores expire if probes stop running.

---

## Background Scheduler

`ScoringScheduler.run_forever()` runs as an asyncio task during the app lifespan (only when `scoring.enabled` is `true`):

| Probe | Due condition |
|---|---|
| Health | `last_run is None` or `now - last_run >= health_interval_hours` |
| Search | Per (plugin, category, bucket): `last_run is None` or at least `7 / search_runs_per_week` days since the last run |

Safeguards:

- 10-second initial delay after startup, then a tick every 5 minutes.
- Per-probe-type concurrency limits (semaphores).
- Exceptions in a tick are logged with traceback; the loop continues.
- Clean cancellation on app shutdown via `asyncio.CancelledError`.

---

## Live Integration (StremioStreamUseCase)

Scored selection is active when `stremio.scoring_enabled` is `true` **and** a score store exists, which requires `scoring.enabled: true`. Otherwise all plugins are searched.

| Parameter | Default | Description |
|---|---|---|
| `scoring_enabled` | `false` | Use scores to limit plugin selection |
| `max_plugins_scored` | `5` | Top-N plugins when scoring is active |
| `exploration_probability` | `0.15` | Chance to add one random mid-score plugin |
| `stremio_deadline_ms` | `2000` | Currently unused (no effect) |
| `max_items_total` | `50` | Currently unused (no effect) |
| `max_items_per_plugin` | `20` | Currently unused (no effect) |

Behavior (`_select_plugins()`):

1. Load the `"current"`-bucket snapshot of the request category for every `stream`/`both` plugin; a missing snapshot counts as score 0.5, confidence 0.0.
1. **Cold-start guard:** if fewer than 50% of plugins have `confidence > 0.1`, search all plugins.
1. Select the top `max_plugins_scored` plugins by `final_score` (descending).
1. **Exploration slot:** with probability `exploration_probability`, add one random plugin from the rest that has `confidence >= 0.1`.
1. Search the selected plugins and continue with title matching, ranking, and resolution as usual (see [Stremio Addon](./stremio-addon.md)).

---

## Per-Plugin Configuration

Plugin defaults can be overridden from YAML:

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
|---|---|---|---|
| Timeout | `timeout` | `_timeout` | 15.0 (httpx plugins) |
| Concurrency | `max_concurrent` | `_max_concurrent` | 5 |
| Max results | `max_results` | `_max_results` | 1000 |
| Enabled | `enabled` | (plugin removed from the registry) | `true` |

Overrides are applied right after `plugins.discover()` in the composition root. Unknown plugin names are logged as warnings. `PlaywrightPluginBase` defines no `_timeout` attribute, so a `timeout` override most likely has no effect on Playwright plugins.

---

## Debug Endpoint

```http
GET /api/v1/stats/plugin-scores?plugin=sto&category=5000&bucket=current
```

All query parameters are optional filters. The response lists snapshots sorted by `final_score` (descending), values rounded to 4 decimals:

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

Returns `503` with `{"error": "scoring_not_enabled"}` when scoring is not enabled.

---

## Configuration

### Scoring section (`scoring:` in YAML)

| Setting | YAML key | Env override | Default | Description |
|---|---|---|---|---|
| Enable scoring | `enabled` | `SCAVENGARR_SCORING_ENABLED` | `false` | Enable background probing and the score store |
| Health half-life | `health_halflife_days` | — | `2.0` | Health EWMA half-life (days) |
| Search half-life | `search_halflife_weeks` | — | `2.0` | Search EWMA half-life (weeks) |
| Health interval | `health_interval_hours` | — | `24.0` | Hours between health probes |
| Search frequency | `search_runs_per_week` | — | `2` | Search probes per week per (plugin, category, bucket) |
| Health timeout | `health_timeout_seconds` | — | `5.0` | Health probe timeout |
| Search timeout | `search_timeout_seconds` | — | `10.0` | Search probe timeout |
| Search max items | `search_max_items` | — | `20` | Results analysed per search probe |
| Health concurrency | `health_concurrency` | — | `5` | Parallel health probes |
| Search concurrency | `search_concurrency` | — | `3` | Parallel search probes |
| Score TTL | `score_ttl_days` | — | `30` | Score expiry (days) |
| Health weight | `w_health` | `SCAVENGARR_SCORING_W_HEALTH` | `0.4` | Health weight in the composite |
| Search weight | `w_search` | `SCAVENGARR_SCORING_W_SEARCH` | `0.6` | Search weight in the composite |

### Stremio selection keys (`stremio:` in YAML)

| Setting | YAML key | Default | Description |
|---|---|---|---|
| Use scores | `scoring_enabled` | `false` | Enable scored plugin selection (requires `scoring.enabled`) |
| Max plugins | `max_plugins_scored` | `5` | Top-N plugins per request |
| Exploration | `exploration_probability` | `0.15` | Mid-score plugin inclusion chance |
| Deadline | `stremio_deadline_ms` | `2000` | Currently unused (no effect) |
| Max items total | `max_items_total` | `50` | Currently unused (no effect) |
| Max per plugin | `max_items_per_plugin` | `20` | Currently unused (no effect) |

These keys have no dedicated environment variables.

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

| Test file | Coverage |
|---|---|
| `tests/unit/infrastructure/test_ewma.py` | Pure scoring functions (incl. captcha → 0.0) |
| `tests/unit/infrastructure/test_plugin_score_cache.py` | Cache persistence + index management |
| `tests/unit/infrastructure/test_query_pool.py` | IMDB Suggest query generation + fallback |
| `tests/unit/infrastructure/test_health_prober.py` | HEAD/GET probing incl. Cloudflare detection (`respx`) |
| `tests/unit/infrastructure/test_search_prober.py` | Plugin search + hoster checks |
| `tests/unit/infrastructure/test_scoring_scheduler.py` | Health/search cycles + tick |

Scored plugin selection (`_select_plugins()`) currently has no dedicated unit test.

```bash
poetry run pytest tests/unit/infrastructure/test_ewma.py \
  tests/unit/infrastructure/test_plugin_score_cache.py \
  tests/unit/infrastructure/test_query_pool.py \
  tests/unit/infrastructure/test_health_prober.py \
  tests/unit/infrastructure/test_search_prober.py \
  tests/unit/infrastructure/test_scoring_scheduler.py -v
```

---

## Source Code References

| Component | Path |
|---|---|
| Domain entities | `src/scavengarr/domain/entities/scoring.py` |
| Score store port | `src/scavengarr/domain/ports/plugin_score_store.py` |
| EWMA functions | `src/scavengarr/infrastructure/scoring/ewma.py` |
| Health prober | `src/scavengarr/infrastructure/scoring/health_prober.py` |
| Search prober | `src/scavengarr/infrastructure/scoring/search_prober.py` |
| Query pool | `src/scavengarr/infrastructure/scoring/query_pool.py` |
| Scheduler | `src/scavengarr/infrastructure/scoring/scheduler.py` |
| Cache store | `src/scavengarr/infrastructure/persistence/plugin_score_cache.py` |
| Config | `src/scavengarr/infrastructure/config/schema.py` (`ScoringConfig`, `StremioConfig`, `PluginOverride`) |
| Composition | `src/scavengarr/interfaces/composition.py` (`_wire_scoring`, `_apply_plugin_overrides`) |
| Use case | `src/scavengarr/application/use_cases/stremio_stream.py` (`_select_plugins`) |
| Debug API | `src/scavengarr/interfaces/api/stats/router.py` |
