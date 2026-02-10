from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import structlog
import uvicorn

from scavengarr.infrastructure.config import load_config
from scavengarr.infrastructure.logging.setup import configure_logging
from scavengarr.interfaces.main import build_app

log = structlog.get_logger(__name__)


def _parse_args(argv: Iterable[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="scavengarr")

    # Server options
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host (overrides HOST env).",
    )
    parser.add_argument(
        "--port",
        default=None,
        type=int,
        help="Bind port (overrides PORT env).",
    )

    # Config wiring flags (no business logic)
    parser.add_argument(
        "--config",
        default=None,
        help="Path to YAML config file.",
    )
    parser.add_argument(
        "--dotenv",
        default=None,
        help="Path to .env file.",
    )
    parser.add_argument(
        "--plugin-dir",
        default=None,
        help="Override plugins directory.",
    )
    parser.add_argument(
        "--log-level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Override log level.",
    )
    parser.add_argument(
        "--log-format",
        default=None,
        choices=["json", "console"],
        help="Override log format.",
    )

    return parser.parse_args(argv)


def start(argv: Iterable[str] | None = None) -> None:
    """
    Process entrypoint.

    Best practice: Load config exactly once here, then build the FastAPI app with it.
    """

    if argv is None:
        argv = sys.argv[1:]

    args = _parse_args(argv)

    host = args.host or os.getenv("HOST", "0.0.0.0")
    port = int(args.port or os.getenv("PORT", "7979"))

    config_path = Path(args.config) if args.config else None
    dotenv_path = Path(args.dotenv) if args.dotenv else None

    cli_overrides: dict[str, Any] = {}
    if args.plugin_dir:
        cli_overrides["plugin_dir"] = args.plugin_dir
    if args.log_level:
        cli_overrides["log_level"] = args.log_level
    if args.log_format:
        cli_overrides["log_format"] = args.log_format

    config = load_config(
        config_path=config_path,
        dotenv_path=dotenv_path,
        cli_overrides=cli_overrides,
    )

    log_config = configure_logging(config)

    uvicorn.run(
        build_app(config),
        host=host,
        port=port,
        log_config=log_config,
    )


if __name__ == "__main__":
    raise SystemExit(start())
