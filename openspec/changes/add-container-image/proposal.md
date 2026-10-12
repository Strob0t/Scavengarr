# Change: Add a Published Multi-Arch Container Image

## Why

Production (a Raspberry Pi 4 behind a VPN container) builds its image from GitHub with a Dockerfile that is not in the repository: it fetches `staging` with `ADD <git>#ref`, ships the plugins inside the image while `docker-compose.yml` mounts them, seeds `config.yaml` once, and takes over ten minutes per build. watchtower, which already updates the other containers at 04:00, cannot update a locally built image. Every production round of the last week began with "please rebuild", the deployed commit was invisible (`scavengarr_build_info` names the package version only), and a cached `RUN git clone` layer once kept production on an old commit for weeks. A CI-built image on GHCR for `linux/amd64` and `linux/arm64` makes deploys a `docker compose pull`, lets watchtower do them, and carries its commit.

## What Changes

- **Image workflow** `.github/workflows/image.yml`: on pushes to `staging`, on tags `v*` and on demand, build `Dockerfile.prod` natively per architecture (amd64 on `ubuntu-latest`, arm64 on `ubuntu-24.04-arm`, both free for public repositories) and push one multi-arch manifest to `ghcr.io/<owner>/scavengarr` with the tags `staging` (moving, for the `staging` branch), `vX.Y.Z` and `latest` (tags), and `sha-<short sha>` (every build). Build arguments `SCAVENGARR_COMMIT` and `SCAVENGARR_BUILT` (introduced by item I4 of `docs/plans/ideas-backlog.md`, which this change presupposes), OCI labels (`org.opencontainers.image.revision`, `.version`, `.source`), GitHub Actions layer cache.
- **`Dockerfile.prod` bundles the plugins** (`COPY plugins/ /app/plugins/`): a bind mount over `/app/plugins` still wins, so the compose file's mount becomes an optional override, and the docs stop saying the image has no plugins.
- **Config seeding in the entrypoint**: the image keeps the default `config.yaml` beside the config directory and `docker/entrypoint.sh` copies it into `/app/config/` only when no file is there (today the image copies it straight into the directory a volume mount shadows).
- **Compose and docs**: `docker-compose.yml` gets the `image: ghcr.io/<owner>/scavengarr:latest` form as the default with the `build:` block as the commented alternative; README's install section and `docs/features/configuration.md` describe pull, tags, watchtower, and the production example (image, config volume, cache volume, optional plugins override); AGENTS.md §1 "Merge to main" gains the release tag (`git tag vX.Y.Z` on the merge commit, `git push origin vX.Y.Z`) that produces `vX.Y.Z` and `latest`.
- The maintainer's Pi Dockerfile retires; their compose switches to `image: ghcr.io/<owner>/scavengarr:staging` (or `latest` for releases only) — the maintainer's step, outside the repository.

## Impact

- Affected specs: new capability `delivery`.
- Affected code: `.github/workflows/image.yml` (new), `Dockerfile.prod`, `docker/entrypoint.sh`, `docker-compose.yml`, `README.md`, `docs/features/configuration.md`, `AGENTS.md` §1, `CHANGELOG.md`; `tests/unit/infrastructure/test_repository_files.py` if it checks the Dockerfile or the entrypoint.
- Permissions: the workflow needs `packages: write` (and `contents: read`); a GHCR package starts private even for a public repository (GitHub docs, "Configuring a package's access control and visibility"); the maintainer makes it public once in the package settings (irreversible), and then it has the same exposure as the repository (`docs/plans/next-steps.md` §6). A private package would need a registry login on every host and counts against the account's package storage and transfer quota.
- Risk: `ubuntu-24.04-arm` runner availability or a slow first arm64 build (Chromium download, wheel builds) → the first run measures it; the fallback is QEMU (`docker/setup-qemu-action`) for arm64 in the same job, slower but working.
- Cost: none in the app; two CI jobs per push to `staging` (minutes, free for public repositories).
