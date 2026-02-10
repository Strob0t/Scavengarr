# API Reference

Scavengarr exposes a Torznab-compatible HTTP API designed for integration with
Prowlarr, Sonarr, Radarr, and other Arr applications.

Default base URL: `http://localhost:7979`

## Torznab Endpoints

### List Indexers

```
GET /api/v1/torznab/indexers
```

Returns a JSON list of all available plugins.

**Response (200):**
```json
{
  "indexers": [
    {
      "name": "filmpalast",
      "version": "1.0.0",
      "mode": "scrapy"
    }
  ]
}
```

### Capabilities

```
GET /api/v1/torznab/{plugin_name}?t=caps
```

Returns Torznab capabilities XML for the given plugin.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `plugin_name` | string | Plugin identifier (e.g. `filmpalast`) |

**Response (200):** `application/xml`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server title="scavengarr (filmpalast)" version="0.1.0"/>
  <limits max="100" default="50"/>
  <searching>
    <search available="yes" supportedParams="q"/>
    <movie-search available="no"/>
    <tv-search available="no"/>
  </searching>
  <categories>
    <category id="2000" name="Movies"/>
    <category id="5000" name="TV"/>
  </categories>
</caps>
```

### Search

```
GET /api/v1/torznab/{plugin_name}?t=search&q={query}
```

Executes a multi-stage scrape and returns Torznab RSS results.

**Query Parameters:**
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `t` | string | yes | Must be `search` |
| `q` | string | yes | Search query |
| `cat` | string | no | Category filter (e.g. `2000`) |
| `extended` | int | no | Prowlarr extended flag (`1` = test mode) |

**Response (200):** `application/xml`
```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:torznab="http://torznab.com/schemas/2015/feed">
  <channel>
    <title>scavengarr (filmpalast)</title>
    <item>
      <title>Iron.Man.2008.1080p.BluRay.x264</title>
      <link>http://localhost:7979/api/v1/download/abc123</link>
      <guid>https://example.com/original-link</guid>
      <enclosure url="http://localhost:7979/api/v1/download/abc123"
                 type="application/x-crawljob" length="0"/>
      <torznab:attr name="seeders" value="10"/>
      <torznab:attr name="peers" value="2"/>
      <torznab:attr name="size" value="4831838208"/>
      <torznab:attr name="category" value="2000"/>
      <torznab:attr name="downloadvolumefactor" value="0.0"/>
      <torznab:attr name="uploadvolumefactor" value="0.0"/>
    </item>
  </channel>
</rss>
```

**Error Responses:**
| Status | Condition |
|--------|-----------|
| 400 | Missing `q` parameter or invalid query |
| 404 | Plugin not found |
| 422 | Unsupported action or plugin mode |
| 503 | No plugins available |

### Prowlarr Test Mode

When Prowlarr tests an indexer, it sends `t=search&extended=1` without a query.
Scavengarr handles this by performing a lightweight HTTP probe against the plugin's
`base_url`:

- **Reachable:** Returns one synthetic test item (HTTP 200)
- **Unreachable:** Returns empty RSS (HTTP 503)
- **No base_url:** Returns empty RSS (HTTP 422)

### Plugin Health Check

```
GET /api/v1/torznab/{plugin_name}/health
```

Lightweight reachability probe for the plugin's domain.

**Response (200):**
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

## Download Endpoints

### Download CrawlJob File

```
GET /api/v1/download/{job_id}
```

Returns a `.crawljob` file for JDownloader integration. Called automatically by
Sonarr/Radarr when processing Torznab search results.

**Path Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `job_id` | string | CrawlJob UUID |

**Response (200):** `application/x-crawljob`

Response headers include:
- `Content-Disposition: attachment; filename="{name}_{job_id}.crawljob"`
- `X-CrawlJob-ID: {job_id}`
- `X-CrawlJob-Package: {package_name}`
- `X-CrawlJob-Links: {link_count}`

**Error Responses:**
| Status | Condition |
|--------|-----------|
| 404 | CrawlJob not found or expired |
| 500 | Repository or serialization failure |

### CrawlJob Info

```
GET /api/v1/download/{job_id}/info
```

Returns CrawlJob metadata as JSON (useful for debugging).

**Response (200):**
```json
{
  "job_id": "abc123-...",
  "package_name": "Iron.Man.2008",
  "created_at": "2025-01-01T12:00:00+00:00",
  "expires_at": "2025-01-01T13:00:00+00:00",
  "is_expired": false,
  "validated_urls": ["https://example.com/download/1"],
  "source_url": "https://example.com/movie/1",
  "comment": "Source: https://example.com/movie/1",
  "auto_start": "TRUE",
  "priority": "DEFAULT"
}
```

## Health Check

```
GET /healthz
```

**Response (200):**
```json
{"status": "ok"}
```

## Prowlarr Integration

To add Scavengarr as an indexer in Prowlarr:

1. Go to **Settings > Indexers > Add Indexer**
2. Select **Generic Torznab**
3. Configure:
   - **URL:** `http://<scavengarr-host>:7979/api/v1/torznab/<plugin_name>`
   - **API Key:** (leave empty, not required)
   - **Categories:** `2000` (Movies), `5000` (TV)
4. Click **Test** to verify connectivity
