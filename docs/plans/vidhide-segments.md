# VidHide Segments behind the HLS Proxy (step 27)

**Question.** Step 24's play check (`docs/plans/stremio-latency.md`, seventh round) found 6 of 6 VidHide streams failing behind the HLS proxy: the playlists came back, the segments answered 403. The proxy fetches from the Pi, the address that resolved the stream, so the suspicion was the headers it sends (`_player_headers` with the resolver's stored `Referer`).

**Probe.** `scripts/probes/vidhide_segments.py` takes the newest stored VidHide HLS link (or the streams of `--imdb`), resolves its hoster URL again with the XFS resolver, and walks the proxy's fetch chain for both: stream URL, first variant playlist, first segment. It builds every URL with the proxy's own functions (`_proxy_uri`, `build_cdn_url`), so it shows whether the proxy would fetch a segment or leave it to the player. Each chain runs under six header sets: the stored headers with the player's User-Agent (what the proxy sends), the Referer with the resolver's User-Agent, Referer and Origin with the player's User-Agent, the Referer alone, the player's User-Agent alone, no headers. It prints hosts, statuses and content types only.

```bash
poetry run python scripts/prodctl.py probe vidhide_segments          # the Pi
PORT=7981 PYTHONPATH=src poetry run python -P scripts/probes/vidhide_segments.py --imdb tt1375666  # dev server
```

## Reading (2026-10-07)

| Where | Link | Stream / variant / segment, all six header sets | Segment proxied? |
|---|---|---|---|
| Pi (VPN address) | stored *Inception* link, moflix-stream.click, 27 min old | 200 / 200 / 200 `video/MP2T` | no |
| Pi | the same hoster URL resolved again | 200 / 200 / 200 | no |
| dev container, with the fix | stored and fresh *Inception* link | 200 / 200 / 200 | yes |

**The headers do not matter.** Every set, no headers included, gets the playlists and the segment from both addresses.

**The proxy left the segments to the player.** VidHide's media playlists list their segments as absolute URLs. All 18 stored VidHide links in production name the CDN host in mixed case in their stream URL (4 to 9 capital letters; `2ZO6sb3MYz7fapc.acek-cdn.com`), and the playlists list the same host in lower case. `_proxy_uri` compared `(scheme, netloc)` as written, found another origin and left each segment URL as it was, so the player fetched it from its own address. VidHide's segment tokens are bound to the address that resolved the stream (the Pi's VPN exit), so the player's fetch from the home connection got 403. This is the address binding of finding 17, reached through a proxy bypass rather than a header defect.

## Fix

`hls_proxy._same_origin` compares scheme and host with the host lower-cased, for the manifest rewrite (`_proxy_uri`) and for the CDN URL the proxy builds from a client's path (`build_cdn_url`, the open-proxy guard). The fetch keeps the stored host as written. With the fix, the dev server's proxy lists VidHide's variant and segments as proxy URLs and serves the segment (200, `video/MP2T`). Tests: `tests/unit/infrastructure/test_hls_proxy.py` (a mixed-case base with a lower-case segment URL, and `build_cdn_url` with a lower-case `//host` path).

**Open.** The production check after the next deploy: a VidHide stream played through `scavengarr.lan` from the home connection (`scripts/stremio_playcheck.py`).
