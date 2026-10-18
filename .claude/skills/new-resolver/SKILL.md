---
name: new-resolver
description: Add or fix a Scavengarr hoster resolver (XFS config, generic DDL config, or individual streaming/DDL resolver with respx tests and composition wiring). Use when asked to support a new file/stream hoster or repair a broken one.
argument-hint: "<hoster name or URL>"
---

Hoster: $ARGUMENTS

Read `docs/how-to/new-resolver.md` and follow it. The non-negotiables:

1. **Reuse first**: an XFS-based hoster is only a new `XFSConfig` in `xfs.py`, a plain DDL page only a new `GenericDDLConfig` in `generic_ddl.py`, appended to `ALL_XFS_CONFIGS` / `ALL_DDL_CONFIGS` (raise the count in its test; the per-config tests and the wiring are automatic). An alias domain of an existing hoster only needs `extra_domains` / `supported_domains`.
2. **Reference**: grep `.devdata/JDownloader2/plugins/` for the domain; the JDownloader plugin gives the file-ID regex, offline markers and API endpoints.
3. **Individual resolver** only if step 1 does not fit: `src/scavengarr/infrastructure/hoster_resolvers/<name>.py` with a docstring that starts `<Hoster> hoster resolver — <what it does>.`, tests first in `tests/unit/infrastructure/test_<name>_resolver.py` with `respx` (one test per offline marker), registered in `build_hoster_resolvers()` of `src/scavengarr/interfaces/composition.py` before the generic resolvers.
4. Regenerate `docs/reference/resolvers.md`: `PYTHONPATH=src poetry run python scripts/generate_resolver_list.py`.
5. Finish with the `commit` skill.
