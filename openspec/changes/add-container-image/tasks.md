## 1. Image contents

- [ ] 1.1 `Dockerfile.prod`: `ARG`/`ENV` `SCAVENGARR_COMMIT`, `SCAVENGARR_BUILT` (item I4), `COPY plugins/ /app/plugins/`, the default config at `/app/config.default.yaml`, OCI labels from build arguments; the image still starts without any volume.
- [ ] 1.2 `docker/entrypoint.sh`: seed `/app/config/config.yaml` from `/app/config.default.yaml` when missing (log one line); keep the executable bit (`tests/unit/infrastructure/test_repository_files.py`).
- [ ] 1.3 Local check: `docker build -f Dockerfile.prod --build-arg SCAVENGARR_COMMIT=$(git rev-parse --short HEAD) .` is not possible in the dev container (no Docker, AGENTS.md §9) — verify the Dockerfile by the first workflow run instead, and `bash -n docker/entrypoint.sh`.

## 2. Workflow

- [ ] 2.1 `.github/workflows/image.yml`: triggers (`push` to `staging`, `push` tags `v*`, `workflow_dispatch`), `permissions: contents: read, packages: write`, jobs `build-amd64` (`ubuntu-latest`) and `build-arm64` (`ubuntu-24.04-arm`) with `docker/login-action` (GHCR, `GITHUB_TOKEN`), `docker/metadata-action` (tags: branch `staging`, semver from `v*`, `latest` on tags, `sha-`), `docker/build-push-action` (push by digest, `cache-from/to: type=gha`, build args commit and build time), then a `merge` job that creates the multi-arch manifest with `docker buildx imagetools create` and the metadata tags.
- [ ] 2.2 First run on `staging`: record build times per architecture and the image size in the CHANGELOG entry; if `ubuntu-24.04-arm` is unavailable, switch arm64 to QEMU in one job and note it.
- [ ] 2.3 Pull check from the dev container is impossible (no Docker); the maintainer pulls `ghcr.io/<owner>/scavengarr:staging` on the Pi and `prodctl.py metrics | grep build_info` shows the commit (acceptance).

## 3. Compose and docs

- [ ] 3.1 `docker-compose.yml`: `image: ghcr.io/<owner>/scavengarr:latest` as default, `build:` commented as the alternative, the plugins mount commented as an override, the config and cache volumes as before.
- [ ] 3.2 README (install: pull, tags, watchtower note), `docs/features/configuration.md` (deployment section: image, volumes, seeding, `SCAVENGARR_*` variables unchanged), AGENTS.md §1 (release tag after the merge), `docs/plans/ideas-backlog.md` (I7 row), `CHANGELOG.md`.
- [ ] 3.3 Production example for the maintainer in `docs/features/configuration.md` ("Behind a VPN container"): the service block with `image`, `network_mode: service:<vpn>`, the two volumes, and the watchtower label; nothing from their compose copied.
