## ADDED Requirements

### Requirement: Published Multi-Arch Image
CI SHALL build the production image for `linux/amd64` and `linux/arm64` from `Dockerfile.prod` and publish one multi-arch manifest to GHCR on every push to `staging` (tag `staging`), on every release tag `vX.Y.Z` (tags `vX.Y.Z` and `latest`) and on demand, each build also tagged `sha-<short sha>`.

#### Scenario: Push to staging
- **WHEN** a commit is pushed to `staging`
- **THEN** `ghcr.io/<owner>/scavengarr:staging` points at an image built from that commit for both architectures
- **AND** the image's `SCAVENGARR_COMMIT` environment variable and `org.opencontainers.image.revision` label name the commit

#### Scenario: Release tag
- **WHEN** the tag `v0.3.0` is pushed
- **THEN** `ghcr.io/<owner>/scavengarr:v0.3.0` and `:latest` point at the same manifest

### Requirement: Self-Contained Image
The image SHALL contain the plugins and a default configuration and SHALL start without any volume; a bind mount over `/app/plugins` overrides the bundled plugins, and the entrypoint SHALL seed `/app/config/config.yaml` only when the config directory holds none.

#### Scenario: First start with an empty config volume
- **WHEN** the container starts with an empty volume at `/app/config`
- **THEN** the entrypoint copies the default `config.yaml` there and the app loads it

#### Scenario: Restart with an edited config
- **WHEN** the volume already holds a `config.yaml`
- **THEN** the entrypoint leaves it unchanged

#### Scenario: Plugins override
- **WHEN** a directory is mounted at `/app/plugins`
- **THEN** the app loads the plugins from the mount, not the bundled ones

### Requirement: Documented Deployment by Image
The repository's compose file and documentation SHALL describe the image-based deployment (image, tags, volumes, watchtower) as the default and the source build as the alternative, and the release procedure SHALL include the release tag that produces the versioned image.

#### Scenario: Release procedure
- **WHEN** a release is merged to `main`
- **THEN** AGENTS.md §1 names the tag step (`git tag vX.Y.Z`, `git push origin vX.Y.Z`) after the merge
