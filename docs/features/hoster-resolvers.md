[← Back to Index](./README.md)

# Hoster Resolver System

> Validates file availability and extracts direct video URLs from streaming hosters.

---

## Overview

Scavengarr includes **39 hoster resolvers** that validate whether a file on a hosting service is still available. Streaming resolvers additionally extract a direct `.mp4`/`.m3u8` video URL from an embed page.

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

---

## Resolver Categories

### Streaming resolvers

Extract a direct video URL (`.mp4`/`.m3u8`) from an embed page.

| Resolver | Domains | Technique |
|---|---|---|
| VOE | voe.sx + mirrors | Multi-method: JSON extraction, obfuscated JS |
| Streamtape | streamtape.com + mirrors | Token extraction from page source |
| SuperVideo | supervideo.cc | XFS extraction + Playwright Cloudflare fallback |
| DoodStream | dood.wf + mirrors | `pass_md5` API endpoint extraction |
| Filemoon | filemoon.sx + mirrors | Packed JS unpacker + Byse SPA challenge flow |
| Mixdrop | mixdrop.ag + mirrors | Token extraction, multi-domain |
| VidGuard | vidguard.to + mirrors | Multi-domain embed resolution |
| Vidking | vidking.online | Embed page validation |
| Stmix | stmix.io | Embed page validation |
| SerienStream | s.to, serien.sx | Domain matching + page validation |

### DDL resolvers (non-XFS)

Validate file availability without extracting a video URL. Return the canonical file URL.

| Resolver | Domains | Technique |
|---|---|---|
| Filer.net | filer.net | Public status API |
| Rapidgator | rapidgator.net | Website scraping |
| DDownload | ddownload.com, ddl.to | XFS page check + canonical URL normalization |
| Alfafile | alfafile.net | Page scraping |
| AlphaDDL | alphaddl.com | Page scraping |
| Fastpic | fastpic.org, fastpic.ru | Image host validation |
| Filecrypt | filecrypt.cc | Container validation |
| FileFactory | filefactory.com | Page scraping |
| FSST | fsst.me | Page scraping |
| Go4up | go4up.com | Mirror link validation |
| Nitroflare | nitroflare.com, nitro.download | Page scraping |
| 1fichier | 1fichier.com + mirrors | Page scraping, multi-domain |
| Turbobit | turbobit.net + mirrors | Page scraping, multi-domain |
| Uploaded | uploaded.net, ul.to | Page scraping, multi-domain |

### XFS consolidated resolvers

15 XFileSharingPro-based hosters are consolidated into a single generic `XFSResolver` with parameterised `XFSConfig` in `xfs.py`. Each hoster is described by:

- **name** — resolver identifier
- **domains** — `frozenset` of second-level domain names for URL matching
- **file_id_re** — compiled regex to extract the 12-char alphanumeric file ID
- **offline_markers** — tuple of strings that indicate the file is deleted/expired

| Hoster | Domains | Notes |
|---|---|---|
| Katfile | katfile | Custom offline markers |
| Hexupload | hexupload | Standard markers |
| Clicknupload | clicknupload, clickndownload | Multi-domain |
| Filestore | filestore | Standard markers |
| Uptobox | uptobox, uptostream | Multi-domain |
| Funxd | funxd | `/e/`/`/d/` path prefix |
| Bigwarp | bigwarp | Extended markers |
| Dropload | dropload | Extended markers |
| Goodstream | goodstream | Extended markers |
| Savefiles | savefiles | Extended markers |
| Streamwish | 9 domains | Extended + custom markers |
| Vidmoly | vidmoly | `/w/` path prefix support |
| Vidoza | vidoza, videzz | Custom markers |
| Vinovo | vinovo | 12+ char file IDs |
| Vidhide | 6 domains | Lowercase-only file IDs |

Adding a new XFS hoster requires only adding an `XFSConfig` constant and appending it to `ALL_XFS_CONFIGS`. Tests are automatically parameterised.

---

## Registry Features

| Feature | Description |
|---|---|
| Domain matching | Match URL domain to appropriate resolver |
| Content-type probing | Fallback for unrecognized domains — probe URL for direct video links |
| Redirect following | Follow HTTP redirects to find final domain |
| Hoster hint support | Plugin-provided hoster name for rotating domains |
| Stream preflight | Filter dead hoster links before `/stream` resolution |

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

The XFS resolver tests are parameterised over all 15 configs (219 test cases total).

Live contract tests in `tests/live/test_resolver_live.py` validate resolvers against real hoster URLs.

---

## Source Code

| Component | Path |
|---|---|
| XFS module (15 hosters) | `src/scavengarr/infrastructure/hoster_resolvers/xfs.py` |
| Registry | `src/scavengarr/infrastructure/hoster_resolvers/registry.py` |
| Content-type probe | `src/scavengarr/infrastructure/hoster_resolvers/probe.py` |
| Individual resolvers | `src/scavengarr/infrastructure/hoster_resolvers/<name>.py` |
| XFS tests | `tests/unit/infrastructure/test_xfs_resolver.py` |
| Resolver tests | `tests/unit/infrastructure/test_<name>_resolver.py` |
| Live tests | `tests/live/test_resolver_live.py` |
