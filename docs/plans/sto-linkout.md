# sto link-outs from the Pi

Finding 16 of `ideas-backlog.md`: in production sto's episode links reach the serienstream resolver as sto's own link-outs (`/r?t=<token>`) and fail there (`serienstream_invalid_url`, 13 times in the three hours around the seventh round); *Haus des Geldes* S01E01 lost its only matching result to it ([title-matching.md](title-matching.md)). The question: is the link-out gate unsolved on the Pi only, always, or does the resolver reject a link the plugin could have resolved? This file holds the evidence; the fix (plugin, resolver, or the gate solving) is assigned from it.

## How sto resolves a link-out

An episode page lists one button per hoster with a link-out `/r?t=<token>`. The plugin opens each with its httpx client and the episode page as `Referer` and takes the first `Location` that leaves the site (`HttpxPluginBase._resolve_redirect`). When none of an episode's link-outs leaves the site, the site gates them (`StoPlugin._gated`): for a stream request the stealth browser passes the Turnstile widget of the player once (`_pass_link_gate`, up to 60 s, as a task of its own) and the page is read again with the adopted session. A failed pass is not retried for 300 s (`_GATE_RETRY_S`); meanwhile, and after a failed pass, the link-outs stay as they are, for JDownloader. The serienstream resolver accepts only `/serie/<slug>` paths, so a link-out that reaches it is always `invalid_url`.

## Probe

`scripts/probes/sto_linkout.py` creates the sto plugin from the app's registry, finds the series with the plugin's own search, reads the episode page's hoster buttons without resolving them and follows the first two link-outs with the plugin's client, headers and `Referer`, one hop at a time; then it asks the plugin's own `_resolve_redirect` for the same link. No browser, no cache writes; it prints hosts only.

### Dev container (home connection, 2026-10-07 ~10:20)

```
site serienstream.to
series 'Haus des Geldes' (3 search hits)
episode S01E01: 2 hoster buttons (VOE, Doodstream)
- VOE: hops 302 | final host voe.sx (left the site) | gate markers: none | body 0 B | 0.1 s
  plugin resolution: voe.sx (0.1 s)
- Doodstream: hops 302 | final host myvidplay.com (left the site) | gate markers: none | body 0 B | 0.1 s
  plugin resolution: myvidplay.com (0.1 s)
```

### Production container (the Pi behind the VPN, same minutes, `prodctl.py probe scripts/probes/sto_linkout.py`)

```
site serienstream.to
series 'Haus des Geldes' (3 search hits)
episode S01E01: 2 hoster buttons (VOE, Doodstream)
- VOE: hops 200 | final host serienstream.to (stayed on the site) | gate markers: none | body 936 B | 0.1 s
  page: title '' | loads nothing | words window.top
  plugin resolution: unresolved (0.1 s)
- Doodstream: hops 200 | final host serienstream.to (stayed on the site) | gate markers: none | body 936 B | 0.1 s
  page: title '' | loads nothing | words window.top
  plugin resolution: unresolved (0.1 s)
```

### Gate passes in production (log, 07:00–10:30)

| time | event |
|---|---|
| 07:12:32 | `sto_link_gate_unsolved` |
| 09:04:39 | `sto_link_gate_unsolved` |
| 09:05:36 | *Haus des Geldes* request: no pass attempted (within 300 s of the failed one), link-outs kept, both `serienstream_invalid_url` |
| 10:02–10:03 | 3 × `sto_link_gate_passed`, 1 × `sto_link_gate_unsolved` |

## Reading

- **On the Pi only.** The same link-out, with the same client, headers and `Referer`, redirects at once to the hoster from the home connection and answers from the Pi with a 936-byte page that only works inside the site's player (`window.top` check, no title, nothing loaded). The page is not itself a Turnstile or Cloudflare page; the widget sits in the episode page's player, which the plugin's browser pass clicks. So the site gates link-outs per IP (the VPN address, as `_episode_links` notes for 2026-10-04), not for every client.
- **The browser pass works sometimes.** Three passes succeeded and three failed in the window. After a failure the plugin waits 300 s, and every stream request in that time keeps sto's link-outs unresolved.
- **The resolver loses nothing resolvable.** From the Pi, without a passed session, plain httpx gets no redirect, so the serienstream resolver could not follow the link-out either; its `invalid_url` only makes the failure visible late, and the result costs a resolver attempt and a slot in the answer.

**Options for the fix (assignment follows):** (1) the plugin drops results whose links all stayed gated in a stream request instead of handing link-outs to the resolver (no stream lost, one less useless result); (2) the gate pass becomes more reliable or retries sooner (the 3 of 6 failures are the loss); (3) the resolver follows `/r?t=` with the plugin's adopted session, which needs the session shared across the two, the largest change. Option 1 is cheap and independent of 2.
