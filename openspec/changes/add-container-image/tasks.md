## 1. Image contents

- [x] 1.1 `Dockerfile.prod`: `ARG`/`ENV` `SCAVENGARR_COMMIT`, `SCAVENGARR_BUILT` (item I4), `COPY plugins/ /app/plugins/`, the default config at `/app/config.default.yaml`, OCI labels from build arguments; the image still starts without any volume.
- [x] 1.2 `docker/entrypoint.sh`: seed `/app/config/config.yaml` from `/app/config.default.yaml` when missing (log one line); keep the executable bit (`tests/unit/infrastructure/test_repository_files.py`). (Done: the default sits beside the entrypoint, `/app/config.default.yaml`, and the target is `SCAVENGARR_CONFIG`; an unwritable directory logs why and the app starts on its defaults; tests in `tests/unit/infrastructure/test_entrypoint.py`. Also done: `COPY --chown` instead of the final `chown -R`, which duplicated the venv and Chromium in a layer.)
- [x] 1.3 Local check: `docker build -f Dockerfile.prod --build-arg SCAVENGARR_COMMIT=$(git rev-parse --short HEAD) .` is not possible in the dev container (no Docker, AGENTS.md §9) — verify the Dockerfile by the first workflow run instead, and `bash -n docker/entrypoint.sh`. (Done: `bash -n` and `sh -n` pass; the first run built both architectures, 2026-10-06.)

## 2. Workflow

- [x] 2.1 `.github/workflows/image.yml`: triggers (`push` to `staging`, `push` tags `v*`, `workflow_dispatch`), `permissions: contents: read, packages: write`, jobs `build-amd64` (`ubuntu-latest`) and `build-arm64` (`ubuntu-24.04-arm`) with `docker/login-action` (GHCR, `GITHUB_TOKEN`), `docker/metadata-action` (tags: branch `staging`, semver from `v*`, `latest` on tags, `sha-`), `docker/build-push-action` (push by digest, `cache-from/to: type=gha`, build args commit and build time), then a `merge` job that creates the multi-arch manifest with `docker buildx imagetools create` and the metadata tags.
- [x] 2.2 First run on `staging`: record build times per architecture and the image size in the CHANGELOG entry; if `ubuntu-24.04-arm` is unavailable, switch arm64 to QEMU in one job and note it. (Done, 2026-10-06: `ubuntu-24.04-arm` ran; without a cache amd64 4.6 min and arm64 4.3 min in parallel, with it 26 s each; 481 MiB amd64, 484 MiB arm64 compressed.)
- [ ] 2.3 Pull check from the dev container is impossible (no Docker); the maintainer pulls `ghcr.io/<owner>/scavengarr:staging` on the Pi and `prodctl.py metrics | grep build_info` shows the commit (acceptance).

## 3. Compose and docs

- [x] 3.1 `docker-compose.yml`: `image: ghcr.io/<owner>/scavengarr:latest` as default, `build:` commented as the alternative, the plugins mount commented as an override, the config and cache volumes as before.
- [x] 3.2 README (install: pull, tags, watchtower note), `docs/features/configuration.md` (deployment section: image, volumes, seeding, `SCAVENGARR_*` variables unchanged), AGENTS.md §1 (release tag after the merge), `docs/plans/ideas-backlog.md` (I7 row), `CHANGELOG.md`.
- [x] 3.3 Production example for the maintainer in `docs/features/configuration.md` ("Behind a VPN container"): the service block with `image`, `network_mode: service:<vpn>`, the two volumes, and the watchtower label; nothing from their compose copied.
