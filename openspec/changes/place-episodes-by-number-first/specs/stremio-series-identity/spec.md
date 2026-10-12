## ADDED Requirements

### Requirement: A Shorter Title Yields To The Full One
A series result whose titles hold none of the request's series titles whole, while one of them is a strict word subset of a request title, SHALL count as a partial match. A plugin's partial matches SHALL be dropped when the same plugin's search gave a match that is not partial, and SHALL count otherwise. Movie results SHALL NOT be affected.

#### Scenario: The parent beside the series
- **WHEN** a plugin's results for "Naruto: Shippūden" hold the series "Naruto Shippuden" and the series "Naruto"
- **THEN** the series Naruto is dropped
- **AND** Naruto Shippuden is kept

#### Scenario: A site that lists only the shorter name
- **WHEN** a plugin's results for "Bleach: Thousand Year Blood War" hold only the series Bleach
- **THEN** Bleach is kept as a partial match, and its episode is located by title only

#### Scenario: Movies keep their subset match
- **WHEN** a movie result is named "Dune 2021" for the request "Dune: Part One"
- **THEN** it is kept as today
