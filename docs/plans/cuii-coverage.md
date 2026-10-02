# CUII Block List vs. Plugins and Hosters (2026-10-01)

The CUII (Clearingstelle Urheberrecht im Internet) list of domains that German ISPs block, compared with Scavengarr's plugins and hoster resolvers. Question: which streaming sites on the list lack a plugin, and which hosters do the listed sites use that no resolver handles?

## Method

- **Source:** https://cuiiliste.de/domains, read through its API (`https://api.cuiiliste.de/blocked_domains`). It lists 292 domains, grouped here by their second-level name into 87 sites.
- **Liveness:** every domain was resolved through Cloudflare's DNS-over-HTTPS and its homepage fetched (status, final host after redirects, title).
  - 266 domains resolve and 226 answer.
  - The dev container's resolver (192.168.88.2) answers like Cloudflare for every listed domain, so the network Scavengarr runs in does not apply the CUII block.
  - Deployments behind an ISP resolver would lose most German plugins.
- **Matching:** each group was matched against the plugins' `_domains`, then by redirect target.
- **Content:** sites without a match were classified from their homepage. Streaming candidates were examined further: search, detail pages and players, through the stealth browser where Cloudflare challenges.
- **Hosters:** collected from the covered plugins' live results for 4–8 titles each (films and episodes), plus the sites' own pages where captchas hide the links:
  - kinox detail pages;
  - Burning Series season pages;
  - the nox API.
- **Resolver mapping:** every hoster name or domain was mapped with `HosterResolverRegistry.canonical_hoster`, the registry built like the composition root.

## Film and Series Sites on the List

| CUII group | Listed domains | Where they lead | Plugin |
|---|---|---|---|
| kinox, kinos, kinoz | 64 subdomains | KinoZ.TO network (61 answer) | `kinox` |
| megakino, megakino1–17 | 99 | megakino21.com (44), 16 NXDOMAIN, rest timeouts or empty pages | `megakino` |
| hdfilme | 10 | hdfilme.ceo (5); hdfilme.app NXDOMAIN | `hdfilme` |
| filmpalast | filmpalast.to | filmpalast.to | `filmpalast` |
| filmpalast | filmpalast.one, .pro | filmpalast.one: **not filmpalast**, a fourth theme of the hdfilme database (Oppenheimer is news id 23684, as on hdfilme, streamcloud, streamkiste) | (hdfilme mirror group) |
| streamcloud | 5 | streamcloud.download (.forum, .plus, .press, .uno redirect there); **streamcloud.my is a gambling site** | `streamcloud` |
| serienstream | serienstream.to, .cx | serienstream.to | `sto` |
| s | s.to | NXDOMAIN worldwide (gone, not blocked) | (`sto`) |
| burningseries | burningseries.ac, .cx | burningseries.ac | `burningseries` |
| bs | bs.to, www, midas | NXDOMAIN worldwide (gone) | (`burningseries`) |
| kinoger | kinoger.com, .to, .co | Cloudflare challenge (kinoger.co does not answer) | `kinoger` |
| cine | cine.to | cine.to | `cine` |
| serienfans | serienfans.org | Cloudflare challenge | `serienfans` |
| filmfans | filmfans.org | Cloudflare challenge | `filmfans` |
| serienjunkies | 11 | serienjunkies.org, .us (others time out) | `serienjunkies` |
| nox | nox.to, .tv | nox.to | `nox` |
| kinogo | kinogo.biz, .limited, .ec | KinoGo, a **Russian** DataLife Engine site | **none** |

Covered but without Stremio streams: the links of these sites sit behind captchas.

| Site | Link gate | What the plugin delivers |
|---|---|---|
| kinox | image captcha on the link-outs | names its hosters (Dood.to, Vinovo.to), no links |
| Burning Series | reCAPTCHA v2 | downloads only |
| cine | reCAPTCHA gateway | downloads only |
| nox | ALTCHA unlock at grab time | Torznab results only |

## Missing Plugins

| Site | Content | Assessment |
|---|---|---|
| **kinogo** (kinogo.biz, .limited; .ec behind Cloudflare) | Films and series, Russian dubs only. DataLife Engine with its own player `cinemar.cc` (HLS from `cinemap.cc`) | The only film/series site on the list without a plugin. It needs a plugin and a `cinemar` resolver, with language `ru`. **Recommendation: skip**, Russian audio only. |
| filmpalast.one / .pro | Same database as hdfilme, streamcloud and streamkiste | No new titles or links. It could join the `hdfilme` mirror group as one more domain. **Recommendation: skip.** |

Every other film or series site on the list has a plugin. The rest of the list is out of scope for Torznab movies/TV and Stremio:

- **Live sports, 31 groups:** buffsports, buffstreams, footybitex, harleyquinnwidget (no answer, listed with the sports sites), hesgoalguide, jokerlivestream, jokertvguide, livetv, livetv882/901/902/903, newerastreams, qatarstreams, soccerbox, socceronline, soccerworldcup, sportplus (seized), stikeout, streamed, tazz, tazztv, tennis, totalsportek24, totalsportekz, vipbox, vipboxtv, vipleague, vipleaguestreams, viprow, vipstand. These are live events, not titles with IMDb ids.
- **E-books and papers, 4:** annas-archive, libgen, sci-hub, ibooks.
- **Music, 7:** canna, canna-power (German MP3 board), getrockmusic, israbox, israbox-music, isrbx, newalbumreleases. canna is the only German one; a Torznab audio plugin would be a separate decision.
- **Games and ROMs, 11:** fitgirl-repacks, nswgame, nswpedia, nxbrew, romslab, romsns, rpgonly, switchrom, switchroms, taodung, ziperto.

## Hosters of the Listed Sites

| Hoster | Used by | Resolver | Notes |
|---|---|---|---|
| VOE (voe.sx, random mirror domains) | filmpalast, megakino, s.to, Burning Series, cine; kinoger.ru redirects to a VOE mirror | `voe` | plays |
| DoodStream (dood.to, doodstream.com, myvidplay.com, playmogo.com) | hdfilme/streamcloud/streamkiste, kinox, Burning Series, s.to, kinoger | `doodstream` | plays (browser) |
| Filemoon | Burning Series | `filemoon` | plays (browser, slow) |
| Vidmoly | Burning Series | `vidmoly` (XFS) | plays |
| Dropload (dr0pstream.com) | hdfilme network | `dropload` (XFS) | captcha, about 7 s |
| Mixdrop (mxdrop.to) | hdfilme network | `mixdrop` | plays (since 2026-10-01) |
| SuperVideo | hdfilme network | `supervideo` | its CDN answers 429 |
| Vinovo | kinox | `vinovo` | plays |
| Firestream | filmpalast, kinoger | `firestream` | plays |
| Vidara (vidaraa.cc) | filmpalast | `strmup` | plays |
| Vidsonic | filmpalast | `vidsonic` | plays |
| Strmup | filmpalast | `strmup` | plays |
| Veev | megakino, kinoger | `veev` | plays |
| Streamtape, Vidoza | cine | `streamtape`, `vidoza` | cine serves downloads only |
| Rapidgator, DDownload, 1fichier, Katfile, Turbobit, Uploaded, Filer.net | serienjunkies, serienfans, filmfans (the latter two in filecrypt containers) | all registered (DDL validation) | Torznab only |
| gxplayer (watch.gxplayer.xyz) | megakino ("Stream in HD", the second link of each film) | `gxplayer` (since 2026-10-02) | HLS master from the watch page's video object (port of JDownloader's `GxplayerXyz`); 5 of 5 films play |
| kinoger.pw | kinoger ("Stream HD" tab; the only one on some series such as "Dune: Prophecy") | `strmup` (since 2026-10-02) | a white-label Vidara player (`POST /api/stream`); claimed by host, since kinoger.ru links redirect to VOE; 4 of 4 play |
| fsst (fsst.online) | kinoger (first player tab; the only one on its single-player pages) | `fsst` (stream extraction since 2026-10-02) | Kernel Video Sharing player, Playerjs quality list, best `get_file` MP4; 8 of 8 play. The former DDL config matched no kinoger link |
| kinoger.p2pplay.pro | none (0 of 19 current kinoger pages) | none, not built | an obfuscated single-page app with AES-encrypted API answers and ad gating; the only known id (`#n6lc6`, from old plugin docs) starts no stream in a browser |
| **rubyvidhub** (rubyvidhub.com) | kinoger ("go" player tab, 1 of 26 collected links) | **none** | origin down: Cloudflare 522 on two probes an hour apart (2026-10-02), streamruby.com as well; not analysable, not built |
| **cinemar.cc** | kinogo | **none** | only relevant with a kinogo plugin |

Since 2026-10-02 the player hosters of the covered sites resolve, except three:

- **rubyvidhub**: its server was down while checked; recheck before building a resolver (it may be a StreamRuby mirror).
- **p2pplay**: no current kinoger page links it.
- **cinemar.cc**: only kinogo uses it, and kinogo has no plugin.

## Follow-ups

- Done with this analysis: streamcloud's fallback domain `streamcloud.my` became an online-slot site and was replaced by `streamcloud.plus`. A probe of all 89 plugin domains found no other parked or hijacked domain.
- Done 2026-10-02:
  - resolvers for gxplayer (`gxplayer.py`), kinoger.pw (`strmup`, host claim) and fsst streams (`fsst.py`);
  - two kinoger parser bugs found on the way: films were labelled as series (no film results), and pages with a single player returned no links (9 of 19 pages).
- Open:
  - rubyvidhub, once its server answers again;
  - no kinogo plugin unless Russian sources are wanted.
