# Tech stack

> **Purpose:** Every dependency with its purpose and the reason for it, the tools beside the Python packages and the alternatives not used.
> **Status:** reference
> **As of:** cf8d62a9

Every dependency in `pyproject.toml` with what it does here and why it is the one, the tools beside the Python packages, and the alternatives deliberately not used.
The versions live in `pyproject.toml` and `poetry.lock` only.

## Rule

A commit that adds, removes or replaces a dependency updates this file in the same commit: the package's line under [Runtime](#runtime) or [Development](#development) with its purpose and its reason, and for a replaced package a line under [Not used](#not-used).
`tests/unit/infrastructure/test_tech_stack_doc.py` fails when a dependency has no line here, when a line names no dependency, and when a package listed as not used is a dependency.
The order of preference is that of [AGENTS.md](../../AGENTS.md#4-dependencies): the standard library first, then an established library, then code of our own.
Where no comparison was recorded, the reason given is what the code relies on.

## Runtime

### Language and server

| Package | Purpose here | Why this one |
|---|---|---|
| `python` | The interpreter: `pyproject.toml` sets the range, and the image runs the version of its base image (`Dockerfile.prod`). | The image runs the newest version of the range for its faster interpreter and asyncio, and CI tests that version and the lowest (`.github/workflows/ci.yml`). |
| `fastapi` | The HTTP framework of the Torznab, Stremio, download and API routes and of the app's lifespan (`interfaces/`). | Async routes with typed parameters and a lifespan for the wiring, on Starlette. |
| `uvicorn` | The ASGI server the CLI starts (`interfaces/cli/__main__.py`). | It takes uvloop and httptools when they are installed, and it bounds the graceful shutdown, so an open playback cannot hold a stop. |
| `uvloop` | The event loop in C, which uvicorn takes when it is installed (`loop="auto"`). | The asyncio loop took 13 to 16 % of a stream request's Python CPU on the production host (py-spy). |
| `httptools` | The server's HTTP parser in C, which uvicorn takes when it is installed (`http="auto"`). | Of uvicorn's `standard` extra only uvloop and httptools are installed, since the app uses neither websockets nor watchfiles. |

### Requests and pages

| Package | Purpose here | Why this one |
|---|---|---|
| `httpx` | The async HTTP client of the plugins, the resolvers, the link validation and the metadata clients: one shared client (`build_http_client()` in `interfaces/composition.py`). | Its transport API carries the retry and rate-limit transport, the private-address guard and the asyncio network backend (`infrastructure/common/`), and respx mocks it in the tests. |
| `selectolax` | The HTML parser of every plugin: lexbor with CSS selectors (`infrastructure/plugins/dom.py`). | It parses in C and releases the GIL, where `html.parser` held the GIL for every page. |
| `patchright` | The browser: one shared Chromium for the Playwright plugins, the stealth pool and the resolvers' captures (`infrastructure/browser/`). | A Playwright fork without the leaks Cloudflare detects (`Runtime.enable`, `Console.enable`, the automation flags), with Playwright's API. |
| `cryptography` | AES-128-CBC decryption of Click'n'Load containers (`infrastructure/plugins/clicknload.py`) and of UPN Share's API answers (`infrastructure/hoster_resolvers/upns.py`). | The standard library has no AES. |

- The `http2` extra of httpx installs h2 for `http.http2`, which stays off: with HTTP/2 the plugin searches used 22 % more CPU on the production host, since h2's framing runs in Python, and were not faster.
- Before selectolax, `html.parser` took 21 to 27 % of a stream request's Python CPU on the production host and 34 % of its GIL samples.
- Headful under Xvfb, Patchright passed Cloudflare's interactive Turnstile on every tested site after one click, while Playwright with playwright-stealth stayed blocked after three clicks.
- Headless, every engine tried (Playwright, Patchright, Camoufox, nodriver) was rejected at the Turnstile step, so the image runs the browser headful on a virtual display.

### Titles and release names

| Package | Purpose here | Why this one |
|---|---|---|
| `guessit` | Reads release names (title, year, season, episode, quality, language) for the title match, the release parser and the episode filter (`infrastructure/stremio/release_guess.py`). | Its rules (rebulk) know the scene's naming conventions, so the code keeps no grammar of its own for them. |
| `rapidfuzz` | The fuzzy match of titles and episode titles (`infrastructure/stremio/title_matcher.py`, `infrastructure/plugins/episode_index.py`). | Token-sort and token-set ratios in C++, faster and more robust than the `difflib.SequenceMatcher` it replaced. |
| `unidecode` | Titles and search queries in ASCII for matching and searching (`application/stremio/queries.py`, `infrastructure/plugins/relevance.py`). | It transliterates many scripts, where the umlaut and transliteration tables it replaced knew a few characters. |

guessit took 25 to 33 % of a stream request's Python CPU on the production host, so `release_guess.py` parses each name once and keeps the result.

### Configuration, cache and logs

| Package | Purpose here | Why this one |
|---|---|---|
| `pydantic-settings` | The typed settings and their layers: CLI, `SCAVENGARR_*` environment, YAML and defaults (`infrastructure/config/`). | One validated model reads every source, and `SecretStr` shows a secret as `***` in the startup line `config_effective`. |
| `python-dotenv` | Loads a `--dotenv` file into the environment before the settings are read (`infrastructure/config/load.py`). | The file's values must reach `os.environ`, where the CLI reads `HOST` and `PORT`, and a variable already set keeps its value (`override=False`). |
| `pyyaml` | Reads the config file, and in the tests the repository's YAML files (Compose, Dependabot, the workflows, the alert rules). | YAML is the config file's format, and `yaml.safe_load` builds plain data only. |
| `diskcache` | The default cache backend: one SQLite database in `cache.dir` (`infrastructure/cache/diskcache_adapter.py`). | Keys with an expiry and no service to run; the adapter runs its disk I/O in worker threads. |
| `redis` | The optional cache backend through `redis.asyncio` (`infrastructure/cache/redis_adapter.py`, `cache.backend: redis`). | For a deployment that runs Redis, such as the Compose profile `redis`, the cache lives in a service of its own instead of the container's files. |
| `structlog` | The structured logs: key-value records as JSON or for the console (`infrastructure/logging/setup.py`). | Context variables carry `request_id` into every line of a request and its tasks, and the libraries' standard log records pass the same processors. |

Pydantic comes with pydantic-settings, and only the config package imports it.

### Metrics and traces

| Package | Purpose here | Why this one |
|---|---|---|
| `prometheus-client` | `/metrics` in the Prometheus text format, from a registry of the app's own (`infrastructure/telemetry/metrics.py`). | The reference library, pure Python, and Prometheus pulls from it. |
| `opentelemetry-sdk` | The core's stages as trace spans, only with `telemetry.tracing_endpoint` set (`infrastructure/telemetry/tracing.py`). | Tracing on demand needs no rebuild, and without an endpoint the SDK is not loaded. |
| `opentelemetry-exporter-otlp-proto-http` | Sends the spans over OTLP/HTTP to the endpoint, such as Tempo of the Compose profile `tracing`. | It is loaded with the SDK only, over the urllib3 transport. |

Installed, the OpenTelemetry packages add about 5 MB to the image and about 2 MB of memory with tracing off ([Cost](../current/observability.md#cost)).

## Development

| Package | Purpose here | Why this one |
|---|---|---|
| `pytest` | The test runner with the markers `integration`, `live` and `benchmark`; `addopts` leaves out the live tests and the benchmarks. | Fixtures and parametrization, which the suite is built on. |
| `pytest-asyncio` | Runs every `async def` test on an event loop (`asyncio_mode = "auto"`). | An async test needs no marker of its own. |
| `pytest-xdist` | Spreads the suite over worker processes (`pytest -n auto`, at most 8), handing them out in chunks of at most eight tests (`--maxschedchunk=8`). | The suite takes about a third of its serial time; the chunk size ends the workers together, where the default `load` mode left one running alone for 6 to 10 s (16.3 s instead of 24.4 s); the comment above `addopts` in `pyproject.toml` has the times. |
| `pytest-cov` | Coverage reports on demand (`pytest --cov=scavengarr`); no check runs it. | The pytest plugin of coverage.py. |
| `respx` | Mocks httpx at its transport, so plugin and resolver tests get recorded answers without a network. | It is made for httpx, and resolver tests must use it ([AGENTS.md](../../AGENTS.md#6-testing-tdd-mandatory)). |
| `ruff` | Lint (`E`, `F`, `W`, `C90`, `I`) and format, in pre-commit and in the edit hook. | One fast tool for what flake8, isort and black do, pinned to the `rev` of `ruff-pre-commit` so that both run the same version. |
| `basedpyright` | Type checks in `standard` mode, in pre-commit (`scripts/basedpyright.sh`) and in the editor. | The editor and the hook apply the same rules, and it is on Open VSX, where Pylance is not. |
| `pre-commit` | Runs ruff, basedpyright and the file hygiene hooks before every commit and in CI (`.pre-commit-config.yaml`). | One file defines the checks for the developer and for CI. |

## Beside the Python packages

| Tool | Purpose here | Where it is set |
|---|---|---|
| Poetry | Installs the dependencies from `pyproject.toml` and `poetry.lock`. | `Dockerfile.prod`, the dev container |
| Chromium | Patchright's own build, the browser of the Playwright plugins and the captures. | `python -m patchright install chromium` in `Dockerfile.prod` and `.devcontainer/setup.sh` |
| Xvfb | The virtual display of the headful browser in the image. | `Dockerfile.prod`, `docker/entrypoint.sh` |
| curl | The image's healthcheck. | `Dockerfile.prod` |
| Redis, Byparr, Tempo | The optional services of the Compose profiles `redis`, `solver` and `tracing`. | `docker-compose.yml` |
| Node | The dev container's tools: playwright-mcp, the OpenSpec CLI and the skills. | `.devcontainer/devcontainer.json`, `.devcontainer/setup.sh` |

## Not used

| Alternative | Instead | Why not |
|---|---|---|
| `playwright` with `playwright-stealth` | Patchright | Headful under Xvfb it stayed blocked at Cloudflare's Turnstile after three clicks, where Patchright passed after one. |
| `camoufox`, `nodriver`, `seleniumbase` | Patchright | Full browsers that are no faster on the production host, and Patchright passed while keeping the Playwright API the code is written against. |
| `selenium` | Patchright | The browser code builds on Playwright's contexts, routes and request events, and a WebDriver stack needs a patched driver against bot detection, as the frozen undetected-chromedriver inside FlareSolverr shows. |
| FlareSolverr or another solver as a required service | The own browser in the process; a FlareSolverr-compatible solver such as Byparr is an optional fallback (`playwright.solver_url`). | The plugins and the resolvers need the page itself for clicks and captures, which a solver's answer of HTML and cookies cannot give. |
| `scrapy` | Plugins on `HttpxPluginBase` or `PlaywrightPluginBase` | It ran the former YAML plugins and went with them, so every plugin is a Python class on a base that shares the app's client and browser. |
| `beautifulsoup4`, `lxml` | selectolax | Declared but imported nowhere, they were removed before the parsers moved from `html.parser` to selectolax. |
| `aiohttp`, `niquests`, `pyreqwest` | httpx | No independent CPU benchmark of them was found, and none on aarch64, so a switch has no measured gain. |
| `curl_cffi` and other clients that imitate Chrome's TLS | httpx with the browser's cookies and User-Agent | From the production host's address, httpx with the session of a browser solve got the challenged pages, and curl_cffi did no better. |
| Captcha-solving services, paid scraping APIs, residential proxies | The own browser and the in-process solvers (ALTCHA, the image captcha) | A third party would see the traffic, and a solver service needs a proxy on the same exit address. |
| `difflib` (standard library) | rapidfuzz | Its sequence ratio is slower and less robust for titles whose words differ in order or number. |
| `typer`, `click` | `argparse` (standard library) | The CLI has a few flags, which the standard library covers. |
| Pydantic models in the domain | `@dataclass` entities and value objects | The domain stays free of frameworks and I/O ([decision 0001](../target/decisions/0001-dependency-rule.md)). |
| `mypy`, Pylance | basedpyright | Pylance is not on Open VSX, and one checker in `standard` mode serves the editor and pre-commit alike. |
| `black`, `isort`, `flake8` | ruff | One tool does the work of the three at one pinned version. |
| OpenTelemetry metrics | prometheus-client | They would need Prometheus' OTLP receiver, which has no authentication, or a collector. |
| The OpenTelemetry instrumentation of FastAPI and httpx | The core's own stages (`TelemetryPort`) | Their spans carry each request's URL, and the stream URLs hold tokens; the core's spans carry neither URLs nor titles. |
