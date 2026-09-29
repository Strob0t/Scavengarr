[← Back to Index](./README.md)

# Hoster Resolver System

> Validates file availability on hosting services and extracts direct video URLs from streaming hosters.

---

## Overview

Scavengarr ships **59 hoster resolvers**: 22 individual resolvers, 12 generic DDL hosters consolidated in `GenericDDLResolver`, and 25 XFileSharingPro (XFS) hosters consolidated in `XFSResolver`. Every resolver checks whether a file is still available; video-extracting resolvers additionally return a direct `.mp4`/`.m3u8` URL plus the HTTP headers the CDN needs.

Resolvers are registered in `HosterResolverRegistry`, which dispatches each URL to a resolver by its second-level domain, follows redirects and plugin hoster hints for unknown domains, falls back to a HEAD content-type probe, and caches the outcome in memory.

---

## Architecture

```text
URL → HosterResolverRegistry.resolve(url, hoster=<plugin hint>)
      ├── Result cache hit → cached ResolvedStream | None
      ├── Resolver for URL domain (resolver name, then supported_domains alias) → resolver.resolve()
      ├── No resolver → follow HTTP redirects → resolver for final domain
      ├── Still none → resolver for plugin hoster hint (rotating mirror domains)
      └── Still none → HEAD content-type probe (video/* or HLS) → ResolvedStream | None
```

All resolvers implement the same port:

```python
class HosterResolverPort(Protocol):
    @property
    def name(self) -> str: ...
    async def resolve(self, url: str) -> ResolvedStream | None: ...
```

`resolve()` returns `ResolvedStream(video_url=..., headers=..., is_hls=..., quality=...)` on success and `None` when the file is offline, deleted, or cannot be extracted.

### Domain dispatch

The registry matches `extract_domain(url)` (second-level domain, e.g. `"https://www.voe.sx/e/abc"` → `"voe"`) against resolver names. Resolvers that expose a `supported_domains` property are also registered under every alias domain (e.g. `filelions` → `vidhide`, `d0000d` → `doodstream`, `streamta` → `streamtape`): all XFS and generic DDL resolvers plus the individual resolvers with mirror lists (VOE, DoodStream, Streamtape, VidGuard, Strmup, Filemoon, FireStream, DDownload, Serienstream). Other individual resolvers are reached when the URL's second-level domain equals the resolver `name`, via a redirect to such a domain, or via the plugin-provided hoster hint. The registry strips surrounding whitespace from the URL first (scraped links sometimes end in a newline).

> **Known issue:** `moflix-stream` is listed in VidGuard's `_DOMAINS` and in Vidhide's `extra_domains`. The XFS resolvers register after VidGuard, so the registry maps `moflix-stream` URLs to the Vidhide resolver.

### Playback headers

Video-extracting resolvers set `ResolvedStream.headers` with the headers required for CDN playback. The Stremio addon forwards them via `behaviorHints.proxyHeaders` or routes HLS streams through its HLS proxy (see [Stremio Addon](./stremio-addon.md)).

| Resolver | Headers |
|---|---|
| VOE | `Referer: <embed_url>` |
| Filemoon | Legacy page: `Referer: <embed URL after redirects>`; Byse player: `Referer` of the captured stream request (the player's frame origin) |
| SuperVideo | `Referer: <embed_url>` |
| Streamtape | `Referer: https://<response host>/` |
| DoodStream | `Referer: <base_url>` |
| XFS video hosters | `Referer: <embed URL after redirects>` |
| Veev | `Referer: <origin>/`, `User-Agent: <UA used to resolve>` (token is UA-bound) |
| FireStream, Playmate, Vixeo | none (signed / public HLS URLs) |
| StreamUp (strmup) | `Origin: <scheme>://<host>`, `Referer: <scheme>://<host>/` |
| Vidsonic | `Origin: <scheme>://<host>`, `Referer: <scheme>://<host>/` |

### CDN verification

`_verify.py` provides `verify_video_url()`: a HEAD request (8 s timeout, redirects followed) with the playback headers. Only `200`/`206` counts as reachable. It is used by the XFS video path, VOE, Streamtape, and SuperVideo, and filters out IP-locked CDN tokens (e.g. LULUVID/LULUVDOO tokens bound to Cloudflare's edge IP).

### Browser capture

Some players create the stream URL only while they run. Filemoon's Byse player shows "Click play button in order to verify you're a human" and then runs a fingerprint attestation, a proof-of-work captcha (a custom memory-hard hash, not SHA-256, about 65k attempts at difficulty 16) and an AES-GCM encrypted playback call. Replaying that flow in Python is slow and breaks with every Byse update, so the resolver lets the player do it: `StealthPool.capture_media(url, timeout=...)` opens the embed page in the Patchright stealth context, solves a Cloudflare challenge if present, waits 3 s for autoplay, then clicks the page centre (the play button) up to 3 times, 5 s apart, and returns the first request for a `.m3u8`/`.mpd`/`.mp4`/`master.txt` URL as `CapturedMedia(url, referer)`.

- The page loads with stylesheets and fonts (a page route overrides the context's resource block): without CSS the play button is not where the click lands. Only `media` downloads are aborted; their URL is already known when requested.
- Popups opened by ad scripts (often on the first click) are closed.
- Seek-preview playlists are not the stream: URLs containing `thumbnail`, `sprite` or `preview` are ignored (vixeo requests `thumbnails.m3u8`, a playlist of JPEGs, before the video).
- Shares the `fetch_text()` concurrency limit (`fetch_concurrency`, at most 2 pages).
- Resolvers call it only at play time (`/stremio/play/{id}` resolves one link), so a few seconds per resolve are acceptable (Filemoon live: 2–5 s).
- Dead files end the capture early: the page title and visible text (not the HTML, player scripts carry such strings as error templates) are checked for notices like "Video not found", "File is no longer available", "No such file", "has been removed" after the Cloudflare step and after every click (savefiles says so only after the play click). Live: dead dood link 0.7 s, dead savefiles link 8.5 s instead of 18 s.

`_browser.py` (`capture_stream(stealth_pool, embed_url, hoster)`) turns a capture into a `ResolvedStream` (`is_hls` from `.m3u8`, `Referer` of the captured request, else the embed URL) and is shared by:

| Resolver | When |
|---|---|
| Filemoon | Byse player pages, or a non-200 embed page (skipped when the details API reports the video gone or embed-restricted) |
| XFS video hosters (savefiles, bigwarp, streamwish, …) | Cloudflare challenge on the embed page, or no video in the page but a captcha widget in front of the player (Turnstile / reCAPTCHA play button, e.g. dr0pstream) |
| DoodStream | Cloudflare challenge (every mirror redirects to `playmogo.com`, which is challenged) |
| SuperVideo | Cloudflare challenge (replaces its own Playwright HTML fetch) |
| Vixeo | always (no httpx path) |

Without a `StealthPool` (tests, `stealth_pool=None`) these paths return `None`. Captured URLs are not HEAD-verified: the browser just requested them.

> **Known issue:** SuperVideo's CDN (`*.serversicuro.cc`) answers non-browser clients with a "Loading..." page whose script redirects to a tokenized URL, which in turn redirects curl/httpx to ad trackers, even with the browser's User-Agent and cookie. The resolved URL plays in a browser but not in players that do not run JavaScript.

---

## Resolver Categories

### Video-extracting streaming resolvers (individual)

Extract a direct video URL (`.mp4`/`.m3u8`) from an embed page.

| Resolver | `name` | Domains accepted | Technique |
|---|---|---|---|
| VOE | `voe` | `voe.*`; rotating mirrors via redirect or hoster hint | Multi-method: `application/json` deobfuscation chain, direct regex, base64 variables |
| Streamtape | `streamtape` | `streamtape`, `streamta`, `strtape`, `shavetape`, `tapeblocker`, `streamtapeadblock(user)`, `gettapeads`, … (13 names) | Token extraction from page source |
| SuperVideo | `supervideo` | `supervideo.*` | XFS-style JWPlayer extraction; browser capture on a Cloudflare 403 |
| DoodStream | `doodstream` | `dood`, `doods`, `doodstream`, `ds2play`, `d0o0d`, `vidply`, `myvidplay`, `playmogo`, … (23 names; all mirrors currently redirect to `playmogo.com`) | `pass_md5` endpoint extraction; browser capture on a Cloudflare challenge (the player passes an invisible Turnstile headful; its CDN URL `…cloudatacdn.com/…~id?token=…` has no file extension and is taken from the video element request) |
| Filemoon | `filemoon` | `filemoon`, `filemooon`, `byse`, rotating Byse domains (`bysezejataos`, `bysekoze`, …; 14 names from JD2 `FilemoonSxCrawler`) | Packed JS unpacker (legacy pages); Byse player pages via browser capture (`StealthPool.capture_media`), after the details API `/api/videos/<id>/embed/details` rules out a gone video (404) or a domain-restricted embed (403 `embedding … not allowed`) |
| StreamUp | `strmup` | `strmup`, `streamup`, `vidara`, `vidaraa` | `streaming_url` from page, AJAX `/ajax/stream` fallback; HLS. Vidara hosts use the JSON API `POST /api/stream` (`{"device": "web", "filecode": id}` → `streaming_url`, 404 when gone; JD2 `VidaraTo`) |
| Vidsonic | `vidsonic` | `vidsonic` | Hex-obfuscated, pipe-delimited HLS URL decoding |

### Validate-only resolvers (individual)

Check availability and return the original URL without headers. The Stremio addon drops such "echo" results because the URL is not a playable video (`is_direct_video_url()` in `src/scavengarr/application/stremio/stream_builder.py`).

| Resolver | `name` | Domains accepted | Technique |
|---|---|---|---|
| VidGuard | `vidguard` | `vidguard`, `vid-guard`, `vgfplay`, `vgembed`, `v6embed`, `vembed`, `bembed`, `listeamed`, `moflix-stream` | Embed page validation (`/d/`, `/e/`, `/v/` paths) |
| Vidking | `vidking` | `vidking.net` | Page validation (`/e/`, `/d/`, `/embed/movie/{id}`, `/embed/tv/{id}/{s}/{e}`) |
| Stmix | `stmix` | `stmix.io` | Page validation |
| SerienStream | `serienstream` | `s.to`, `www.s.to`, `serienstream.*`, `serien.*` | Page validation (`/serie/` or `/serien/` slug) |
| SendVid | `sendvid` | `sendvid.com` | Status API (`/api/v1/videos/{id}/status.json`, 404 = offline) + page 200 check |
| FireStream | `firestream` | `firestream.to` → `firestream.site` (`/e/<id>`) | Port of JD2 `FirestreamTo`: embed page → `<script id="video-data">` (`encodingStatus` must be `completed`) + `<script id="token-blob">` → `POST /api/videos/<id>/resolve` `{"blob"}` on the host that served the page (the token is host-bound) → `signedVideoUrl` (HLS); 404 embed page = gone |
| Vixeo | `vixeo` | `vixeo.io` (`/e/<id>`; `/login` and other paths are not videos) | Vidsonic's current player: a JavaScript app that builds the signed `*.vidsonic.net/secure/.../index.m3u8` URL at runtime, so the stream is taken from the browser capture (~4 s) |
| Playmate | `playmate` | `playmate.to` (`/watch/<id>`, `/e/<id>`) | Port of JD2 `PlaymateTo`: `GET /api/video-meta?filecode=` (404 / `success: false` = gone) → `POST /api/s` `{"c": id, "d": "web"}` with `Origin` + watch-page `Referer` → `sx` (HLS master, `master.txt`). The API answers 403 to non-browser user agents |
| Vinovo | `vinovo` | `vinovo.to`, `vinovo.si` (`/e/` or `/d/`, 12+ chars) | Player API without captcha (Turnstile only guards the official download button): page token from `<meta name="token">` and CDN base from `data-base` on `/e/{id}`, `POST /api/file/url/{id}` (`recaptcha=&token=…`, XHR) → stream token → `{data-base}/stream/{token}` (port of JDownloader `VinovoTo`, stream path); offline on "Video not found". Token bound to the resolving User-Agent like veev; the CDN can take ~30 s to the first byte |
| Veev | `veev` | `veev.to` (`/e/`, `/d/` or bare ID, 12+ chars) | Player API without captcha: LZW-decode the `window._vvto` token, `/dl?op=player_api&cmd=gi`, decode `file.dv[0].s` → direct MP4 (port of JDownloader `VeevTo`); offline on `Watch video - Veev.to` title or "File not found". Resolves with a browser User-Agent and returns it in the playback headers: veevcdn binds the stream token to that UA (another UA gets 403) |

### DDL resolvers (individual)

Validate file availability without extracting a video URL and return the canonical file URL with `StreamQuality.UNKNOWN`.

| Resolver | `name` | Domains accepted | Technique |
|---|---|---|---|
| Filer.net | `filer` | `filer.net` | Public status API |
| Rapidgator | `rapidgator` | `rapidgator.net`, `rapidgator.asia`, `rg.to` | Website scraping |
| DDownload | `ddownload` | `ddownload`, `ddl` | XFS page check + canonical URL normalization |
| Mediafire | `mediafire` | `mediafire` | Public file info API; offline on error `110`/`111` or a set `delete_date` |
| GoFile | `gofile` | `gofile` | Guest token (cached 25 min) + content availability API |

### Generic DDL resolvers (12 hosters)

`GenericDDLResolver` in `generic_ddl.py` handles 12 hosters, each described by a `GenericDDLConfig` (`name`, `domains`, `file_id_re`, `offline_markers`, `file_id_source` = `"path"` or `"query"`, `min_file_id_len`). The resolver GETs the URL, treats non-200 responses, offline markers, and redirects to `/404` or `error` URLs as offline, and otherwise returns the original URL.

| Hoster | Domains | Notes |
|---|---|---|
| Alfafile | `alfafile` | `/file/{id}` |
| AlphaDDL | `alphaddl` | Minimum file ID length 3 |
| Fastpic | `fastpic` | Image host, `/view/` and `/fullview/` paths |
| Filecrypt | `filecrypt` | `/Container/{id}` validation |
| FileFactory | `filefactory` | `/file/{id}` |
| FSST | `fsst` | Optional `/e/` or `/d/` prefix |
| Go4up | `go4up` | `/dl/` and `/link/` paths |
| Mixdrop | `mixdrop`, `mxdrop`, `m1xdrop`, `mixdrop23` | `/f/`, `/e/`, `/emb/` paths |
| Nitroflare | `nitroflare`, `nitro` | `/view/` and `/watch/` paths |
| 1fichier | `1fichier`, `alterupload`, `cjoint`, `desfichiers`, `dfichiers`, `megadl`, `mesfichiers`, `piecejointe`, `pjointe`, `tenvoi`, `dl4free` | File ID taken from the query string |
| Turbobit | `turbobit`, `turb`, `turbo` | Minimum file ID length 6 |
| Uploaded | `uploaded`, `ul` | Optional `/file/` prefix |

Adding a new generic DDL hoster requires only a `GenericDDLConfig` constant appended to `ALL_DDL_CONFIGS`. Tests are parameterised automatically.

### XFS resolvers (26 hosters)

`XFSResolver` in `xfs.py` handles 27 XFileSharingPro-based hosters, each described by an `XFSConfig`:

- `name` — resolver identifier
- `domains` — `frozenset` of second-level domain names for URL matching
- `file_id_re` — compiled regex that extracts the file ID from the URL path
- `offline_markers` — strings that indicate the file is deleted/expired
- `is_video_hoster` — `True` for streaming hosters (extract the video URL from the embed page)
- `needs_captcha` — `True` for hosters behind Cloudflare Turnstile or anti-bot JS (return `None`)
- `extra_domains` — additional aliases (mostly from JDownloader plugins)

#### DDL hosters (validate only)

| Hoster | Domains | Notes |
|---|---|---|
| Katfile | `katfile` | Custom offline markers |
| Hexupload | `hexupload` | Standard markers |
| Clicknupload | `clicknupload`, `clickndownload` | Standard markers |
| Filestore | `filestore` | Standard markers |
| Uptobox | `uptobox`, `uptostream` | Custom offline markers |
| Hotlink | `hotlink` | Standard markers |

#### Video hosters (extract video URL)

The resolver fetches `/e/{file_id}`, checks offline markers and error redirects, extracts the video URL (see [Shared video extraction](#shared-video-extraction)), and verifies it with `verify_video_url()`. When the embed page is an XFS splash form (`<form id="F1" action="/dl">`), it automatically POSTs `op=embed&file_code=<id>&auto=1` to `/dl` and extracts from the returned player page. This applies to every XFS video hoster, not to a fixed list.

| Hoster | Domains | Notes |
|---|---|---|
| Funxd | `funxd` | `/e/` or `/d/` prefix |
| Bigwarp | `bigwarp` | Custom offline markers |
| Dropload | `dropload`, `dr0pstream` | Extended markers; the dr0pstream player sits behind a Turnstile play button (browser capture) |
| Goodstream | `goodstream` | `/video/embed/<short id>/<size>` links (goodstream.one, formerly .uno; the `/e/<id>` route serves the same page); `No such file` offline marker |
| Savefiles | `savefiles`, `streamhls` | Extended markers |
| Streamwish | 32 domains (`streamwish`, `dwish`, `hglink`, `obeywish`, `awish`, `embedwish`, …) | Extended markers + Streamwish-specific markers |
| Vidmoly | `vidmoly` | `/w/` path prefix support. Known issue: the embed page is a "Loading..." script redirect (`?ch=1&js=<JWT>`) that sends non-browser clients, and on networks that block ad domains (Pi-hole) even the stealth browser, to `click-v4.plarclck.com`; no stream is reachable then |
| Vidoza | `vidoza`, `videzz` | Custom markers |
| Vidhide | 6 primary (`vidhide`, `filelions`, …) + 25 aliases (`streamhide`, `louishide`, `moflix-stream`, …) | Lowercase-only file IDs |
| Mp4Upload | `mp4upload` | Standard markers |
| Uqload | `uqload` | Standard markers |
| Vidshar | `vidshar`, `vedshare` | Standard markers |
| Vidroba | `vidroba`, `vidoba` | Standard markers |
| Vidspeed | `vidspeed`, `vidspeeds`, `xvideosharing` | Standard markers |
| StreamRuby | `streamruby` | Standard markers |
| Lulustream | `lulustream`, `luluvdo`, `luluvid`, `lulu`, `luluvdoo`, `cdn1` | Standard markers |
| Upstream | `upstream` | Standard markers |
| Vidnest | `vidnest` | Custom markers |

#### Captcha-required (return None)

| Hoster | Domains | Notes |
|---|---|---|
| Wolfstream | `wolfstream` | Anti-bot JS redirect to an ad domain (retested 2026-09-28) |

Veev and Vinovo left this list: they have their own resolvers now (see below).

Adding a new XFS hoster requires only an `XFSConfig` constant appended to `ALL_XFS_CONFIGS`. Tests are parameterised automatically.

### Shared video extraction

`_video_extract.py` (`extract_video_url()`) is used by the XFS resolver and the Filemoon resolver. Strategies, in order:

1. Streamwish `"hls2":"https://…"` JSON key.
1. Dean Edwards packed JS (`eval(function(p,a,c,k,e,d)…)`) — unpacked, then searched for JWPlayer `sources`/`file` HLS or MP4 URLs.
1. JWPlayer `sources: [{file: "…"}]` directly in the page (thumbnail/track URLs skipped).
1. Any quoted `.m3u8`/`.mp4` URL in the page.

---

## Registry Features

| Feature | Description |
|---|---|
| Domain matching | Resolver `name` first, then `supported_domains` aliases (XFS, generic DDL and individual resolvers with mirror lists) |
| Redirect following | Unknown domains are followed via GET; the final domain is dispatched again |
| Hoster hint | Plugin-provided hoster name as a fallback for rotating mirror domains |
| Content-type probe | HEAD request; `video/*` or `application/vnd.apple.mpegurl` responses become a `ResolvedStream` |
| Result cache | In-memory, keyed by URL: alive results 1 h, dead results 15 min |
| Redirect cache | Redirect mappings cached 1 h |
| Cache limits | Each cache holds at most 10,000 entries; expired entries are evicted every 1,000 `resolve()` calls |
| Cleanup | `cleanup()` calls `cleanup()` on every resolver that has one (app shutdown) |

The dead-link liveness probe in `probe.py` (`probe_urls_stealth()`: httpx phase, then the Patchright stealth browser for Cloudflare-blocked URLs) is separate from the registry and used by the Stremio stream use case; see [Stremio Addon](./stremio-addon.md).

---

## Adding a New Resolver

**XFS-based hoster:** add an `XFSConfig` constant in `xfs.py` (`name`, `domains`, `file_id_re`, `offline_markers`, `is_video_hoster`, optionally `needs_captcha`/`extra_domains`) and append it to `ALL_XFS_CONFIGS`. **Generic DDL hoster:** add a `GenericDDLConfig` constant in `generic_ddl.py` and append it to `ALL_DDL_CONFIGS`. In both cases tests are parameterised automatically and the composition root picks the config up via `create_all_xfs_resolvers()` / `create_all_ddl_resolvers()`.

**Any other hoster:**

1. Create `src/scavengarr/infrastructure/hoster_resolvers/<name>.py`:
   - Module level: `_DOMAINS` set/frozenset, `_FILE_ID_RE` regex, helpers such as `_extract_file_id()`.
   - A class with a `name` property and `async def resolve(self, url: str) -> ResolvedStream | None`; the constructor takes `http_client: httpx.AsyncClient`; log via `structlog.get_logger(__name__)`.
   - Flow: extract file ID → build canonical URL → fetch page/API → check offline markers → return `ResolvedStream` or `None`.
   - Video-extracting resolvers return the direct video URL plus playback `headers` (verify it with `verify_video_url()` from `_verify.py`); DDL resolvers return the canonical file URL with `StreamQuality.UNKNOWN`.
   - Use `extract_domain(url)` from `scavengarr.infrastructure.hoster_resolvers` for domain matching (`"https://www.voe.sx/e/abc"` → `"voe"`).
   - The registry dispatches by `name`; if the hoster uses other second-level domains, expose a `supported_domains` property (`frozenset[str]`) so the registry maps them too.
1. Add `tests/unit/infrastructure/test_<name>_resolver.py` with `respx` mocks (see [Testing](#testing)):
   - `TestExtractFileId`: valid domains, `www` prefix, http scheme, invalid/short IDs, non-matching domains.
   - `TestResolver`: `test_name`, valid file, one test per offline marker, HTTP errors, network errors, invalid URLs, error redirects.
1. Wire it into the `resolvers=[...]` list in `src/scavengarr/interfaces/composition.py`: `<Name>Resolver(http_client=state.http_client)`.

**JDownloader reference sources:** `.devdata/JDownloader2/plugins/` and `.devdata/JDownloader2/controlling/` (not versioned) are SVN working copies of `svn://svn.jdownloader.org/jdownloader/trunk/src/jd/{plugins,controlling}`; use the JDownloader hoster/decrypter plugins there as reference when adding or fixing resolvers. `.devcontainer/sync-jdownloader.sh` checks them out or runs `svn update` on every container start; files pulled in by the last sync that brought changes are listed in `.devdata/JDownloader2/CHANGES.md`.

---

## Testing

All resolver tests use `respx` (httpx-native HTTP mocking):

```python
@respx.mock
@pytest.mark.asyncio()
async def test_resolves_valid_file(self) -> None:
    respx.get(url).respond(200, text=html)
    async with httpx.AsyncClient() as client:
        result = await Resolver(http_client=client).resolve(url)
    assert result is not None
```

The XFS resolver tests are parameterised over all `ALL_XFS_CONFIGS` entries and the generic DDL tests over all `ALL_DDL_CONFIGS` entries.

| Test file | Coverage |
|---|---|
| `tests/unit/infrastructure/test_xfs_resolver.py` | XFS resolver, all configs |
| `tests/unit/infrastructure/test_generic_ddl_resolver.py` | Generic DDL resolver, all configs |
| `tests/unit/infrastructure/test_<name>_resolver.py` | Individual resolvers |
| `tests/unit/infrastructure/test_hoster_registry.py` | Registry dispatch, redirects, hints, caching |
| `tests/unit/infrastructure/test_video_extract.py` | Shared video URL extraction |
| `tests/unit/infrastructure/test_verify_video_url.py` | `verify_video_url()` |
| `tests/unit/infrastructure/test_hoster_probe.py` | Liveness probe (`probe.py`) |
| `tests/unit/infrastructure/test_stealth_pool.py` | `StealthPool` |
| `tests/unit/infrastructure/test_cloudflare.py` | Cloudflare challenge detection |
| `tests/live/test_resolver_live.py` | Live contract tests against real hoster URLs (`-m live`) |

---

## Source Code References

| Component | Path |
|---|---|
| Port | `src/scavengarr/domain/ports/hoster_resolver.py` |
| `ResolvedStream` entity | `src/scavengarr/domain/entities/stremio.py` |
| Registry + `extract_domain()` | `src/scavengarr/infrastructure/hoster_resolvers/registry.py` |
| XFS module | `src/scavengarr/infrastructure/hoster_resolvers/xfs.py` |
| Generic DDL module | `src/scavengarr/infrastructure/hoster_resolvers/generic_ddl.py` |
| Video extraction utilities | `src/scavengarr/infrastructure/hoster_resolvers/_video_extract.py` |
| CDN verification | `src/scavengarr/infrastructure/hoster_resolvers/_verify.py` |
| Cloudflare detection | `src/scavengarr/infrastructure/browser/cloudflare.py` |
| Liveness probe | `src/scavengarr/infrastructure/hoster_resolvers/probe.py` |
| Stealth pool (Playwright) | `src/scavengarr/infrastructure/browser/stealth_pool.py` |
| Individual resolvers | `src/scavengarr/infrastructure/hoster_resolvers/<name>.py` |
| Composition wiring | `src/scavengarr/interfaces/composition.py` |
