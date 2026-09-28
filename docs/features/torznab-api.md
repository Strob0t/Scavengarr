[← Back to Index](./README.md)

# Torznab API Reference

> Endpoints, parameters, XML format and error behaviour of Scavengarr's Torznab API for Prowlarr and other Arr applications.

---

## Endpoints Overview

All routes are mounted under the `/api/v1` prefix. The default base URL is `http://localhost:7979` (`PORT` env var or `--port`).

| Method | Path | Response | Description |
|---|---|---|---|
| `GET` | `/api/v1/torznab/indexers` | JSON | List all discovered plugins |
| `GET` | `/api/v1/torznab/{plugin_name}?t=caps` | XML | Plugin capabilities |
| `GET` | `/api/v1/torznab/{plugin_name}?t=search&q={query}` | XML | Search (RSS feed) |
| `GET` | `/api/v1/torznab/{plugin_name}/health` | JSON | Plugin reachability check |
| `GET` | `/api/v1/download/{job_id}` | File | Download `.crawljob` file |
| `GET` | `/api/v1/download/{job_id}/info` | JSON | CrawlJob metadata |
| `GET` | `/api/v1/healthz` | JSON | Liveness probe |
| `GET` | `/api/v1/readyz` | JSON | Readiness probe |

The Stremio addon endpoints (`/api/v1/stremio/...`) are documented in [Stremio Addon](./stremio-addon.md); the stats endpoints (`/api/v1/stats/...`) in [Plugin Scoring & Probing](./plugin-scoring-and-probing.md).

---

## Torznab Endpoints

### List Indexers

```http
GET /api/v1/torznab/indexers
```

Returns a JSON object with an `indexers` array containing every plugin the registry discovered (plugins disabled via `plugins.overrides.<name>.enabled: false` are removed at startup). Each plugin is loaded to read its `version` and `mode` class attributes; if loading fails, both fields are `null`.

**Response (200 OK):** `application/json`

```json
{
  "indexers": [
    {"name": "filmpalast", "version": "1.0.0", "mode": "httpx"},
    {"name": "animeloads", "version": "1.0.0", "mode": "playwright"}
  ]
}
```

The `mode` field indicates the plugin's base class:

- `httpx` — `HttpxPluginBase`, static HTML scraping
- `playwright` — `PlaywrightPluginBase`, JavaScript-heavy sites requiring browser rendering

### Capabilities (caps)

```http
GET /api/v1/torznab/{plugin_name}?t=caps
```

Returns Torznab capabilities XML for the plugin. Prowlarr queries this endpoint during indexer setup.

| Parameter | In | Required | Description |
|---|---|---|---|
| `plugin_name` | path | yes | Plugin identifier (e.g. `filmpalast`) |
| `t` | query | yes | Must be `caps` |

**Response (200 OK):** `application/xml`

```xml
<?xml version='1.0' encoding='utf-8'?>
<caps>
  <server title="scavengarr (filmpalast)" version="0.1.0"/>
  <limits max="100" default="50"/>
  <searching>
    <search available="yes" supportedParams="q"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"/>
    <category id="5000" name="TV"/>
    <category id="8000" name="Other"/>
  </categories>
</caps>
```

| Element | Description |
|---|---|
| `<server>` | `{app_name} ({plugin.name})`; version is hardcoded to `0.1.0` |
| `<limits>` | `max=100`, `default=50` (from `TorznabCaps`) |
| `<searching>` | Only free-text search (`supportedParams="q"`) |
| `<categories>` | Always `2000`, `5000`, `8000` — identical for every plugin; plugin-specific (sub)categories are not advertised |

Movie-search (`imdbid`) and TV-search (`tvdbid`, `season`, `ep`) are not supported. An unknown plugin returns an empty RSS feed (not a caps document) with HTTP 404.

> **Known issue:** caps advertises `default="50"`, but the search endpoint's `limit` parameter defaults to `100`.

### Search

```http
GET /api/v1/torznab/{plugin_name}?t=search&q={query}
```

Runs the plugin's search and returns the results as a Torznab RSS 2.0 feed.

| Parameter | In | Required | Description |
|---|---|---|---|
| `plugin_name` | path | yes | Plugin identifier |
| `t` | query | yes | Must be `search` (any other value except `caps` → 422) |
| `q` | query | no | Search query; if missing, see [Prowlarr Test Mode](#prowlarr-test-mode) |
| `cat` | query | no | Comma-separated category IDs; only the first ID is passed to the plugin |
| `extended` | query | no | Only evaluated when `q` is missing (`1` = reachability probe) |
| `offset` | query | no | Result offset (default `0`) |
| `limit` | query | no | Maximum results returned (default `100`) |

> **Known issue:** a non-numeric `cat` value (e.g. `cat=abc`) raises an unhandled `ValueError`, which returns HTTP 500 in dev/test (an empty feed with HTTP 200 in prod).

**Response (200 OK):** `application/xml`, plus an `X-Cache: HIT|MISS` header indicating whether the plugin results came from the search cache.

```xml
<?xml version='1.0' encoding='utf-8'?>
<rss xmlns:torznab="http://torznab.com/schemas/2015/feed" version="2.0">
  <channel>
    <title>scavengarr (filmpalast)</title>
    <description>Scavengarr Torznab feed</description>
    <link>http://localhost:7979/</link>
    <language>en-us</language>
    <item>
      <title>Iron.Man.2008.1080p.BluRay.x264</title>
      <guid isPermaLink="false">https://hoster.example/file/abc</guid>
      <link>http://localhost:7979/api/v1/download/3f2b...-uuid</link>
      <description>Iron.Man.2008.1080p.BluRay.x264</description>
      <pubDate>Mon, 01 Jan 2025 12:00:00 +0000</pubDate>
      <torznab:attr name="category" value="2000"/>
      <torznab:attr name="size" value="4831838208"/>
      <torznab:attr name="seeders" value="0"/>
      <torznab:attr name="peers" value="0"/>
      <torznab:attr name="grabs" value="0"/>
      <torznab:attr name="downloadvolumefactor" value="0.0"/>
      <torznab:attr name="uploadvolumefactor" value="0.0"/>
      <torznab:attr name="minimumratio" value="0"/>
      <torznab:attr name="minimumseedtime" value="0"/>
      <enclosure url="http://localhost:7979/api/v1/download/3f2b...-uuid" length="4831838208" type="application/x-crawljob"/>
    </item>
  </channel>
</rss>
```

**Search flow (what happens internally):**

1. `TorznabSearchUseCase` resolves the plugin from the registry and looks up the search cache (key `search:<sha256(plugin:query:category)>`).
1. On a cache miss it calls `plugin.search(query, category=...)`; the plugin performs its own multi-stage scraping (search → detail → links).
1. `HttpxSearchEngine.validate_results()` validates the download links in parallel (see [Link Validation](./link-validation.md)). Non-empty results are written to the search cache with TTL `cache.search_ttl_seconds` (default `900`, `0` disables caching) or the plugin's `cache_ttl` attribute if set.
1. `CrawlJobFactory` turns every result into its own `CrawlJob`; all jobs are saved to the repository in parallel.
1. The item list is sliced to `[offset : offset + limit]` and rendered by the presenter.

**Key XML fields:**

| XML element | Description |
|---|---|
| `<title>` | `release_name`, falling back to `title` |
| `<guid>` | The result's primary download URL (after link promotion), used for deduplication |
| `<link>` | `{base_url}api/v1/download/{job_id}` (the CrawlJob endpoint) |
| `<description>` | Result description, falling back to `title` |
| `<pubDate>` | Time the response was rendered — not the release date |
| `<enclosure>` | Same URL as `<link>`, `length` = size in bytes, `type="application/x-crawljob"` |

**Pagination:** the use case builds the full item list (and creates CrawlJobs for all results), then applies `items[offset : offset + limit]`. Repeated requests for other pages hit the search cache.

```http
GET /api/v1/torznab/filmpalast?t=search&q=iron+man&offset=100&limit=100
```

### Prowlarr Test Mode

A search request with `extended=1` and **no** `q` parameter is handled as a lightweight reachability probe instead of a full search:

1. The plugin's `base_url` attribute is read.
1. The origin URL (`scheme://host/`) is probed with `HEAD` (5 s timeout, redirects followed).
1. If `HEAD` returns 405 or 501, a streamed `GET` with `Range: bytes=0-0` is sent instead.

The site counts as reachable when any HTTP response arrives — the status code is not evaluated.

| Condition | Response | Status |
|---|---|---|
| Site reachable | RSS with one synthetic item titled `{app_name} ({plugin}) - reachable` | 200 |
| Site unreachable (DNS/TCP/TLS/timeout) | Empty RSS feed | 503 |
| Plugin has no `base_url` | Empty RSS feed | 422 |
| Unknown plugin | Empty RSS feed | 500 (dev/test), 200 (prod) |

> **Known issue:** an unknown plugin name on the probe returns 500 (dev/test) / 200 (prod) instead of 404, because the registry raises `PluginNotFoundError`, which the router does not map.

If `q` is missing and `extended` is not `1`, the API returns an empty RSS feed with HTTP 200. Outside prod, the channel description reads `Missing query parameter 'q'`.

### Plugin Health Check

```http
GET /api/v1/torznab/{plugin_name}/health
```

Runs the same lightweight probe as the test mode against the plugin's `base_url` and returns JSON diagnostics. `reachable` is `true` for any HTTP response, regardless of status code.

**Response (200 OK):** `application/json`

```json
{
  "plugin": "filmpalast",
  "base_url": "https://filmpalast.to",
  "checked_url": "https://filmpalast.to/",
  "reachable": true,
  "status_code": 200,
  "error": null
}
```

The response gains a `mirrors` key only if the plugin sets a `mirror_urls` attribute (mirrors are probed only when the primary is unreachable); no plugin currently does — plugins declare fallback domains via `_domains` instead (see [Mirror URL Fallback](./mirror-url-fallback.md)).

| Status | Condition |
|---|---|
| 200 | Probe executed (check `reachable`) |
| 422 | Plugin has no `base_url` |
| 500 (dev/test) / 200 (prod) | Unknown plugin or other registry error; body `{"plugin": ..., "reachable": false, "error": ...}` |

> **Known issue:** an unknown plugin name returns 500 (dev/test) / 200 (prod) instead of 404, because the registry raises `PluginNotFoundError` rather than `TorznabPluginNotFound`.

---

## Download Endpoints

### Download CrawlJob File

```http
GET /api/v1/download/{job_id}
```

Serves the `.crawljob` file referenced by a search result's `<link>`. See [CrawlJob System](./crawljob-system.md) for the job lifecycle and file format.

**Response (200 OK):** `application/x-crawljob`

| Header | Description |
|---|---|
| `Content-Disposition` | `attachment; filename="{package_name}_{job_id[:8]}.crawljob"` (characters other than letters, digits, space, `-`, `_` in the package name become `_`) |
| `X-CrawlJob-ID` | The CrawlJob UUID |
| `X-CrawlJob-Package` | Package name (display name in JDownloader) |
| `X-CrawlJob-Links` | Number of links in the job |

| Status | Condition |
|---|---|
| 404 | CrawlJob not found or expired |
| 500 | Repository or serialization failure |

CrawlJobs expire 1 hour after the search that created them; this TTL is fixed in the composition root and not configurable.

### CrawlJob Info

```http
GET /api/v1/download/{job_id}/info
```

Returns CrawlJob metadata as JSON for debugging. Expired jobs are still returned (with `"is_expired": true`) as long as they are in the cache.

**Response (200 OK):** `application/json`

```json
{
  "job_id": "3f2b...-uuid",
  "package_name": "Iron Man",
  "created_at": "2025-01-01T12:00:00+00:00",
  "expires_at": "2025-01-01T13:00:00+00:00",
  "is_expired": false,
  "validated_urls": [
    "https://hoster1.example/file/abc",
    "https://hoster2.example/file/def"
  ],
  "source_url": "https://filmpalast.to/stream/iron-man",
  "comment": "Size: 4.5 GB | Source: https://filmpalast.to/stream/iron-man",
  "auto_start": "TRUE",
  "priority": "DEFAULT"
}
```

| Field | Type | Description |
|---|---|---|
| `job_id` | string | CrawlJob UUID4 |
| `package_name` | string | Display name in JDownloader |
| `created_at` | ISO 8601 | Creation timestamp |
| `expires_at` | ISO 8601 | Expiration timestamp |
| `is_expired` | boolean | Whether `now > expires_at` |
| `validated_urls` | string[] | Download links in the job |
| `source_url` | string \| null | Original detail page URL |
| `comment` | string \| null | Human-readable description |
| `auto_start` | string | `TRUE` / `FALSE` / `UNSET` |
| `priority` | string | `HIGHEST` / `HIGHER` / `HIGH` / `DEFAULT` / `LOWER` |

| Status | Condition |
|---|---|
| 404 | CrawlJob not found |
| 500 | Repository error |

---

## Application Health

### Liveness

```http
GET /api/v1/healthz
```

Returns 200 as long as the process runs. It does not check plugin or site reachability — use the [plugin health endpoint](#plugin-health-check) for that.

```json
{"status": "ok", "plugins": 42, "hosters": ["voe", "streamtape", "..."]}
```

`plugins` is the number of discovered plugins, `hosters` the list of supported hoster resolvers.

### Readiness

```http
GET /api/v1/readyz
```

Returns `{"status": "ready"}` with 200 once application startup has completed, otherwise `{"status": "not_ready"}` with 503.

---

## Rate Limiting

All endpoints are protected by a per-client-IP sliding-window rate limit. The default is `120` requests per minute, configured via `http.api_rate_limit_rpm` (YAML) or `SCAVENGARR_API_RATE_LIMIT_RPM`; `0` disables it.

When the limit is exceeded, the API returns HTTP 429 with a JSON body (also for Torznab endpoints, which otherwise return XML):

```json
{"error": "Rate limit exceeded", "retry_after_seconds": 60}
```

The response carries a `Retry-After: 60` header.

---

## Error Handling

Scavengarr maps domain exceptions on the Torznab endpoint (`/api/v1/torznab/{plugin_name}`) to HTTP status codes. Every error response is an RSS feed without items.

### Exception Mapping

| Exception | Status (dev/test) | Status (prod) | Trigger |
|---|---|---|---|
| `TorznabBadRequest` | 400 | 400 | Invalid query (defensive; the router handles missing `q` before the use case) |
| `TorznabPluginNotFound` | 404 | 404 | Unknown plugin on `t=caps` or `t=search` with `q` |
| `TorznabUnsupportedAction` | 422 | 422 | `t` is neither `caps` nor `search` |
| `TorznabExternalError` | 502 | **200** | Plugin search or link validation failed |
| Any other exception | 500 | **200** | Unexpected internal error (incl. unknown plugin on the probe, non-numeric `cat`) |

### Production Error Behavior

In production (`SCAVENGARR_ENVIRONMENT=prod`), upstream and internal errors return an empty RSS feed with HTTP 200, so Prowlarr does not mark the indexer as failed because of transient issues. In prod the channel description is always the default text:

```xml
<?xml version='1.0' encoding='utf-8'?>
<rss version="2.0">
  <channel>
    <title>scavengarr (filmpalast)</title>
    <description>Scavengarr Torznab feed</description>
    <link>http://localhost:7979/</link>
    <language>en-us</language>
  </channel>
</rss>
```

In dev and test, the error message is placed in the channel `<description>` and the real status code is returned.

---

## Torznab XML Format

Scavengarr generates RSS 2.0 with the Torznab namespace `http://torznab.com/schemas/2015/feed` (prefix `torznab`). The namespace is only declared when the feed contains items.

### Torznab Attributes

Each `<item>` contains these `<torznab:attr>` elements:

| Attribute | Type | Source |
|---|---|---|
| `category` | int | `SearchResult.category` as set by the plugin (e.g. `2000`, `5000`, `5070`); default `2000` |
| `size` | int | Size string parsed to bytes (`0` if unknown) |
| `seeders` | int | `SearchResult.seeders`, `0` if unset |
| `peers` | int | `SearchResult.leechers`, `0` if unset |
| `grabs` | int | `0` |
| `downloadvolumefactor` | float | `0.0` (direct download, freeleech equivalent) |
| `uploadvolumefactor` | float | `0.0` |
| `minimumratio` | int | Always `0` |
| `minimumseedtime` | int | Always `0` |

Because Scavengarr serves direct download links, not torrents, the volume factors and seeding requirements tell Prowlarr that no seeding applies.

### GUID Strategy

The `<guid>` holds the result's primary download URL, not the Scavengarr download URL. This lets Prowlarr deduplicate results across indexers that point to the same file.

### Enclosure Type

The `<enclosure>` uses `type="application/x-crawljob"` to indicate that the URL serves a `.crawljob` file rather than a torrent.

---

## Source Code References

| Component | Path |
|---|---|
| Torznab router | `src/scavengarr/interfaces/api/torznab/router.py` |
| Download router | `src/scavengarr/interfaces/api/download/router.py` |
| App factory (`healthz`, `readyz`, routers) | `src/scavengarr/interfaces/app.py` |
| API rate limit middleware | `src/scavengarr/interfaces/api/middleware.py` |
| XML presenter | `src/scavengarr/infrastructure/torznab/presenter.py` |
| Torznab entities and exceptions | `src/scavengarr/domain/entities/torznab.py` |
| CrawlJob entity | `src/scavengarr/domain/entities/crawljob.py` |
| Search use case | `src/scavengarr/application/use_cases/torznab_search.py` |
| Caps use case | `src/scavengarr/application/use_cases/torznab_caps.py` |
| Indexers use case | `src/scavengarr/application/use_cases/torznab_indexers.py` |
