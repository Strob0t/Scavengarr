# Header-Bound Hosters (Row 59)

Status: step 1 measured 2026-10-11 with `scripts/probes/header_binding.py` (21600e2; its byte-range reading 05ea203). No hoster needs a change. The one hoster bound on the `play` route is VEEV, and `/play` already resolves VEEV per player. The row ends with the tables and the next hypotheses (step 3 of row 59 in `docs/plans/ideas-backlog.md`).

## Question

The maintainer's player answered a stream with "Format/Quelldatei wird nicht unterstützt" (the second report). The hypothesis: a hoster's CDN answers the registry's checks but not the player. The checks send the playback headers (`verify_video_url()` and `check_playable()` in `src/scavengarr/infrastructure/hoster_resolvers/_verify.py`: the stored headers, `Referer`, `Origin` or a resolver's User-Agent, over the browser User-Agent). The player fetches a `/play` redirect's target itself, with its own User-Agent and without them.

Stremio reaches a stream by one of three routes (`build_stream_from_resolved()` in `src/scavengarr/application/stremio/stream_builder.py`):

- `proxy`: HLS through Scavengarr's proxy, which asks the CDN from the server with the stored headers over the browser User-Agent (`_player_headers()` in `src/scavengarr/infrastructure/stremio/hls_proxy.py`).
- `file`: an address-bound file (DoodStream, MixDrop, Vinovo, FSST) through the pass-through `/proxy/<id>/file`, with the same headers.
- `play`: every other file, a redirect to the CDN that the player follows itself.

The first two routes send the check's headers from the check's address, so only the `play` route can fail on a header binding.

## Method

The probe asks the CDN of each stored resolution for its first 2 KiB (a `Range` GET, redirects followed, a fresh client per request, so no cookie carries over). It asks in up to four ways:

- `check`: the check's headers.
- `bare`: a Firefox User-Agent and nothing else, the player of the hypothesis.
- `player UA`: the stored headers with the Firefox User-Agent.
- `check UA`: the check's User-Agent without the stored headers.

The last two ways run only when headers other than the User-Agent are stored. The verdict names what the bare player lacks: `free`, `user-agent`, `headers` or `user-agent + headers`. It says `dead here` when the check itself gets no media from the dev container.

When the check gets a playlist, the probe also reads the media playlist (a master's first variant) as the proxy reads it. It names whether the playlist addresses its segments by byte range (`EXT-X-BYTERANGE`, or an `EXT-X-MAP` with `BYTERANGE`). This is the advisor's hypothesis for the HLS titles: the proxy's segment route asks the CDN without the player's `Range` header and answers 200, so a byte-range segment would arrive as the whole file.

Both sources are fetched from the dev container on the LAN, as the maintainer's player fetches them:

- **production**: the links in production's cache, read through Portainer, resolved from the production host's address. The probe sends no stream request to production.
- **dev**: the live suite's hoster links (`scripts/probes/resolver_urls.py`), resolved in the dev container with the app's registry. A file bound to the resolving address plays from here, so only a header binding shows. Two link sets: the suite's of 2026-10-10 and a set collected 2026-10-11 from 11:23 to 11:29 UTC.

```bash
PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/header_binding.py --source production --since 5400
PYTHONPATH=src xvfb-run -a poetry run python -P scripts/probes/header_binding.py --source dev --urls <links file>
```

## Production: The Eleventh Round's Links

The probe read 90 links (distinct video URLs) stored after the rebuild at 10:58 UTC: the deploy check's and the eleventh round's (`docs/plans/stremio-latency.md` → Eleventh round). It asked them from 11:13 to 11:17 UTC. It asked the 66 HLS links again for byte ranges from 11:28 to 11:31 UTC.

| Hoster | Route | Links | CDN | Stored headers | check | bare | player UA | check UA | Verdict | Byte ranges |
|---|---|---|---|---|---|---|---|---|---|---|
| doodstream | file | 10 | cloudatacdn.com | Referer | 200 html | ConnectError | 200 html | ConnectError | dead here | – |
| fsst | file | 2 | incvideo1.online | – | 410 html | 410 html | – | – | dead here | – |
| mixdrop | file | 4 | mxcontent.net | Referer | 403 html | 403 html | 403 html | 403 html | dead here | – |
| vinovo | file | 8 | vincdn.net | Referer, User-Agent | 403 html | 403 html | 403 html | 403 html | dead here | – |
| firestream | proxy | 4 | firestream.to | – | 206 playlist | 206 playlist | – | – | free | no ×4 |
| gxplayer | proxy | 4 | gxplayer.xyz | – | 200 playlist | 200 playlist | – | – | free | no ×4 |
| playmate | proxy | 1 | plauymito.live | – | 200 playlist | 200 playlist | – | – | free | no ×1 |
| strmup | proxy | 5 | 97bf1.com | Origin, Referer | 200/206 playlist | 200/206 playlist | 200/206 playlist | 200/206 playlist | free | no ×5 |
| vidmoly | proxy | 11 | vmbox.space, vmnow.online, vmpx.online | Referer | 200/206 playlist | 200/206 playlist | 200/206 playlist | 200/206 playlist | free | no ×11 |
| vidsonic | proxy | 5 | vidsonic.net | Origin, Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no ×5 |
| voe | proxy | 36 | cloudwindow-route.com | Referer | 403 html | 403 html | 403 html | 403 html | dead here | – |

No link took the `play` route.

## Dev Container: The Live Suite's Links

Three runs: 11:17 to 11:19 UTC on the 2026-10-10 set, 11:30 to 11:32 UTC on the 2026-10-11 set, and 11:33 to 11:34 UTC on the 2026-10-10 links of VEEV, VidHide and Playmate again, with byte ranges. The last column names the set whose link resolved. Where both sets resolved, every way answered alike.

| Hoster | Route | CDN | Stored headers | check | bare | player UA | check UA | Verdict | Byte ranges | Resolved |
|---|---|---|---|---|---|---|---|---|---|---|
| streamtape | play | streamtape.com | Referer | 206 mp4 | 206 mp4 | 206 mp4 | 206 mp4 | free | – | both sets |
| veev | play | veevcdn.co | Referer, User-Agent | 206 mp4 | 403 html | 403 html | 206 mp4 | user-agent | – | 2026-10-10 |
| doodstream | file | cloudatacdn.com | Referer | 206 mp4 | ConnectError | 206 mp4 | ConnectError | headers | – | both sets |
| fsst | file | incvideo1.online | – | 206 mp4 | 206 mp4 | – | – | free | – | both sets |
| mixdrop | file | mxcontent.net | Referer | 206 mp4 | 403 html | 403 html | 206 mp4 | user-agent | – | both sets |
| vinovo | file | vincdn.net | Referer, User-Agent | 206 mp4 | 403 html | 403 html | 206 mp4 | user-agent | – | both sets |
| filemoon | proxy | owphbf24.com, r66nv9ed.com | Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no | both sets |
| firestream | proxy | firestream.to | – | 206 playlist | 206 playlist | – | – | free | no | both sets |
| gxplayer | proxy | gxplayer.xyz | – | 200 playlist | 200 playlist | – | – | free | no | both sets |
| playmate | proxy | plauymito.live | – | 200 playlist | 200 playlist | – | – | free | no | 2026-10-10 |
| strmup | proxy | 97bf1.com | Origin, Referer | 206 playlist | 206 playlist | 206 playlist | 206 playlist | free | no | both sets |
| vidhide | proxy | dramiyos-cdn.com, acek-cdn.com | Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no | 2026-10-10 |
| vidsonic | proxy | vidsonic.net | Origin, Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no | both sets |
| vixeo | proxy | vidsonic.net | Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no | 2026-10-11 |
| voe | proxy | cloudwindow-route.com | Referer | 200 playlist | 200 playlist | 200 playlist | 200 playlist | free | no | both sets |

No link of Dropload, RPMPlay, SuperVideo, UPNS or Vidoza resolved in either set: the registry answered no stream.

## Reading

1. **No production link took the `play` route.** Since step 26 the address-bound files take `/proxy/<id>/file` and HLS takes the proxy. All 90 stored links of the eleventh round took one of the two. The `play` route keeps the files of hosters that are neither HLS nor address-bound: of the measured ones, StreamTape and VEEV.
2. **VEEV is the only hoster bound on the `play` route, and it is the known case.** Its CDN answers the stored User-Agent with or without the Referer, and refuses the Firefox User-Agent with or without it: the binding is the User-Agent alone. VEEV implements `ClientBoundResolverPort` (`src/scavengarr/infrastructure/hoster_resolvers/veev.py`), so `/play` resolves it again with the player's User-Agent and Accept-Language. Step 2 of the row names that port as the remedy for a User-Agent binding, so nothing changes. StreamTape answers every way.
3. **The file route's bindings are what the pass-through sends.** Resolved here, DoodStream's CDN wants the Referer, MixDrop's and Vinovo's want the check's User-Agent (Vinovo's is stored with the link), and FSST's wants nothing. The pass-through sends the stored headers over the browser User-Agent, the check's headers. From the LAN, production's links of all four are dead every way (an HTML page, 403 or 410): they are bound to the production host's address, which the pass-through supplies. The eleventh round's file branch played 9 of 9 such files through it.
4. **The HLS playlists answer the LAN, except VOE's production links.** VOE's production playlists answer 403 every way from here, and the dev container's VOE link answers every way: VOE binds the playlist to the resolving address (or to a lifetime of minutes), and the proxy asks from the production host.
5. **No media playlist uses byte ranges.** 39 media playlists of 10 hosters (30 from production, 9 from the dev container) carry neither `EXT-X-BYTERANGE` nor a byte-range `EXT-X-MAP`. The proxy's segment route, which forwards no `Range` and answers 200, breaks none of them that way. The `200 instead of 206` answers to the play check's 2 KiB range on HLS segments (eleventh round) stay harmless here; row 64 keeps that finding to files and byte-range playlists.
6. **Production's logs show reachability, not header binding, for the hosters the advisor named.** The registry's playback checks of VEEV failed with `ConnectTimeout` five times from 11:03 to 11:14 UTC (`prodctl.py logs --grep veev`). A check error answers no stream, so production served no VEEV stream in the round, while veevcdn answered the dev container. FIRESTREAM's one failure in the round was a 30 s timeout, and its playlists answer the LAN. SuperVideo's breaker is open after an unplayable outcome, and Filemoon's, Dropload's and Vixeo's opened after timeouts (the digest). Whether a player shows the report's message for a stalled or failed answer is unverified.

## Limits

- One link per hoster and link set from the dev container. Production's window had no `play` link, so the `play` route's evidence is two hosters from the dev container.
- Dropload, RPMPlay, SuperVideo, UPNS and Vidoza had no link resolve here and none stored in production's window. Their CDNs are unmeasured.
- The probe's player is one Firefox User-Agent without `Accept-Language`. VEEV's `Accept-Language` binding (production, 2026-10-06) was not tested again.
- The byte-range reading covers a master's first variant only, not its other variants or audio renditions.
- The report's own stream (title, hoster, time) is unknown. The tables test the hypothesis on today's streams, not on the reported one.

No resolver, route or flag changed, so the row's live proof is these tables.

## Next Hypotheses

The header hypothesis explains the report for no measured hoster. What remains, cheapest first:

1. **The device log.** The maintainer can read it in Stremio's settings right after the message. The stream's address there names its route (`/play/<id>`, `/proxy/<id>/file` or the proxy's playlist), and the player's error separates a refused answer from an unsupported format. Only the maintainer can read it, and it decides between the next two hypotheses.
2. **The player's codecs.** Every file the dev container got starts as MP4. The codecs inside (H.264 or HEVC video, AAC or AC-3/E-AC-3 audio) are unread, and a player without HEVC or AC-3 decoding, a browser among them, can refuse such a file. Measurable without a player: the MP4's `stsd` box (in the `moov`, at the start of a faststart file) and the HLS master's `CODECS` attribute.
3. **The HLS variant beyond byte ranges.** fMP4 segments (`EXT-X-MAP`), HEVC variants or separate audio renditions (`EXT-X-MEDIA`), which some players do not support. The probe reads a master's first variant only.


## The Codecs of the Round's Streams (Row 66)

Hypothesis 2, measured: the round of `docs/plans/round-titles.txt` against production on 2026-10-11, 12:11:40 to 12:17:46 UTC (`scripts/stremio_round.py` from the dev container, each title once, in sequence; no playback through the proxy in the 5 minutes before, by production's log; Mr. Normal, row 66's play check at 0a6deac). Headline: 18 of 19 titles with streams (Breaking Bad S01E02 none), 72 streams, 71 playable; the one failure is FIRESTREAM on Der Schuh des Manitu (`FAIL hls codecs unread, 1272 segments: 502, 502`, the CDN's 502 as in row 64's proof). First answers: median 3.1 s, max 7.3 s (3 HIT, 15 STALE).

| Hoster | Codecs | Streams |
|---|---|---|
| DOODSTREAM | codecs unread | 1 |
| DOODSTREAM | h264/aac | 6 |
| FIRESTREAM | codecs unread | 1 |
| FIRESTREAM | h264/aac | 2 |
| FSST | h264/aac | 5 |
| GXPLAYER | h264/aac | 3 |
| MIXDROP | h264/aac | 2 |
| PLAYMATE | h264/aac | 1 |
| STRMUP | h264/aac | 7 |
| VIDMOLY | h264/aac | 10 |
| VIDSONIC | h264/aac | 5 |
| VINOVO | h264/aac | 6 |
| VOE | h264/aac | 23 |

**No hoster delivered HEVC, AC-3 or E-AC-3**: all 70 streams whose codecs were read are H.264 video with AAC audio, over all 11 hosters, files (DoodStream, FSST, MixDrop, Vinovo) and HLS (VOE, Vidmoly, STRMUP, Vidsonic, GXPlayer, FireStream, Playmate) alike. So for this sample hypothesis 2 does not explain "Format/Quelldatei wird nicht unterstützt": no stream of the round needs a decoder a browser lacks.

Limits:
- 2 of 72 streams unread: the failing FIRESTREAM (no segment came) and one DOODSTREAM file (the round prints no verdict for a playable stream, so its reason is not recorded: a `moov` beyond the last 8 MiB, a tail read not answered 206, or the codec read's 10 s).
- HLS: the master's first variant only (the one the check plays); another variant or a separate audio rendition (`EXT-X-MEDIA`) can carry other codecs, which is hypothesis 3.
- 19 titles, mostly German-dubbed films and series of the round; the maintainer's reported stream is not known to be among them. The device log (hypothesis 1) remains the way to name it.


## The Five Hosters Without a Measurement (Row 67)

Dropload, RPMPlay, SuperVideo, UPNS and Vidoza resolved no link in row 59's sets. On 2026-10-11, 12:30 to 12:52 UTC, from the dev container only (Mr. Normal, `handoff-67`), every link the stream plugins gave for the titles of `docs/plans/round-titles.txt` (`scripts/probes/resolver_urls.py`'s search, keeping all links of the five) was resolved with the app's registry (up to 15 distinct links per hoster). One to four pages per hoster were opened in a headful Patchright Chromium under `xvfb` and in playwright-mcp. Hosts and statuses only; no URL is recorded.

| Hoster | Links (plugins) | Resolver | The page in a browser | Verdict |
|---|---|---|---|---|
| Dropload | 80 (hdfilme, streamcloud, streamkiste, kinoking) | finds the stream: past the captcha with the browser fallback, on `r1.dropcdn.io`; the registry's check then fails (`ConnectError`, `ConnectTimeout`) | the player loads (a 1 h 13 min video), and its media requests to `r1.dropcdn.io` fail (`ERR_ADDRESS_UNREACHABLE`) | **The CDN is unreachable from this network**: `r1.dropcdn.io` resolves to the same public address locally and over public DNS, and TCP 443 times out. The resolver is not broken. kinoking's one link on the old domain `dropload.io` is dead (TLS handshake reset; its id does not resolve on `dr0pstream.com`) |
| SuperVideo | 61 (hdfilme, kinoking, streamcloud, streamkiste) | finds the stream: past Cloudflare's 403 with the browser fallback, on `hfsNNN.serversicuro.cc`; the CDN answers `429 Too many requests` (server Cowboy, no `Retry-After`), again after a 10-minute pause | the site's own player fails the same way: `This video file cannot be played (Error Code: 232011)`, its requests to the CDN fail; another page says `File is no longer available as it expired or has been deleted.` | **The CDN refuses this network (429)**, and some links are dead. The resolver is not broken |
| UPNS | 13 (moflix) | `upns_file_offline` for 11, one timeout, one 429 | the player stays at "Loading..." (headful too); its `/api/v1/info` decrypts to `title: False, poster: False`, and `/api/v1/video` answers 404 `Video not found or deleted` (4 of 4 checked) | **Dead links**: the hoster says the files are deleted. The resolver stays (the maintainer's rule) |
| RPMPlay | 13 (moflix) | `rpmplay_file_offline` for 10, three 429 | the same player and the same answers (4 of 4 checked) | **Dead links**. The resolver stays (the maintainer's rule) |
| Vidoza | 3 (kinoking, movie2k) | `private_address_refused`: the LAN's DNS answers `0.0.0.0` for `vidoza.net` (a blocklist), and the app refuses a private address | at the public DNS address the front page answers 200 (`Vidoza \| free video hosting`), while all 3 files answer 404, as `/<id>.html` and as `/embed-<id>.html` | **Dead links**, and **the LAN's DNS blocks the hoster** |

Live resolver tests of the five (`tests/live/test_resolver_live.py`, `-k` the five names): before (the links of 2026-10-10), `test_live_url_resolves` failed for all five and `test_dead_url_returns_none` passed for all five. After (each hoster's first fresh link), the same result. No resolver changed.

For the maintainer (not resolver changes, so rows of their own if wanted):
- **The LAN's DNS blocks hosts the app needs**: `vidoza.net` here, and `db.videasy.net`, the cineby plugin's API (`cineby_api_unreachable error=PrivateAddressError` in the same probe). Production runs on the same LAN; whether it uses the same resolver is not measured here.
- **Two CDNs refuse this network**: `r1.dropcdn.io` (no TCP connection) and `serversicuro.cc` (429). Production probably leaves through the same public address, so its Dropload and SuperVideo streams likely fail the same way; not measured there, since this row stays off production.

Limits: one afternoon, from one network; the Dropload and SuperVideo verdicts may differ from another address (a block by address cannot be told from an outage without a second vantage point). UPNS and RPMPlay: 8 of 26 links checked through the API by hand, the rest by the resolver's 404 reading; all came from moflix.


## The Other Capture Hosters (Row 81)

At 15:31 UTC on 2026-10-11 production's open hoster breakers were Dropload, Filemoon, SuperVideo and Vixeo, all browser captures. Does row 78's ad tag, or another one, take their clicks too? The capture probe (`scripts/probes/capture_timing.py --diagnose`, 2 runs each, one profile per call) on the live suite's link of each hoster (`.cache/live/resolver-urls.json`), then each resolver alone and one GET of its stream (with the stored headers and bare) to name the stage where a run stops. Dev container only, 2026-10-11, 16:00 to 16:15 UTC (Mr. Normal, `handoff-81`); the production host's run follows on Mr. Fable's signal.

### Dev container

| Hoster | Page | Run | Captured | Clicks at | Stream at (CDN) | Popups | After the clicks | Ad domains loaded and refused here |
|---|---|---:|---|---|---|---:|---|---|
| SuperVideo | `supervideo.cc` `/e/` | 1 | yes | – | 1.1 s (`serversicuro.cc`) | 0 | none needed: jwplayer, Cloudflare's script check on the page's own host (`/cdn-cgi/challenge-platform/`, 200), the playlist | `llvpn.com` (`/tag.min.js`), `stummelbeelbow.com`, `cloudflareinsights.com` |
| SuperVideo | | 2 | yes | – | 0.5 s (`serversicuro.cc`) | 0 | the same | the same three |
| Vixeo | `vixeo.io` `/e/` | 1 | yes | 4.3 s | 4.3 s (`vidsonic.net`) | 0 | the page's playlist, then the CDN's | 10 sites: `al5sm.com` (`/tag.min.js`), `betteradsystem.com`, `mayhapmonisms.com`, `woofbeginner.com`, `runative-syndicate.com`, `cloudfront.net`, `emcv4fcvxbefc7ve9wgfq.cfd`, `uwterphco.com`, `zutdhkwkm.com`, and a script host under `vidsonic.net` (`/js/`, ×2) while the CDN's own host answered; `8nrjifigyphw77n6pr.rest` pending |
| Vixeo | | 2 | yes | 4.1 s | 4.1 s (`vidsonic.net`) | 0 | the same | the same |
| Dropload | `dr0pstream.com` `/e/` | 1 | yes | 3.6, 8.7 s | 9.7 s (`dropcdn.io`) | 0 | a Turnstile widget from `challenges.cloudflare.com` (`api.js`, its challenge requests 200, one 401), then the playlist | `llvpn.com` (`/tag.min.js`), `ancillammmm.com`, `hubeamily.com` |
| Dropload | | 2 | yes | – | 0.6 s (`dropcdn.io`) | 0 | no widget (the profile kept run 1's pass) | the same three |

Every frame had a video element without an error; MediaSource plays H.264 and AAC.

The stage after the capture (the resolver alone, then one GET of its stream):

| Hoster | Resolver | CDN, stored headers | CDN, bare | Stage |
|---|---|---|---|---|
| SuperVideo | a stream on `hfs311.serversicuro.cc`, 1.2 s, header `Referer` | first 200 `text/html`, 615 bytes: a "Loading..." page whose script sends the client to the same playlist path with three more query keys (`ch`, `js`, `sid`); after about ten requests within 10 minutes `ConnectTimeout` | the same page, then `429` (17 bytes, server Cowboy, no `Retry-After`) | **the CDN**: a script gate in front of the playlist, and rate-limited |
| Dropload | a stream on `ds1.dropcdn.io`, 4.6 s, header `Referer` | `ConnectTimeout` | `ConnectError` | **the CDN**: no connection from this network |
| Vixeo | a stream on `sfy-01-fr.vidsonic.net`, 5.2 s, header `Referer` | 200 playlist | 200 playlist | none: free (as in row 66's table) |

The app's registry gave no stream for SuperVideo and Dropload in the same session (`scripts/probes/header_binding.py --source dev`: "not resolved"): its playback check reaches the CDN after the capture and fails there.

### Reading

- **Row 78's ad tag is in two more players**: SuperVideo and Dropload load `llvpn.com/tag.min.js` as Filemoon does, and Vixeo loads a tag of the same path from `al5sm.com`. The dev container's resolver refuses all of them, so no click here was taken by an ad (0 popups in 6 runs). Whether they take the clicks on the production host is the production host's run.
- **Two of the three stop at the CDN here, not at the capture**: SuperVideo's `serversicuro.cc` puts a script gate before its playlist (a player that runs no script gets HTML) and answers 429 after about ten requests; Dropload's `dropcdn.io` takes no connection (row 67 saw the same for `r1.dropcdn.io`, now `ds1`). An ad block cannot fix either of them.
- **Dropload's page shows the Turnstile widget** from `challenges.cloudflare.com`, so if Dropload ever opts into the own-scripts rule, row 80's exception is the one that keeps it passable.
- **Vixeo** captures and plays here: if its breaker is open on the production host, the cause is there.

### Limits

- One link per hoster, one afternoon, one network; two runs in one profile, so run 2 is the warm case.
- SuperVideo's 429 may come from this probe's own requests (about ten within 10 minutes); row 67 saw it after a 10-minute pause too.
- The CDN checks are one GET each; the gate's redirect was not followed.
- The dev container's resolver refuses the ad domains, so this column names what a page tried to load, not what it does when it gets it.

### In production (2026-10-11, 16:57 to 17:00 UTC)

The same three links and the same probe in production's container (`scripts/prodctl.py probe capture_timing --diagnose --runs 2`), production's navigation bound of 10 s, each call after a `prodctl.py stats` pre-check (the app idle: CPU 0.00 to 0.01 of 4 cores, about 415 MiB), on Mr. Fable's signal; then two masked log reads for the stage. Production ran 3a257fe.

| Hoster | Call | Run | Captured | Clicks at | Stream at (CDN) | Popups | Ad domains loaded |
|---|---|---:|---|---|---|---:|---|
| Vixeo | plain | 1 | no (the player asks for no media, network state 0) | 5.7, 10.8, 16.0 s | – | 3 | every one the dev container refuses loads: `al5sm.com` (`/tag.min.js`), `betteradsystem.com`, `mayhapmonisms.com`, `runative-syndicate.com`, `emcv4fcvxbefc7ve9wgfq.cfd`, the script host under `vidsonic.net`, then `adsco.re`, `255md.com`, `rtmark.net` |
| Vixeo | plain | 2 | no | 5.8, 10.9, 16.1 s | – | 3 | the same |
| Vixeo | `--block al5sm.com` | 1 | yes | 5.6 s | 5.8 s (`vidsonic.net`) | 0 | the same without `al5sm.com` (`blocked`) |
| Vixeo | `--block al5sm.com` | 2 | yes | 5.0 s | 5.5 s (`vidsonic.net`) | 0 | the same |
| SuperVideo | plain | 1 | no browser page: the resolver ended in 1.0 s before a capture | – | – | 0 | – |
| SuperVideo | plain | 2 | no browser page (0.3 s) | – | – | 0 | – |
| Dropload | plain | 1 | yes | 4.6, 10.5 s | 13.3 s (`dropcdn.io`) | 0 | `llvpn.com` (`/tag.min.js`), `ancillammmm.com`, `hubeamily.com` (`rtmark.net` requested, pending); the Turnstile widget from `challenges.cloudflare.com` passed |
| Dropload | plain | 2 | yes | – | 2.2 s (`dropcdn.io`) | 0 | the same, no widget (run 1's pass) |

The stage, from production's own half-open trials of 16:33 to 16:34 UTC (one request, `hdfilme`'s links; both breakers reopened for 3600 s):

- **SuperVideo**: `hoster_resolve_unplayable` at 16:33:30: the resolver gave a stream and the registry's playback check failed. It stops at the CDN, as in the dev container (`serversicuro.cc`'s script gate and 429).
- **Dropload**: `dropload_captcha_browser_fallback` at 16:33:29, `hoster_resolve_timeout` at 16:34:29. The capture needs 13.3 s cold here (the widget, two clicks), past the 10 s bound. Whether its CDN answers the production host is not measured: the capture does not fetch the playlist, and the trial never got to the check.

Reading:

- **Vixeo is row 78's case in a second player**: its own tag (`al5sm.com/tag.min.js`, the path of Filemoon's `llvpn.com` tag) takes every click on the production host, and blocking that one domain gives a stream in 2 of 2 runs with the other ad scripts still loaded. Row 79's own-scripts rule would block it too (`al5sm.com` is neither the page's site nor a frame's), so the fix candidate is Vixeo opting into the rule, a fix row for Mr. Fable to decide.
- **Dropload's ad tag takes no click on the production host** (0 popups in 2 runs with `llvpn.com` loaded): its time goes to the Turnstile widget. Row 80's exception keeps the widget passable if Dropload ever opts in.
- **SuperVideo** is the CDN's, on both networks; no capture change helps it.

Limits: one link per hoster, two runs per call in one profile (run 2 is the warm case), one afternoon. The stage lines come from one production request per hoster. SuperVideo's probe runs show no reason of their own for ending before a page.

### Vixeo with the own-scripts rule (Row 83)

Row 81's production reading made Vixeo opt into row 79's rule (`own_scripts_only=True` in `vixeo.py`, Mr. Fable's design; the rule unchanged, no domain list). The probe runs the resolver, so it applies the rule. Dev container, 2026-10-11 ~17:10 UTC, the same link, 2 runs:

| Run | Captured | Clicks at | Stream at (CDN) | Popups | Scripts |
|---:|---|---|---|---:|---|
| 1 | yes | 3.9 s | 3.9 s (`vidsonic.net`) | 0 | all 11 foreign script sites `blocked` (`al5sm.com`'s tag, the other ad sites, the script host under `vidsonic.net`) |
| 2 | yes | 3.9 s | 3.9 s (`vidsonic.net`) | 0 | the same |

No regression against row 81's dev runs (4.3 and 4.1 s, the same sites refused by the resolver there). The player's code is inline in its page: the page loads no script of its own site, before or after, so no script the player needs comes from another site.

On the production host (2026-10-12, 06:33 UTC, production on staging 39f4790, the same link, `scripts/prodctl.py probe capture_timing --hoster vixeo --runs 2 --diagnose` without `--block`, so only the app's rule aborts; after a `prodctl.py stats` pre-check, the app idle):

| Run | Captured | Clicks at | Stream at (CDN) | Popups | Scripts |
|---:|---|---|---|---:|---|
| 1 | yes | 5.3 s | 5.5 s (`vidsonic.net`) | 0 | all 11 foreign script sites `blocked`: `al5sm.com`'s tag, `betteradsystem.com`, `mayhapmonisms.com`, `woofbeginner.com`, `runative-syndicate.com`, `cloudfront.net`, `uwterphco.com`, `zutdhkwkm.com`, two hosts of random names under `.cfd` and `.rest` (other names than row 81's), the script host under `vidsonic.net` |
| 2 | yes | 4.8 s | 5.0 s (`vidsonic.net`) | 0 | the same 11 |

Row 81's plain runs on the production host gave no stream in 2 of 2, with 3 popups each; with the rule both runs give a stream after one click, as row 81's `--block al5sm.com` call did (5.8 and 5.5 s). The ad traffic the tag pulled in on the production host (`adsco.re`, `255md.com`, `rtmark.net`) no longer appears. The two random ad hosts changed their names overnight, which a domain list would have missed and the rule does not. Limits: one link, two runs in one profile, one morning.
