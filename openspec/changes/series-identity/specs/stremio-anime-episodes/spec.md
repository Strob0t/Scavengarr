## ADDED Requirements

### Requirement: The Request Carries An Episode Reference
For a series request with a season and an episode, the use case SHALL build an `EpisodeRef` with the request's season and episode, the English title and release date of that episode from the Cinemeta list, and an absolute number: the Kitsu episode number when the request came as a `kitsu:` id, else the episode's position among the list's regular seasons ordered by season and episode.

#### Scenario: An IMDb request for One Piece S5E2
- **WHEN** the request is `tt0388629:5:2` and Cinemeta lists 8, 22, 17 and 13 episodes for seasons 1 to 4
- **THEN** the reference has the title "The First Line of Defense? The Giant Whale Laboon Appears!" and the absolute number 62

#### Scenario: A Kitsu request
- **WHEN** the request came as `kitsu:12` episode 1089 and was translated to `tt0388629:22:4`
- **THEN** the reference's absolute number is 1089

### Requirement: A Reference Needs A Title Or A Number
A reference SHALL carry a title or an absolute number; without a Cinemeta meta, and for an episode the list lacks when no Kitsu number is given, there SHALL be no reference and the plugins SHALL be searched as today.

#### Scenario: No meta
- **WHEN** Cinemeta does not answer
- **THEN** the plugins are searched without a reference, as today

#### Scenario: An episode the list lacks
- **WHEN** the request is `tt0388629:23:30` and Cinemeta's list ends before it, and the request did not come as a `kitsu:` id
- **THEN** the plugins are searched without a reference, as today

#### Scenario: An episode the list lacks with a Kitsu number
- **WHEN** the same request came as `kitsu:12` episode 1180
- **THEN** the reference has the absolute number 1180 and no title

### Requirement: Plugins Declare Episode Location
A plugin that locates episodes by the reference SHALL declare `locates_episodes = True`; the plugin search SHALL pass `episode_ref` to such plugins only, and every other plugin SHALL be called as today.

#### Scenario: A plugin without the capability
- **WHEN** the plugin search dispatches a request with a reference to a plugin without `locates_episodes`
- **THEN** the plugin's `search` is called with query, category, season and episode only

### Requirement: aniworld Indexes The Series
With a reference, aniworld SHALL read the series' season pages into an index of rows (season, episode, German title, English title, absolute number when the row shows one), cached for 7 days per series.

#### Scenario: A long-runner's season page
- **WHEN** the index is built from One Piece's `staffel-2` page
- **THEN** it holds 16 rows numbered 62 to 77 with their English titles

#### Scenario: A series without numbers
- **WHEN** the index is built from Demon Slayer's `staffel-3` page
- **THEN** it holds 11 rows with titles and no absolute numbers

### Requirement: aniworld Locates The Episode
aniworld SHALL locate the row by the absolute number and its neighbours within 5 confirmed by a title match of at least 0.6, else by the exact normalised English title, else by the best fuzzy title of at least 0.85; without a row it SHALL answer no result for that series and SHALL never build the page from the request's numbers; without a reference it SHALL answer as today.

#### Scenario: Located by number
- **WHEN** the reference is S5E2 with the absolute number 62 and the index's row 62 is `staffel-2/episode-1` "The First Line of Defense? The Giant Whale Laboon Appears!"
- **THEN** the hoster links come from `staffel-2/episode-1`

#### Scenario: The number is one off and the title confirms the neighbour
- **WHEN** the reference is S22E4 "Entering a New Chapter! Luffy and Sabo's Paths!" with the absolute number 1088 and that title is the row numbered 1089
- **THEN** the hoster links come from the row numbered 1089

#### Scenario: Located by title where the site shows no numbers
- **WHEN** the reference is Demon Slayer S4E1 "Someone's Dream" and the index has no absolute numbers
- **THEN** the hoster links come from the row with the English title "Someone's Dream" (`staffel-3/episode-1`)

#### Scenario: Not located
- **WHEN** no row matches the reference by number or title
- **THEN** aniworld answers no result for the series and logs `aniworld_episode_not_located`

#### Scenario: No reference
- **WHEN** the search carries no reference
- **THEN** aniworld fetches `/staffel-<season>/episode-<episode>` as today

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

### Requirement: The Search Cache Key Changes
The Stremio search cache key SHALL carry the version `v2`, so entries cached before the change are not answered.

#### Scenario: Old entries
- **WHEN** the cache holds `stremio:search:series:tt0388629:5:2` from before the change
- **THEN** the request reads `stremio:search:v2:series:tt0388629:5:2` and searches anew
