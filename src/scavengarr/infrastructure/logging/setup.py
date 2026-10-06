"""Structlog + stdlib logging with async QueueHandler emission."""

from __future__ import annotations

import atexit
import copy
import logging
import logging.config
import queue
import re
import sys
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from typing import Any

import structlog
from structlog.typing import EventDict

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
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
    },
    # No uvicorn.access: uvicorn runs with access_log=False, the app's
    # http_request line replaces it
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
    },
}


def _drop_color_message(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    event_dict.pop("color_message", None)
    return event_dict


def _add_record_created_timestamp_utc(
    _: Any, __: Any, event_dict: EventDict
) -> EventDict:
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


def _foreign_pre_chain() -> list[structlog.typing.Processor]:
    """Processors for stdlib (non-structlog) records."""
    return [
        _drop_color_message,
        structlog.contextvars.merge_contextvars,
        _add_record_created_timestamp_utc,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        # Traceback as text: the JSON renderer cannot serialize exc_info
        structlog.processors.format_exc_info,
        _shorten_urls,
        _redact_secrets,
    ]


def _make_processor_formatter(
    config: AppConfig,
) -> structlog.stdlib.ProcessorFormatter:
    """Formatter rendering structlog and stdlib records alike."""
    return structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_foreign_pre_chain(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _make_renderer(config),
        ],
    )


class _StructlogPreservingQueueHandler(QueueHandler):
    """QueueHandler that hands records over unformatted.

    The stock ``prepare()`` renders the message to a string, which breaks
    structlog's dict messages. A shallow copy is enough: the listener thread
    only reads the record (``ProcessorFormatter.format`` copies it again).
    A deep copy failed on tracebacks and on log fields that cannot be
    copied, and the record was lost.
    """

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)


# Values that never reach the logs: secret query parameters (TMDB api_key,
# Torznab apikey, tokens) and passwords in URLs (redis://:password@host)
_SECRET_PARAM_RE = re.compile(
    r"(?i)\b(api[_-]?key|access_token|token|passw(?:or)?d|secret)=[^&\s'\"]+"
)
_URL_PASSWORD_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]*:)[^\s/@]+@")


def _redact_secrets(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """Mask secrets in every string field, rendered exceptions included."""
    for key, value in event_dict.items():
        if isinstance(value, str) and ("=" in value or "@" in value):
            value = _SECRET_PARAM_RE.sub(r"\1=***", value)
            event_dict[key] = _URL_PASSWORD_RE.sub(r"\1***@", value)
    return event_dict


# A URL up to its origin, and the rest of it (path, query, fragment)
_URL_RE = re.compile(r"\b(https?://[^/\s'\"?#]+)[^\s'\"]*")


def _shorten_urls(_: Any, __: Any, event_dict: EventDict) -> EventDict:
    """URLs in a third-party record's text keep only their origin.

    httpx logs each request's full URL, and exceptions quote it: the path
    and query of a video URL carry the CDN's tokens and the client's address
    (``i=``). The app's own events name a CDN by its domain instead.
    """
    for key, value in event_dict.items():
        if isinstance(value, str) and "://" in value:
            event_dict[key] = _URL_RE.sub(r"\1", value)
    return event_dict


def _structlog_processors() -> list[structlog.typing.Processor]:
    """Processors for structlog events, up to the stdlib handover."""
    return [
        _drop_color_message,
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.format_exc_info,
        _redact_secrets,
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ]


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
        "foreign_pre_chain": _foreign_pre_chain(),
        "processors": [
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    }

    cfg.setdefault("handlers", {})
    cfg["handlers"]["default"]["formatter"] = "structlog"

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

    processor_formatter = _make_processor_formatter(config)

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
        processors=_structlog_processors(),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    cfg = build_logging_config(config)

    logging.config.dictConfig(cfg)

    _enable_async_logging(config)

    log.info(
        "logging_configured", log_format=config.log_format, log_level=config.log_level
    )
