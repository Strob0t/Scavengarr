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
│  with validated  │     │  (TTL: 1 h, cfg) │     │  Requests        │
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
1. **Link validation** — `HttpxSearchEngine.validate_results()` checks the results in order until the requested page (`offset + limit`) is full; it drops dead links and results without any valid link (see [Link Validation](./link-validation.md)).
1. **CrawlJob creation** — `CrawlJobFactory` converts each `SearchResult` of that page into its own `CrawlJob`.
1. **Cache storage** — `CacheCrawlJobRepository` stores each job under `crawljob:{job_id}` with the `cache.crawljob_ttl_seconds` TTL (default 3600); all jobs of a search are saved in parallel. An item whose job could not be saved is dropped from the answer (`crawljob_save_failed`): its grab would answer 404.
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
     │                                  │── Resolve links (jobs with resolve_plugin)
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
| `resolve_plugin` | `str \| None` | `None` | Plugin that resolves `validated_urls` at grab time (see [Grab-Time Resolution](#grab-time-resolution)); `None` once the links are final |

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
| `extract_passwords` | `list[str]` | `[]` | Archive passwords (serialized as a JSON array); filled from `SearchResult.metadata["archive_password"]` (e.g. animeloads: `www.anime-loads.org`) |
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

When a search result has several validated download links (e.g. from different hosters), all of them go into the same `.crawljob` file, in the deterministic order produced by link validation (primary link first, then alternatives). JDownloader processes all links in the job, which gives redundancy if one hoster is slow or offline. With `validate_download_links: false` no link is checked, and each job holds only the result's `download_link` (unless the plugin filled `validated_links` itself).

```text
text=https://hoster1.example/file/abc
https://hoster2.example/file/def
https://hoster3.example/file/ghi
```

---

## CrawlJobFactory

`CrawlJobFactory.create_from_search_result(result, *, resolve_plugin=None)` converts a `SearchResult` into a `CrawlJob`; `resolve_plugin` names the plugin that resolves the links at grab time.

### Constructor Parameters

| Parameter | Default | Description |
|---|---|---|
| `ttl_seconds` | `3600` | Job lifetime (`expires_at`) |
| `auto_start` | `True` | Sets `auto_start` to `TRUE` (or `FALSE`) |
| `default_priority` | `Priority.DEFAULT` | Download priority |

The composition root (`build_crawljob_store()`) creates factory and repository with `ttl_seconds = cache.crawljob_ttl_seconds`, so the cache entry and `expires_at` expire together — except that a job resolved at grab time (`GrabResolvingPlugin`: nox, animeloads) is stored again with a fresh TTL, so its entry outlives `expires_at` while the download still answers 404 after it; `auto_start` and `default_priority` are not exposed in the configuration.

### Field Mapping

| SearchResult field | CrawlJob field | Notes |
|---|---|---|
| `title` | `package_name` | Fallback: `"Scavengarr Download"` |
| `validated_links` | `validated_urls` | Fallback: `[download_link]` when `validated_links` is empty |
| `validated_links` | `text` | Same list joined with `\r\n` |
| `source_url` | `source_url` | Original detail page URL |
| `release_name` | `filename` | Only written when set |
| `metadata["archive_password"]` | `extract_passwords` | One-element list when set, else `[]` |
| (plugin is a `GrabResolvingPlugin`) | `resolve_plugin` | Set by `TorznabSearchUseCase`, not by the result; see [Grab-Time Resolution](#grab-time-resolution) |
| `description`, `size`, `source_url` | `comment` | `"desc \| Size: X \| Source: URL"`; parts omitted when empty; fallback `"Downloaded via Scavengarr"` |

### Usage

```python
# src/scavengarr/interfaces/composition.py
factory = CrawlJobFactory(
    ttl_seconds=config.cache.crawljob_ttl_seconds,
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
| TTL | `cache.crawljob_ttl_seconds` (default 3600 seconds) |
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
- `Content-Disposition: attachment; filename="{package_name}_{job_id[:8]}.crawljob"; filename*=UTF-8''{percent-encoded name}` — in `filename` everything but ASCII letters, digits, space, `-` and `_` becomes `_` (header values are Latin-1; an en dash or CJK title used to cause a 500), `filename*` carries the real name (RFC 6266)
- Custom headers `X-CrawlJob-ID`, `X-CrawlJob-Package` (percent-encoded package name), `X-CrawlJob-Links`
- Values in the `.crawljob` (`packageName`, `filename`, `downloadFolder`, `comment`, `downloadPassword`) are written on one line: line breaks from scraped titles or descriptions are collapsed to spaces, so a site cannot add its own keys (e.g. `downloadFolder=`). `text` (the links) is written as stored.
- `404` — job not found or expired; `500` — repository or serialization failure; `502` — grab-time resolution failed (the Arr app treats the grab as failed and can try another release)

### Grab-Time Resolution

Some sites put their real download links behind a captcha or count every unlocked link against a download quota (nox: ALTCHA gateway plus hourly/weekly limits). Resolving those links for every search result would burn the quota on results nobody downloads. Such plugins implement the optional `GrabResolvingPlugin` protocol (`domain/plugins/base.py`):

```python
async def resolve_download(self, url: str) -> list[str]: ...
```

- At search time `TorznabSearchUseCase` stores the plugin name in `CrawlJob.resolve_plugin`; `validated_urls` still hold the page URLs the plugin returned.
- When the job is grabbed, `CrawlJobResolveUseCase` (`application/use_cases/crawljob_resolve.py`) calls `resolve_download()` for each URL, drops duplicates, stores the job again with the resolved links and without `resolve_plugin`, and serves it. A repeated grab of the same job reuses the stored links (no second captcha).
- No links, an unknown plugin or a plugin error → `CrawlJobResolveError` → HTTP `502`.

Implementations:

| Plugin | Page URL in the result | Resolution on grab |
|---|---|---|
| `nox` | `/media/<slug>?release=<id>` | Online links of the release (`/api/releases/<slug>`), one ALTCHA proof-of-work challenge solved in-process (`infrastructure/captcha/altcha.py`) → pass token that unlocks every link (`/go/<token>/url?cp=…`). Refusals are logged as `nox_unlock_refused` with `message`/`reason` (`hourly_limit`, `weekly_limit`, …) |
| `animeloads` | `/media/<slug>?release=<n>` | Release tab of the media page (browser, DDoS-Guard) → "odd one out" image captcha solved in the page → Click'n'Load packages decrypted (`infrastructure/plugins/clicknload.py`). One captcha per episode anonymously (releases up to 13 episodes), one per release with `SCAVENGARR_ANIMELOADS_USERNAME`/`_PASSWORD`. Series-level URLs are returned unchanged |

Live smoke (one grab per plugin, opt-in): `tests/live/test_grab_resolve_live.py`.
- `/download/{job_id}/info` shows the stored (possibly unresolved) state and never triggers resolution.

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

- CrawlJobs expire **`cache.crawljob_ttl_seconds` (default 1 hour) after the search** that created them — not after the grab.
- Raise the TTL when download clients grab late (e.g. a queue that only fetches hours after the search); it must be greater than 0.
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
