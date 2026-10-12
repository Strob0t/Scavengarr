## ADDED Requirements

### Requirement: A Challenge Is Its Own Resolution Outcome
The hoster resolver registry SHALL end a resolution as `challenge` when its capture showed a challenge (Cloudflare's interstitial or Turnstile widget, a captcha provider's frame, or a request its resolver names as the hoster's captcha) and the resolve bound cut it, or it ended without a stream while the challenge stood. A `challenge` SHALL neither count for nor reset the hoster's circuit breaker, and SHALL not be cached.

#### Scenario: Cut after the hoster's captcha
- **WHEN** Filemoon's capture requests the player's `captcha` and the 10 s resolve bound cuts it
- **THEN** the resolution ends as `challenge`
- **AND** the breaker's failure count is unchanged

#### Scenario: A widget and no stream within the bound
- **WHEN** Dropload's capture shows a Turnstile widget and returns no stream before the bound
- **THEN** the resolution ends as `challenge`
- **AND** the link is not cached as dead

#### Scenario: A timeout without a challenge
- **WHEN** a capture that showed no challenge is cut by the bound
- **THEN** the resolution ends as `timeout` and counts for the breaker, as before

#### Scenario: A half-open trial
- **WHEN** the half-open trial of a hoster's breaker ends as `challenge`
- **THEN** the trial's slot is freed and the breaker stays half-open

### Requirement: A Page Past Its Challenge Gives A Verdict
A page that got past its challenge (an interstitial that cleared, a widget whose token came, the hoster's offline notice) SHALL give its verdict on the link: a resolution that ends without a stream after it SHALL be judged as one without a challenge (`dead`), until a later challenge stands again. A cut after a passed challenge SHALL end as `challenge` and SHALL start no cooldown.

#### Scenario: A dead link behind a passed challenge
- **WHEN** Dropload's capture clears the interstitial on a cold context and the page shows "File not found"
- **THEN** the resolution ends as `dead` and is cached as dead
- **AND** Dropload's next link is resolved without a cooldown

#### Scenario: A cut past its challenge
- **WHEN** the bound cuts a capture after it cleared its interstitial
- **THEN** the resolution ends as `challenge` and runs on in the background
- **AND** no cooldown starts

### Requirement: The Challenge Cooldown
After a `challenge` whose challenge stood the registry SHALL skip the hoster's links for 1 minute, and for 5, 15 and then 30 minutes after each further consecutive one, staying at 30. A stream from the hoster SHALL end the cooldown and reset its steps; a passed challenge SHALL NOT end it. A skipped link SHALL be logged as `hoster_resolve_challenge_cooldown`, counted, and not cached. The cooldown SHALL be checked before the breaker and SHALL NOT outlive a restart.

#### Scenario: The first challenge
- **WHEN** Filemoon's resolution ends as `challenge` at 12:00:00
- **THEN** Filemoon's links are skipped until 12:01:00
- **AND** the next one after that is resolved

#### Scenario: The steps
- **WHEN** the resolutions after the 1-minute and the 5-minute cooldown end as `challenge` too
- **THEN** the next cooldowns last 15 minutes and then 30 minutes, and every further one 30 minutes

#### Scenario: A stream ends it
- **WHEN** a Filemoon resolution returns a stream
- **THEN** no cooldown runs for Filemoon
- **AND** its next `challenge` starts at 1 minute again

#### Scenario: A half-open breaker in a cooldown
- **WHEN** a hoster's breaker is half-open and its challenge cooldown runs
- **THEN** the link is skipped without claiming the trial's slot

### Requirement: A Challenged Capture Finishes In The Background
When the resolve bound cuts a capture that showed a challenge, the registry SHALL answer `challenge` at once and SHALL let the resolution run on, shielded from the request, for up to 20 s after the cut. At most one such completion SHALL run per hoster, and a second cut capture of that hoster SHALL be cancelled at the cut. A completion SHALL keep only the page its capture holds, and shutdown SHALL cancel it.

#### Scenario: Completed in the background
- **WHEN** the 10 s bound cuts Filemoon's capture after its `captcha` request and the player requests its playlist 7 s later
- **THEN** the request is answered at the cut without Filemoon's stream
- **AND** the stream is cached under the link

#### Scenario: One completion per hoster
- **WHEN** a second Filemoon capture is cut after a challenge while a completion runs
- **THEN** the second capture is cancelled at the cut and ends as `challenge`

#### Scenario: The deadline
- **WHEN** a completion whose challenge still stands has no stream 20 s after the cut
- **THEN** it is cancelled and its page closes
- **AND** the hoster's cooldown steps up

#### Scenario: A completion past its challenge finds a dead link
- **WHEN** a completion gets past its interstitial and the page shows the hoster's offline notice
- **THEN** the link is cached as dead
- **AND** the hoster's cooldown does not step up

#### Scenario: Shutdown
- **WHEN** the app shuts down while a completion runs
- **THEN** the registry's `aclose()` cancels it and its page closes

### Requirement: A Completion's Stream Counts As The Hoster's Stream
A stream a background completion finds SHALL pass the registry's judgement as a resolution's stream does, the playback check included where the resolver needs one. It SHALL be cached under its link for the resolution cache's lifetime and SHALL count as the hoster's stream: it ends the challenge cooldown and closes a half-open breaker. Only resolutions the registry caches SHALL complete in the background.

#### Scenario: The next request
- **WHEN** a completion's stream passed the judgement and its link is requested again
- **THEN** the registry answers it from the cache
- **AND** the hoster's next link is resolved without a cooldown

#### Scenario: A player's own resolution
- **WHEN** a resolution for one player's headers (`resolve_for_client`) is cut after a challenge
- **THEN** it ends as `challenge` without a background completion

### Requirement: A Passive Widget Marks Nothing
A resolver whose player loads a captcha widget that does not gate its stream SHALL declare it (`passive_widget`), and a captcha provider's frame SHALL NOT mark its resolutions; an interstitial and the hoster's own captcha still SHALL. Every other capture SHALL be marked by a provider's frame. reCAPTCHA SHALL count by its checkbox frame (`anchor`) only: its invisible widget and its puzzle frame (`bframe`), which loads hidden with every widget, SHALL NOT mark.

#### Scenario: DoodStream's invisible Turnstile
- **WHEN** DoodStream's capture loads its player's Turnstile frame and ends without a stream
- **THEN** the resolution is judged as without a challenge
- **AND** no cooldown starts

#### Scenario: A gate in front of the player
- **WHEN** an XFS hoster's page has no video and shows a captcha widget in front of the player (Dropload)
- **THEN** the widget's frame marks the resolution

#### Scenario: reCAPTCHA's puzzle frame
- **WHEN** a page loads reCAPTCHA's `bframe` and no visible checkbox frame
- **THEN** the resolution is not marked
