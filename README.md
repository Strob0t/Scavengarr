# Scavengarr

Scavengarr is a Prowlarr-compatible Torznab/Newznab indexer that accepts search requests via HTTP and returns Torznab XML results.
Project docs and important files: `.devcontainer/`, `docker-compose.yml`, `.env.example`, `ARCHITECTURE.md`, `AGENTS.md`.

## Project status

This is an early-stage project and most of the code is “vibe coded”.  
There are no tests yet.

## Dev Container (recommended)

Prerequisites:
- Docker (Docker Desktop / Docker Engine)
- VS Code + “Dev Containers” extension
- Git

Steps:
1. Clone the repo and open it in VS Code.
2. Run: `Dev Containers: Reopen in Container`.

## Running locally

Common options:
- Using Docker Compose: `docker compose up --build` (see `docker-compose.yml`).
- Running directly in the dev container (example):
  - `poetry run start --factory --host 0.0.0.0 --port 7979`

## Configuration & plugins

- Put local env vars into a `.env` file (see `.env.example`).
- Plugins live in the `plugins/` directory in this repo (examples/dev plugins).
- For deeper design details, read `ARCHITECTURE.md`.
