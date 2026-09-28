[← Back to Index](./README.md)

# CrawlJob System

> Every Torznab search result becomes a `CrawlJob` that bundles its validated download links into a JDownloader `.crawljob` file, served via a stable download URL.

---

## Overview

Torznab's `<link>` element expects a URL that returns a downloadable file. Since Scavengarr indexes streaming and one-click hoster sites (not torrent trackers), it cannot provide a `.torrent` or magnet link. Instead, each search result points to `/api/v1/download/{job_id}`, which serves a `.crawljob` file that JDownloader processes via its FolderWatch extension.

```text
┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐
│  Torznab Search  │     │  CrawlJob Cache  │     │  Arr Application │
│                  │     │                  │     │  (Sonarr/Radarr) │
│  SearchResult ───┼──→  │  CrawlJob stored │     │                  │
│  with validated  │     │  (TTL: 1 hour)   │     │  Requests        │
│  download links  │     │                  │  ←──┤  /download/{id}  │
└──────────────────┘     │  Serves .crawljob│──→  │                  │
                         └──────────────────┘     └────────┬─────────┘
                                                           │ Blackhole download client
                                                           ▼
                                                ┌──────────────────┐
                                                │  JDownloader     │
                                                │  (FolderWatch)   │
                                                └──────────────────┘
```

---

## End-to-End Flow

1. **Search** — Torznab `?t=search&q=...` makes the plugin return `SearchResult` objects with download links.
1. **Link validation** — `HttpxSearchEngine.validate_results()` drops dead links and results without any valid link (see [Link Validation](./link-validation.md)).
1. **CrawlJob creation** — `CrawlJobFactory` converts each `SearchResult` into its own `CrawlJob`.
1. **Cache storage** — `CacheCrawlJobRepository` stores each job under `crawljob:{job_id}` with a 3600-second TTL; all jobs of a search are saved in parallel.
1. **Torznab XML** — each `<item>` has `<link>` and `<enclosure>` pointing to `/api/v1/download/{job_id}`.
1. **Grab** — Sonarr/Radarr request the download URL when a result is grabbed.
1. **Delivery** — the download endpoint serves the serialized `.crawljob` file; the Arr download client drops it into JDownloader's FolderWatch directory.

```text
Sonarr/Radarr                      Scavengarr
     │                                  │
     │  GET /api/v1/download/{job_id}   │
     │─────────────────────────────────→│
     │                                  │── Lookup in cache
     │                                  │── Check expiry
     │                                  │── Serialize to .crawljob
     │  200 OK (application/x-crawljob) │
     │←─────────────────────────────────│
     │                                  │
     │  Blackhole client saves file     │
     │  to JDownloader watch dir        │
```

---

## CrawlJob Entity

The frozen `CrawlJob` dataclass models a JDownloader `.crawljob` file with the fields of JDownloader's `CrawlJobStorable` format plus Scavengarr metadata.

### Core Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `job_id` | `str` | UUID4 (auto) | Unique identifier |
| `text` | `str` | `""` | Download links, line-separated (required by JDownloader) |
| `package_name` | `str` | `"Scavengarr Download"` | Display name in JDownloader's package list |
| `filename` | `str \| None` | `None` | Override filename |
| `comment` | `str \| None` | `None` | Human-readable description |

### Validation Metadata

| Field | Type | Default | Description |
|---|---|---|---|
| `validated_urls` | `list[str]` | `[]` | Download links in the job |
| `source_url` | `str \| None` | `None` | Original indexer detail page URL |
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
| `chunks` | `int` | `0` | Max parallel chunks (`0` = JDownloader default) |
| `priority` | `Priority` | `DEFAULT` | Download priority |

### Archive and Security

| Field | Type | Default | Description |
|---|---|---|---|
| `extract_passwords` | `list[str]` | `[]` | Archive passwords (serialized as a JSON array) |
| `download_password` | `str \| None` | `None` | Password for protected links |

### Advanced Options

| Field | Type | Default | Description |
|---|---|---|---|
| `deep_analyse_enabled` | `bool` | `False` | Deep link analysis |
| `add_offline_link` | `bool` | `True` | Add link even if offline |
| `overwrite_packagizer_enabled` | `bool` | `False` | Override packagizer settings |
| `set_before_packagizer_enabled` | `bool` | `False` | Set values before the packagizer runs |

### Methods

- `is_expired()` — returns `datetime.now(timezone.utc) > expires_at`.
- `to_crawljob_format()` — serializes the job to the `.crawljob` text format.

---

## Enums

### BooleanStatus

JDownloader's tri-state boolean for behavior flags:

```python
# src/scavengarr/domain/entities/crawljob.py
class BooleanStatus(str, Enum):
    TRUE = "TRUE"
    FALSE = "FALSE"
    UNSET = "UNSET"
```

### Priority

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

## File Format

`to_crawljob_format()` writes JDownloader's key-value property format, compatible with the [FolderWatch extension](https://board.jdownloader.org/showthread.php?t=58281). Optional lines (`filename`, `downloadFolder`, `comment`, `extractPasswords`, `downloadPassword`) are only written when set.

### Example Output

```text
# Generated by Scavengarr
# Job ID: a1b2c3d4-e5f6-7890-abcd-ef1234567890
# Created: 2025-01-15T12:00:00+00:00
# Expires: 2025-01-15T13:00:00+00:00

text=https://hoster1.example/file/abc
https://hoster2.example/file/def
packageName=Iron Man
filename=Iron.Man.2008.1080p.BluRay.x264
comment=Size: 4.2 GB | Source: https://indexer.example/movie/123
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

When a search result has several validated download links (e.g. from different hosters), all of them go into the same `.crawljob` file, in the deterministic order produced by link validation (primary link first, then alternatives). JDownloader processes all links in the job, which gives redundancy if one hoster is slow or offline.

```text
text=https://hoster1.example/file/abc
https://hoster2.example/file/def
https://hoster3.example/file/ghi
```

---

## CrawlJobFactory

`CrawlJobFactory.create_from_search_result(result, *, job_id=None)` converts a `SearchResult` into a `CrawlJob`.

### Constructor Parameters

| Parameter | Default | Description |
|---|---|---|
| `default_ttl_hours` | `1` | Job lifetime in hours |
| `auto_start` | `True` | Sets `auto_start` to `TRUE` (or `FALSE`) |
| `default_priority` | `Priority.DEFAULT` | Download priority |

The composition root creates the factory with exactly these values; they are not exposed in the configuration.

### Field Mapping

| SearchResult field | CrawlJob field | Notes |
|---|---|---|
| `title` | `package_name` | Fallback: `"Scavengarr Download"` |
| `validated_links` | `validated_urls` | Fallback: `[download_link]` when `validated_links` is empty |
| `validated_links` | `text` | Same list joined with `\r\n` |
| `source_url` | `source_url` | Original detail page URL |
| `release_name` | `filename` | Only written when set |
| `description`, `size`, `source_url` | `comment` | `"desc \| Size: X \| Source: URL"`; parts omitted when empty; fallback `"Downloaded via Scavengarr"` |

### Usage

```python
# src/scavengarr/interfaces/composition.py
factory = CrawlJobFactory(
    default_ttl_hours=1,
    auto_start=True,
    default_priority=Priority.DEFAULT,
)
crawl_job = factory.create_from_search_result(search_result)
```

---

## Storage

CrawlJobs are persisted via the `CrawlJobRepository` port, implemented by `CacheCrawlJobRepository` on top of the `CachePort` (diskcache or Redis, selected by `cache.backend`).

| Property | Value |
|---|---|
| Key format | `crawljob:{job_id}` |
| Serialization | JSON (enums by value, datetimes as ISO 8601) |
| TTL | 3600 seconds (hardcoded in the composition root) |
| Backend | `cache.backend`: `diskcache` (default) or `redis` |

In the `dev` environment the cache is cleared on every startup, so CrawlJobs do not survive a restart.

### Repository Protocol

```python
# src/scavengarr/domain/ports/crawljob_repository.py
@runtime_checkable
class CrawlJobRepository(Protocol):
    async def save(self, job: CrawlJob) -> None: ...
    async def get(self, job_id: str) -> CrawlJob | None: ...
```

### Cache Implementation

```python
# src/scavengarr/infrastructure/persistence/crawljob_cache.py (simplified)
class CacheCrawlJobRepository:
    def __init__(self, cache: CachePort, ttl_seconds: int = 3600): ...

    async def save(self, job: CrawlJob) -> None:
        key = f"crawljob:{job.job_id}"
        await self.cache.set(key, _serialize_crawljob(job), ttl=self.ttl)

    async def get(self, job_id: str) -> CrawlJob | None:
        data = await self.cache.get(f"crawljob:{job_id}")
        if data is None:
            return None
        try:
            return _deserialize_crawljob(data)
        except (json.JSONDecodeError, KeyError, ValueError):
            return None  # logged as crawljob_deserialize_error
```

A corrupt cache entry is therefore treated like a missing job (HTTP 404).

---

## Download Endpoints

### Download File

`GET /api/v1/download/{job_id}` serves the `.crawljob` file:

- Content type `application/x-crawljob`
- `Content-Disposition: attachment; filename="{package_name}_{job_id[:8]}.crawljob"` — characters other than letters, digits, space, `-` and `_` in the package name are replaced with `_`
- Custom headers `X-CrawlJob-ID`, `X-CrawlJob-Package`, `X-CrawlJob-Links`
- `404` — job not found or expired; `500` — repository or serialization failure

### Job Info

`GET /api/v1/download/{job_id}/info` returns the job metadata as JSON. Expired jobs are still returned (with `"is_expired": true`) while they remain in the cache; `404` only when the job is missing.

```json
{
  "job_id": "a1b2c3d4-...",
  "package_name": "Iron Man",
  "created_at": "2025-01-15T12:00:00+00:00",
  "expires_at": "2025-01-15T13:00:00+00:00",
  "is_expired": false,
  "validated_urls": ["https://hoster1.example/file/abc"],
  "source_url": "https://indexer.example/movie/123",
  "comment": "Size: 4.2 GB | Source: https://indexer.example/movie/123",
  "auto_start": "TRUE",
  "priority": "DEFAULT"
}
```

See the [Torznab API Reference](./torznab-api.md#download-endpoints) for the full endpoint reference.

---

## Expiration

- CrawlJobs expire **1 hour after the search** that created them — not after the grab.
- The lifetime is fixed (factory `default_ttl_hours=1`, repository `ttl_seconds=3600`) and cannot be changed via configuration.
- Expired jobs return HTTP 404 from the download endpoint; the cache TTL removes them from storage.
- If a grab fails with 404, re-run the search to create fresh jobs.

---

## Integration with Arr Applications

1. **Prowlarr** is configured with Scavengarr as a Generic Torznab indexer (one indexer per plugin) and syncs it to Sonarr/Radarr.
1. **Search results** contain `<link>`/`<enclosure>` URLs pointing to `/api/v1/download/{job_id}`.
1. **Sonarr/Radarr** need a Blackhole-type download client whose watch/drop folder is JDownloader's FolderWatch directory — this is required setup, otherwise the grabbed file is not handed to JDownloader.
1. On a grab, the download client fetches the `.crawljob` file from Scavengarr and saves it into that folder.
1. **JDownloader** picks up the file via FolderWatch and downloads the links.

See [Prowlarr Integration](./prowlarr-integration.md) for the full setup.

---

## Source Code References

| Component | Path |
|---|---|
| `CrawlJob`, `BooleanStatus`, `Priority` | `src/scavengarr/domain/entities/crawljob.py` |
| `CrawlJobFactory` | `src/scavengarr/application/factories/crawljob_factory.py` |
| `CrawlJobRepository` port | `src/scavengarr/domain/ports/crawljob_repository.py` |
| `CacheCrawlJobRepository` | `src/scavengarr/infrastructure/persistence/crawljob_cache.py` |
| Wiring (TTL, factory defaults) | `src/scavengarr/interfaces/composition.py` |
| Download endpoints | `src/scavengarr/interfaces/api/download/router.py` |
| Search use case (job creation) | `src/scavengarr/application/use_cases/torznab_search.py` |
| Unit tests (entity) | `tests/unit/domain/test_crawljob.py` |
| Unit tests (factory) | `tests/unit/application/test_crawljob_factory.py` |
| Unit tests (repository) | `tests/unit/infrastructure/test_crawljob_cache.py` |
