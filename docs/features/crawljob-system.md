[← Back to Index](./README.md)

# CrawlJob System

The CrawlJob system bridges Scavengarr's Torznab search results with JDownloader.
When a search returns results, each result is converted into a `CrawlJob` entity that
bundles validated download links in JDownloader's `.crawljob` format. This enables
Sonarr, Radarr, and other Arr applications to trigger downloads through JDownloader
without manual intervention.

---

## Overview

The CrawlJob system operates as a download indirection layer:

```
┌──────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  Torznab Search  │     │  CrawlJob Cache  │     │  Arr Application │
│                  │     │                  │     │  (Sonarr/Radarr) │
│  SearchResult ───┼──→  │  CrawlJob stored │     │                  │
│  with validated  │     │  (TTL: 1 hour)   │     │  Requests        │
│  download links  │     │                  │  ←──┤  /download/{id}  │
└──────────────────┘     │  Serves .crawljob│──→  │                  │
                         └─────────────────┘     └──────────┬───────┘
                                                            │
                                                            ▼
                                                 ┌──────────────────┐
                                                 │  JDownloader     │
                                                 │  (FolderWatch)   │
                                                 │  Processes       │
                                                 │  .crawljob file  │
                                                 └──────────────────┘
```

**Why CrawlJobs?**

Torznab's `<link>` element expects a URL that returns a downloadable file. Since
Scavengarr indexes streaming and one-click hoster sites (not torrent trackers), it
cannot provide a direct `.torrent` or magnet link. Instead, it serves a `.crawljob`
file that JDownloader processes via its FolderWatch extension.

---

## End-to-End Flow

1. **Search** -- Torznab `?t=search&q=...` returns `SearchResult` objects with download links
2. **Link Validation** -- Dead/unreachable links are filtered out via parallel HEAD/GET requests (see [Link Validation](./link-validation.md))
3. **CrawlJob Creation** -- `CrawlJobFactory` converts each `SearchResult` into a `CrawlJob` with validated links and JDownloader metadata
4. **Cache Storage** -- `CacheCrawlJobRepository` stores the job with key `crawljob:{job_id}` and a 1-hour TTL
5. **Torznab XML** -- The response includes `<link>` pointing to `/api/v1/download/{job_id}`
6. **Arr Download** -- Sonarr/Radarr request the download URL when the user grabs a result
7. **File Delivery** -- The download endpoint serves the serialized `.crawljob` file

```
Prowlarr/Radarr                    Scavengarr
     │                                  │
     │  GET /api/v1/download/{job_id}   │
     │─────────────────────────────────→│
     │                                  │── Lookup in cache
     │                                  │── Check expiry
     │                                  │── Serialize to .crawljob
     │  200 OK (application/x-crawljob) │
     │←─────────────────────────────────│
     │                                  │
     │  Save to JDownloader watch dir   │
     │                                  │
```

---

## CrawlJob Entity

The `CrawlJob` dataclass models a JDownloader `.crawljob` file with all fields from
the `CrawlJobStorable` format.

### Core Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `job_id` | `str` | UUID4 (auto) | Unique identifier |
| `text` | `str` | `""` | Newline-separated download links (required by JDownloader) |
| `package_name` | `str` | `"Scavengarr Download"` | Display name in JDownloader package list |
| `filename` | `str \| None` | `None` | Override filename |
| `comment` | `str \| None` | `None` | Human-readable description |

### Validation Metadata (Scavengarr-specific)

| Field | Type | Default | Description |
|---|---|---|---|
| `validated_urls` | `list[str]` | `[]` | Validated download links |
| `source_url` | `str \| None` | `None` | Original indexer page URL |
| `created_at` | `datetime` | `now(UTC)` | Creation timestamp |
| `expires_at` | `datetime` | `now(UTC) + 1h` | Expiration timestamp |

### JDownloader Behavior Flags

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_start` | `BooleanStatus` | `TRUE` | Auto-start download when added |
| `auto_confirm` | `BooleanStatus` | `UNSET` | Skip confirmation dialogs |
| `forced_start` | `BooleanStatus` | `UNSET` | Force-start download |
| `enabled` | `BooleanStatus` | `TRUE` | Enable the download entry |
| `extract_after_download` | `BooleanStatus` | `UNSET` | Auto-extract archives |

### Download Configuration

| Field | Type | Default | Description |
|---|---|---|---|
| `download_folder` | `str \| None` | `None` | Custom download directory |
| `chunks` | `int` | `0` | Max parallel chunks (0 = JDownloader default) |
| `priority` | `Priority` | `DEFAULT` | Download priority: HIGHEST, HIGHER, HIGH, DEFAULT, LOWER |

### Archive/Security

| Field | Type | Default | Description |
|---|---|---|---|
| `extract_passwords` | `list[str]` | `[]` | Archive passwords (JSON array format) |
| `download_password` | `str \| None` | `None` | Password for protected links |

### Advanced Options

| Field | Type | Default | Description |
|---|---|---|---|
| `deep_analyse_enabled` | `bool` | `False` | Deep link analysis |
| `add_offline_link` | `bool` | `True` | Add link even if offline |
| `overwrite_packagizer_enabled` | `bool` | `False` | Override packagizer settings |
| `set_before_packagizer_enabled` | `bool` | `False` | Set values before packagizer runs |

---

## Enums

### `BooleanStatus`

JDownloader uses a tri-state boolean for behavior flags:

```python
# src/scavengarr/domain/entities/crawljob.py
class BooleanStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNSET = "UNSET"  # Use JDownloader's own default
```

### `Priority`

Download priority levels:

```python
# src/scavengarr/domain/entities/crawljob.py
class Priority(str, Enum):
    HIGHEST = "HIGHEST"
    HIGHER = "HIGHER"
    HIGH = "HIGH"
    DEFAULT = "DEFAULT"
    LOWER = "LOWER"
```

---

## `.crawljob` File Format

The `to_crawljob_format()` method serializes a `CrawlJob` to JDownloader's
key-value property file format, compatible with the
[FolderWatch extension](https://board.jdownloader.org/showthread.php?t=58281).

### Example Output

```properties
# Generated by Scavengarr
# Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
# Created: 2025-01-15T12:00:00+00:00
# Expires: 2025-01-15T13:00:00+00:00

text=https://example.com/download/file1
https://example.com/download/file2
packageName=Iron.Man.2008.1080p.BluRay
filename=Iron.Man.2008.1080p.BluRay
comment=Size: 4.2 GB | Source: https://indexer.example.com/movie/123
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

### Multi-Link Packaging

A key feature of CrawlJobs is **multi-link packaging**. When a search result has
multiple validated download links (e.g., from different hosters), all links are
bundled into a single `.crawljob` file:

```
text=https://hoster1.com/file/abc123
https://hoster2.com/file/def456
https://hoster3.com/file/ghi789
```

JDownloader processes all links in the job, providing redundancy if one hoster is slow
or offline.

---

## CrawlJobFactory

The factory converts `SearchResult` objects into `CrawlJob` entities.

### Configuration

| Setting | Default | Description |
|---|---|---|
| `default_ttl_hours` | `1` | Job expiration time in hours |
| `auto_start` | `True` | JDownloader auto-start flag |
| `default_priority` | `Priority.DEFAULT` | Download priority |

### Field Mapping

| SearchResult Field | CrawlJob Field | Notes |
|---|---|---|
| `title` | `package_name` | Fallback: `"Scavengarr Download"` |
| `validated_links` | `text` | Joined with `\r\n` (Windows line endings for JD compatibility) |
| `validated_links` | `validated_urls` | Direct copy |
| `source_url` | `source_url` | Original detail page URL |
| `release_name` | `filename` | Override filename if present |
| `description`, `size`, `source_url` | `comment` | Formatted: `"desc \| Size: X \| Source: URL"` |

### Usage

```python
# src/scavengarr/application/factories/crawljob_factory.py
factory = CrawlJobFactory(
    default_ttl_hours=1,
    auto_start=True,
    default_priority=Priority.DEFAULT,
)

crawl_job = factory.create_from_search_result(search_result)
```

---

## Storage

CrawlJobs are persisted via the `CrawlJobRepository` port, implemented by
`CacheCrawlJobRepository`.

### Storage Details

| Property | Value |
|---|---|
| Key format | `crawljob:{job_id}` |
| Serialization | Python `pickle` |
| TTL | 3600 seconds (1 hour) |
| Backend | Diskcache (SQLite) or Redis |

### Repository Protocol

```python
# src/scavengarr/domain/ports/crawljob_repository.py
class CrawlJobRepository(Protocol):
    async def save(self, job: CrawlJob) -> None: ...
    async def get(self, job_id: str) -> CrawlJob | None: ...
```

### Cache Implementation

```python
# src/scavengarr/infrastructure/persistence/crawljob_cache.py
class CacheCrawlJobRepository:
    async def save(self, job: CrawlJob) -> None:
        key = f"crawljob:{job.job_id}"
        await self.cache.set(key, pickle.dumps(job), ttl=self.ttl)

    async def get(self, job_id: str) -> CrawlJob | None:
        key = f"crawljob:{job_id}"
        data = await self.cache.get(key)
        if data is None:
            return None
        return pickle.loads(data)
```

---

## Download Endpoint

### `GET /api/v1/download/{job_id}`

Serves the `.crawljob` file for a given job ID.

**Response:**
- Content-Type: `application/x-crawljob`
- Content-Disposition: `attachment; filename="PackageName_jobid.crawljob"`
- Custom headers: `X-CrawlJob-ID`, `X-CrawlJob-Package`, `X-CrawlJob-Links`

**Error cases:**
- `404` -- Job not found or expired
- `500` -- Repository or serialization failure

### `GET /api/v1/download/{job_id}/info`

Returns CrawlJob metadata as JSON (for debugging/inspection):

```json
{
  "job_id": "a1b2c3d4-...",
  "package_name": "Iron.Man.2008",
  "created_at": "2025-01-15T12:00:00+00:00",
  "expires_at": "2025-01-15T13:00:00+00:00",
  "is_expired": false,
  "validated_urls": ["https://..."],
  "source_url": "https://...",
  "comment": "Size: 4.2 GB",
  "auto_start": "TRUE",
  "priority": "DEFAULT"
}
```

---

## Expiration

- CrawlJobs expire after **1 hour** by default (configurable via `default_ttl_hours`)
- The `is_expired()` method checks `datetime.now(UTC) > expires_at`
- Expired jobs return HTTP 404 from the download endpoint
- Cache TTL handles automatic cleanup at the storage layer

---

## Integration with Arr Applications

The CrawlJob system is designed for seamless integration with the Arr stack:

1. **Prowlarr** discovers Scavengarr as a Torznab indexer
2. **Search results** include `<link>` elements pointing to `/api/v1/download/{job_id}`
3. When a user **grabs** a result in Sonarr/Radarr, it requests the download URL
4. Scavengarr returns the `.crawljob` file
5. The Arr application saves it to JDownloader's **FolderWatch** directory
6. JDownloader processes the links automatically

---

## Source Code References

| Component | File |
|---|---|
| `CrawlJob` entity | `src/scavengarr/domain/entities/crawljob.py` |
| `BooleanStatus` enum | `src/scavengarr/domain/entities/crawljob.py` |
| `Priority` enum | `src/scavengarr/domain/entities/crawljob.py` |
| `CrawlJobFactory` | `src/scavengarr/application/factories/crawljob_factory.py` |
| `CrawlJobRepository` port | `src/scavengarr/domain/ports/crawljob_repository.py` |
| `CacheCrawlJobRepository` | `src/scavengarr/infrastructure/persistence/crawljob_cache.py` |
| Download endpoint | `src/scavengarr/interfaces/api/download/router.py` |
| Unit tests (entity) | `tests/unit/domain/test_crawljob.py` |
| Unit tests (factory) | `tests/unit/application/test_crawljob_factory.py` |
| Unit tests (cache) | `tests/unit/infrastructure/test_crawljob_cache.py` |
