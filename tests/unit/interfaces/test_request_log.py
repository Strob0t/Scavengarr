"""Tests for the access log line of each request (``http_request``)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from scavengarr.infrastructure.config import AppConfig
from scavengarr.interfaces.app import create_app, loggable_query


class TestLoggableQuery:
    def test_torznab_parameters_are_logged(self) -> None:
        query = "t=search&q=Iron+Man&cat=2000&extended=1&offset=0&limit=100"

        assert loggable_query("/api/v1/torznab/kinoger", query) == query

    def test_prowlarrs_apikey_is_masked(self) -> None:
        assert (
            loggable_query("/api/v1/torznab/kinoger", "t=caps&apikey=s3cret")
            == "t=caps&apikey=***"
        )

    def test_every_value_of_other_paths_is_masked(self) -> None:
        """Proxied HLS paths carry the CDN's tokens and the client's
        address, also under Torznab's parameter names."""
        query = "t=tok3n&q=1&i=203.0.113.7&e=1700000000"

        assert (
            loggable_query("/api/v1/stremio/proxy/abc/seg-1.ts", query)
            == "t=***&q=***&i=***&e=***"
        )

    def test_no_query_stays_empty(self) -> None:
        assert loggable_query("/api/v1/healthz", "") == ""


class TestAccessLog:
    def test_the_logged_query_is_masked(self) -> None:
        with capture_logs() as logs:
            TestClient(create_app(AppConfig())).get(
                "/api/v1/healthz", params={"token": "s3cret"}
            )

        [entry] = [e for e in logs if e["event"] == "http_request"]
        assert entry["query"] == "token=***"
