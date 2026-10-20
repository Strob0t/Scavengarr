"""Tests for Torznab router category parsing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from scavengarr.domain.entities import TorznabQuery
from scavengarr.interfaces.api.torznab.router import router


def _make_app(
    mock_search_uc_cls: MagicMock,
) -> FastAPI:
    """Create a minimal FastAPI app with the torznab router."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    # Wire up minimal app state
    state = MagicMock()
    state.config.environment = "dev"
    state.config.app_name = "Scavengarr"
    app.state.plugins = state.plugins
    app.state.search_engine = state.search_engine
    app.state.crawljob_factory = state.crawljob_factory
    app.state.crawljob_repo = state.crawljob_repo
    app.state.config = state.config
    app.state.http_client = state.http_client

    return app


class TestRouterCategoryParsing:
    """Verify `cat` query parameter is parsed and passed to TorznabQuery."""

    @patch(
        "scavengarr.interfaces.api.torznab.router.TorznabSearchUseCase",
    )
    def test_single_category_parsed(self, mock_uc_cls: MagicMock) -> None:
        """cat='2000' should produce category=2000."""
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = []
        mock_uc_cls.return_value = mock_instance

        app = _make_app(mock_uc_cls)
        client = TestClient(app)

        client.get("/api/v1/torznab/scnlog?t=search&q=test&cat=2000")

        # Verify execute was called with correct TorznabQuery
        mock_instance.execute.assert_awaited_once()
        query: TorznabQuery = mock_instance.execute.call_args[0][0]
        assert query.category == 2000

    @patch(
        "scavengarr.interfaces.api.torznab.router.TorznabSearchUseCase",
    )
    @pytest.mark.parametrize(
        ("cat", "category", "kinds"),
        [
            # Prowlarr's own searches and its RSS sync: the whole list
            ("2000,5000", None, (2000, 5000)),
            ("5000,2040,2000,5070", None, (2000, 5000)),
            # One kind with child ids (Sonarr): the first id, as before
            ("5030,5040", 5030, ()),
            ("2000,", 2000, ()),
        ],
    )
    def test_ids_grouped_by_kind(
        self,
        mock_uc_cls: MagicMock,
        cat: str,
        category: int | None,
        kinds: tuple[int, ...],
    ) -> None:
        """One kind keeps the first id; several kinds search unfiltered and
        name the kinds, sorted."""
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = []
        mock_uc_cls.return_value = mock_instance

        app = _make_app(mock_uc_cls)
        client = TestClient(app)

        client.get(f"/api/v1/torznab/scnlog?t=search&q=test&cat={cat}")

        query: TorznabQuery = mock_instance.execute.call_args[0][0]
        assert (query.category, query.kinds) == (category, kinds)

    @patch(
        "scavengarr.interfaces.api.torznab.router.TorznabSearchUseCase",
    )
    def test_an_invalid_id_after_the_first_is_a_bad_request(
        self, mock_uc_cls: MagicMock
    ) -> None:
        app = _make_app(mock_uc_cls)
        client = TestClient(app)

        resp = client.get("/api/v1/torznab/scnlog?t=search&q=test&cat=2000,abc")

        assert resp.status_code == 400
        mock_uc_cls.return_value.execute.assert_not_called()

    @patch(
        "scavengarr.interfaces.api.torznab.router.TorznabSearchUseCase",
    )
    def test_empty_cat_gives_none(self, mock_uc_cls: MagicMock) -> None:
        """cat='' (default) should produce category=None."""
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = []
        mock_uc_cls.return_value = mock_instance

        app = _make_app(mock_uc_cls)
        client = TestClient(app)

        client.get("/api/v1/torznab/scnlog?t=search&q=test")

        query: TorznabQuery = mock_instance.execute.call_args[0][0]
        assert query.category is None

    @patch(
        "scavengarr.interfaces.api.torznab.router.TorznabSearchUseCase",
    )
    def test_no_cat_param_gives_none(self, mock_uc_cls: MagicMock) -> None:
        """Missing cat param entirely should produce category=None."""
        mock_instance = AsyncMock()
        mock_instance.execute.return_value = []
        mock_uc_cls.return_value = mock_instance

        app = _make_app(mock_uc_cls)
        client = TestClient(app)

        client.get("/api/v1/torznab/scnlog?t=search&q=test&cat=")

        query: TorznabQuery = mock_instance.execute.call_args[0][0]
        assert query.category is None
