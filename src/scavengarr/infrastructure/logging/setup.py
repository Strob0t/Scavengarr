"""Structlog + stdlib logging with async QueueHandler emission."""

from __future__ import annotations

import atexit
import copy
import logging
import logging.config
import queue
import sys
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
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
            "fmt": (
                '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
            ),
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
    event_dict.pop("color_message", None)
    return event_dict


def _add_record_created_timestamp_utc(
    _: Any, __: Any, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """
    Ensure timestamps for non-structlog (foreign) LogRecords
    match the time when the record was created, not the time
    when the background listener formats it.

    ProcessorFormatter sets event_dict["_record"] for foreign records.
    """
    record = event_dict.get("_record")
    if isinstance(record, logging.LogRecord):
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        event_dict["timestamp"] = dt.isoformat().replace("+00:00", "Z")
    return event_dict


_QUEUE_LISTENER: QueueListener | None = None


def _make_renderer(config: AppConfig) -> structlog.typing.Processor:
    """Return the appropriate structlog renderer for the configured format."""
    if config.log_format == "json":
        return structlog.processors.JSONRenderer()
    return structlog.dev.ConsoleRenderer()


def build_logging_config(config: AppConfig) -> dict[str, Any]:
    """
    Build a uvicorn-compatible logging config dict (dictConfig),
    based on Uvicorn's default LOGGING_CONFIG, but rendered through structlog.

    Dynamic behavior:
    - Do not hardcode logger names.
    - Apply config.log_level to all loggers already present in BASE_LOGGING_CONFIG.
    - Everything else is controlled via root logger level/handlers.
    """
    cfg = copy.deepcopy(BASE_LOGGING_CONFIG)

    renderer = _make_renderer(config)

    cfg.setdefault("formatters", {})
    cfg["formatters"]["structlog"] = {
        "()": structlog.stdlib.ProcessorFormatter,
        "foreign_pre_chain": [
            _drop_color_message,
            structlog.contextvars.merge_contextvars,
            _add_record_created_timestamp_utc,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
        ],
        "processors": [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    }

    cfg.setdefault("handlers", {})
    cfg["handlers"]["default"]["formatter"] = "structlog"
    cfg["handlers"]["access"]["formatter"] = "structlog"

    level = config.log_level

    cfg.setdefault("loggers", {})
    for _, logger_cfg in cfg["loggers"].items():
        if isinstance(logger_cfg, dict):
            logger_cfg["level"] = level

    cfg["root"] = {"handlers": ["default"], "level": level}

    return cfg


def _stop_async_listener() -> None:
    global _QUEUE_LISTENER
    if _QUEUE_LISTENER is not None:
        try:
            _QUEUE_LISTENER.stop()
        finally:
            _QUEUE_LISTENER = None


def _enable_async_logging(config: AppConfig) -> None:
    """
    Route ALL stdlib logging through a QueueHandler.

    Emit via QueueListener in a background thread.

    We bypass dictConfig's handlers for emission to ensure:
    - No blocking I/O on the caller thread (esp. asyncio loop)
    - Stable timestamps (foreign records use LogRecord.created)
    """
    global _QUEUE_LISTENER

    _stop_async_listener()

    renderer = _make_renderer(config)

    processor_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[
            _drop_color_message,
            structlog.contextvars.merge_contextvars,
            _add_record_created_timestamp_utc,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
        ],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    class _MaxLevelFilter(logging.Filter):
        def __init__(self, max_level: int) -> None:
            super().__init__()
            self._max_level = max_level

        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno <= self._max_level

    class _MinLevelFilter(logging.Filter):
        def __init__(self, min_level: int) -> None:
            super().__init__()
            self._min_level = min_level

        def filter(self, record: logging.LogRecord) -> bool:
            return record.levelno >= self._min_level

    stdout_handler = logging.StreamHandler(stream=sys.stdout)
    stdout_handler.setFormatter(processor_formatter)
    stdout_handler.addFilter(
        _MaxLevelFilter(logging.WARNING)
    )  # DEBUG/INFO/WARNING -> stdout

    stderr_handler = logging.StreamHandler(stream=sys.stderr)
    stderr_handler.setFormatter(processor_formatter)
    stderr_handler.addFilter(_MinLevelFilter(logging.ERROR))  # ERROR/CRITICAL -> stderr

    q: queue.Queue[logging.LogRecord] = queue.Queue()

    class _StructlogPreservingQueueHandler(QueueHandler):
        """QueueHandler that preserves structlog event_dicts.

        Keeps record.msg as dict without breaking them.
        """

        def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
            # Preserve dict-msg for structlog ProcessorFormatter
            return copy.deepcopy(record)

    queue_handler = _StructlogPreservingQueueHandler(q)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(queue_handler)
    root.setLevel(config.log_level)

    for name in list(logging.root.manager.loggerDict.keys()):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
        logger.setLevel(config.log_level)

    _QUEUE_LISTENER = QueueListener(
        q, stdout_handler, stderr_handler, respect_handler_level=True
    )
    _QUEUE_LISTENER.start()
    atexit.register(_stop_async_listener)


def configure_logging(config: AppConfig) -> None:
    """
    Configure structlog + stdlib logging.

    Sets up structlog processors, applies a one-time dictConfig for handler
    structure, then replaces all handlers with an async QueueHandler/QueueListener
    pipeline.  Does NOT return a config dict -- uvicorn.run() must receive
    ``log_config=None`` so it does not call dictConfig a second time.
    """
    structlog.configure(
        processors=[
            _drop_color_message,
            structlog.contextvars.merge_contextvars,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.format_exc_info,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    cfg = build_logging_config(config)

    logging.config.dictConfig(cfg)

    _enable_async_logging(config)

    log.info(
        "logging_configured", log_format=config.log_format, log_level=config.log_level
    )
