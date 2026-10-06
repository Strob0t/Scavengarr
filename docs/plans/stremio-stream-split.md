# Plan: Split `stremio_stream.py` Along Its Phases

Item I14 of `docs/plans/ideas-backlog.md`. Status 2026-10-06: proposed, not started. Behaviour-preserving refactor; no OpenSpec change (no capability changes).

## Problem

`src/scavengarr/application/use_cases/stremio_stream.py` has 939 lines and 26 methods, its constructor binds 31 attributes, and its unit test file has 2,667 lines with 84 tests across nine classes and four different factory helpers (`_make_use_case`, `_resolving_use_case`, `_cached_use_case`, …). It is the most-changed module of the application layer (78 of the repository's 799 commits touch it, 9 of the last 100; the next application module, `torznab_search.py`, has 15), the parallel sessions had to coordinate around it in the review rounds, and the docstring's six-step flow is spread over methods that interleave: `_answer` alone spans 117 lines and drives title resolution, plugin selection, the shared search, ranking, resolution and formatting. Earlier rounds already moved pieces out (`application/stremio/`: `plugin_search.py` runner and breakers, `resolution.py` per-hoster resolution, `search_cache.py`, `search_progress.py`, `queries.py`, `stream_builder.py` formatting), so the remaining module is the orchestration plus three phases that still live inline.

## Target

The use case keeps the request flow and the lifecycle; each phase becomes a small collaborator in `application/stremio/` with its own tests. Nothing changes for callers: `StremioStreamUseCase.__init__` keeps its signature (composition root `interfaces/composition.py:658` untouched), `execute()`, `aclose()`, the log events, metrics stages and the cache keys stay as they are.

| Module (new or extended) | Takes over from the use case | Owns |
|---|---|---|
| `application/stremio/title_resolution.py` — `TitleResolver` | `_resolve_title_info`, `_resolve_title_infos`, `_title_filter`, `_collect_languages`, `_group_by_languages` | TMDB/IMDb title lookups per language, the title-match filter with its seven thresholds (`title_match_threshold`, year bonus/penalties, tolerances), language grouping of plugins |
| `application/stremio/plugin_selection.py` — `PluginSelector` | `_select_plugins` and the scored sort (line 926) | plugin discovery for `provides=stream`, mirror groups, score store ranking, exploration (`max_plugins_scored`, `exploration_probability`, `scoring_enabled`) |
| `application/stremio/title_search.py` — `TitleSearch` | `_search`, `_search_lang_groups`, `_search_progress`, `_shared_search`, `_search_done`, `_refresh` | one shared in-flight search per title (single-flight), the `SearchCache` with stale-while-revalidate, the background refresh semaphore, the `PluginSearchRunner` call |
| `application/stremio/resolution.py` — extend with `ResolveFlow` (or fold into `HosterResolution`) | `_resolve`, `_resolve_in_background`, `_cached_resolutions`, `_resolve_as_results_arrive` | fast path from `cached_resolution_fn`, resolve-with-grace against the soft deadline, resolution of the top streams as results arrive, the background completion |
| `application/stremio/answer.py` — functions, no class | `_rank`, `_convert`, `_cache_and_proxy`, `_save_links` | convert → sort → dedupe/cap → `format_stream` → stream link store → proxy URLs; where `add-stream-media-quality` puts `apply_resolution` |
| `use_cases/stremio_stream.py` (remains, ~250 lines) | — | `execute`, `_answer` (as the readable sequence of the six steps), `_spawn`/`_task_done`/`aclose` task registry, telemetry stages, the protocols `_StremioConfig`, `_StreamSorter`, `_Search` |

Dependencies flow use case → collaborators → ports; no collaborator imports the use case. Collaborators get what they need through their constructors (the use case constructs them in `__init__` from the same arguments it receives today, so `composition.py` does not change). `_StremioConfig` is split per collaborator only if it keeps the attribute names (the `StremioConfig` pydantic model satisfies all of them).

## Rules

- **Exclusive access**: while this refactor is in flight, no other change touches `use_cases/stremio_stream.py` or `tests/unit/application/test_stremio_stream.py` (handoff rule in `docs/plans/ideas-backlog.md`). Land `add-stream-media-quality` first or put its merge step into `answer.py` as part of step 5.
- **No behaviour change**: every commit keeps all 84 use-case tests and the three e2e suites (`tests/e2e/test_stremio_endpoint.py`, `test_stremio_series_e2e.py`, `test_stremio_streamable_e2e.py`) green without edits to assertions. Tests move, they do not change meaning: the class that tested a phase moves to the collaborator's test file with its factory (e.g. `TestTitleMatchFiltering` → `tests/unit/application/test_title_resolution.py`), the use-case file keeps `TestExecute`, `TestResolvePhase`-level flow tests and the cached-answer tests.
- **Log events and metrics**: names and fields stay (`stremio_search_complete`, `stremio_all_filtered`, `stremio_search_no_results`, `stage()` labels); `docs/features/observability.md` needs no change — if a field would move to another event, stop and record it in this plan first.
- **One commit per extraction**, `refactor(stremio): extract <phase> from the stream use case`, each with `pre-commit` and `pytest -n auto` green; push after each so the parallel sessions see the shrinking file early.

## Steps

1. `TitleResolver` (smallest coupling: TMDB port, filter function, thresholds). Tests: move `TestTitleMatchFiltering` and the title-info tests; add none.
2. `PluginSelector` (registry, score store, mirror groups). Tests: move the plugin-selection tests from `TestExecute`/`TestMultiLanguageDispatch`.
3. `TitleSearch` (search runner, cache, progress, refresh; the largest step). Tests: the `_cached_use_case` tests and `TestMultiLanguageDispatch`'s search parts.
4. `ResolveFlow` in `resolution.py`. Tests: `TestResolvePhase` and `TestResolverEchoFiltering` with `_resolving_use_case`.
5. `answer.py` functions (and `apply_resolution` if `add-stream-media-quality` is not already in). Tests: `TestStreamLinkProxy`, `TestStreamLinkSaveFailures`, format assertions.
6. Final pass: `_answer` reads as the six steps of the class docstring; constructor binds the collaborators instead of 31 attributes; update `docs/architecture/clean-architecture.md` (module list under `application/stremio/`) and `docs/features/stremio-addon.md` (pipeline section names the modules), `CHANGELOG.md` one line.

Expected sizes after step 6: use case ~250 lines; new modules 120–250 lines each; the use-case test file under 1,000 lines.

## Risks

- The shared-search single-flight and the background refresh hold task references and semaphores that `aclose()` drains; moving them into `TitleSearch` must keep `aclose()` ordering (search tasks before resolution tasks before the cache). Keep `_spawn`'s task registry in the use case and pass it into collaborators that spawn.
- `_answer` reads `progress` while searches still run; the extraction must not introduce an extra `await` between reading `progress.results` and the cap/dedupe (a race the tests would not catch). Keep the sequence synchronous where it is today.
- Test factories drift: four helpers build the use case with different mocks. Give each collaborator one small factory in its test file and leave the use-case factories for flow tests only.

## Acceptance

- All tests green before and after each step; `poetry run pytest -n auto` and `pre-commit` clean.
- `grep -c "def " src/scavengarr/application/use_cases/stremio_stream.py` ≤ 10; no collaborator imports `use_cases.stremio_stream`.
- Composition root diff is empty; the live check `poetry run pytest -m live -k stremio` (if present) and one manual Stremio request on the dev server behave as before.
