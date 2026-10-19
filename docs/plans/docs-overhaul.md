# Docs overhaul: one style, target and current state only, history archived

> **Purpose:** The target layout of these docs, the document contract and the steps to get there.
> **Status:** plan (open); the work is rows 110 to 120 of `docs/backlog.md`
> **As of:** 9d2fc4c7
> **Evidence:** an inventory of every Markdown file in the repository (117 files, 21,870 lines, 2.43 MB; the numbers below are its final pass)

## Problem

The docs grew by rows. Of 117 Markdown files, 31 (8,258 lines, 38 % of the lines and 58 % of the bytes) are historical narrative: 27 of the 28 plans, the three refactor records and the CHANGELOG, round-by-round measurement logs next to the decisions they led to; the eight archived OpenSpec changes (33 files, 2,846 lines) come on top and are history by design. The feature and architecture docs describe the current behaviour, but in several styles (heading depth H2 to H4, paragraph-long table cells, single lines of up to 4 KB, no front matter anywhere), with ASCII boxes and directory trees instead of diagrams, and with backlog ids and dated asides inside ("row 106", "finding 11", "checked earlier"). What is target and what is current state is told apart only by reading a plan's dates.

The prose has drifted from the code in eleven places. The worst:

1. `docs/architecture/codeplan.md` and `docs/features/stremio-addon.md` describe a shared search deadline and a half-budget rule for the circuit breaker; `application/stremio/plugin_search.py` has neither (a plugin's timeout runs from its slot, the answer budget cuts no plugin, every timeout is a breaker failure).
2. Five places in four docs say some settings have no environment variable; `infrastructure/config/load.py` reads every setting from the environment.
3. `docs/architecture/clean-architecture.md` draws infrastructure as the ring around application, which under "dependencies point inward" would let infrastructure import application; its own rule table, `FEATURES.md` and the code forbid that.
4. Neither architecture doc names `SeriesMetaPort`, `CinemetaClient`, `release_guess.py`, `episode_index.py`, `episodes.py`, `domain_list.py`, `PassKeeper` or `ChallengeFlag`; the index's layout tree names modules that do not exist.
5. Copies that drift apart: the download endpoints (two docs), deployment (three), the resolver inventory (two), project layout and tech stack (three), the Stremio settings (two), and AGENTS.md §3, §7 and §9 restating feature-doc facts in 1 to 2.6 KB lines.
6. Two OpenSpec changes with every task done are not archived, so two specs still state rules the code dropped; six capabilities have no spec.

A reader who asks "how does a Stremio search work now, and what is planned" reads five files.

## Model

- The index for agents and humans, a status header on every document, numbered decision records with a template, and a "Documentation rules" section: the maintainer's `CodeForge` layout (the example repository named for this overhaul, `cityforall`, was unreachable from the dev container; its practices are adopted when it can be read, ledger item B1).
- The split of the current docs into explanation, how-to and reference (Diátaxis), with target documents as their own kind because the maintainer asked for target and current state only.
- Mermaid for every diagram (GitHub renders it; ASCII art drifts), the OpenSpec specs as the normative target per capability, generated reference tables wherever a script can produce them.

## Target layout

```text
README.md                   user entry: what, quick start; doc links only into docs/README.md
CONTRIBUTING.md             the one copy of dev setup, tests, project layout, tech stack
AGENTS.md                   agent rules; facts become links into docs/current/
CHANGELOG.md                release history (unchanged by this plan; see Open points)
docs/README.md              the only index: one line per doc with its status; no hand-kept counts
docs/target/                Soll: what must hold; changes only by a decision
  architecture.md           dependency rule, layer contracts, invariants (Mermaid layer graph)
  coding-guidelines.md      PYTHON-BEST-PRACTICES cut to the rules this codebase follows
  decisions/NNNN-<slug>.md  decision records for the decisions in force; _template.md
docs/current/               Ist: how it works now; one doc per subsystem, no dates, no row ids
  architecture.md           module map, request flows, composition and shutdown order
  stremio-addon.md          flow, endpoints, matching, ranking, search cache, anime ids, proxies
  torznab-and-crawljobs.md  Torznab request flow, CrawlJob life cycle, download endpoints
  plugins.md                bases, metadata contract, search stages, scoring and probing, selection
  plugin-domains.md         domain fallback, official domain lists, mirror groups
  hoster-resolvers.md       registry, dispatch, bound CDNs, verification, capture, outcomes
  browser-and-challenges.md stealth pool, page gate, clearance and capture state, challenges
  link-validation.md        HEAD then GET, batches, integration
  observability.md          what the core records and where (metrics, stats, history, tracing)
  known-issues.md           the open defects, one line each with its backlog row
docs/how-to/                task guides: deploy, connect-prowlarr, stremio-deployment, new-plugin,
                            new-resolver, operations (prodctl, digest, probes), performance-measurement
docs/reference/             lookups, generated where possible: configuration, torznab-api, metrics,
                            plugins, resolvers
docs/agents/                agent tooling: skills, mcp-servers, orchestration (the rules of the backlog)
docs/backlog.md             open work only: one row per item with status, owner and link
docs/plans/                 open plans only (this one, challenge-reactions, stremio-latency until closed)
docs/archive/               read-only history: README.md index (file, what it was, outcome, successor)
  plans/  refactor/  backlog-2026-10.md
openspec/specs/             normative target per capability: the finished changes archived, the two
                            capabilities without a change written from the current behaviour
openspec/changes/archive/   unchanged: OpenSpec's own decision history
```

Why this split: a target doc changes only by a decision and a current doc only with the code, so a reader knows which one to trust; dates, row ids and measurements stay in the archive, where they are history rather than noise; generated reference tables cannot drift.

The archive folder is `docs/archive/` (the maintainer's example was "docs/sources"): its index carries each file's outcome and successor, which is what readers expect from an archive, while "sources" suggests citations. The name is one `git mv` if the maintainer prefers the other.

## Document contract

Every document under `docs/` outside the archive and the generated reference files starts with its title and a status block:

```markdown
# <Title>

> **Status:** current | target | how-to | reference | plan
> **As of:** <short hash of the staging commit the text describes>
```

An archived document keeps its text and gets `> **Status:** archived` plus `> **Superseded by:** <path>`; the generated reference files carry their generator's name on line 3 instead.

Sections in a fixed order per kind:

| Kind | Sections |
|---|---|
| `current/` | Purpose · How it works (with a Mermaid diagram where a flow or state machine exists) · Configuration (a link into `reference/configuration.md`, no copied tables) · Observability (a link into `reference/metrics.md`) · Limits · Target (the open rows and specs, as links) · References (modules, specs, archive files) |
| `target/` | Rule · Rationale · Consequences · Exceptions · References |
| `target/decisions/` | Context · Decision · Consequences · Status (accepted, superseded by NNNN) · References (the archive file with the evidence) |
| `how-to/` | Goal · Prerequisites · Steps · Verify · Troubleshooting |
| `reference/` | generated tables where possible; otherwise the lookup tables with one line per entry |

Style, the same for every document:

- English; present tense for current behaviour; "must" for rules; "will" only in Target sections.
- The privacy rule (AGENTS.md §1): no clock times or dates of observations, no request ids, no titles or ids of the maintainer's requests, no LAN, ISP, VPN or device names; durations, counts and build hashes are the evidence.
- No backlog ids ("row 106", "finding 11", "N1", "R2") and no dated asides outside `docs/backlog.md`, `docs/plans/` and the archive: a current doc states the behaviour, the archive index states where it came from.
- One sentence per line in paragraphs (semantic line breaks: small diffs, exact edits); a table cell holds at most one sentence, longer content becomes a paragraph or a list under the table.
- Headings to H3; one H1; paths, settings and symbols in backticks; a current doc with a flow or state machine has at least one Mermaid diagram, and the diagram and the prose say the same thing; no ASCII boxes.
- Fixed terms: the production host, the dev container, the maintainer, a request, a search, a capture, a pass, a challenge, a mirror group, the answer budget, the plugin timeout; "CrawlJob" for the entity, `.crawljob` for the file.
- No hand-kept counts (plugins, resolvers, tests, versions): the generated reference files carry the counts, and a version example reads `<version>`.

Diagrams per current doc, each a Mermaid fence:

| Doc | Diagrams |
|---|---|
| `current/architecture.md` | the Torznab search and the Stremio stream request as sequence diagrams; the composition order and the shutdown sequence (both halves: the app's drain and Uvicorn's wait) as flowcharts |
| `target/architecture.md` | the layer graph with the allowed edges only, matching the rule table |
| `current/stremio-addon.md` | the search cache entry's states (missing, fresh, stale, partial, refreshing) as a state diagram; the answer budget against the plugin timeout as a sequence; the `kitsu:` id translation as a flowchart |
| `current/hoster-resolvers.md` | a link's outcome states (stream, dead, challenge with its cooldown steps, breaker open) as a state diagram; the dispatch (client-bound, address-bound → `/play` or `/proxy/<id>/file`) as a flowchart |
| `current/browser-and-challenges.md` | the page gate as a flowchart; the capture state's life cycle (restore, use, save, expire) as a state diagram |
| `current/plugins.md` | the search stages as a sequence; the plugin selection for a Stremio request (score, mirror group, reachability) as a flowchart |
| `current/plugin-domains.md` | the domain decision (configured domains, official list and marker, redirect, recheck) as a flowchart |
| `current/torznab-and-crawljobs.md` | request to XML as a sequence; the CrawlJob life cycle as a state diagram |
| `current/link-validation.md` | HEAD then GET as a flowchart |

## Guard tests

- `tests/unit/infrastructure/test_docs_paths.py` covers the new folders (`docs/target/`, `docs/current/`, `docs/how-to/`, `docs/reference/`, `docs/agents/`, `docs/backlog.md`), not the archive (it cites removed modules by design).
- New `tests/unit/infrastructure/test_docs_style.py`: the status block on line 3 of every document it covers, no heading that names a round, trial, measurement, proof or finding outside the archive and `docs/plans/`, no backlog id outside `docs/backlog.md`, `docs/plans/` and the archive. Row 110 introduces it for the new folders and `docs/README.md`; row 119 enforces it for every document outside the archive.
- `test_docs_privacy.py` stays as it is (its backlog path moves with row 118).
- The generated reference files keep their guards (`test_config_doc.py`, `test_plugin_list_doc.py`) and get one for the resolver list (row 115).

## Steps

The rows live in the backlog's Order table; this is what each one delivers and where its content comes from. The two coders never share a file: each row creates the docs it names, deletes only the docs it replaces, adds its own lines to `docs/README.md` and `CHANGELOG.md`, and touches `AGENTS.md` only on the line its row names. Mr. Normal takes 110, 111, 114, 116, 117 and 119 in that order; Ultracode takes 112, 113, 115 and 120; Mr. Fable takes 118 and the reviews.

### Row 110: the scaffold (Mr. Normal)

- `docs/README.md` replaces `docs/features/README.md`: the index with one line per document and its status, written for agents first ("start here"), with a "Documentation rules" section that holds the contract above; it lists the documents under their current paths and every later row adds its own lines.
- `docs/target/decisions/_template.md`; the archive's index (columns: file, what it was, outcome, successor doc); the three refactor records moved under the archive's refactor folder with the archived header.
- The index links of `README.md`, `AGENTS.md` (§2) and `CONTRIBUTING.md` repointed; `test_docs_paths.py` extended; `test_docs_style.py` added for the new folders.

### Row 111: the archive (Mr. Normal)

- The closed plans and measurement logs move unchanged to the archive's plans folder, file names kept: `anime-ids-spike`, `antibot-patchright`, `browser-page-budget`, `captcha-solving`, `cloudflare-solvers`, `code-review-2026-10-12`, `code-review-fixes`, `cuii-coverage`, `filmo-plugin`, `header-bound-hosters`, `integration-tests`, `more-plugins`, `next-steps`, `optimization-options`, `pi-performance`, `playwright-engine`, `plugin-domains`, `plugin-repair`, `round5-measures`, `search-caching`, `series-episodes`, `sto-linkout`, `stremio-stream-split`, `title-matching`, `vidhide-segments`. Each gets the archived header with its successor; the archive index gets one row per file.
- One path rewrite over everything that cites `docs/plans/<name>`: 93 references from 39 files (docstrings in `src/` and `plugins/`, the help texts of `scripts/stremio_round.py`, `stremio_measure.py`, `stremio_profile.py`, `playback_under_load.py`, the tests that name them, the specs).
- The open items of closed plans are listed under a "Carried over" heading in the backlog's Ideas section, one line each, for Mr. Fable to triage: the sidecar check of the anti-bot plan's phase 3, step 8 of the captcha plan, the six open integration tests, the open next steps (monitoring, a scheduled live run, 3.5, 3.6), the missing plugins of the CUII reading and the candidates of `more-plugins`, the open observations of the review fixes, R6, R8 and R9 of the challenge decision (when that plan is archived).
- Stays open in `docs/plans/`: this plan, `backlog-2026-10.md` (row 118), `challenge-reactions.md` and `stremio-latency.md` (archived by row 118 when their open points close).

### Row 112: architecture (Ultracode)

- `docs/target/architecture.md`: the dependency rule and the layer contracts from `clean-architecture.md`'s rule part and AGENTS.md §3's invariants, the layer graph with the allowed edges only.
- `docs/current/architecture.md`: the module map (the inventories of `clean-architecture.md` and `codeplan.md`, plus the modules both miss), the request flows, the composition order and the complete shutdown sequence; the Stremio search timing as `plugin_search.py` has it.
- `docs/target/decisions/`: one record per decision in force, distilled from the archived plans and the backlog's Decisions section: the dependency rule; anime ids from the Anime Kitsu addon with Fribb's list as the fallback; the answer budget beside the plugin timeout; episode placement by number first; the read-ahead of three segments; direct streams proxied; the plugin timeout of 30 s; dead-site plugins stay enabled; the pass keeper's demand bound; the adaptive page budget; own-scripts-only captures; a challenge as an outcome outside the breaker; mirror groups; the resolver state snapshot across restarts; the privacy rule for docs and commit texts.
- `docs/architecture/` deleted; the links of AGENTS.md (§2, §3, §10) and CONTRIBUTING.md repointed.

### Row 113: Stremio and the browser (Ultracode)

- `docs/current/stremio-addon.md` from `stremio-addon.md`'s flow, endpoints, matching, ranking, anime ids and proxies, the search cache's states from the search-continuation spec, the settings as links.
- `docs/how-to/stremio-deployment.md`: the streaming server, the VPN container, the AIOStreams settings (from `stremio-latency.md`).
- `docs/current/browser-and-challenges.md` from the closed browser plans (`playwright-engine`, `browser-page-budget`, `antibot-patchright`, `captcha-solving`) and the decision of `challenge-reactions.md`: the stealth pool, the page gate, the clearance store, the capture state, a challenge's outcome and cooldown.
- `docs/features/stremio-addon.md` deleted; the four links to `captcha-solving.md` (README.md, `FEATURES.md`, the old index, the architecture doc) repointed.

### Row 114: plugins (Mr. Normal)

- `docs/current/plugins.md` from `plugin-system.md`, the contract part of `python-plugins.md` and `plugin-scoring-and-probing.md`, plus what only the archive holds: the metadata convention (the year as the start year, genres comma-joined, from `title-matching.md`), the s.to season-row markup (from `series-episodes.md`), the link-out gate (from `sto-linkout.md`).
- `docs/current/plugin-domains.md`: `mirror-url-fallback.md` rewritten without its contradictions (a permanent redirect replaces the base URL, an answering-only domain is rechecked), the official domain lists (from `plugin-domains.md`), the mirror groups.
- `docs/how-to/new-plugin.md` from the how-to part of `python-plugins.md`, `multi-stage-scraping.md`, the checklist of `more-plugins.md` and the triage method of `plugin-repair.md`.
- `docs/reference/plugins.md`: `docs/plugins.md` moved with the paths in `scripts/generate_plugin_list.py` and `test_plugin_list_doc.py`; the `new-plugin` skill's links.
- Deleted: `plugin-system.md`, `python-plugins.md`, `plugin-scoring-and-probing.md`, `mirror-url-fallback.md`, `multi-stage-scraping.md`.

### Row 115: resolvers (Ultracode)

- `docs/current/hoster-resolvers.md` from the registry, capture and challenge parts of `hoster-resolvers.md`; `docs/how-to/new-resolver.md` from its "Adding a New Resolver".
- `docs/reference/resolvers.md` generated from the registrations, `ALL_XFS_CONFIGS` and `ALL_DDL_CONFIGS` by a new `scripts/generate_resolver_list.py`, guarded like the plugin list.
- `docs/features/hoster-resolvers.md` deleted; the `new-resolver` skill's links.

### Row 116: configuration, Torznab, validation, Prowlarr (Mr. Normal)

- `docs/reference/configuration.md`: precedence and the generated tables (`scripts/generate_config_doc.py`, `test_config_doc.py`, `tests/integration/test_config_loading.py` repointed); every setting has its environment variable, no "YAML-only".
- `docs/how-to/deploy.md`: Docker, image tags, build identity (`<version>` in the example), the reverse proxy, the resolver caveat of the CUII reading.
- `docs/reference/torznab-api.md`: the one owner of the download endpoints, the caps example without a version number.
- `docs/current/torznab-and-crawljobs.md` from `crawljob-system.md` and the Torznab flow; `docs/current/link-validation.md` with its environment line fixed; `docs/how-to/connect-prowlarr.md` with the deployment copy replaced by a link and the shutdown as a link into the architecture doc.
- Deleted: `configuration.md`, `torznab-api.md`, `crawljob-system.md`, `link-validation.md`, `prowlarr-integration.md`.

### Row 117: observability, operations, guidelines (Mr. Normal)

- `docs/current/observability.md` (what the core records), `docs/reference/metrics.md` (names, labels, endpoints), `docs/how-to/operations.md` (prodctl, digest, probes, the post-deploy check), `docs/how-to/performance-measurement.md` (the "How to re-measure" part of `pi-performance.md` and the scripts it names).
- `docs/current/known-issues.md` from the CHANGELOG's `KNOWN_ISSUES`, one line per defect with its backlog row; the CHANGELOG section becomes a link.
- `docs/target/coding-guidelines.md`: `docs/PYTHON-BEST-PRACTICES.md` cut to the rules this codebase follows; AGENTS.md §5's guide line repointed.
- Deleted: `observability.md`, `PYTHON-BEST-PRACTICES.md`.

### Row 118: the backlog and OpenSpec (Mr. Fable, docs freeze)

- `docs/backlog.md`: the open rows only, one line each (id, item, owner, status, link to the plan or spec); the archived backlog: everything else of `backlog-2026-10.md`; `docs/agents/orchestration.md`: the working and orchestration rules; `test_docs_privacy.py`'s path.
- OpenSpec: `openspec archive` for `add-stream-media-quality` and `place-episodes-by-number-first` (their specs then state the current rules), `openspec/project.md`'s change list, task 6.3 of the archived series-identity change into the backlog.
- `challenge-reactions.md` and `stremio-latency.md` archived when the production readings of `react-to-challenges` (tasks 1.10, 2.7) and the open investigation close.
- Two spec gaps closed with the archiving: the `stremio-anime-episodes` spec requires the search cache key `v2` while `application/stremio/search_cache.py` writes `v3`, and the `source="unchecked"` rule of AGENTS.md §7 is in no spec.

### Row 119: closure (Mr. Normal)

- AGENTS.md §3, §7 and §9 trimmed to rules with links into `docs/current/`; CONTRIBUTING.md the one owner of the project layout and tech stack; `docs/features/FEATURES.md` deleted with its feature list as lines of `docs/README.md`; README.md's doc links.
- `test_docs_style.py` enforced for every document outside the archive; `docs/features/` gone.
- The device name of the production machine replaced by "the production host" where the ledger's decision P1 says so (`arm64` examples stay).

### Row 120: the missing specs (Ultracode)

- `openspec/specs/configuration/` and `openspec/specs/plugin-system/` written from the current behaviour (their changes were archived without specs); the other four capabilities get their specs when their open changes are archived.

## Open points

- **CHANGELOG entries.** 2,083 lines, 36 of them over 1 KB, with measurement narratives inside; the inventory suggests capping entries at one to three lines with a link and moving the narratives to the archive. Keep a Changelog allows both. The maintainer decides; until then the CHANGELOG is untouched by this plan.
- **The device name** (the production machine's model in about 30 files): the privacy rule bans device names; row 119 applies the maintainer's decision (ledger P1).
- **The example repository** (`cityforall`): unreachable from the dev container; its practices are adopted when it can be read (ledger B1).
- **This plan** is deleted once its follow-up rows have landed; the git history keeps it (`docs/README.md` → History).

## Follow-ups

The open points were decided with the model repository at hand: the device name becomes "the production host" (row 123), the archive is deleted and history is read from git (row 132), and the model's conventions this layout lacked become rows 127 to 133 of `docs/backlog.md`: the index as a document map with the overall state, AGENTS.md as rules only under 3,000 words with the change-to-documents table, a tech-stack reference with the reasons, a glossary, a purpose line in every header, and the alternatives in every decision record. The CHANGELOG keeps its length. This plan is deleted with row 132.
