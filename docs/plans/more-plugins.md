# Plan: Additional Plugins

**Status:** Mostly Complete (34 plugins implemented)
**Priority:** Low (ongoing)
**Related:** `plugins/`, `docs/features/python-plugins.md`

## Current State

Scavengarr ships with **34 plugins** covering German streaming, DDL, and anime sites:

### Httpx plugins (22)

| Plugin | Site | Type | Notes |
|---|---|---|---|
| aniworld | aniworld.to | stream | Anime, domain fallback |
| burningseries | bs.to | stream | Series only |
| cine | cine.to | stream | JSON API |
| dataload | data-load.me | download | DDL forum, vBulletin auth |
| einschalten | einschalten.in | stream | JSON API |
| filmfans | filmfans.org | download | Release parsing via API |
| fireani | fireani.me | stream | Anime, JSON API |
| haschcon | haschcon.com | stream | |
| hdfilme | hdfilme.legal | stream | MeineCloud link extraction |
| kinoger | kinoger.com | stream | Domain fallback |
| kinoking | kinoking.cc | stream | Movie/series detection |
| kinox | kinox.to | stream | 9 mirror domains, AJAX embeds |
| megakino | megakino.me | stream | |
| megakino_to | megakino.org | stream | JSON API |
| movie2k | movie2k.cx | stream | 2-stage HTML scraping |
| movie4k | movie4k.sx | stream | JSON API, multi-domain |
| myboerse | myboerse.bz | download | DDL forum, multi-domain |
| nima4k | nima4k.org | download | Category browsing |
| serienfans | serienfans.org | download | TV series DDL, JSON API, season/episode |
| sto | s.to | stream | TV-only |
| streamcloud | streamcloud.plus | stream | Domain fallback |
| streamkiste | streamkiste.taxi | stream | 5 mirror domains |

### Playwright plugins (9)

| Plugin | Site | Type | Notes |
|---|---|---|---|
| animeloads | anime-loads.org | stream | DDoS-Guard bypass |
| boerse | boerse.sx | download | Cloudflare + vBulletin auth |
| byte | byte.to | download | Cloudflare, iframe links |
| ddlspot | ddlspot.com | download | Pagination up to 1000 |
| ddlvalley | ddlvalley.me | download | WordPress pagination |
| moflix | moflix-stream.xyz | stream | Internal API, Cloudflare |
| mygully | mygully.com | download | Cloudflare + vBulletin auth |
| scnsrc | scnsrc.me | download | Scene releases, multi-domain |
| streamworld | streamworld.ws | stream | Playwright mode |

### YAML plugins (3)

| Plugin | Site | Type | Notes |
|---|---|---|---|
| filmpalast_to | filmpalast.to | stream | Original plugin |
| scnlog | scnlog.me | download | Scene log, pagination |
| warezomen | warezomen.com | download | Converted from Python |

## Remaining Candidates

Sites not yet covered that could benefit from plugins:

- **serienjunkies.org** — Captcha-protected DDL with multi-step extraction
- **dokustream.de** — Documentary streaming
- **filmkiste.to** — Movie/TV streaming
- **xcine.me** — German movie streaming
- **goldstreamtv.com** — German streaming aggregator

## Plugin Quality Checklist

Every new plugin must meet these standards:

- [ ] Uses `HttpxPluginBase` or `PlaywrightPluginBase` (no raw client setup)
- [ ] Configurable settings at top of file with section headers (`_DOMAINS`, `_MAX_PAGES`, etc.)
- [ ] Category filtering via site's filter system mapped to Torznab categories
- [ ] Pagination up to 1000 items (`_MAX_PAGES` based on results-per-page)
- [ ] Bounded concurrency via `self._new_semaphore()` (default 3)
- [ ] `season`/`episode` params in `search()` signature
- [ ] `provides` attribute set to `"stream"` or `"download"`
- [ ] `default_language` attribute set (typically `"de"`)
- [ ] Unit tests with mocked HTTP responses
- [ ] Live smoke test entry in `tests/live/`
- [ ] Handles missing fields gracefully (partial results, not crashes)
- [ ] Uses `self._safe_fetch()` / `self._safe_parse_json()` for error handling
