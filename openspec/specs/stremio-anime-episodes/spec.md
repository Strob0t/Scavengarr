# stremio-anime-episodes Specification

## Purpose
Find an anime request's episode on sites that number episodes their own way: the request carries an episode reference (the English title, the air date and an absolute number, Kitsu's where one Kitsu entry counts the series), and plugins that declare episode location place it on the site's own season pages, or answer nothing for the series. How it behaves: `docs/current/plugins.md` and `docs/current/stremio-addon.md`.

## Requirements

### Requirement: The Request Carries An Episode Reference
For a series request with a season and an episode, the use case SHALL build an `EpisodeRef` with the request's season and episode, the English title and release date of that episode from the Cinemeta list, and an absolute number: a Kitsu episode number when it lies within 5 of the episode's position or the list lacks the episode, else the position.

#### Scenario: An IMDb request for Anime A S5E2
- **WHEN** the request is `<Anime A>:5:2` and Cinemeta lists 8, 22, 17 and 13 episodes for seasons 1 to 4
- **THEN** the reference has the title "<episode title>? <episode title>!" and the absolute number 62

#### Scenario: A Kitsu request
- **WHEN** the request came as `kitsu:<Anime A>` episode 1089 and was translated to `<Anime A>:22:4`
- **THEN** the reference's absolute number is 1089 (the position is 1088)

#### Scenario: A Kitsu entry that spans one season
- **WHEN** the request came as the Kitsu entry of a second season, episode 3, and was translated to S2E3 of a series whose first season lists 25 episodes
- **THEN** the reference's absolute number is 28, the position, not 3

### Requirement: The Reference Names Its Number's Source
The reference SHALL name where its absolute number comes from: `kitsu` for a Kitsu number; `position` for the position of a series no Kitsu entry counts whole, which the sites count by; `unchecked` for the position of any other series (the anime id list not in memory, or a series a Kitsu entry counts whole whose number did not come out), which may lie one off the sites' count.

#### Scenario: A Kitsu number
- **WHEN** the request came as `kitsu:<Anime A>` episode 1089
- **THEN** the reference's source is `kitsu`

#### Scenario: A position the sites count by
- **WHEN** the request is S2E3 of a series no Kitsu entry counts whole
- **THEN** the reference's absolute number is the position with the source `position`

#### Scenario: A position no Kitsu entry vouches for
- **WHEN** the request is `<Anime A>:22:4`, a Kitsu entry counts the series whole, and the anime ids give the episode no number
- **THEN** the reference's absolute number is the position 1088 with the source `unchecked`

### Requirement: An IMDb Anime Request Carries The Kitsu Number
For an IMDb series request the use case SHALL look up the episode's Kitsu number when one Kitsu entry counts the series from its start: Fribb's id list maps the IMDb id to a Kitsu entry without a season, and the Anime Kitsu addon's record of that entry places its episode 1 at S1E1 and the requested episode exactly once. A `kitsu:` request keeps its own number.

#### Scenario: An IMDb request for a long-running anime
- **WHEN** the request is `<Anime A>:22:4`, and Fribb's list maps `<Anime A>` to the Kitsu entry 12 without a season, whose addon record places episode 1 at S1E1 and episode 1089 at S22E4
- **THEN** the reference's absolute number is 1089, Kitsu's (the position is 1088)

### Requirement: The Position Skips Episode 0 Entries
The position SHALL count the Cinemeta list's entries of season 1 and up with an episode number of 1 and up, ordered by season and episode; an episode 0 entry of a regular season SHALL have no position.

#### Scenario: An episode 0 entry in a regular season
- **WHEN** Cinemeta lists S2E0 of `<Anime D>` before S2E1, after 49 episodes in season 1
- **THEN** S2E1's position is 50, and S2E0 itself has no position

### Requirement: A Reference Needs A Title Or A Number
A reference SHALL carry a title or an absolute number; without a Cinemeta meta, and for an episode the list lacks when no Kitsu number is given, there SHALL be no reference and the plugins SHALL be searched as today.

#### Scenario: No meta
- **WHEN** Cinemeta does not answer
- **THEN** the plugins are searched without a reference, as today

#### Scenario: An episode the list lacks
- **WHEN** the request is `<Anime A>:23:30` and Cinemeta's list ends before it, and the request did not come as a `kitsu:` id
- **THEN** the plugins are searched without a reference, as today

#### Scenario: An episode the list lacks with a Kitsu number
- **WHEN** the same request came as `kitsu:<Anime A>` episode 1180
- **THEN** the reference has the absolute number 1180 and no title

### Requirement: Plugins Declare Episode Location
A plugin that locates episodes by the reference SHALL declare `locates_episodes = True`; the plugin search SHALL pass `episode_ref` to such plugins only, and every other plugin SHALL be called as today.

#### Scenario: A plugin without the capability
- **WHEN** the plugin search dispatches a request with a reference to a plugin without `locates_episodes`
- **THEN** the plugin's `search` is called with query, category, season and episode only

### Requirement: aniworld Indexes The Series
With a reference, aniworld SHALL read the series' season pages into an index of rows (season, episode, German title, English title, absolute number when the row shows one), cached for 7 days per series.

#### Scenario: A long-runner's season page
- **WHEN** the index is built from Anime A's `staffel-2` page
- **THEN** it holds 16 rows numbered 62 to 77 with their English titles

#### Scenario: A series without numbers
- **WHEN** the index is built from Anime H's `staffel-3` page
- **THEN** it holds 11 rows with titles and no absolute numbers

### Requirement: aniworld Locates The Episode
aniworld SHALL locate the row that shows the reference's absolute number, whatever its title. Only when no row shows that number, or the reference carries none, SHALL it locate the row by its neighbours within 5 confirmed by a title match of at least 0.6, else by the exact normalised English title, else by the best fuzzy title of at least 0.85. Without a row it SHALL answer no result for that series and SHALL never build the page from the request's numbers; without a reference it SHALL answer as today.

#### Scenario: Located by number
- **WHEN** the reference is S5E2 with the absolute number 62 and the index's row 62 is `staffel-2/episode-1` "<episode title>? <episode title>!"
- **THEN** the hoster links come from `staffel-2/episode-1`

#### Scenario: The number's row wins against a better title
- **WHEN** the reference is the second part of a two-part case with the absolute number 400, and its English title, a word subset of the first part's, matches the row numbered 399 as well as the row numbered 400
- **THEN** the hoster links come from the row numbered 400

#### Scenario: A row that shows the number in place of a title
- **WHEN** the reference is S22E4 with the absolute number 1089 and the site's rows read "Episode 1088" and "Episode 1089" without English titles
- **THEN** the row numbered 1089 is located by number

#### Scenario: The number is one off and the title confirms the neighbour
- **WHEN** the reference is "<episode title>" with the absolute number 1088, no row shows 1088, and that title is the row numbered 1089
- **THEN** the hoster links come from the row numbered 1089

#### Scenario: Located by title where the site shows no numbers
- **WHEN** the reference is Anime H S4E1 "Someone's Dream" and the index has no absolute numbers
- **THEN** the hoster links come from the row with the English title "Someone's Dream" (`staffel-3/episode-1`)

#### Scenario: Not located
- **WHEN** no row shows the reference's number and no row matches it by title
- **THEN** aniworld answers no result for the series and logs `aniworld_episode_not_located`

#### Scenario: No reference
- **WHEN** the search carries no reference
- **THEN** aniworld fetches `/staffel-<season>/episode-<episode>` as today

### Requirement: An Unchecked Number Needs The Title's Confirmation
Where the reference's number is `unchecked`, a plugin SHALL take the row that shows it only when that row carries no English title, the reference carries no title, or the row's title matches the reference's at least as well as any neighbour's does; otherwise it locates by title among the rows within 5 of the number, the number's row first among equal matches, then by title alone, and answers nothing for the series when no row matches.

#### Scenario: The unchecked number's row carries another title
- **WHEN** the reference is S22E4 "<episode title>" with the unchecked position 1088, the row numbered 1088 carries a different English title and the row numbered 1089 carries the reference's
- **THEN** the hoster links come from the row numbered 1089

#### Scenario: The site shows no titles
- **WHEN** the reference's position 1088 is unchecked and the rows read "Episode 1088" and "Episode 1089" without English titles
- **THEN** the row numbered 1088 is located by number

#### Scenario: The row's title confirms the number
- **WHEN** the reference's position 1088 is unchecked and the row numbered 1088 carries the reference's English title
- **THEN** the row numbered 1088 is located by number

### Requirement: A Located Result Names Both Placements
A located result's `metadata["season"]` and `["episode"]` SHALL be the request's, and the result SHALL carry `site_season`, `site_episode` and `episode_located_by` (`number` or `title`).

#### Scenario: Metadata of a located result
- **WHEN** aniworld locates S5E2 at `staffel-2/episode-1` by number
- **THEN** the result's metadata has season 5, episode 2, site_season 2, site_episode 1 and episode_located_by `number`

### Requirement: sto And fireani Follow The Rule
sto SHALL locate the episode of its anime results (category 5070) by the same rules if its season rows carry the absolute number or the English title (verified from the dev container first), and fireani SHALL do so from its `GetAnime` answer when the site answers again; until then each stays as it is.

#### Scenario: sto's rows carry the number
- **WHEN** a captured s.to season page lists its episodes with `[Episode NNN]`
- **THEN** sto declares `locates_episodes` and locates its 5070 results by number and title

### Requirement: The Search Cache Key Carries The Placement
The Stremio search cache key SHALL carry the version `v3` and, after the season and episode, the reference's placement: the absolute number, prefixed `u` when its source is `unchecked`, or `-` without a number. Entries cached under an earlier version are not answered, and a Kitsu request (its own number) and the IMDb request for the same season and episode (the list's position) share neither a cache entry nor a running search.

#### Scenario: Old entries
- **WHEN** the cache holds `stremio:search:v2:series:<Anime A>:5:2` from before the change
- **THEN** the request reads `stremio:search:v3:series:<Anime A>:5:2:62` and searches anew

#### Scenario: A Kitsu and an IMDb request for the same episode
- **WHEN** the Kitsu request for <Anime A> episode 1089 (translated to S22E4) and the IMDb request `<Anime A>:22:4` whose number did not come out (the position 1088, unchecked) arrive together
- **THEN** the first reads `stremio:search:v3:series:<Anime A>:22:4:1089`, the second `stremio:search:v3:series:<Anime A>:22:4:u1088`, and each runs its own search

### Requirement: A Parent Series Is Located By Title Only
When a plugin's series matches the request only as a shorter form of the request's series titles (none of its titles holds a request title whole, and one is a strict word subset of a request title, as Anime E for "Anime E: Anime E: Sequel"), aniworld and sto SHALL locate the episode on it by title only, without the reference's absolute number and without the neighbour rule.

#### Scenario: A sequel kept as the parent's season
- **WHEN** the request is "Anime E: Anime E: Sequel" S1E13, whose absolute number is 13, and the site holds it as season 17 of the series Anime E
- **THEN** the episode is located on Anime E's index by its English title in season 17
- **AND** never on Anime E's row numbered 13

#### Scenario: No title match on the parent
- **WHEN** the request is "Anime C: Sequel" S1E6, a site lists only the series Anime C, and no Anime C row carries the episode's English title
- **THEN** that site answers no result for the request

#### Scenario: The full title is not partial
- **WHEN** the site's series for the request "Anime C: Sequel" is "Anime C: Sequel"
- **THEN** the number rule of "aniworld Locates The Episode" applies
