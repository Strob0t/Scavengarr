"""Tests for the async logging pipeline (structlog + QueueHandler)."""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading

import pytest
import structlog

from scavengarr.infrastructure.config.schema import AppConfig
from scavengarr.infrastructure.logging import setup


def _exception_record() -> logging.LogRecord:
    """A stdlib record with a traceback, like uvicorn's ASGI error log."""
    try:
        1 / 0  # noqa: B018
    except ZeroDivisionError:
        exc_info = sys.exc_info()
    return logging.LogRecord(
        "uvicorn.error",
        logging.ERROR,
        __file__,
        1,
        "Exception in ASGI application",
        None,
        exc_info,
    )


class TestQueueHandler:
    def test_record_with_traceback_is_handed_over(self) -> None:
        # A traceback cannot be deep-copied: the record used to be lost
        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = setup._StructlogPreservingQueueHandler(q)

        handler.handle(_exception_record())

        assert q.get_nowait().exc_info is not None

    def test_log_field_that_cannot_be_copied(self) -> None:
        q: queue.Queue[logging.LogRecord] = queue.Queue()
        handler = setup._StructlogPreservingQueueHandler(q)
        event = {"event": "evt", "lock": threading.Lock()}
        record = logging.LogRecord("app", logging.INFO, __file__, 1, event, None, None)

        handler.handle(record)

        assert q.get_nowait().msg["event"] == "evt"


class TestForeignExceptions:
    def test_json_log_carries_the_traceback(self) -> None:
        formatter = setup._make_processor_formatter(AppConfig(log_format="json"))

        line = json.loads(formatter.format(_exception_record()))

        assert line["event"] == "Exception in ASGI application"
        assert "ZeroDivisionError" in line["exception"]


class TestSecretRedaction:
    @pytest.mark.parametrize(
        ("raw", "redacted"),
        [
            (
                "https://api.themoviedb.org/3/movie/1?api_key=abc123&language=de-DE",
                "https://api.themoviedb.org/3/movie/1?api_key=***&language=de-DE",
            ),
            ("t=search&q=matrix&apikey=deadbeef", "t=search&q=matrix&apikey=***"),
            ("redis://:hunter2@redis:6379/0", "redis://:***@redis:6379/0"),
            ("redis://app:hunter2@redis:6379/0", "redis://app:***@redis:6379/0"),
            ("https://voe.sx/e/abc?autoplay=1", "https://voe.sx/e/abc?autoplay=1"),
        ],
    )
    def test_masks_secret_values(self, raw: str, redacted: str) -> None:
        assert setup._redact_secrets(None, "info", {"url": raw})["url"] == redacted

    def test_rendered_exception_is_redacted(self) -> None:
        # httpx puts the full request URL into HTTPStatusError messages
        try:
            raise RuntimeError(
                "Client error '429' for url "
                "'https://api.themoviedb.org/3/x?api_key=SECRET1&language=de'"
            )
        except RuntimeError:
            exc_info = sys.exc_info()
        record = logging.LogRecord(
            "httpx", logging.WARNING, __file__, 1, "tmdb_http_error", None, exc_info
        )
        formatter = setup._make_processor_formatter(AppConfig(log_format="json"))

        line = formatter.format(record)

        assert "SECRET1" not in line
        assert "api_key=***" in line

    def test_structlog_events_are_redacted_after_rendering_exceptions(self) -> None:
        processors = setup._structlog_processors()
        assert processors.index(setup._redact_secrets) > processors.index(
            structlog.processors.format_exc_info
        )
