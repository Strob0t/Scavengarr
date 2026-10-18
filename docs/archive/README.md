# Archive

> **Status:** reference
> **As of:** d17c66f7

Read-only history: the closed plans, the measurement logs and the refactor records, each kept with its text.
An archived document starts with `> **Status:** archived` and names its successor in `> **Superseded by:**`.
The current behaviour lives in the documents of [`docs/README.md`](../README.md); this index says where each file came from and what replaced it.

| File | What it was | Outcome | Successor |
|---|---|---|---|
| [`refactor/clean-architecture-migration.md`](refactor/clean-architecture-migration.md) | The record of the migration from a flat codebase to the domain, application, infrastructure and interfaces layers. | Completed before the first release. | `docs/target/architecture.md` (the rule), `docs/current/architecture.md` (the module map) |
| [`refactor/pydantic-domain-removal.md`](refactor/pydantic-domain-removal.md) | The record of the removal of Pydantic from the domain layer. | Completed: the domain models are plain dataclasses. | none (history) |
| [`refactor/german-english-translation.md`](refactor/german-english-translation.md) | The record of the translation of comments, docstrings and log messages from German to English. | Completed before the first release. | none (history) |
| [`plans/anime-ids-spike.md`](plans/anime-ids-spike.md) | The comparison of anime id sources for `kitsu:` requests. | Built: the Anime Kitsu addon's meta, with Fribb's id list as the fallback. | `docs/current/stremio-addon.md` (row 113) |
| [`plans/antibot-patchright.md`](plans/antibot-patchright.md) | The anti-bot plan: Patchright, the browser fallback of httpx plugins and an optional solver sidecar. | Built; the sidecar adapter is not checked against a running sidecar (Carried over). | `docs/current/browser-and-challenges.md` (row 113) |
| [`plans/browser-page-budget.md`](plans/browser-page-budget.md) | The design of the page gate: which request gets a stealth page, and how many pages exist. | Built: the page gate with its adaptive page budget. | `docs/current/browser-and-challenges.md` (row 113) |
| [`plans/captcha-solving.md`](plans/captcha-solving.md) | Captcha and challenge solving: the detector, grab-time links, clearance cookies and the solver chain. | Built; the recognizer container of step 8 is deferred (Carried over). | `docs/current/browser-and-challenges.md` (row 113) |
| [`plans/cloudflare-solvers.md`](plans/cloudflare-solvers.md) | A comparison of FlareSolverr, trawl and Scavengarr's own challenge stack. | Research; its recommendations went into the challenge decision. | `docs/current/browser-and-challenges.md` (row 113) |
| [`plans/code-review-2026-10-12.md`](plans/code-review-2026-10-12.md) | The findings of a full code review of `staging`, mapped to backlog rows. | Fixed by rows 95 to 99. | none (history) |
| [`plans/code-review-fixes.md`](plans/code-review-fixes.md) | The fix waves of the first full code review of `staging`. | Done in four waves; its open observations are carried over. | none (history) |
| [`plans/cuii-coverage.md`](plans/cuii-coverage.md) | The CUII block list against the plugins and hoster resolvers. | A snapshot; its missing plugin and resolver are carried over. | `docs/how-to/deploy.md` (row 116) |
| [`plans/filmo-plugin.md`](plans/filmo-plugin.md) | The site analysis and the production trials of the filmo plugin. | Built: the plugin with the site's official domain list. | `docs/current/plugin-domains.md` (row 114) |
| [`plans/header-bound-hosters.md`](plans/header-bound-hosters.md) | Probe readings of CDNs that bind a video URL to the player's headers. | Built: `ClientBoundResolverPort` for VEEV and the own-scripts rule of captures. | `docs/current/hoster-resolvers.md` (row 115) |
| [`plans/integration-tests.md`](plans/integration-tests.md) | The design of the integration test suite: categories, fixtures and markers. | Mostly built; six tests are open (Carried over). | none (history) |
| [`plans/more-plugins.md`](plans/more-plugins.md) | The plugin inventory, the remaining candidate sites and a quality checklist. | The candidates are carried over; the checklist goes into the new-plugin guide. | `docs/how-to/new-plugin.md` (row 114) |
| [`plans/next-steps.md`](plans/next-steps.md) | An early project status and its prioritised next steps. | Superseded by the backlog; its open steps are carried over. | `docs/backlog.md` (row 118) |
| [`plans/optimization-options.md`](plans/optimization-options.md) | Where a stream request's time went, and the options to cut it. | Decided into the measures of `round5-measures.md`. | none (history) |
| [`plans/pi-performance.md`](plans/pi-performance.md) | The performance baseline of the production host and its later measurements. | Measured; its re-measuring steps go into the performance guide. | `docs/how-to/performance-measurement.md` (row 117) |
| [`plans/playwright-engine.md`](plans/playwright-engine.md) | The shared browser pool and its resource management. | Built. | `docs/current/browser-and-challenges.md` (row 113) |
| [`plans/plugin-domains.md`](plans/plugin-domains.md) | Domain probe readings of several plugins and the official domain lists. | Built: the domain fallback with the official lists and their markers. | `docs/current/plugin-domains.md` (row 114) |
| [`plans/plugin-repair.md`](plans/plugin-repair.md) | The triage and repair of broken plugins. | Done; its triage method goes into the new-plugin guide. | `docs/how-to/new-plugin.md` (row 114) |
| [`plans/round5-measures.md`](plans/round5-measures.md) | The measures after the fifth end-to-end Stremio check. | Built; its answer rule was later replaced by the answer budget. | none (history) |
| [`plans/search-caching.md`](plans/search-caching.md) | The search result cache and the Stremio search cache. | Built; its early answer was later replaced by the answer budget. | `docs/current/stremio-addon.md` (row 113) |
| [`plans/series-episodes.md`](plans/series-episodes.md) | Probe readings of the episode filter per plugin, before and after each change. | Built: the episode filter and the plugins' episode placement. | `docs/current/plugins.md` (row 114) |
| [`plans/sto-linkout.md`](plans/sto-linkout.md) | The sto link-outs seen in production, and three options for a fix. | Built: the plugin drops results whose links all stayed gated. | `docs/current/plugins.md` (row 114) |
| [`plans/stremio-stream-split.md`](plans/stremio-stream-split.md) | The split of the Stremio stream use case along its phases. | Done: the phase collaborators under `application/stremio/`. | `docs/current/architecture.md` (row 112) |
| [`plans/title-matching.md`](plans/title-matching.md) | Title-match probe runs for short and German series titles, and the plugins' years and genres. | Built; its metadata convention goes into the plugins doc. | `docs/current/plugins.md` (row 114) |
| [`plans/vidhide-segments.md`](plans/vidhide-segments.md) | VidHide's HLS segments behind the proxy. | Fixed: the proxy compares hosts case-insensitively. | `docs/current/stremio-addon.md` (row 113) |
