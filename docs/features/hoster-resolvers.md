[← Back to Index](./README.md)

# Hoster Resolver System

> Validates file availability and extracts direct video URLs from streaming hosters.

---

## Overview

Scavengarr includes **56 hoster resolvers** that validate whether a file on a hosting service is still available. Streaming resolvers additionally extract a direct `.mp4`/`.m3u8` video URL from an embed page.

Resolvers are registered in the `HosterResolverRegistry`, which matches incoming URLs to the appropriate resolver by domain. A content-type probing fallback handles unrecognized domains.

---

## Architecture

```
URL → HosterResolverRegistry
      ├── Domain match → specific resolver → ResolvedStream | None
      └── No match → ContentTypeProbe → ResolvedStream | None
```

All resolvers implement the same interface:

```python
class HosterResolverPort(Protocol):
    @property
    def name(self) -> str: ...
    async def resolve(self, url: str) -> ResolvedStream | None: ...
```

Returns `ResolvedStream(video_url=..., quality=..., headers=...)` on success, `None` when the file is offline/deleted.

### Playback Headers

Streaming resolvers include an `headers` dict on `ResolvedStream` with the HTTP headers required for CDN playback (typically `Referer`). These headers are forwarded to Stremio via `behaviorHints.proxyHeaders` so the player's streaming server sends them when fetching the video.

| Resolver | Required Headers |
|---|---|
| VOE | `Referer: <embed_url>` |
| Filemoon | `Referer: <embed_url>` |
| SuperVideo | `Referer: <embed_url>` |
| Streamtape | `Referer: <host>/` |
| DoodStream | `Referer: <base_url>` |
| XFS video hosters | `Referer: <embed_url>` |
| StreamUp (strmup) | `Referer: <embed_url>` |
| Vidsonic | `Referer: <embed_url>` |
| SendVid | `Referer: <embed_url>` |

---

## Resolver Categories

### Streaming resolvers (individual)

Extract a direct video URL (`.mp4`/`.m3u8`) from an embed page.

| Resolver | Domains | Technique |
|---|---|---|
| VOE | voe.sx + mirrors | Multi-method: JSON extraction, obfuscated JS |
| Streamtape | streamtape.com + mirrors | Token extraction from page source |
| SuperVideo | supervideo.cc | XFS extraction + StealthPool Cloudflare fallback |
| DoodStream | dood.wf + mirrors | `pass_md5` API endpoint extraction |
| Filemoon | filemoon.sx + mirrors | Packed JS unpacker + Byse SPA challenge flow |
| VidGuard | vidguard.to + mirrors | Multi-domain embed resolution |
| Vidking | vidking.online | Embed page validation (movie + series paths) |
| Stmix | stmix.io | Embed page validation |
| SerienStream | s.to, serien.sx | Domain matching + page validation |
| SendVid | sendvid.com | Two-stage: API status check + page video extraction |
| StreamUp (strmup) | strmup.com, vidara | HLS extraction with page + AJAX fallback |
| Vidsonic | vidsonic.com | HLS extraction with hex-obfuscated URL decoding |

### DDL resolvers (individual)

Validate file availability without extracting a video URL. Return the canonical file URL.

| Resolver | Domains | Technique |
|---|---|---|
| Filer.net | filer.net | Public status API |
| Rapidgator | rapidgator.net | Website scraping |
| DDownload | ddownload.com, ddl.to | XFS page check + canonical URL normalization |
| Mediafire | mediafire.com | Public file info API, offline via error 110 + delete_date |
| GoFile | gofile.io | Ephemeral guest token (25-min cache), content availability API |

### Generic DDL resolvers (12 hosters consolidated)

12 DDL-based hosters are consolidated into a single `GenericDDLResolver` with parameterised `GenericDDLConfig` in `generic_ddl.py`.

| Hoster | Domains | Notes |
|---|---|---|
| Alfafile | alfafile | Page scraping |
| AlphaDDL | alphaddl | Page scraping |
| Fastpic | fastpic | Image host (org + ru domains) |
| Filecrypt | filecrypt | Container validation |
| FileFactory | filefactory | Page scraping |
| FSST | fsst | Page scraping |
| Go4up | go4up | Mirror link validation |
| Mixdrop | mixdrop | Multi-domain, token extraction |
| Nitroflare | nitroflare, nitro | Page scraping |
| 1fichier | 1fichier + mirrors | Multi-domain page scraping |
| Turbobit | turbobit + mirrors | Multi-domain page scraping |
| Uploaded | uploaded, ul | Multi-domain page scraping |

Adding a new generic DDL hoster requires only adding a `GenericDDLConfig` constant and appending it to `ALL_DDL_CONFIGS`. Tests are automatically parameterised.

### XFS consolidated resolvers (27 hosters)

27 XFileSharingPro-based hosters are consolidated into a single generic `XFSResolver` with parameterised `XFSConfig` in `xfs.py`. Each hoster is described by:

- **name** — resolver identifier
- **domains** — `frozenset` of second-level domain names for URL matching
- **file_id_re** — compiled regex to extract the file ID
- **offline_markers** — tuple of strings that indicate the file is deleted/expired
- **is_video_hoster** — `True` for streaming hosters (extract video URL from embed page)
- **needs_captcha** — `True` for hosters requiring Cloudflare Turnstile (return `None`)
- **extra_domains** — JDownloader-sourced domain aliases

#### DDL hosters (validate only)

| Hoster | Domains | Notes |
|---|---|---|
| Katfile | katfile | Custom offline markers |
| Hexupload | hexupload | Standard markers |
| Clicknupload | clicknupload, clickndownload | Multi-domain |
| Filestore | filestore | Standard markers |
| Uptobox | uptobox, uptostream | Multi-domain |
| Hotlink | hotlink | Standard markers |

#### Video hosters (extract video URL)

Fetch `/e/{file_id}` embed page, extract HLS/MP4 URL via JWPlayer config, Dean Edwards packed JS, or Streamwish `hls2` patterns. After extraction, a **HEAD verification** against the CDN URL filters out IP-locked CDN tokens (e.g. LULUVID/LULUVDOO whose tokens are bound to Cloudflare's edge IP). Only URLs returning 200/206 are returned as `ResolvedStream` with `Referer` header.

| Hoster | Domains | Notes |
|---|---|---|
| Funxd | funxd | `/e/`/`/d/` path prefix |
| Bigwarp | bigwarp | Two-step form POST for splash pages |
| Dropload | dropload | Extended markers |
| Goodstream | goodstream | Extended markers |
| Savefiles | savefiles | Two-step form POST |
| Streamwish | 9+ domains (obeywish, awish, embedwish, ...) | `hls2` pattern + extended markers |
| Vidmoly | vidmoly | `/w/` path prefix support |
| Vidoza | vidoza, videzz | Custom markers |
| Vidhide | 6+ domains (filelions, streamhide, louishide, ...) | Lowercase-only file IDs |
| Mp4Upload | mp4upload | Standard extraction |
| Uqload | uqload | Standard extraction |
| Vidshar | vidshar | Standard extraction |
| Vidroba | vidroba | Standard extraction |
| Vidspeed | vidspeed | Standard extraction |
| StreamRuby | streamruby | Two-step form POST |
| Lulustream | lulustream | Standard extraction |
| Upstream | upstream | Standard extraction |
| Vidnest | vidnest | Standard extraction |

#### Captcha-required (return None)

| Hoster | Domains | Notes |
|---|---|---|
| Veev | veev | Cloudflare Turnstile required |
| Vinovo | vinovo | Cloudflare Turnstile required |
| Wolfstream | wolfstream | Anti-bot JS redirect |

Adding a new XFS hoster requires only adding an `XFSConfig` constant and appending it to `ALL_XFS_CONFIGS`. Tests are automatically parameterised.

### Shared video extraction

The `_video_extract.py` module provides shared utilities for extracting video URLs from embed pages, used by both XFS and Filemoon resolvers:

- **JWPlayer config extraction** — parse `sources: [{file: "..."}]` from page JS
- **Dean Edwards packed JS unpacking** — decode `eval(function(p,a,c,k,e,d)...)`
- **Streamwish `hls2` pattern** — extract HLS URL from `var hls2 = "..."` assignments

---

## Registry Features

| Feature | Description |
|---|---|
| Domain matching | Match URL domain to appropriate resolver |
| Content-type probing | Fallback for unrecognized domains — probe URL for direct video links |
| Redirect following | Follow HTTP redirects to find final domain |
| Hoster hint support | Plugin-provided hoster name for rotating domains |
| Stream preflight | Filter dead hoster links before `/stream` resolution |
| Domain alias mapping | All `supported_domains` from XFS/DDL resolvers are mapped (e.g., vidhide family aliases) |
| Cache eviction | Periodic eviction of expired entries prevents unbounded memory growth |

---

## Testing

All resolver tests use **respx** (httpx-native HTTP mocking) for realistic test coverage:

```python
@respx.mock
@pytest.mark.asyncio()
async def test_resolves_valid_file(self) -> None:
    respx.get(url).respond(200, text=html)
    async with httpx.AsyncClient() as client:
        result = await Resolver(http_client=client).resolve(url)
    assert result is not None
```

The XFS resolver tests are parameterised over all 27 configs. The generic DDL tests are parameterised over all 12 configs.

Live contract tests in `tests/live/test_resolver_live.py` validate resolvers against real hoster URLs.

---

## Source Code

| Component | Path |
|---|---|
| XFS module (27 hosters) | `src/scavengarr/infrastructure/hoster_resolvers/xfs.py` |
| Generic DDL module (12 hosters) | `src/scavengarr/infrastructure/hoster_resolvers/generic_ddl.py` |
| Video extraction utilities | `src/scavengarr/infrastructure/hoster_resolvers/_video_extract.py` |
| Registry | `src/scavengarr/infrastructure/hoster_resolvers/registry.py` |
| Content-type probe | `src/scavengarr/infrastructure/hoster_resolvers/probe.py` |
| Stealth pool (Playwright) | `src/scavengarr/infrastructure/hoster_resolvers/stealth_pool.py` |
| Individual resolvers | `src/scavengarr/infrastructure/hoster_resolvers/<name>.py` |
| XFS tests | `tests/unit/infrastructure/test_xfs_resolver.py` |
| Generic DDL tests | `tests/unit/infrastructure/test_generic_ddl_resolver.py` |
| Resolver tests | `tests/unit/infrastructure/test_<name>_resolver.py` |
| Live tests | `tests/live/test_resolver_live.py` |
