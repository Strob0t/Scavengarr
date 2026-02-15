"""Debug endpoint for plugin scoring state."""

from __future__ import annotations

from typing import Any, cast

import structlog
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/plugin-scores")
async def plugin_scores(
    request: Request,
    plugin: str | None = Query(default=None, description="Filter by plugin name."),
    category: int | None = Query(default=None, description="Filter by category ID."),
    bucket: str | None = Query(default=None, description="Filter by age bucket."),
) -> JSONResponse:
    """Return current plugin scoring state for debugging.

    Supports optional filtering by plugin, category, and/or bucket.
    Returns 503 if scoring is not enabled.
    """
    state = cast(AppState, request.app.state)
    score_store = state.plugin_score_store

    if score_store is None:
        return JSONResponse(
            status_code=503,
            content={"error": "scoring_not_enabled"},
        )

    snapshots = await score_store.list_snapshots(plugin=plugin)

    results: list[dict[str, Any]] = []
    for snap in snapshots:
        if category is not None and snap.category != category:
            continue
        if bucket is not None and snap.bucket != bucket:
            continue
        results.append(
            {
                "plugin": snap.plugin,
                "category": snap.category,
                "bucket": snap.bucket,
                "health_score": {
                    "value": round(snap.health_score.value, 4),
                    "n_samples": snap.health_score.n_samples,
                    "last_ts": snap.health_score.last_ts.isoformat(),
                },
                "search_score": {
                    "value": round(snap.search_score.value, 4),
                    "n_samples": snap.search_score.n_samples,
                    "last_ts": snap.search_score.last_ts.isoformat(),
                },
                "final_score": round(snap.final_score, 4),
                "confidence": round(snap.confidence, 4),
                "updated_at": snap.updated_at.isoformat(),
            }
        )

    return JSONResponse(content={"scores": results, "count": len(results)})
