## ADDED Requirements

### Requirement: Kitsu Ids in Stream Requests
The Stremio manifest SHALL list `kitsu` among its `idPrefixes`, and the stream route SHALL accept `kitsu:<anime id>` for movies and `kitsu:<anime id>:<episode>` for series.

#### Scenario: Series episode from an anime catalog
- **WHEN** Stremio requests `GET /api/v1/stremio/stream/series/kitsu:7442:3.json`
- **THEN** the request is parsed with Kitsu id `7442` and episode `3`
- **AND** the response is `{"streams": [...]}` built by the same pipeline as a `tt` request

#### Scenario: Malformed Kitsu id
- **WHEN** the stream id is `kitsu:abc` or `kitsu:7442:x`
- **THEN** the response is `{"streams": []}` and nothing is searched

### Requirement: Anime Id Translation
The system SHALL translate a `kitsu:` request into the pipeline's internal request before title resolution: the IMDb or TMDB id, content type, season and episode of the title as the sites list it, through Kitsu's entry (titles, TheTVDB mappings) and episode record (`seasonNumber`, `relativeNumber`) and TMDB's lookup by TheTVDB id.

#### Scenario: Entry mapped to a TheTVDB season
- **WHEN** Kitsu's entry carries a `thetvdb/season` mapping for season 2 and TMDB finds the series by its TheTVDB id
- **THEN** the internal request has the series' IMDb id, season 2 and the requested episode number
- **AND** the plugins receive the German title from TMDB as for a `tt` request

#### Scenario: Long-runner episode placed by Kitsu's episode record
- **WHEN** the entry is one series with hundreds of episodes and the requested episode's record has `seasonNumber` 12 and `relativeNumber` 4
- **THEN** the internal request has season 12 and episode 4

#### Scenario: Movie
- **WHEN** the entry's `subtype` is `movie`
- **THEN** the internal request is a movie request without season or episode, whatever Stremio's route type was

#### Scenario: No mapping
- **WHEN** Kitsu's entry has no TheTVDB mapping or TMDB finds nothing for it
- **THEN** the plugins search with Kitsu's romaji and English titles and the entry's year, season 1 (or the mapped season) and Kitsu's episode number
- **AND** the log has `anime_id_translated` with `season_source` `default`

### Requirement: Bounded and Cached Kitsu Lookups
The system SHALL cache Kitsu entries and episode records for 30 days and SHALL make at most one request to Kitsu per entry and per episode, with a 5 s timeout and no retry.

#### Scenario: Second request for the same episode
- **WHEN** the same `kitsu:<id>:<episode>` is requested again within 30 days
- **THEN** no request goes to Kitsu

#### Scenario: Kitsu unreachable
- **WHEN** Kitsu does not answer within 5 s for an entry not in the cache
- **THEN** the response is `{"streams": []}`, the log has `anime_id_lookup_failed`, and no plugin is searched

### Requirement: Shared Cache Across Id Schemes
Translated requests SHALL use the translated id in the search cache key, so a title opened from Cinemeta and from an anime catalog shares one search cache entry and its links.

#### Scenario: Same title, two catalogs
- **WHEN** `tt0388629:12:4` was answered a minute ago and `kitsu:12:900` translates to the same IMDb id, season and episode
- **THEN** the second answer comes from the search cache
