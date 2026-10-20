# Glossary

> **Purpose:** The terms of this codebase in one sentence each, with the document that holds them.
> **Status:** reference
> **As of:** 7a6eb282

One line per term: what it means in Scavengarr, and where its full description lives.
The documents use these terms in this sense; the fixed terms of the [documentation rules](../README.md#documentation-rules) are among them.

## Plugins and searches

| Term | Meaning | Described in |
|---|---|---|
| Plugin | One Python file in `plugins/` that searches one site and returns normalised `SearchResult` objects. | [Plugins](../current/plugins.md#purpose) |
| Category | The Torznab category a plugin labels a result with from the site's own data, while `served_category()` and `filter_by_category()` answer a request with the categories the site tells apart. | [Plugins](../current/plugins.md#categories-and-relevance) |
| Mirror group | Sites that front one database share a `mirror_group`: a Stremio request asks one member, and Torznab keeps each member as an indexer of its own. | [Plugin domains](../current/plugin-domains.md#mirror-groups), [decision 0013](../target/decisions/0013-mirror-groups.md) |
| Domain check | `_verify_domain()`, the first step of a plugin's search, picks the one of the plugin's `_domains` that serves the site. | [Plugin domains](../current/plugin-domains.md#the-domain-check) |
| Raising mode | `_fetch_text(raising=True)` and `_safe_fetch(raising=True)`, through `_fetch_search_page()` (a GET or a POST): a failure of the site on the first search page raises `TimeoutError` or `PluginUnreachableError` instead of answering `None`, so the plugin history does not count it as a search without results. | [Plugins](../current/plugins.md#failures-of-the-site) |
| Plugin score | A stream plugin's weighted health and search quality from background probes (`final_score`), by which a Stremio request picks its plugins. | [Plugins](../current/plugins.md#scores-and-probes) |
| Plugin history | `PluginHistory`, the long-term record of each plugin's searches, results, timeouts, challenges and site checks per UTC day, which is the evidence for keeping or retiring a plugin. | [Observability](../current/observability.md#the-plugin-record) |
| Episode reference | The `EpisodeRef` a Stremio series request hands to the plugins that locate episodes: the request's season and episode, the episode's English title and air date, and an absolute number with its source. | [Stremio addon](../current/stremio-addon.md#the-episode-reference) |
| Locate | A plugin with `locates_episodes = True` places the request's episode on the site's own season pages (`locate()`) instead of using the request's numbers. | [Plugins](../current/plugins.md#episodes) |
| Episode placement | `locate()` takes the row that shows the reference's absolute number, and only without such a row or a number does the title decide. | [Decision 0004](../target/decisions/0004-episode-placement-by-number-first.md) |

## Torznab

| Term | Meaning | Described in |
|---|---|---|
| CrawlJob | A stored bundle of a result's validated links, which the download endpoint serves as a JDownloader `.crawljob` file. | [Torznab and CrawlJobs](../current/torznab-and-crawljobs.md#the-crawljob) |
| Grab-time link | A link that a plugin behind a captcha or a download quota resolves only when an Arr app grabs the result (`GrabResolvingPlugin`). | [Torznab and CrawlJobs](../current/torznab-and-crawljobs.md#grab-time-resolution) |

## Stremio

| Term | Meaning | Described in |
|---|---|---|
| Search cache entry | The `CachedSearch` of one title: its results, the start of its search and the plugins that had not finished (`missing`). | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Missing | A title without an entry, for which a request starts the search or joins the one running. | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Fresh | An entry younger than `cache.search_ttl_seconds` with no plugin missing, from which a request answers. | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Partial | An entry that names missing plugins, from which a request answers while one completion search fills it. | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Stale | An entry past `cache.search_ttl_seconds`, from which a request still answers for 6 h while one refresh renews it. | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Refreshing | A stale or partial entry whose background search runs, which ends fresh or, with a plugin still missing, partial. | [Stremio addon](../current/stremio-addon.md#the-search-cache) |
| Answer budget | The time after its start at which a Stremio request answers with the results known by then (`stremio.answer_budget_seconds`), while the plugins run on to their plugin timeout. | [Stremio addon](../current/stremio-addon.md#the-answer-budget), [decision 0003](../target/decisions/0003-answer-budget-beside-the-plugin-timeout.md) |
| Plugin timeout | The time a plugin's search may run before it is cut (`stremio.plugin_timeout_seconds`). | [Decision 0007](../target/decisions/0007-plugin-timeout-of-30-s.md) |
| Read-ahead | The HLS proxy's `SegmentReadAhead` fetches three segments ahead of the player per playback. | [Stremio addon](../current/stremio-addon.md#hls-proxy), [decision 0005](../target/decisions/0005-read-ahead-of-three-segments.md) |
| Address-bound stream | A stream whose CDN plays only for the address that resolved it (`address_bound = True`), so the addon sends it through its file proxy instead of `/play`. | [Hoster resolvers](../current/hoster-resolvers.md#bound-cdns), [decision 0006](../target/decisions/0006-direct-streams-proxied.md) |
| Client-bound stream | A stream whose CDN binds the URL to headers the player sends itself (`ClientBoundResolverPort`), so `/play` resolves it with each player's values. | [Hoster resolvers](../current/hoster-resolvers.md#bound-cdns) |

## Hosters and resolvers

| Term | Meaning | Described in |
|---|---|---|
| Hoster | A streaming or direct-download site whose embed or file pages a plugin's results link to. | [Hoster resolvers](../current/hoster-resolvers.md#purpose) |
| Resolver | A hoster resolver (`HosterResolverPort`) checks a hoster link and, for a streaming hoster, turns it into a playable URL with the headers its CDN wants. | [Hoster resolvers](../current/hoster-resolvers.md#resolvers), [resolver list](resolvers.md) |
| Breaker | `PluginCircuitBreaker` skips a plugin for one requested category, or a hoster, after failures in a row until its cooldown has passed. | [Stremio addon](../current/stremio-addon.md#circuit-breakers), [Hoster resolvers](../current/hoster-resolvers.md#the-circuit-breaker) |
| Half-open probe | The one request a breaker lets through after its cooldown, while the concurrent requests still skip, whose outcome closes the breaker or opens it again. | [Stremio addon](../current/stremio-addon.md#circuit-breakers) |
| Cooldown | The wait of an open breaker, 60 s at first and doubled after each failed probe up to 1 h, or of a hoster after a `challenge`, 1, 5, 15 and then 30 min. | [Stremio addon](../current/stremio-addon.md#circuit-breakers), [Browser and challenges](../current/browser-and-challenges.md#challenges) |
| Pass | What a solved challenge leaves in the browser, such as clearance cookies or a player's own state, like the captcha pass Filemoon's player keeps for 30 min. | [Browser and challenges](../current/browser-and-challenges.md#the-clearance-store-and-the-capture-state), [Hoster resolvers](../current/hoster-resolvers.md#filemoons-pass-keeper) |
| Keeper | `PassKeeper` renews Filemoon's pass with one background capture while there is demand, so the next request's capture finds it. | [Hoster resolvers](../current/hoster-resolvers.md#filemoons-pass-keeper), [decision 0009](../target/decisions/0009-pass-keeper-demand-bound.md) |

## Browser and challenges

| Term | Meaning | Described in |
|---|---|---|
| Capture | A run of a hoster's player in the stealth context that returns the stream request the player makes (`capture_media()`), for players that create the URL only while they run. | [Browser and challenges](../current/browser-and-challenges.md#captures) |
| Challenge | A page that stands between a request and its content, which `detect_challenge()` classifies once as a Cloudflare or DDoS-Guard page, Turnstile, reCAPTCHA, hCaptcha or ALTCHA. | [Browser and challenges](../current/browser-and-challenges.md#challenges), [decision 0012](../target/decisions/0012-challenge-outside-the-breaker.md) |
| Mark | The note that a page showed a challenge: a plugin search's `ChallengeFlag`, settled with whether the plugin got past, or a hoster resolution's `ChallengeMark`. | [Browser and challenges](../current/browser-and-challenges.md#challenges) |
| Page gate | `PageGate` decides how many stealth pages run and which work's claim (`play`, `plugin`, `capture` or `background`) gets the next one. | [Browser and challenges](../current/browser-and-challenges.md#the-page-gate), [decision 0010](../target/decisions/0010-adaptive-page-budget.md) |
| Stealth context | The one Patchright context of `StealthPool` on the shared browser, for the work that has to look like a person's browser. | [Browser and challenges](../current/browser-and-challenges.md#the-stealth-pool) |
| Clearance | The clearance cookies of Cloudflare, DDoS-Guard and the HostAdmin WAF, which `ClearanceStore` keeps in the cache backend and puts into every new context. | [Browser and challenges](../current/browser-and-challenges.md#the-clearance-store-and-the-capture-state) |
