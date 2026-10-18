# Connect Prowlarr

> **Status:** how-to
> **As of:** bdd3f0c4

## Goal

Add Scavengarr's plugins to Prowlarr as Torznab indexers, sync them to Sonarr and Radarr, and have JDownloader download what they grab.
Scavengarr answers like a torrent indexer, but its items are direct-download links packaged as `.crawljob` files, which JDownloader picks up from a watch folder.

| Component | Role |
|---|---|
| Prowlarr | The indexer manager: sends the searches and syncs the indexers to the Arr apps. |
| Scavengarr | The Torznab indexer: searches the sites, answers RSS feeds and serves the `.crawljob` files. |
| Sonarr, Radarr | The media managers: grab results through a Blackhole download client. |
| JDownloader | The download manager: picks up the `.crawljob` files through FolderWatch. |

## Prerequisites

- Scavengarr running and reachable from Prowlarr ([Deploy Scavengarr](deploy.md)), with `SCAVENGARR_ENVIRONMENT=prod`, the image's default: errors then answer an empty feed with 200, and the cache survives a restart.
- Prowlarr, and Sonarr or Radarr connected to it.
- JDownloader with the FolderWatch extension, and a directory that both the Arr apps and JDownloader can reach (a shared volume, for example).

## Steps

### 1. Find the plugin names

```bash
curl http://<scavengarr-host>:7979/api/v1/torznab/indexers
```

Each `name` is one indexer; the names are case-sensitive.
The [plugin list](../reference/plugins.md) says what each site offers.

### 2. Add an indexer per plugin

1. In Prowlarr, open **Settings > Indexers**, click **Add Indexer** and choose **Generic Torznab**.
1. Fill in the fields:

   | Field | Value |
   |---|---|
   | Name | Any name, such as `Scavengarr - filmpalast`. |
   | URL | `http://<scavengarr-host>:7979/api/v1/torznab/<plugin>` |
   | API Key | Empty: there is no authentication. |
   | Categories | `2000` (Movies), `5000` (TV). |

1. Click **Test**, then **Save**.

When both run in one Docker network, use the container name as the host (`http://scavengarr:7979/...`):

```yaml
services:
  scavengarr:
    # ... as in Deploy Scavengarr
    networks: [arr-network]
  prowlarr:
    image: lscr.io/linuxserver/prowlarr:latest
    networks: [arr-network]

networks:
  arr-network:
    driver: bridge
```

The test asks for the capabilities (`t=caps`) and sends a search without a query, which Scavengarr answers with a reachability probe of the plugin's site instead of a search ([test mode](../reference/torznab-api.md#prowlarr-test-mode)).
Repeat the step for each plugin: every plugin is an indexer of its own.

### 3. Sync the indexers to Sonarr and Radarr

1. In Prowlarr, open **Settings > Apps** and add the Sonarr and Radarr instances.
1. Prowlarr pushes the Scavengarr indexers to each of them.
1. Sync profiles limit an indexer to some apps, such as a movie plugin to Radarr only.

### 4. Add a Blackhole download client

The results are `.crawljob` files, not torrents, so a torrent client cannot take them.
In Sonarr and Radarr, add a download client of the Blackhole type whose folder is JDownloader's FolderWatch directory: on a grab the app fetches the `.crawljob` file from Scavengarr and saves it there.

### 5. Point JDownloader at the folder

Enable the FolderWatch extension in JDownloader's settings and point it at the same directory; it then picks up each file and downloads its links.
How a search becomes a feed and a grab a file is described in [Torznab and CrawlJobs](../current/torznab-and-crawljobs.md).

## Verify

```bash
# The capabilities
curl "http://<scavengarr-host>:7979/api/v1/torznab/<plugin>?t=caps"

# A search: an RSS feed whose items link to /api/v1/download/<job id>
curl "http://<scavengarr-host>:7979/api/v1/torznab/<plugin>?t=search&q=<title>"

# A job's links and expiry, without downloading the file
curl "http://<scavengarr-host>:7979/api/v1/download/<job id>/info"

# The plugin's site
curl "http://<scavengarr-host>:7979/api/v1/torznab/<plugin>/health"
```

A grab in Sonarr or Radarr then puts a `.crawljob` file into the watch folder, and JDownloader lists its package.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The Prowlarr test fails. | Check `curl http://<host>:7979/api/v1/healthz`; check the plugin name against `/api/v1/torznab/indexers`; in Docker, use one network and the container name instead of `localhost`. |
| The test fails, but searches work. | The test probes the plugin's current domain only, the first one until a search has picked a working one; `/api/v1/torznab/<plugin>/health` shows it. |
| HTTP 429. | Prowlarr's many indexers share one client IP and the API rate limit; raise `http.api_rate_limit_rpm` or set it to `0` ([rate limiting](../reference/torznab-api.md#rate-limiting)). |
| Empty results, the site works in a browser. | The site's layout changed and the plugin needs an update; or a challenge blocks it: `playwright.browser_fallback` (on by default) or a solver sidecar (`playwright.solver_url`, compose profile `solver`) handles Cloudflare, and other protections need a plugin on `PlaywrightPluginBase`. |
| Empty results, the log shows `links_filtered`. | Link validation dropped every result; to test, set `SCAVENGARR_VALIDATE_DOWNLOAD_LINKS=false`, and each CrawlJob then holds the result's primary link only. |
| An empty feed with 200 and nothing in it. | A site that is down gives an empty feed; the log shows `<plugin>_timeout` or `<plugin>_fetch_error`, and in `prod` a plugin error also answers an empty feed. |
| The grab answers 404. | The job expired: it lives `cache.crawljob_ttl_seconds` after the search, not after the grab; raise it for a client that grabs late, or search again. |
| The grab answers 404 after a restart. | `dev` clears the cache at every start, and diskcache without a mounted cache directory loses it with the container. |
| The grab answers 502. | nox and animeloads resolve the links at the grab (captcha, download quota) and failed (`crawljob_resolve_failed`); try later or another release. |
| No log output. | Set `SCAVENGARR_LOG_LEVEL=DEBUG`; for readable lines `SCAVENGARR_LOG_FORMAT=console`, since `prod` logs JSON. |

On shutdown the app lets the requests in flight finish before it closes the HTTP client and the cache ([Architecture: shutdown](../current/architecture.md#shutdown)); `docker stop` kills the container after its grace period, 10 s by default.
