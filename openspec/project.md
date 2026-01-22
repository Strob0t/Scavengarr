# Project Context

## Purpose
Scavengarr is designed to be a self-hosted, container-ready indexer that emulates the Torznab/Newznab API used by applications like Prowlarr. It scrapes forum-style and meta-search sites using Playwright, normalizes the results, and serves them through various Torznab endpoints.

## Tech Stack
- **Language**: Python 3.12
- **Framework**: FastAPI for the web server
- **Scraping**: Playwright for browser-driven web scraping
- **Cache**: Redis or Diskcache for caching responses
- **Containerization**: Docker for deployment and orchestration

## Project Conventions

### Code Style
- Follows PEP 8 guidelines
- Uses `black` for code formatting with a line length of 88 characters
- Static analysis and linting with `ruff`
- Import order management via `isort`
- Full type annotations everywhere

### Architecture Patterns
- Layered architecture with a focus on emulating the Torznab/Newznab API
- Unified mode for single-process deployment or distributed mode for scalability with separate coordinator and worker processes
- Configuration via Pydantic models

### Testing Strategy
- Behavior-driven testing approach
- End-to-end > Integration > Unit testing hierarchy
- Tests implemented using Pytest
- Use of real dependencies wherever feasible to minimize mocks

### Git Workflow
- Feature branch names: `<type>/<issue>-<short-desc>` (e.g., `feat/123-add-cache`)
- Commit messages follow the `<type>(<scope>): <subject>` format
- Use of `pre-commit` hooks for linting, formatting, type-checking, and running tests

## Domain Context
Understanding of forum-style and meta-search site structures is crucial for defining scraping plugins and handling authentication strategies. The system must effectively emulate Torznab endpoints to integrate with Prowlarr and similar applications.

## Important Constraints
- Must operate efficiently in both unified and distributed deployment modes
- Must adhere to privacy and security best practices, including secure handling of authentication tokens and cookies
- Compatibility with the Torznab/Newznab XML schema is required

## External Dependencies
- GitHub repositories for code management and issue tracking
- Docker Hub for container images
- Redis for caching (when configured)