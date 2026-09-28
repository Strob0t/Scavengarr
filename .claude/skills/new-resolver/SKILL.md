---
name: new-resolver
description: Add or fix a Scavengarr hoster resolver (XFS config, generic DDL config, or individual streaming/DDL resolver with respx tests and composition wiring). Use when asked to support a new file/stream hoster or repair a broken one.
argument-hint: "<hoster name or URL>"
---

Hoster: $ARGUMENTS

Read `docs/features/hoster-resolvers.md` → "Adding a New Resolver" and "Testing", then:

1. **Reuse first**: an XFS-based hoster is only a new `XFSConfig` in `xfs.py`, a plain DDL page only a new `GenericDDLConfig` in `generic_ddl.py`, appended to `ALL_XFS_CONFIGS` / `ALL_DDL_CONFIGS` (tests and wiring are automatic). An alias domain of an existing hoster only needs `extra_domains` / `supported_domains`.
2. **Reference**: grep `.devdata/JDownloader2/plugins/` for the domain; the JDownloader plugin gives the file-ID regex, offline markers and API endpoints.
3. **Individual resolver** only if step 1 does not fit: `src/scavengarr/infrastructure/hoster_resolvers/<name>.py`, tests first in `tests/unit/infrastructure/test_<name>_resolver.py` with `respx` (one test per offline marker), wire into `resolvers=[...]` in `src/scavengarr/interfaces/composition.py`.
4. Update the resolver lists in `docs/features/hoster-resolvers.md` and `CHANGELOG.md`, then use the `commit` skill.
