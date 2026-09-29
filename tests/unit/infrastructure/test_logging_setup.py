"""Tests for the async logging pipeline (structlog + QueueHandler)."""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading

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
