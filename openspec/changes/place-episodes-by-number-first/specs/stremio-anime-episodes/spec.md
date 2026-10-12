## MODIFIED Requirements

### Requirement: aniworld Locates The Episode
aniworld SHALL locate the row that shows the reference's absolute number, whatever its title. Only when no row shows that number, or the reference carries none, SHALL it locate the row by its neighbours within 5 confirmed by a title match of at least 0.6, else by the exact normalised English title, else by the best fuzzy title of at least 0.85. Without a row it SHALL answer no result for that series and SHALL never build the page from the request's numbers; without a reference it SHALL answer as today.

#### Scenario: Located by number
- **WHEN** the reference is S5E2 with the absolute number 62 and the index's row 62 is `staffel-2/episode-1` "The First Line of Defense? The Giant Whale Laboon Appears!"
- **THEN** the hoster links come from `staffel-2/episode-1`

#### Scenario: The number's row wins against a better title
- **WHEN** the reference is the second part of a two-part case with the absolute number 400, and its English title, a word subset of the first part's, matches the row numbered 399 as well as the row numbered 400
- **THEN** the hoster links come from the row numbered 400

#### Scenario: A row that shows the number in place of a title
- **WHEN** the reference is S22E4 with the absolute number 1089 and the site's rows read "Episode 1088" and "Episode 1089" without English titles
- **THEN** the row numbered 1089 is located by number

#### Scenario: The number is one off and the title confirms the neighbour
- **WHEN** the reference is "Entering a New Chapter! Luffy and Sabo's Paths!" with the absolute number 1088, no row shows 1088, and that title is the row numbered 1089
- **THEN** the hoster links come from the row numbered 1089

#### Scenario: Located by title where the site shows no numbers
- **WHEN** the reference is Demon Slayer S4E1 "Someone's Dream" and the index has no absolute numbers
- **THEN** the hoster links come from the row with the English title "Someone's Dream" (`staffel-3/episode-1`)

#### Scenario: Not located
- **WHEN** no row shows the reference's number and no row matches it by title
- **THEN** aniworld answers no result for the series and logs `aniworld_episode_not_located`

#### Scenario: No reference
- **WHEN** the search carries no reference
- **THEN** aniworld fetches `/staffel-<season>/episode-<episode>` as today

## ADDED Requirements

### Requirement: A Parent Series Is Located By Title Only
When a plugin's series matches the request only as a shorter form of the request's series titles (none of its titles holds a request title whole, and one is a strict word subset of a request title, as Bleach for "Bleach: Thousand Year Blood War"), aniworld and sto SHALL locate the episode on it by title only, without the reference's absolute number and without the neighbour rule.

#### Scenario: A sequel kept as the parent's season
- **WHEN** the request is "Bleach: Thousand Year Blood War" S1E13, whose absolute number is 13, and the site holds it as season 17 of the series Bleach
- **THEN** the episode is located on Bleach's index by its English title in season 17
- **AND** never on Bleach's row numbered 13

#### Scenario: No title match on the parent
- **WHEN** the request is "Naruto: Shippūden" S1E6, a site lists only the series Naruto, and no Naruto row carries the episode's English title
- **THEN** that site answers no result for the request

#### Scenario: The full title is not partial
- **WHEN** the site's series for the request "Naruto: Shippūden" is "Naruto Shippuden"
- **THEN** the number rule of "aniworld Locates The Episode" applies

## REMOVED Requirements

### Requirement: A Number-Only Row Is Located By The Number
**Reason**: The number's row is now located whatever its title ("aniworld Locates The Episode"), so a row without a title needs no rule of its own; its second clause, a titled row needing the title's confirmation, is what row 82 measured wrong.
**Migration**: None; its scenario is "A row that shows the number in place of a title" of "aniworld Locates The Episode".
