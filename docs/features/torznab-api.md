[← Back to Index](./README.md)

# Torznab API Reference

Scavengarr exposes a Torznab-compatible HTTP API designed for integration with
Prowlarr, Sonarr, Radarr, and other *Arr applications. All Torznab endpoints
return XML conforming to the RSS 2.0 + Torznab namespace specification.

**Default base URL:** `http://localhost:7979`

---

## Endpoints Overview

| Method | Path | Response | Description |
|--------|------|----------|-------------|
| `GET` | `/api/v1/torznab/indexers` | JSON | List all loaded plugins |
| `GET` | `/api/v1/torznab/{plugin_name}?t=caps` | XML | Plugin capabilities |
| `GET` | `/api/v1/torznab/{plugin_name}?t=search&q={query}` | XML | Search (RSS feed) |
| `GET` | `/api/v1/torznab/{plugin_name}/health` | JSON | Plugin health check |
| `GET` | `/api/v1/download/{job_id}` | File | Download `.crawljob` file |
| `GET` | `/api/v1/download/{job_id}/info` | JSON | CrawlJob metadata |
| `GET` | `/healthz` | JSON | Application health check |

---

## Torznab Endpoints

### List Indexers

```
GET /api/v1/torznab/indexers
```

Returns a JSON array of all available plugins discovered by the plugin registry.
Each entry includes the plugin name, version, and scraping mode.

**Response (200 OK):** `application/json`

```json
{
  "indexers": [
    {
      "name": "filmpalast",
      "version": "1.0.0",
      "mode": "scrapy"
    },
    {
      "name": "example-site",
      "version": "1.0.0",
      "mode": "playwright"
    }
  ]
}
```

The `mode` field indicates which scraping engine the plugin uses:
- `scrapy` -- static HTML scraping (fast, low resource usage)
- `playwright` -- JavaScript-heavy sites requiring browser rendering

This endpoint is useful for discovering available plugins before constructing
Torznab URLs for Prowlarr configuration.

---

### Capabilities (caps)

```
GET /api/v1/torznab/{plugin_name}?t=caps
```

Returns Torznab capabilities XML for the specified plugin. Prowlarr queries this
endpoint during indexer setup to determine supported search types and categories.

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `plugin_name` | string | yes | Plugin identifier (e.g., `filmpalast`) |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `t` | string | yes | Must be `caps` |

**Response (200 OK):** `application/xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
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

**Capabilities breakdown:**

| Element | Description |
|---------|-------------|
| `<server>` | Application name (including plugin) and version |
| `<limits>` | Maximum results per query (`max=100`) and default page size (`default=50`) |
| `<searching>` | Supported search types -- currently only free-text `q` parameter |
| `<categories>` | Torznab categories: `2000` (Movies), `5000` (TV), `8000` (Other) |

> **Note:** Movie-search (`imdbid`) and TV-search (`tvdbid`, `season`, `ep`) are
> not yet supported. Prowlarr falls back to free-text search automatically.

---

### Search

```
GET /api/v1/torznab/{plugin_name}?t=search&q={query}
```

Executes a multi-stage scrape against the target site and returns results as a
Torznab RSS 2.0 feed. This is the primary endpoint used by Prowlarr for indexer
searches.

**Path Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `plugin_name` | string | yes | Plugin identifier |

**Query Parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `t` | string | yes | Must be `search` |
| `q` | string | yes | Search query (URL-encoded) |
| `cat` | string | no | Category filter (e.g., `2000,5000`) |
| `extended` | int | no | Prowlarr extended flag (`1` = test mode) |

**Response (200 OK):** `application/xml`

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>scavengarr (filmpalast)</title>
    <description>Scavengarr Torznab feed</description>
    <link>http://localhost:7979/</link>
    <language>en-us</language>
    <item>
      <title>Iron.Man.2008.1080p.BluRay.x264</title>
      <link>http://localhost:7979/api/v1/download/abc123-def456</link>
      <guid isPermaLink="false">https://example.com/original-link</guid>
      <description>Iron.Man.2008.1080p.BluRay.x264</description>
      <pubDate>Mon, 01 Jan 2025 12:00:00 +0000</pubDate>
      <enclosure url="http://localhost:7979/api/v1/download/abc123-def456"
                 length="4831838208" type="application/x-crawljob"/>
      <torznab:attr name="category" value="2000"/>
      <torznab:attr name="size" value="4831838208"/>
      <torznab:attr name="seeders" value="10"/>
      <torznab:attr name="peers" value="2"/>
      <torznab:attr name="grabs" value="0"/>
      <torznab:attr name="downloadvolumefactor" value="0.0"/>
      <torznab:attr name="uploadvolumefactor" value="0.0"/>
      <torznab:attr name="minimumratio" value="0"/>
      <torznab:attr name="minimumseedtime" value="0"/>
    </item>
  </channel>
</rss>
```

**Search flow (what happens internally):**

1. The `TorznabSearchUseCase` loads the plugin from the registry.
2. The `SearchEngine` executes the plugin's multi-stage pipeline (search results, detail pages, link extraction).
3. Download links are validated in parallel via HEAD/GET probes.
4. Valid links are bundled into a `CrawlJob` and cached.
5. The presenter renders each result as an RSS `<item>` with the CrawlJob download URL as the `<link>` and `<enclosure>`.

**Key XML fields:**

| XML Element | Description |
|-------------|-------------|
| `<title>` | Release name (or title if no release name available) |
| `<link>` | Scavengarr download URL pointing to the CrawlJob endpoint |
| `<guid>` | Original source download URL (used for deduplication) |
| `<enclosure>` | Same as `<link>` with `type="application/x-crawljob"` and file size |
| `torznab:attr name="category"` | Torznab category (2000=Movies, 5000=TV) |
| `torznab:attr name="size"` | File size in bytes (parsed from human-readable size strings) |
| `torznab:attr name="seeders"` | Number of seeders (0 for direct downloads) |
| `torznab:attr name="peers"` | Number of peers (0 for direct downloads) |
| `torznab:attr name="downloadvolumefactor"` | `0.0` for direct downloads (freeleech equivalent) |
| `torznab:attr name="uploadvolumefactor"` | `0.0` for direct downloads |

> **Note:** Because Scavengarr handles direct download links (not torrents),
> `downloadvolumefactor` and `uploadvolumefactor` are set to `0.0` and
> `minimumratio`/`minimumseedtime` are set to `0`. This tells Prowlarr that
> no seeding requirements apply.

---

### Prowlarr Test Mode

When Prowlarr tests an indexer, it sends a special request:

```
GET /api/v1/torznab/{plugin_name}?t=search&extended=1
```

This request has `extended=1` but **no `q` parameter**. Scavengarr handles this
as a lightweight reachability probe rather than a full search:

1. The plugin's `base_url` is extracted from the plugin definition.
2. A lightweight HTTP probe is sent to the origin URL (`scheme://host/`).
3. The probe tries `HEAD` first (cheapest), falls back to a minimal `GET` with `Range: bytes=0-0` if `HEAD` returns 405/501.

**Outcomes:**

| Condition | Response | Status |
|-----------|----------|--------|
| Site reachable | One synthetic test item in RSS | 200 |
| Site unreachable | Empty RSS feed | 503 |
| Plugin has no `base_url` | Empty RSS feed | 422 |

The synthetic test item includes the plugin name and "reachable" in the title,
with zero-value metadata. This satisfies Prowlarr's test requirement without
performing an actual scrape.

**Missing query (without extended=1):**

If `q` is missing and `extended` is not set to `1`, the API returns an empty RSS
feed with HTTP 200. In non-production environments, the description will contain
"Missing query parameter 'q'".

---

### Plugin Health Check

```
GET /api/v1/torznab/{plugin_name}/health
```

Performs a lightweight HTTP reachability probe against the plugin's `base_url`.
Returns detailed JSON diagnostics including the actual URL checked, HTTP status
code, and mirror status.

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

**With mirrors (primary unreachable):**

```json
{
  "plugin": "example-site",
  "base_url": "https://example.org",
  "checked_url": "https://example.org/",
  "reachable": false,
  "status_code": null,
  "error": "Connection refused",
  "mirrors": [
    {
      "url": "https://mirror1.example.org",
      "reachable": true,
      "status_code": 200
    },
    {
      "url": "https://mirror2.example.org",
      "reachable": false,
      "error": "DNS resolution failed"
    }
  ]
}
```

When the primary `base_url` is unreachable and the plugin has `mirror_urls`
configured, the health endpoint probes each mirror and includes the results.
Mirror probes only run when the primary is down.

**Error responses:**

| Status | Condition |
|--------|-----------|
| 404 | Plugin not found |
| 422 | Plugin has no `base_url` |
| 500 | Internal error (dev/test only; returns 200 in prod) |

---

## Download Endpoints

### Download CrawlJob File

```
GET /api/v1/download/{job_id}
```

Serves a `.crawljob` file for JDownloader integration. This endpoint is called
automatically by Sonarr/Radarr when a user grabs a search result from Prowlarr.

**Path Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | string | CrawlJob UUID (from the RSS `<link>` element) |

**Response (200 OK):** `application/x-crawljob`

The response is a JDownloader-compatible `.crawljob` file containing validated
download links and metadata. Custom response headers provide additional context:

| Header | Description |
|--------|-------------|
| `Content-Disposition` | `attachment; filename="{name}_{job_id}.crawljob"` |
| `X-CrawlJob-ID` | The CrawlJob UUID |
| `X-CrawlJob-Package` | Package name (display name in JDownloader) |
| `X-CrawlJob-Links` | Number of validated download links in the job |

**Example `.crawljob` content:**

```ini
# Generated by Scavengarr
# Job ID: abc123-def456-...
# Created: 2025-01-01T12:00:00+00:00
# Expires: 2025-01-01T13:00:00+00:00

text=https://example.com/download/link1
https://example.com/download/link2
packageName=Iron.Man.2008
comment=Source: https://filmpalast.to/movie/iron-man
autoStart=TRUE
autoConfirm=UNSET
forcedStart=UNSET
enabled=TRUE
extractAfterDownload=UNSET
chunks=0
priority=DEFAULT
deepAnalyseEnabled=false
addOfflineLink=true
overwritePackagizerEnabled=false
setBeforePackagizerEnabled=false
```

**Error responses:**

| Status | Condition |
|--------|-----------|
| 404 | CrawlJob not found or expired |
| 500 | Repository or serialization failure |

CrawlJobs have a configurable TTL (default: 1 hour). After expiration, the
download endpoint returns 404. See [CrawlJob System](./crawljob-system.md) for
details on job lifecycle and multi-link packaging.

---

### CrawlJob Info

```
GET /api/v1/download/{job_id}/info
```

Returns CrawlJob metadata as JSON. Useful for debugging and inspecting cached
jobs without downloading the actual file.

**Response (200 OK):** `application/json`

```json
{
  "job_id": "abc123-def456-...",
  "package_name": "Iron.Man.2008",
  "created_at": "2025-01-01T12:00:00+00:00",
  "expires_at": "2025-01-01T13:00:00+00:00",
  "is_expired": false,
  "validated_urls": [
    "https://example.com/download/link1",
    "https://example.com/download/link2"
  ],
  "source_url": "https://filmpalast.to/movie/iron-man",
  "comment": "Source: https://filmpalast.to/movie/iron-man",
  "auto_start": "TRUE",
  "priority": "DEFAULT"
}
```

**Fields:**

| Field | Type | Description |
|-------|------|-------------|
| `job_id` | string | Unique CrawlJob identifier (UUID4) |
| `package_name` | string | Display name in JDownloader |
| `created_at` | ISO 8601 | Creation timestamp |
| `expires_at` | ISO 8601 | Expiration timestamp |
| `is_expired` | boolean | Whether the job has exceeded its TTL |
| `validated_urls` | string[] | List of validated download links |
| `source_url` | string | Original indexer page URL |
| `comment` | string | Human-readable description |
| `auto_start` | string | JDownloader auto-start flag (`TRUE`/`FALSE`/`UNSET`) |
| `priority` | string | JDownloader priority (`HIGHEST`/`HIGH`/`DEFAULT`/`LOWER`) |

---

## Application Health Check

```
GET /healthz
```

Simple health check endpoint for container orchestration (Docker health checks,
Kubernetes liveness probes).

**Response (200 OK):** `application/json`

```json
{"status": "ok"}
```

This endpoint does not check plugin availability or external site reachability.
For plugin-level health checks, use the [plugin health endpoint](#plugin-health-check).

---

## Error Handling

Scavengarr maps domain exceptions to HTTP status codes. Error behavior differs
between environments to keep Prowlarr stable in production.

### Exception Mapping

| Domain Exception | HTTP Status (dev/test) | HTTP Status (prod) | Description |
|------------------|----------------------|-------------------|-------------|
| `TorznabBadRequest` | 400 | 400 | Invalid query parameters |
| `TorznabPluginNotFound` | 404 | 404 | Unknown plugin name |
| `TorznabUnsupportedAction` | 422 | 422 | Action is not `caps` or `search` |
| `TorznabUnsupportedPlugin` | 422 | 422 | Plugin uses unsupported scraping mode |
| `TorznabNoPluginsAvailable` | 503 | 503 | No plugins loaded in registry |
| `TorznabExternalError` | 502 | **200** | Upstream scraping/network failures |
| Unhandled exceptions | 500 | **200** | Unexpected internal errors |

### Production Error Behavior

In production (`SCAVENGARR_ENVIRONMENT=prod`), Torznab endpoints return empty
RSS feeds with HTTP 200 for upstream and internal errors. This prevents Prowlarr
from marking the indexer as failed due to transient issues.

**Production error response:**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>scavengarr (filmpalast)</title>
    <description>Scavengarr Torznab feed</description>
    <link>http://localhost:7979/</link>
    <language>en-us</language>
  </channel>
</rss>
```

In development and test environments, error descriptions are included in the
RSS `<description>` element and actual HTTP status codes are returned, making
debugging easier.

---

## Torznab XML Format

Scavengarr generates RSS 2.0 XML with the Torznab namespace extension:

```
xmlns:torznab="http://torznab.com/schemas/2015/feed"
```

### Torznab Attributes

Each `<item>` includes `<torznab:attr>` elements for structured metadata:

| Attribute | Type | Description |
|-----------|------|-------------|
| `category` | int | Torznab category ID (2000, 5000, 8000) |
| `size` | int | File size in bytes |
| `seeders` | int | Seeder count (0 for direct downloads) |
| `peers` | int | Peer count (0 for direct downloads) |
| `grabs` | int | Download/grab count |
| `downloadvolumefactor` | float | Download ratio factor (0.0 = freeleech) |
| `uploadvolumefactor` | float | Upload ratio factor |
| `minimumratio` | float | Minimum seed ratio (always 0) |
| `minimumseedtime` | int | Minimum seed time in seconds (always 0) |

### GUID Strategy

The `<guid>` element contains the **original source download URL** (not the
Scavengarr download URL). This allows Prowlarr to deduplicate results across
multiple indexers that may point to the same source files.

### Enclosure Type

The `<enclosure>` element uses `type="application/x-crawljob"` to indicate that
the download URL serves a `.crawljob` file rather than a torrent. The `length`
attribute contains the file size in bytes (parsed from the scraped size string).

---

## Source Code References

| Component | File |
|-----------|------|
| Torznab router | `src/scavengarr/interfaces/api/torznab/router.py` |
| Download router | `src/scavengarr/interfaces/api/download/router.py` |
| XML presenter | `src/scavengarr/infrastructure/torznab/presenter.py` |
| Domain entities | `src/scavengarr/domain/entities/torznab.py` |
| CrawlJob entity | `src/scavengarr/domain/entities/crawljob.py` |
| Application state | `src/scavengarr/interfaces/app_state.py` |
| Search use case | `src/scavengarr/application/use_cases/torznab_search.py` |
| Caps use case | `src/scavengarr/application/use_cases/torznab_caps.py` |
| Indexers use case | `src/scavengarr/application/use_cases/torznab_indexers.py` |
