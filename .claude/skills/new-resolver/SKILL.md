---
name: new-resolver
description: Add or fix a Scavengarr hoster resolver (XFS config, generic DDL config, or individual streaming/DDL resolver with respx tests and composition wiring). Use when asked to support a new file/stream hoster or repair a broken one.
argument-hint: "<hoster name or URL>"
---

Hoster: $ARGUMENTS

Read `docs/how-to/new-resolver.md` and follow it. The non-negotiables (AGENTS.md section 7):

1. **Reuse first**: an XFS-based hoster is only a new `XFSConfig` in `xfs.py`, a plain DDL page only a new `GenericDDLConfig` in `generic_ddl.py`, appended to `ALL_XFS_CONFIGS` / `ALL_DDL_CONFIGS`. Raise the count in its test and in `AGENTS.md` (section 7); the per-config tests and the wiring are automatic, and the README's resolver count follows the generated list (step 5). An alias domain of an existing hoster only needs `extra_domains` / `supported_domains`.
2. **Reference**: grep `.devdata/JDownloader2/plugins/` for the domain; the JDownloader plugin gives the file-ID regex, offline markers and API endpoints. Precondition: the folder exists (`.devcontainer/sync-jdownloader.sh` fills it on container start). Without it, say so and name what the resolver then rests on.
3. **Individual resolver** only if step 1 does not fit: `src/scavengarr/infrastructure/hoster_resolvers/<name>.py` with a docstring that starts `<Hoster> hoster resolver — <what it does>.`, tests first in `tests/unit/infrastructure/test_<name>_resolver.py` with `respx` (one test per offline marker), registered in `build_hoster_resolvers()` of `src/scavengarr/interfaces/composition.py` before the generic resolvers.
4. **What the CDN needs**: declare it on the resolver (`docs/current/hoster-resolvers.md`): `ClientBoundResolverPort` when the video URL is bound to the player's headers, `needs_playback_check = True` without a check of its own, `address_bound = True` or `idle_reset = True` when the CDN plays only for the resolving address or resets idle connections, `challenge_requests` of `capture_stream()` when the hoster runs its own captcha. Probe the binding with `scripts/probes/header_binding.py`. A shorter `autoplay_wait` than the 3 s default stays with the maintainer's timing on the production host.
5. Regenerate `docs/reference/resolvers.md`: `PYTHONPATH=src poetry run python scripts/generate_resolver_list.py` (no poetry: `PYTHONPATH=src .venv/bin/python scripts/generate_resolver_list.py`).
6. Finish with the `commit` skill.
