## ADDED Requirements

### Requirement: A Shorter Title Yields To The Full One
A series result whose titles hold none of the request's series titles whole, while one of them is a strict word subset of a request title, SHALL count as a partial match. A plugin's partial matches SHALL be dropped when the same plugin's search gave a match that is not partial, and SHALL count otherwise. Movie results SHALL NOT be affected.

#### Scenario: The parent beside the series
- **WHEN** a plugin's results for "Anime C: Sequel" hold the series "Anime C: Sequel" and the series "Anime C"
- **THEN** the series Anime C is dropped
- **AND** Anime C: Sequel is kept

#### Scenario: A site that lists only the shorter name
- **WHEN** a plugin's results for "Anime E: Anime E: Sequel" hold only the series Anime E
- **THEN** Anime E is kept as a partial match, and its episode is located by title only

#### Scenario: Movies keep their subset match
- **WHEN** a movie result is named "Movie A" for the request "Movie A: Part One"
- **THEN** it is kept as today
