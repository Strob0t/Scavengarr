# Add a hoster resolver

> **Purpose:** Adding or repairing a hoster resolver, from the unresolved hosters to the regenerated resolver list.
> **Status:** how-to
> **As of:** afe8dbb1

## Goal

Support a new streaming or direct-download hoster, or repair a broken one, so that the links the plugins find for it resolve.
How the registry dispatches and judges the links is in [Hoster resolvers](../current/hoster-resolvers.md), and every registered resolver is in [the resolver list](../reference/resolvers.md).

## Prerequisites

- The dev container with `poetry install` done, and in a worktree `scripts/worktree_venv.sh` run once.
- The JDownloader sources in `.devdata/JDownloader2/plugins/` and `.devdata/JDownloader2/controlling/`, which `.devcontainer/sync-jdownloader.sh` checks out or updates on every container start.
- A few live links of the hoster, collected as in [Verify](#verify).

## Steps

### 1. Pick the hoster

The hosters whose links the plugins deliver without a resolver are counted, the most frequent first:

```bash
poetry run python scripts/prodctl.py state --keys unresolved_hosters
poetry run python scripts/prodctl.py logs --since 4h --grep hoster_without_resolver --fields hoster,plugin
```

The first reads `unresolved_hosters` of production's `GET /api/v1/stats/metrics`, and the second names the plugins that deliver the links, as far back as the log's last 5000 lines reach ([Operate production](operations.md#the-logs-window)).

### 2. Reuse a generic resolver first

- An XFileSharingPro hoster is an `XFSConfig` in `src/scavengarr/infrastructure/hoster_resolvers/xfs.py`, appended to `ALL_XFS_CONFIGS`: `name`, `domains`, `file_id_re`, `offline_markers`, `is_video_hoster`, and `needs_captcha` or `extra_domains` where they apply.
- A plain direct-download page is a `GenericDDLConfig` in `src/scavengarr/infrastructure/hoster_resolvers/generic_ddl.py`, appended to `ALL_DDL_CONFIGS`.
- Either way the per-config tests pick it up: raise the count assertion in `tests/unit/infrastructure/test_xfs_resolver.py` or `tests/unit/infrastructure/test_generic_ddl_resolver.py`, and the composition registers it through `create_all_xfs_resolvers()` or `create_all_ddl_resolvers()`.
- An alias domain of a hoster Scavengarr already resolves needs only an entry in `extra_domains` of its config or in the `supported_domains` of its resolver.

Then continue with [step 7](#7-regenerate-the-resolver-list).

### 3. Read the JDownloader plugin

Find the hoster's plugin by its domain under `.devdata/JDownloader2/plugins/`.
It gives the file id pattern, the offline markers, the API endpoints and the domains, the dead ones among them.
`.devdata/JDownloader2/CHANGES.md` lists the files the last sync with changes pulled in.

### 4. Write the tests first

Create `tests/unit/infrastructure/test_<name>_resolver.py` and mock the hoster with `respx`, the HTTP mock for httpx:

```python
@respx.mock
async def test_resolves_valid_file(self) -> None:
    respx.get(url).respond(200, text=html)
    async with httpx.AsyncClient() as client:
        result = await Resolver(http_client=client).resolve(url)
    assert result is not None
```

- `TestExtractFileId`: the valid domains, a `www` prefix, the `http` scheme, invalid and short ids, domains that do not match.
- `TestResolver`: `test_name`, a valid file, one test per offline marker, HTTP errors, network errors, invalid URLs and error redirects.
- A resolver that takes the stream from the browser gets a stand-in stealth pool, as in `tests/unit/infrastructure/test_vixeo_resolver.py`.

### 5. Write the resolver

Create `src/scavengarr/infrastructure/hoster_resolvers/<name>.py`:

- The module's docstring starts with `<Hoster> hoster resolver — <what it does>.`, since the resolver list quotes that sentence.
- At module level: the domains as a `frozenset`, the file id pattern, and helpers such as `_extract_file_id()`.
- A class with a `name` property and `async def resolve(self, url: str) -> ResolvedStream | None`, whose constructor takes `http_client: httpx.AsyncClient`, logging through `structlog.get_logger(__name__)`.
- The flow: the file id, the canonical URL, the page or the API, the offline markers, then a `ResolvedStream` or `None`.
- A video-extracting resolver returns the direct video URL with its playback headers and checks it with `verify_video_url()` from `_verify.py`.
- A direct-download resolver returns the canonical file URL with `StreamQuality.UNKNOWN`.
- `extract_domain(url)` gives the second-level domain to match (`voe` for `https://www.voe.sx/e/abc`).
- The registry dispatches by `name`, so other second-level domains of the hoster go into a `supported_domains` property, and a full host name whose second-level name other hosts share into `supported_hosts`.

Mark what the hoster's CDN needs:

- `address_bound = True` when the CDN plays the URL only for the address that resolved it, so the file goes through the file proxy.
- `needs_playback_check = True` when the resolver has no check of its own, so the registry checks its streams with `stremio.verify_streams` off too.
- `ClientBoundResolverPort` (`bound_headers`, `resolve_for_client()`) when the CDN binds the URL to headers the player sends itself.

When the stream URL exists only in the running player (built by JavaScript, behind a Cloudflare challenge or a captcha play button), take `stealth_pool: StealthPool | None = None` and return `await capture_stream(self._stealth_pool, embed_url, self.name)` from `_browser.py`.
Prefer an httpx path where one exists, since a capture costs seconds and a browser page.
A hoster whose player runs a captcha of its own names its requests (`challenge_requests=re.compile(...)`, as Filemoon's `_CAPTCHA_REQUESTS`), so a capture cut after one ends as `challenge` and not as a breaker failure.
A player that does not start itself and takes its first click at once can name a shorter wait before that click (`autoplay_wait=0.5`, as Vixeo's), once `scripts/probes/capture_timing.py --autoplay-wait` has shown on the production host that it takes the click then.

### 6. Register the resolver

Add it to `build_hoster_resolvers()` in `src/scavengarr/interfaces/composition.py`, before the generic resolvers, since a domain two resolvers claim stays with the first one: `<Name>Resolver(http_client=http_client)`, and `stealth_pool=stealth_pool` for a resolver that captures.
`tests/unit/interfaces/test_resolver_wiring.py` checks the order, the names and the stealth pool.

### 7. Regenerate the resolver list

```bash
PYTHONPATH=src poetry run python scripts/generate_resolver_list.py
```

`tests/unit/infrastructure/test_resolver_list_doc.py` fails while `docs/reference/resolvers.md` is outdated.

## Verify

- `poetry run pytest tests/unit/infrastructure/test_<name>_resolver.py` passes, and so do `poetry run pre-commit run --all-files` and `poetry run pytest -n auto`.
- The live contract test resolves a real link of the hoster and gets `None` for an altered one:

```bash
PYTHONPATH=src xvfb-run -a poetry run python scripts/probes/resolver_urls.py
xvfb-run -a poetry run pytest -m live tests/live/test_resolver_live.py -k <name>
```

- The first command writes one hoster page URL per resolver into `.cache/live/resolver-urls.json` from the links the plugins find, and its `--avoid <earlier output>` takes each resolver's next link.
- With the dev server running (`PYTHONPATH=src poetry run start` in a worktree), a stream request whose results carry the hoster logs `hoster_resolve_success` with its name.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The hoster's links still log `hoster_without_resolver`. | The link's second-level domain is neither the resolver's name nor one of its aliases. | Add the domain to `supported_domains`, or claim the full host with `supported_hosts`. |
| The start logs `hoster_domain_conflict`. | Two resolvers claim the domain, and the first one registered keeps it. | Remove the domain from one of them, or claim the host on the specific one. |
| The resolver returns a URL, and the stream does not show. | The playback check refused it (`hoster_resolve_unplayable`): a missing header, or an error page with 200. | Set the CDN's headers, and check the URL with `verify_video_url()`. |
| A stream plays from the dev container and not for a player. | The CDN binds the URL to the resolving address or to the player's headers. | Run `scripts/probes/header_binding.py`, then set `address_bound` or implement `ClientBoundResolverPort`. |
| Every link of the hoster is skipped with `hoster_resolve_circuit_open`. | Five timeouts or unplayable streams without a stream between them opened its breaker. | Fix their cause; the half-open probe closes the breaker with its first stream. |
| A capture times out on the production host. | The page loads slowly, or a challenge stands before the stream. | Time it with `scripts/probes/capture_timing.py`, and name the hoster's captcha requests. |
| `-k <name>` selects no live test. | `.cache/live/resolver-urls.json` holds no link of the hoster. | Run `scripts/probes/resolver_urls.py` with `--plugins` of a plugin that finds the hoster. |
