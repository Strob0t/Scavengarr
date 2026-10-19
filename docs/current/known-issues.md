# Known issues

> **Status:** current
> **As of:** d8f375bc

## Purpose

The open defects of the current release, one line each, with where each is tracked.
A fixed defect leaves this list in the commit that fixes it, and the CHANGELOG entry of that commit says so.

## Open defects

| Defect | What happens | Tracked in |
|---|---|---|
| FireStream 1080p can stall | The CDN throttles each connection and a player loads the segments one after another, so even with three segments read ahead a 1080p picture stalls now and then; a 1080p playback with the current depth is still to be checked. | [Decision 0005](../target/decisions/0005-read-ahead-of-three-segments.md), [ideas backlog](../backlog.md) |
| FSST 1080p does not decode in Stremio Web | A desktop browser reports "Error occurred when decoding" for the address-bound stream (H.264 1080p, AAC 5.1), which now goes through the file proxy; it has not been tried again, and the cause is not confirmed. | [Decision 0006](../target/decisions/0006-direct-streams-proxied.md) |
| s.to link-outs are rationed for VPN addresses | For a VPN address s.to's gate is the tier `turnstile_altcha`, and one pass (about 20 s in the browser on the production host) unlocks 3 link-outs, so a Torznab search returns most episodes' s.to link-outs unresolved. | [sto link-outs](../archive/plans/sto-linkout.md) |
| Playmate fails in tsaridas/stremio-docker's web player | The image's nginx answers Playmate's disguised HLS segments (`…_000.css`, `…_001.js`) as web player files with 404, so Playmate streams fail there with error 81. | none |
| animeloads rations captchas per address | After about 30 captchas within about 40 min every answer is rejected for at least 10 min, so an anonymous grab (one captcha per episode) succeeds only a few times per hour and the rest end in HTTP 502; a login (`SCAVENGARR_ANIMELOADS_USERNAME`, `SCAVENGARR_ANIMELOADS_PASSWORD`) needs one captcha per release. | [Captcha solving](../archive/plans/captcha-solving.md) |
| kinox keeps no hoster link | Every mirror's `/redirect/<hash>` opens a three-step image captcha with honeypot fields, also in a real browser, so kinox returns no results until the site drops it. | [Plugin domains](../archive/plans/plugin-domains.md), [ideas backlog](../backlog.md) |
| Some hosters have no resolver | rubyvidhub.com answers Cloudflare 522, embedrise.com plays only a placeholder `video.mp4` that answers 404, and frdl.to uses vidmoly's ad-tracker redirect; JDownloader has plugins for the last two (`EmbedriseCom`, `FreedlInk`). | none |
| GoFile links do not resolve | GoFile refuses guest lookups (401 `error-notPremium`), and its website adds an `X-Website-Token` from an obfuscated script. | none |
| Vidmoly is unreachable behind ad blockers | The embed page's script redirect sends non-browser clients, and the stealth browser on a network that blocks ad domains, to an ad click tracker, so no stream is reachable then. | none |
| SuperVideo streams need a browser-like player | The CDN answers non-browser clients with a JavaScript redirect and ad-tracker redirects, and on the production host behind its VPN with 429 or an HTML page, so the playback check drops the stream. | [ideas backlog](../backlog.md) |
| Dead sites | megakino.to and movie4k.sx answer Cloudflare 522, so megakino_to and movie4k return nothing and their breakers keep them out of most requests; cineby's API host no longer resolves, and the shipped config disables it. | [Decision 0008](../target/decisions/0008-dead-site-plugins-stay-enabled.md) |
| filmfans and serienfans limit bursts | They answer bursts with 429, so an uncached search takes about 2 to 2.5 min and can exceed Prowlarr's request timeout. | [Antibot with patchright](../archive/plans/antibot-patchright.md) |
| Turnstile needs the headful browser | filmfans, serienfans, ddlspot, ddlvalley, scnsrc and kinoger pass Cloudflare's interactive Turnstile only with the headful browser and the browser fallback (`playwright.headless: false` with Xvfb, `playwright.browser_fallback: true`, both defaults), so a setup that turns either off loses them. | [Antibot with patchright](../archive/plans/antibot-patchright.md) |
| A first capture on a slow host can miss the resolve bound | With a fresh browser profile Filemoon's player takes its captcha path (a stream after about 17 s, or none by 21 s) and Dropload its Turnstile (13 s), longer than production's `http.timeout_resolve_seconds` of 10 s, while a warm profile captures in 2 to 7 s. The request that meets it answers without that stream: the capture runs on in the background for up to 20 s and caches the stream for the next request, and a Filemoon capture without a kept captcha pass counts as challenged from its start, so one cut before its captcha runs on too. | [Header-bound hosters](../archive/plans/header-bound-hosters.md), [Decision 0012](../target/decisions/0012-challenge-outside-the-breaker.md) |

## References

- `CHANGELOG.md`: the history of each fix.
- [Backlog](../backlog.md): the open rows behind these defects.
