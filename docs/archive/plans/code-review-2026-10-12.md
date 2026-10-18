# Code review of 2026-10-12

> **Status:** archived
> **Superseded by:** none (history)

The maintainer's `/code-review` at maximum effort over `origin/main...HEAD`: the 39 staging commits since v0.4.0, rows 87 to 93 (row 94 came later). Ten finder angles, one verifier per candidate and a final sweep gave 15 findings. 13 were confirmed by a reproduction with the real code (only pages, sites or times faked), 2 are plausible: the mechanism is real, but no real trigger was shown (findings 8 and 10). Findings 1 and 3 were also checked against the live sites. The review measured no production frequency. Its reproduction scripts stayed in the reviewer's `/tmp` and are not kept.

## Rows

| Row | Findings | Owner |
|---|---|---|
| 95 | 1 | Ultracode, first |
| 96 | 2, 3, 5, 9, 12, 15, and V29 below | Ultracode, after row 95 |
| 97 | 6, 7, 8, and the restore's log lines below | Ultracode, after row 96 |
| 98 | 4, 10, 11 | Ultracode, after row 95 (the same title semantics) |
| 99 | 13, 14, and the conventions and probe items below | Mr. Normal, after the live suite's lost resolver cases |

## Production check (Mr. Fable, 8a1f1db)

- Finding 1 holds in the code: `AnimeIdLists.whole_series()` answers `None` while Fribb's list is not in memory (it never waits; the first call starts the load), so `kitsu_episode()` gives no number, `TitleResolver.episode_ref()` sets the Cinemeta position as `absolute`, and `locate()` takes the row that shows it, whatever its title.
- Finding 3 has not shown in production yet: DoodStream since the start 7 streams, 2 cuts, 4 dead and no `challenge` (`scavengarr_hoster_resolve_total`), and its successes logged `challenge=None`. The review saw the Turnstile frame from the dev container.
- The restore went through: no `capture_state_restore_failed` since the start (the `capture_state_restored` line alone would not prove it, see below).

## Findings

### 1. A position counts as Kitsu's number (row 95, confirmed)

`src/scavengarr/infrastructure/plugins/episode_index.py:245`

- **Defect:** locate() now takes the row that shows ref.absolute by 'number' with no title check. When no Kitsu number is known, title_resolution sets absolute to the Cinemeta position, which is off on long runners. origin/main sent a titled row to the neighbour and title rules instead.
- **Failure:** Right after every start (the Fribb id list loads lazily, so kitsu_episode() is None), or for an hour after a failed list download, <Anime A>:22:4 gets absolute=1088, the Cinemeta position. locate() returns aniworld's #1088 '<episode title>' (staffel-21/episode-197) by 'number', and aniworld labels it S22E4. The episode filter keeps it, the answer is cached under the:1088 key, and the user plays the previous episode. A read-only live run on 2026-10-12 covered the 311 episodes from position 590: HEAD placed 0 correctly, origin/main 297. The test that pinned this case was rewritten to delete row 1088 (tests/unit/infrastructure/test_episode_index.py:137-144).

### 2. A passed challenge stays a challenge (row 96, confirmed)

`src/scavengarr/infrastructure/hoster_resolvers/registry.py:743`

- **Defect:** A resolver's None with the sticky ChallengeMark set always becomes 'challenge': not cached, and the hoster goes into cooldown. That also happens when the challenge was passed and the link is just dead. solve_cloudflare marks 'cloudflare_page' before it solves (turnstile.py:216), and nothing ever clears the mark (page_gate.py:56-59).
- **Failure:** One request has two Dropload links, the dead one ranked first, on a cold context. The capture passes the Cloudflare interstitial, sees the offline notice (stealth_pool.py:604-607) and returns None. _try_resolver ends it as 'challenge' kind=turnstile, caches nothing and starts the 60 s cooldown (300 s after the completion's unsolved step). _guarded then skips the live second link (hoster_resolve_challenge_cooldown left_s=60), so the answer has no Dropload stream. origin/main cached the dead link for 15 min and streamed the live one. Reproduced with the real capture_media, solve_cloudflare, registry and HosterResolution; only the page and pool were faked. If a widget is always present, the dead link is never cached and blocks the hoster's live links on every request while the cooldown climbs to 1800 s.

### 3. A passive widget counts as a challenge (row 96, confirmed)

`src/scavengarr/infrastructure/captcha/detect.py:88`

- **Defect:** challenge_frame() counts any document from challenges.cloudflare.com or *.hcaptcha.com, and every reCAPTCHA frame except the size=invisible anchor. A passively embedded widget, with nothing to pass, therefore marks the capture as challenged.
- **Failure:** Live in the dev container, the app's DoodStreamResolver loaded a Turnstile frame on both the cold and the warm capture of a live vide0.net link, and both logged hoster_resolve_success challenge=turnstile (registry.py:500-502 says None for a warm one). So every DoodStream capture that the bound cuts, or whose player breaks, ends as 'challenge'. The breaker never counts it, every DoodStream link is skipped for 60 to 1800 s, and each cut starts a 20 s background completion that holds a page. Local pages showed the same for invisible reCAPTCHA v2 (its api2/bframe has no size parameter) and invisible hCaptcha: a dead file behind them is not cached as dead and starts the cooldown.

### 4. A mirror standby's partial matches stay (row 98, confirmed)

`src/scavengarr/application/stremio/search_progress.py:147`

- **Defect:** Partial matches are held and dropped per result source_plugin. A mirror standby's results carry its own name, but its runs end under the member's name. So plugin_done() (line 120) never releases the standby's partials, which wait for finish(), and the member's full match never drops them.
- **Failure:** A request for 'Anime C: Sequel' S1E1 goes to mirror group hdfilme/streamcloud. hdfilme delivers 'Anime C: Sequel' on the full query but errors on the base query 'Anime C', so streamcloud stands in for that query and finds only the parent 'Anime C'. The final entry is ['hdfilme:Anime C: Sequel', 'streamcloud:Anime C']; without the group the partial is dropped. The completion search asks only the member, so streamcloud:Anime C stays cached until a stale refresh. In the other direction, a standby's legitimate partial ('Anime E' for 'Anime E: Sequel') misses the budget answer and appears only after every group ends. Reproduced with the real TitleSearch, PluginSearchRunner and SearchProgress.

### 5. The cooldown refuses a play-time re-resolution (row 96, confirmed)

`src/scavengarr/infrastructure/hoster_resolvers/registry.py:626`

- **Defect:** The challenge cooldown in _guarded also refuses play-time re-resolutions (refresh=True from after_refusal and current()). origin/main refused them only while the breaker was open. The cooldown starts on the first challenge, with no threshold.
- **Failure:** A Filemoon link meets a challenge and starts the 60 s cooldown (900 s after repeated misses). Meanwhile the user starts or re-opens a stored Filemoon stream whose video URL has expired. The CDN refuses it, stremio_links calls _resolve_again(refresh=True), and _guarded returns None without calling the resolver, so playback fails at once. origin/main resolved it again and played; reproduced with the real registry against origin/main's. In production (challenge-reactions.md:111), Dropload's completion failed its playback check, which neither ends nor steps up the cooldown, so warm Dropload links were skipped for about 15 min.

### 6. Saving the state walks the live context (row 97, confirmed)

`src/scavengarr/infrastructure/browser/stealth_pool.py:642`

- **Defect:** _save_state runs context.storage_state(indexed_db=True) on the live shared stealth context. The driver opens a hidden page that navigates to every origin the context ever committed, outside the PageGate and with no lease, and sets a context-wide 'creating storage-state page' flag while it does.
- **Failure:** (a) The capture whose lease exit triggers the 200-page recycle has just started its keep. _recycle's any(ctx.pages) guard (shared_browser.py:136) cannot see the hidden page and closes the browser mid-walk. 12 of 12 runs logged capture_state_save_failed, and in 11 of them the patchright driver also hit an unhandled rejection. (b) Any page or ad popup created on that context while the flag is set is treated as the storage-state page, and none of its events reach Python. 0 of 15 popups were delivered, so _close_popup never ran. 1 of 450 raced new_page() pages delivered no request events: a capture that cannot see its media request and caches a live link as dead. (c) The walk costs about 20 ms per visited origin and grows with the context's age (1.2 s at 61 origins on a 16-core dev box).

### 7. An older kept cookie beats a newer clearance (row 97, confirmed)

`src/scavengarr/infrastructure/browser/clearance_store.py:237`

- **Defect:** creation_state() loads the clearance store's cookies first, then lets every usable cookie of every kept capture state overwrite them by (name, domain, path), whatever their age. An older cf_clearance from a capture state therefore beats a newer one that remember() stored. The spec says 'the newest winning per cookie'.
- **Failure:** At t0 a Dropload capture streams and its state is kept with cf_clearance A. Within 600 s Cloudflare challenges again, or a capture solves and finds no stream. remember() stores B, but _keep_state skips the host because it was kept less than 600 s ago. Every context created before the host's next keep (200-page recycle, restart, relaunch) starts with A, which Cloudflare no longer accepts. A carries a one-year Expires, so _usable keeps it for the state's full 24 h. That costs one more challenge per context, and a solve that misses the bound becomes a 'challenge' with its cooldown. Reproduced: keep(A), then remember(B), and creation_state() returns A; origin/main's restore() added B.

### 8. The restore is all or nothing (row 97, plausible)

`src/scavengarr/infrastructure/browser/stealth_pool.py:399`

- **Defect:** The capture-state restore is all-or-nothing. If new_context(storage_state=...) rejects any single origin, the fallback is a bare context without any host's clearance cookies. The rejected entry is neither dropped nor quarantined, so every later context fails the same way.
- **Failure:** A kept host stores IndexedDB data that Playwright saves but cannot restore. Reproduced on Chromium: an object store keyed by 'name' that holds File values, or a unique index over invalid Dates. Every context creation then logs capture_state_restore_failed and starts with no cookies, so every Cloudflare hoster's capture starts cold. Each new capture of that host saves it again with a fresh saved_at, so the failure can outlast 24 h. The spec's corrupt-entry scenario (spec.md:65-67) wants the context created with the clearance cookies alone. origin/main's bare context plus restore() kept them. PLAUSIBLE: no real host was shown to store such data.

### 9. A second attempt overwrites a completion's stream (row 96, confirmed)

`src/scavengarr/infrastructure/hoster_resolvers/registry.py:666`

- **Defect:** Nothing stops a new attempt on a URL whose background completion is still running: completions are keyed by resolver name, and _guarded checks only the cooldown. Both writes go to the same cache key unconditionally, so a later dead result overwrites the completion's stream.
- **Failure:** Link L's capture is cut and handed over. While its completion runs (up to 20 s), another link of the hoster streams (_record calls streamed(), which ends the cooldown) or the cooldown expires. A request (another client, a re-open, or a cached answer's background run) then resolves L again, and two captures of L run at once. The completion caches L's stream, the second capture ends with no stream and no mark, and _attempt caches L as dead for 15 min, so the found stream is lost. In the opposite order, a completion with no stream logs hoster_challenge_unsolved and starts a new cooldown right after L streamed. Reproduced with the real registry, bound and completion scaled down.

### 10. An article makes a partial title (row 98, plausible)

`src/scavengarr/domain/entities/stremio.py:220`

- **Defect:** is_partial_title compares raw word sets. A site name that only drops an article or writes '&' ('Series I', 'Series J') counts as a partial (parent-series) match. SearchProgress drops it as soon as the same plugin returns any non-partial match, such as a spin-off.
- **Failure:** A request for 'Series I' S1E1 (2010) reaches a DDL or forum plugin without the relevant_hits() pre-filter. It returns 'Series I - S01E01' (score 1.0) and 'Series I: Spin-off - S01E01' (0.711, year unknown). HEAD files the real series as partial and the spin-off as matching, so in every arrival order the spin-off is served and the real series dropped; origin/main kept both. 'Series J' against 'Series J: The Anime' behaves the same. PLAUSIBLE: no captured page shows such a pair, and a known year more than 3 years off or an IMDb id in the metadata avoids it.

### 11. Two title sets judge one site name (row 98, confirmed)

`src/scavengarr/application/use_cases/stremio_stream.py:300`

- **Defect:** EpisodeRef.titles is built from the union of all selected plugins' languages, while the title matcher scores each language group with its own reference. So locate()'s is_partial_series and the matcher's partial flag judge the same site name differently. The matcher also lacks the html.unescape that episode_index applies.
- **Failure:** With TMDB titles and the default selection (scoring off, union ['de','en']): de 'Anime D', en 'Anime D', and aniworld names the series 'Anime D'. EpisodeRef.titles contains 'Anime D', so is_partial_series is False and locate() places by number. The matcher's ('de',) reference scores the same result 1.0 with partial=True, holds it until aniworld ends and drops it beside any other aniworld match. With the selection [aniworld, sto], the index's verdict flips to partial. The search cache key leaves the titles out, so the first request's titles decide for joined requests. the plugin guide of the time (now `docs/current/plugins.md`) said both use the same titles.

### 12. Completions can hold every page (row 96, confirmed)

`src/scavengarr/infrastructure/hoster_resolvers/registry.py:827`

- **Defect:** Background completions are capped at one per resolver name only, and 29 resolver names can capture. Each completion keeps its page for up to _COMPLETION_S = 20 s past the cut. With the default 2-page gate, two challenged captures can hold every page while a /play claim waits only 12 s.
- **Failure:** A Filemoon capture and a Dropload capture are both cut after a challenge, and their completions hold both pages (in_use=2, limit=2). A /play re-resolution of a SuperVideo link waits for a page and gets PageBusy at due - _MIN_WORK_S (12 s). /play falls back to the stale link, which fails after a CDN refusal. Reproduced with the real registry and gate, times scaled down. PageBudget adds a page only when CPU is at most 70 %, at least 400 MB is free and nothing changed in the last 30 s, so on a busy production host it can come too late. design.md:63 accepts that pages wait, not that a play resolution ends PageBusy.

### 13. A test leaves the plugin globals set (row 99, confirmed)

`tests/unit/interfaces/test_browser_warmup_lifespan.py:64`

- **Defect:** The new test runs the real create_app lifespan, which sets class-level plugin globals: the shared httpx client, the cache, the browser fetcher, the clearance store and the page timeout (composition.py:501-561). Shutdown closes the client and the cache but leaves the globals pointing at them, and the test does not monkeypatch them.
- **Failure:** Running 'pytest -n 0 tests/unit/interfaces/test_browser_warmup_lifespan.py tests/unit/infrastructure/test_byte_plugin.py' fails 5 byte tests with 'Cannot send a request, as the client has been closed' (reproduced here). Run before tests/unit/infrastructure and tests/unit/application, the test makes 13 tests fail: byte, hdfilme, and aniworld/sto with 'Cache not initialized'. The default 'pytest -n auto' passes only because tests/unit/interfaces is collected last. A different path order, --ff after this test once failed, or a subset run turns CI red. Other tests reset these globals (tests/unit/infrastructure/test_httpx_browser_fallback.py:29-33).

### 14. A dead link-out counts as an unsolved challenge (row 99, confirmed)

`src/scavengarr/infrastructure/plugins/httpx_base.py:785`

- **Defect:** _resolve_redirect settles a link-out's challenge as solved only when it found a target. A challenge that the browser or solver passed in front of a dead or same-host link-out therefore counts in challenges_unsolved.
- **Failure:** Affected: sto, byte and kinox, plus filmfans and serienfans through _resolve_result_links. A link-out gets a 403/503 Cloudflare page from httpx, and the stealth browser passes it or already holds clearance. The link-out then answers a same-host 404 (an expired hash), or, for sto, does not redirect without its Referer. resolve_redirect returns None, and settle_challenge(solved=False) flags the whole search as unsolved. Reproduced with respx: solver ok with url=<own link-out> gives failed=True and unsolved_search=True. One dead link-out in a batch inflates PluginHistory's challenges_unsolved. The maintainer reads that count for a solver trial (N20's 5 % rule), though no solver can fix a dead link.

### 15. The hand-over misses a request's own cancel (row 96, confirmed)

`src/scavengarr/infrastructure/hoster_resolvers/registry.py:730`

- **Defect:** The hand-over decision checks only clock.expired(). That is also True when the request cancels the task in the same loop iteration in which the bound fired. asyncio.timeout then uncancels only its own cancel, so CancelledError propagates and the TimeoutError branch with _challenged() never runs.
- **Failure:** HosterResolution.aclose() cuts a challenged capture (the answer is due) within about one loop iteration of its resolve bound, so both cancels land before the task resumes (cancelling() == 2). The capture is still handed to a background completion, which keeps its page for up to 20 s after the answer went out; registry.py:712-713 and hoster-resolvers.md:270 say a cut request cancels it. No cooldown starts at the cut, the metric says 'cut' instead of 'challenge', and an unsolved completion steps to 60 s instead of 300 s. Reproduced on the default loop and on uvloop. Checking asyncio.current_task().cancelling() == 1 would tell the two cases apart.

## Design decisions behind some findings

- Findings 2, 3 and 5: the general rules come from the `react-to-challenges` design and spec (a `None` with a mark is `challenge`, the frame-host rule, skipping all of a hoster's links during a cooldown); neither covers these cases.
- Finding 12: the cap is a trade-off the design accepts (`design.md:63`).
- Finding 14: the counting rule is in `docs/features/observability.md`.

## Verified, cut at the cap of 15

- **V4:** a site's short name for the same series turns the number rules off. Plausible; documented design, and a live scan of 46 series found no trigger. No row.
- **V15:** the stealth warm-up runs only at start, so after a recycle or a crash a capture relaunches Chromium inside its own bound. Confirmed, but the lazy path is older than these commits. Row 97 decides whether a relaunch warms up too.
- **V29:** `_complete` awaits `_judge` outside its try, so an unexpected check error is never retrieved (Mr. Fable's nit of row 90). Plausible; its second part was the review's only refuted claim. Row 96.
- **Logging (row 97):** `capture_state_restored` is logged before the restore succeeds (`clearance_store.py`, `creation_state()`), and the stealth context no longer logs `clearance_restored`.
- **Probes (row 99):** `scripts/probes/capture_timing.py` copies the state outside the store, and its challenge rule drifts from the app's.
- **Conventions (row 99):** `tests/unit/infrastructure/test_filemoon_resolver.py` (about line 420) uses `AsyncMock` against the respx rule of AGENTS.md §6; stale lines in `AGENTS.md` (about line 175), `docs/architecture/codeplan.md` (about line 69), `docs/architecture/clean-architecture.md` (about line 284) and `README.md` (about line 331), to verify one by one; the change id `openspec/changes/aniworld-number-first` is not verb-led (`openspec/AGENTS.md`), to rename before its archive.
