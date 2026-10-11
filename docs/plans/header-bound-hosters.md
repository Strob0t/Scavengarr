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

- **production**: the links in production's cache, read through Portainer, resolved from the Pi's address. The probe sends no stream request to production.
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
3. **The file route's bindings are what the pass-through sends.** Resolved here, DoodStream's CDN wants the Referer, MixDrop's and Vinovo's want the check's User-Agent (Vinovo's is stored with the link), and FSST's wants nothing. The pass-through sends the stored headers over the browser User-Agent, the check's headers. From the LAN, production's links of all four are dead every way (an HTML page, 403 or 410): they are bound to the Pi's address, which the pass-through supplies. The eleventh round's file branch played 9 of 9 such files through it.
4. **The HLS playlists answer the LAN, except VOE's production links.** VOE's production playlists answer 403 every way from here, and the dev container's VOE link answers every way: VOE binds the playlist to the resolving address (or to a lifetime of minutes), and the proxy asks from the Pi.
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
