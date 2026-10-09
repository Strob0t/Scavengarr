## ADDED Requirements

### Requirement: Series Meta From Cinemeta
The title resolution SHALL read the request's Cinemeta meta (`/meta/<type>/<imdb>.json`) once per request, cached for 7 days, and SHALL set `TitleMatchInfo.imdb_id` to the request's IMDb id and `TitleMatchInfo.animation` to whether the meta's genres name "Animation"; without a meta `animation` SHALL be `None`. The lookup SHALL be recorded as a `stremio_phase` stage with `phase="series_meta"`.

#### Scenario: An animated series
- **WHEN** the meta of `tt0388629` lists the genres Animation, Action and Adventure
- **THEN** the reference's `animation` is `True` and its `imdb_id` is `tt0388629`

#### Scenario: A live-action series
- **WHEN** the meta of `tt11737520` lists Action, Adventure and Comedy
- **THEN** the reference's `animation` is `False`

#### Scenario: Cinemeta does not answer
- **WHEN** the meta request times out
- **THEN** the reference's `animation` is `None`, the stage's outcome is `error`, and the request continues

### Requirement: A Year Outside The Tolerance Drops The Result
The title matcher SHALL drop a result whose year is known and differs from the reference's year by more than the tolerance (3 years for a series, 1 for a movie), whatever its title score; a year within the tolerance SHALL keep today's bonus. The result's year SHALL come from its title, its release name, guessit, or `metadata["year"]` (an int or a string of digits), in that order.

#### Scenario: The live-action release against the anime
- **WHEN** the reference is "One Piece" (1999, series) and a result's release name is `One.Piece.2023.S01E01.GERMAN.DL.720p.WEB.h264-SAUERKRAUT`
- **THEN** the result is dropped with the reason `year`

#### Scenario: The year sits in the metadata
- **WHEN** a result's title and release name carry no year and its `metadata["year"]` is `"2023"`
- **THEN** the year 2023 is compared like a year from the title

#### Scenario: A remake of a film
- **WHEN** the reference is "Dune" (2021, movie) and a result's title is "Dune (1984)"
- **THEN** the result is dropped

#### Scenario: No year on either side
- **WHEN** a result carries no year anywhere
- **THEN** the text rules alone decide

### Requirement: An IMDb Id In The Result Decides
When a result's `metadata` carries `imdb` or `imdb_id` that normalises to `tt` plus digits, the matcher SHALL keep the result if it equals the reference's `imdb_id` and drop it if it differs, without regard to the title score.

#### Scenario: Same id
- **WHEN** the reference's `imdb_id` is `tt0388629` and the result's `metadata["imdb"]` is `tt0388629`
- **THEN** the result is kept with the score 1.2

#### Scenario: Other id
- **WHEN** the result's `metadata["imdb_id"]` is `tt11737520` for the same reference
- **THEN** the result is dropped with the reason `imdb`

### Requirement: Category Against The Reference's Kind
The matcher SHALL drop a result labelled 5070 when the reference's `animation` is `False`, SHALL drop a result labelled 5000 with a non-empty `metadata["genres"]` when `animation` is `True`, and SHALL ignore the category when `animation` is `None` or when the result carries no genres.

#### Scenario: The anime site answers the live action
- **WHEN** the reference is the 2023 series (`animation` `False`) and aniworld's result is labelled 5070
- **THEN** the result is dropped with the reason `category`

#### Scenario: A movie site's series page for the anime
- **WHEN** the reference is the 1999 anime (`animation` `True`) and a result is labelled 5000 with `metadata["genres"]` "Action, Abenteuer"
- **THEN** the result is dropped with the reason `category`

#### Scenario: A result without genres
- **WHEN** the reference's `animation` is `True` and a result is labelled 5000 without genres
- **THEN** the text and year rules decide

#### Scenario: No meta
- **WHEN** the reference's `animation` is `None`
- **THEN** the category is not compared

### Requirement: Dropped Results Log Their Reason
`title_match_filtered` SHALL carry `reason` with one of `score`, `year`, `imdb` and `category`, and `title_match_summary` SHALL count the drops per reason.

#### Scenario: A drop by year
- **WHEN** a result is dropped by the year rule
- **THEN** the `title_match_filtered` log has `reason="year"`

### Requirement: A Year Suffix Is Not A Title Word
`relevant_hits` SHALL ignore a trailing `(YYYY)` of a hit's title when it counts the hit's words, so a hit with the suffix is as exact as one without.

#### Scenario: Both One Piece pages are scraped
- **WHEN** a site's search lists "One Piece" and "One Piece (2023)" for the query "One Piece" with a single-title limit
- **THEN** both hits are kept

### Requirement: The Year Penalty Setting Is Removed
`stremio.title_year_penalty` SHALL no longer exist; a configuration that still sets it SHALL load.

#### Scenario: An old YAML
- **WHEN** a YAML sets `stremio.title_year_penalty: 0.3`
- **THEN** the configuration loads and the key has no effect
