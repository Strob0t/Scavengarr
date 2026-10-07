## ADDED Requirements

### Requirement: Kitsu Ids in Stream Requests
The Stremio manifest SHALL list `kitsu:` among its `idPrefixes`, and the stream route SHALL accept `kitsu:<anime id>` for movies and `kitsu:<anime id>:<episode>` for series.

#### Scenario: Series episode from an anime catalog
- **WHEN** Stremio requests `GET /api/v1/stremio/stream/series/kitsu:7442:3.json`
- **THEN** the request is parsed with Kitsu id `7442` and episode `3`
- **AND** the response is `{"streams": [...]}` built by the same pipeline as a `tt` request

#### Scenario: Malformed Kitsu id
- **WHEN** the stream id is `kitsu:abc` or `kitsu:7442:x`
- **THEN** the response is `{"streams": []}` and nothing is searched

### Requirement: Anime Id Translation
The system SHALL translate a `kitsu:` request into the pipeline's internal request before plugin selection and title resolution: the IMDb id, content type, season and episode of the title as IMDb counts them, from the Anime Kitsu addon's meta for the title (its IMDb id and the episode's `imdbSeason`/`imdbEpisode`), and when the addon does not answer or does not map the entry, from the public anime id list (IMDb id, TheTVDB season and episode offset).

#### Scenario: Split cour placed by the addon
- **WHEN** the addon's meta for `kitsu:41982` carries IMDb id `tt2560140` and its video for episode 3 says `imdbSeason` 3 and `imdbEpisode` 15
- **THEN** the internal request has IMDb id `tt2560140`, season 3 and episode 15
- **AND** the plugins receive the title of `tt2560140` as for a `tt` request
- **AND** the log has `anime_id_translated` with `source` `addon`

#### Scenario: Long-runner episode placed by the addon
- **WHEN** the entry is one series with hundreds of episodes and the addon's video for episode 1000 says `imdbSeason` 21 and `imdbEpisode` 109
- **THEN** the internal request has season 21 and episode 109

#### Scenario: Episode without IMDb numbering
- **WHEN** the addon's meta carries the IMDb id but its video for the episode has no `imdbSeason`
- **THEN** the internal request uses the video's own season and episode
- **AND** the log has `anime_id_translated` with `source` `kitsu`

#### Scenario: Movie
- **WHEN** the addon's meta `type` is `movie` (or, in the fallback, the list's `type` is `MOVIE`)
- **THEN** the internal request is a movie request without season or episode, whatever Stremio's route type was

#### Scenario: Addon unreachable, list places the episode
- **WHEN** the addon does not answer within 5 s for an entry not in the cache
- **AND** the list's record for the Kitsu id has IMDb id `tt2560140`, TheTVDB season 3 and episode offset 12
- **THEN** the internal request for episode 3 has IMDb id `tt2560140`, season 3 and episode 15
- **AND** the log has `anime_id_translated` with `source` `lists`

#### Scenario: List record without a season
- **WHEN** the fallback's record has an IMDb id but no TheTVDB season (a long-runner or a one-season series)
- **THEN** the internal request has season 1 and the requested episode number

#### Scenario: No mapping
- **WHEN** neither the addon's meta nor the list maps the Kitsu id to an IMDb id
- **THEN** the response is `{"streams": []}`, the log has `anime_id_lookup_failed`, and no plugin is searched

### Requirement: Bounded and Cached Lookups
The system SHALL cache the addon's record per title and route type for 30 days and the reduced id list for 7 days, SHALL make at most one request to the addon per title (plus one for an episode missing from a cached record) with a 5 s timeout and no retry, and SHALL download the list at most once per 7 days, retrying a failed download after an hour at the earliest.

#### Scenario: Second request for the same title
- **WHEN** another episode of a `kitsu:` title whose record is cached is requested within 30 days
- **THEN** no request goes to the addon

#### Scenario: Episode newer than the cached record
- **WHEN** the requested episode is not in the cached record
- **THEN** the record is fetched once more; the episode is placed from the fresh record, else from the list

#### Scenario: List unavailable
- **WHEN** the addon does not answer and the list cannot be downloaded
- **THEN** the response is `{"streams": []}`, the log has `anime_id_lists_failed` and `anime_id_lookup_failed`, and the list is not requested again within the hour

### Requirement: Shared Cache Across Id Schemes
Translated requests SHALL use the translated id, season and episode in the search cache key, so a title opened from Cinemeta and from an anime catalog shares one search cache entry and its links.

#### Scenario: Same episode, two catalogs
- **WHEN** `tt0388629:21:109` was answered a minute ago and `kitsu:12:1000` translates to the same IMDb id, season and episode
- **THEN** the second answer comes from the search cache
