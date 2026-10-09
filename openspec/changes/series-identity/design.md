# Design: The right series and the right episode

## Context

A Stremio series request names an IMDb id, a season and an episode. The use
case (`application/use_cases/stremio_stream.py`) translates a `kitsu:` id
first, resolves one `TitleMatchInfo` per plugin language (`application/
stremio/title_resolution.py`: with a TMDB key the TMDB client, in production
`ImdbFallbackClient`, which reads IMDb Suggest's title and start year and the
Wikidata label), builds the queries (`queries.py`: the title, and the part
before a colon; no year, no season), and searches every selected plugin with
`search(query, category=5000, season=, episode=)`. Plugin side,
`relevant_hits` (`infrastructure/plugins/relevance.py`) keeps the hits whose
title contains every query word, and for a single-title request an exact hit
drops the longer ones. App side, `filter_by_title_match` (`infrastructure/
stremio/title_matcher.py`) scores the best of `token_sort_ratio` and
`token_set_ratio` over the title candidates, adds 0.2 for a year within the
tolerance (3 years for a series, 1 for a movie), subtracts 0.3 for one
outside, 0.35 for a sequel-number mismatch, and keeps a score at or above
0.7; the result's year comes from the title or the release name only. The
episode filter (`infrastructure/stremio/episode_filter.py`) reads
`metadata["season"]` and `["episode"]`, else guessit, else the link labels.

The anime sources are aniworld and fireani (always category 5070, accept a
5000 request) and the general sites that label anime by genre (sto, kinoger,
megakino, streamkiste through `stream_category`). The anime id translation
(`infrastructure/anime/`) maps a Kitsu episode to IMDb's season and episode
through the Anime Kitsu addon, else Fribb's list; after that the sites are
assumed to count as IMDb does.

Verified on 2026-10-09 (read-only): Cinemeta's series meta carries `genres`
(One Piece 1999: Animation, Action, Adventure; One Piece 2023: Action,
Adventure, Comedy) and `videos` with `season`, `episode`, `name` and
`released`; aniworld's season pages list each episode as
`<td class="seasonEpisodeTitle"><a href=".../staffel-2/episode-1"><strong>
German title</strong> - <span>English title [Episode 062]</span></a>`, with
the absolute number only for long-runners (One Piece yes, Demon Slayer no);
the Anime Kitsu addon numbers One Piece absolutely (video 62 → S5E2, 1089 →
S22E4) and its titles equal Cinemeta's names.

## Goals / Non-Goals

Goals: a request for the 1999 anime never answers with the 2023 series and
the other way round, wherever a site tells them apart (year, genre, IMDb id);
an anime episode request answers with the site's page of that episode or
nothing; every drop names its reason; no new configuration.

Non-goals: Torznab search is untouched (the gates live in the Stremio
matcher); no title-only search for Kitsu ids without an IMDb id (the spike's
decision stands); no new sources; a site that shows neither year nor genre
nor IMDb id for its "One Piece" page stays ambiguous, and the follow-up for
such plugins (store the detail page's year) is listed from the probe, not
built here.

## Decisions

### Series meta from Cinemeta

`CinemetaClient` (`infrastructure/stremio/cinemeta.py`) fetches
`https://v3-cinemeta.strem.io/meta/<type>/<imdb>.json` (5 s timeout, the
default User-Agent), reduces it to `SeriesMeta(name, year, genres,
episodes)` with `EpisodeMeta(season, episode, name, released)` for a series,
and caches the reduced record for 7 days under `cinemeta:v1:<type>:<imdb>`
(`CachePort`). Alternatives: TMDB's genre ids (dropped by the client today,
and production has no key), Wikidata's P31 and P136 (a second lookup, no
episode list), the Kitsu addon's `animeType` (anime only, a community service
behind Cloudflare). Cinemeta is the catalog the player reads the request's
ids from, answers both questions (kind and episodes) in one request, and is
cached like the Kitsu record. `TitleResolver._title_info` keeps the existing
title client for the title and year in the plugin's language and adds
`imdb_id` (from the request) and `animation` (`"Animation" in genres`; `None`
without a meta) to `TitleMatchInfo`. The lookup is a `stremio_phase` stage
(`phase="series_meta"`, outcome `found`, `not_found`, `error`), next to
`anime_ids`.

### The year decides

`score_title_match` keeps the bonus for a year within the tolerance; a known
year outside it makes the score 0 (the result is dropped) instead of 0.3 less.
The probe showed why a penalty cannot work: an exact title with a wrong year
scores exactly the threshold, and any smaller penalty keeps it, any larger one
also drops near-exact titles with the right year's bonus missing. The
tolerances stay (series 3 years, movies 1 year), so a site's start year off by
one or two passes. The result's year comes from the title, the release name,
guessit, and now `metadata["year"]` (int, or a string of digits), which
kinoger, megakino, streamkiste, hdfilme, streamcloud, movie2k, moflix, cine,
serienfans, filmfans, animeloads and fireani already store. Rollout is
measured: the title probe over the series set of `docs/plans/title-matching.md`
and the three One Piece ids runs before and after; a result the year rule
drops that the old rule kept is listed, and a plugin whose stored year is not
the series' start year (a long-runner labelled with its latest season) gets
its `metadata["year"]` excluded from the rule by name until it stores the
start year. `stremio.title_year_penalty` is removed from `StremioConfig`,
`docs/features/configuration.md`, `title_resolution.py` and the benchmark
conftest; a YAML that still sets it is ignored (the loader ignores unknown
keys; a test proves it for this key).

### An IMDb id decides

A result whose `metadata` carries `imdb` or `imdb_id` (fireani, megakino_to
and movie4k through `data_api.py`) is compared by the id alone: equal to the
reference's → score 1.2 (kept), different → 0 (dropped). The id is normalised
to `tt` plus digits before the comparison; anything else counts as no id.

### Category against the reference's kind

The matcher knows the reference's `animation` and the result's `category`
and `metadata["genres"]`. Rules, applied before the text score:

- Reference not animation (`False`) and result category 5070 → dropped
  (`reason="category"`). aniworld and fireani label everything 5070; sto,
  kinoger, megakino and streamkiste label 5070 by the site's genres.
- Reference animation (`True`), result category 5000 and `metadata["genres"]`
  non-empty → dropped. A site that lists genres and did not call the title
  anime says it is another series; a result without genres (filmpalast,
  kinoking) says nothing, and the text and year rules decide.
- Reference unknown (`None`, no meta) → the category is ignored.

Per-plugin knowledge ("this plugin labels anime") was the alternative; the
matcher has no plugin name, and the genres on the result carry the same
evidence.

### A year suffix is not a title word

`relevant_hits` strips a trailing `(YYYY)` from a hit's title before counting
its words, so "One Piece (2023)" has no extra word next to "One Piece": both
hits are scraped, both results reach the matcher, and the year decides. The
plugin keeps the suffix in the result title (sto does), so
`_extract_result_year` sees it.

### The episode reference

`EpisodeRef` (`domain/entities/stremio.py`, frozen): `season`, `episode`,
`title: str | None` (Cinemeta's English `name`), `aired: str | None`
(`released[:10]`), `absolute: int | None`. The use case builds it after the
title lookup: `title` and `aired` from the Cinemeta list's entry for the
request's season and episode; `absolute` from the Kitsu number when the
request came as `kitsu:` (the addon's numbering is the sites' absolute
numbering: 62 → S5E2, 1089 → S22E4), else the entry's position in the list's
regular seasons ordered by season and episode (One Piece S5E2 → 62, exact;
S22E4 → 1088 where aniworld reads 1089, so the number is an estimate the title
confirms). Without a Cinemeta meta there is no reference and the plugins
answer as today. The reference travels next to `season` and `episode`
through `TitleSearch.progress` and `PluginSearch`; `_dispatch_search` passes
`episode_ref=` only to a plugin with `locates_episodes = True` (a duck-typed
class attribute like `address_bound` and `needs_playback_check`), so the 38
other plugins keep their signatures. The search cache key becomes
`stremio:search:v2:...`, so placements cached before the change expire at
the deploy instead of at their TTL.

### aniworld locates the episode

With a reference, `aniworld.py` reads the series page's season links and
every season page's rows (bounded by the plugin's detail-page semaphore),
keeps `(season, episode, german, english, absolute)` per row, and caches the
index for 7 days under `aniworld:episodes:v1:<slug>` in the plugin's cache
(One Piece: 23 pages once a week; most anime: one to five). Locate order:

1. `absolute` given and the index has numbers: the row with that number and
   its neighbours within 5; of those, the best title match
   (`token_set_ratio` on the normalised English titles) at or above 0.6; the
   numbered row itself when the reference has no title.
2. The row whose normalised English title equals the reference's.
3. The best fuzzy title at or above 0.85 over the whole index.
4. Nothing: the plugin logs `aniworld_episode_not_located` (slug, season,
   episode, absolute) and returns no result for that series. It never
   answers with the request's numbers applied to its own Staffel numbering.

The result's `metadata["season"]` and `["episode"]` stay the request's (what
the result answers, read by the episode filter); `site_season`,
`site_episode` and `episode_located_by` (`number`, `title`) name the page and
the evidence; `aniworld_episode_located` logs them. A request without a
reference (no meta) takes today's URL from the numbers.

### sto and fireani

sto's anime pages (category 5070) come from the same software family as
aniworld; the first task checks a season page's rows from the dev container
(s.to refuses the Pi, finding 16). If they carry the number or the English
title, `sto.py` declares `locates_episodes` and applies the rules above for
its 5070 results; if not, sto stays as it is and the gap is documented.
fireani's `GetAnime` answer lists `animeSeasons[].animeEpisodes[]`; the site
has been dead since 2026-10-08 (`docs/plans/plugin-domains.md`), so its
locate step is specified by the same rules and implemented when a live
answer shows the episode fields (number, title).

## Risks / Trade-offs

- Cinemeta unreachable from the Pi: the stage logs `error`, `animation` is
  unknown, no reference; the behaviour is today's. One request per title,
  cached, within the plugin timeout.
- A site's year is not the start year: the measured rollout above; the
  exclusion by plugin name is a code constant with the probe's evidence, not
  configuration.
- The aniworld index costs one page per season on first use; the semaphore
  bounds it and the cache keeps it weekly. A series page without season links
  (a film) gets no index and answers as today.
- Title variants (One Piece S1E1: "...Who Will Become the Pirate King!" against
  Cinemeta's "...Who's Gonna Be King of the Pirates!") are why rule 1 confirms
  by a low bar (0.6) and rule 3 demands a high one (0.85).
- The matcher's category rules depend on the sites' genre labels; a site that
  calls the live action "Anime" by mistake is dropped for the live action and
  kept for the anime. The probe shows it; the plugin's genre map is the fix.

## Migration

No data migration. The search cache key version drops cached placements at
the deploy. The removed setting is ignored when still present.

## Open Questions

- Whether s.to's season rows carry the absolute number (task 6.1 answers it).
- fireani's episode fields (task 6.3, when the site answers).
