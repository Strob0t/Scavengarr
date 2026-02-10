# Scavengarr Documentation

Scavengarr is a self-hosted, Prowlarr-compatible Torznab/Newznab indexer that scrapes
sources via multi-stage pipelines and delivers results through standard Torznab endpoints.

## Version

Current version: **0.1.0**

## Documentation Structure

| Document | Description |
|----------|-------------|
| [Architecture](architecture.md) | Clean Architecture layers, dependency rules, data flow |
| [API Reference](api.md) | HTTP endpoints, query parameters, response formats |
| [Configuration](configuration.md) | YAML config, environment variables, CLI flags, precedence |
| [Plugin System](plugins.md) | YAML/Python plugin authoring, multi-stage scraping, selectors |
| [CrawlJob System](crawljob.md) | JDownloader integration, `.crawljob` format, download flow |
| [Development Guide](development.md) | Local setup, testing, linting, commit workflow |
| [Deployment](deployment.md) | Docker, Docker Compose, production configuration |

## Quick Links

- **Start the server:** `poetry run start --host 0.0.0.0 --port 7979`
- **Run tests:** `poetry run pytest`
- **Lint & format:** `poetry run pre-commit run --all-files`
- **Torznab search:** `GET /api/v1/torznab/{plugin_name}?t=search&q=iron+man`
- **Torznab caps:** `GET /api/v1/torznab/{plugin_name}?t=caps`
