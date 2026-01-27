from __future__ import annotations

import copy
import logging.config
from typing import Any

import structlog

from scavengarr.infrastructure.config.schema import AppConfig

log = structlog.get_logger(__name__)

BASE_LOGGING_CONFIG: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "()": "uvicorn.logging.DefaultFormatter",
            "fmt": "%(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "()": "uvicorn.logging.AccessFormatter",
            "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _drop_color_message(_: Any, __: Any, event_dict: dict[str, Any]) -> dict[str, Any]:
    # Uvicorn hängt oft "color_message" an; das macht Logs unnötig doppelt.
    event_dict.pop("color_message", None)
    return event_dict


def build_logging_config(config: AppConfig) -> dict[str, Any]:
    """
    Build a uvicorn-compatible logging config dict (dictConfig),
    based on Uvicorn's default LOGGING_CONFIG, but rendered through structlog.
    """
    cfg = copy.deepcopy(BASE_LOGGING_CONFIG)

    # Renderer abhängig von config
    if config.log_format == "json":
        renderer: structlog.typing.Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer()

    # 1) structlog-Formatter hinzufügen
    cfg.setdefault("formatters", {})
    cfg["formatters"]["structlog"] = {
        "()": structlog.stdlib.ProcessorFormatter,
        # foreign_pre_chain läuft für "normale" logging-Records (uvicorn/fastapi/starlette)
        "foreign_pre_chain": [
            _drop_color_message,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
        ],
        "processors": [
            # Entfernt _record/_from_structlog, damit deine Logs sauber aussehen
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    }

    # 2) Beide Handler auf denselben Formatter setzen (damit alles gleich aussieht)
    cfg.setdefault("handlers", {})
    cfg["handlers"]["default"]["formatter"] = "structlog"
    cfg["handlers"]["access"]["formatter"] = "structlog"

    # Optional: wenn du wirklich *einen* Stream willst, kommentier die nächste Zeile ein:
    # cfg["handlers"]["default"]["stream"] = "ext://sys.stdout"

    # 3) Logger-Level aus AppConfig übernehmen + FastAPI/Starlette explizit routen
    cfg.setdefault("loggers", {})
    level = config.log_level

    cfg["loggers"]["uvicorn"]["level"] = level
    cfg["loggers"]["uvicorn.error"]["level"] = level
    cfg["loggers"]["uvicorn.access"]["level"] = level

    # Diese beiden loggen über stdlib logging -> gleiche Handler/Format
    cfg["loggers"]["fastapi"] = {"handlers": [
        "default"], "level": level, "propagate": False}
    cfg["loggers"]["starlette"] = {"handlers": [
        "default"], "level": level, "propagate": False}

    # Root ebenfalls auf euren Level (damit third-party libs auch reinlaufen)
    cfg["root"] = {"handlers": ["default"], "level": level}

    return cfg


def configure_logging(config: AppConfig) -> dict[str, Any]:
    """
    Configure structlog + stdlib logging and return the dict for uvicorn.run(log_config=...).
    """
    structlog.configure(
        processors=[
            _drop_color_message,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.format_exc_info,
            # wichtig: damit structlog -> stdlib logging -> ProcessorFormatter funktioniert
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
        # ProcessorFormatter-Integration ist der Standard-Weg für stdlib logging
    )

    cfg = build_logging_config(config)
    logging.config.dictConfig(cfg)
    log.info("logging_configured", log_format=config.log_format,
             log_level=config.log_level)
    return cfg
