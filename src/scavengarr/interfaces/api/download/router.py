"""Download endpoint for serving .crawljob files to Sonarr/Radarr."""

from __future__ import annotations

from typing import cast

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from scavengarr.interfaces.app_state import AppState

log = structlog.get_logger(__name__)

router = APIRouter(tags=["download"])


@router.get("/api/v1/download/{job_id}")
async def download_crawljob(
    job_id: str,
    request: Request,
) -> Response:
    """Serve .crawljob file for JDownloader integration.

    This endpoint is called by Sonarr/Radarr when they click the download link
    from Torznab search results. The response is a .crawljob file containing
    validated download links in JDownloader format.

    Flow:
        1. Sonarr/Radarr receives Torznab XML with <link>/api/v1/download/{job_id}</link>
        2. They make GET request to this endpoint
        3. We lookup CrawlJob from repository (cache)
        4. Check if expired
        5. Generate .crawljob file content
        6. Return as downloadable file

    Args:
        job_id: Unique CrawlJob identifier (UUID4).
        request: FastAPI request object (for accessing app state).

    Returns:
        Response with .crawljob file content and appropriate headers.

    Raises:
        HTTPException(404): CrawlJob not found or expired.
        HTTPException(500): Internal error (e.g., repository failure).
    """
    state = cast(AppState, request.app.state)

    log.info("download_request", job_id=job_id)

    # === 1) Lookup CrawlJob from Repository ===
    try:
        crawl_job = await state.crawljob_repo.get(job_id)
    except Exception as e:
        log.error(
            "crawljob_lookup_failed",
            job_id=job_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to retrieve CrawlJob from repository",
        ) from e

    # === 2) Check if CrawlJob Exists ===
    if crawl_job is None:
        log.warning("crawljob_not_found", job_id=job_id)
        raise HTTPException(
            status_code=404,
            detail=f"CrawlJob not found: {job_id}",
        )

    # === 3) Check if CrawlJob is Expired ===
    if crawl_job.is_expired():
        log.warning(
            "crawljob_expired",
            job_id=job_id,
            expires_at=crawl_job.expires_at.isoformat(),
        )
        raise HTTPException(
            status_code=404,
            detail=f"CrawlJob expired: {job_id}",
        )

    # === 4) Generate .crawljob File Content ===
    try:
        crawljob_content = crawl_job.to_crawljob_format()
    except Exception as e:
        log.error(
            "crawljob_serialization_failed",
            job_id=job_id,
            error=str(e),
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to generate .crawljob file",
        ) from e

    # === 5) Build Filename ===
    # Sanitize package_name for filename (remove special chars)
    safe_filename = "".join(
        c if c.isalnum() or c in (" ", "-", "_") else "_"
        for c in crawl_job.package_name
    )
    filename = f"{safe_filename}_{job_id[:8]}.crawljob"

    log.info(
        "crawljob_downloaded",
        job_id=job_id,
        filename=filename,
        package_name=crawl_job.package_name,
        link_count=len(crawl_job.validated_urls),
        size_bytes=len(crawljob_content),
    )

    # === 6) Return as Downloadable File ===
    return Response(
        content=crawljob_content,
        media_type="application/x-crawljob",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": "application/x-crawljob",
            "X-CrawlJob-ID": job_id,
            "X-CrawlJob-Package": crawl_job.package_name,
            "X-CrawlJob-Links": str(len(crawl_job.validated_urls)),
        },
    )


@router.get("/api/v1/download/{job_id}/info")
async def get_crawljob_info(
    job_id: str,
    request: Request,
) -> dict:
    """Get CrawlJob metadata without downloading the file.

    Useful for debugging or checking expiry status.

    Args:
        job_id: CrawlJob identifier.
        request: FastAPI request.

    Returns:
        JSON with CrawlJob metadata.

    Raises:
        HTTPException(404): CrawlJob not found.
    """
    state = cast(AppState, request.app.state)

    try:
        crawl_job = await state.crawljob_repo.get(job_id)
    except Exception as e:
        log.error("crawljob_info_lookup_failed", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail="Repository error") from e

    if crawl_job is None:
        raise HTTPException(status_code=404, detail="CrawlJob not found")

    return {
        "job_id": crawl_job.job_id,
        "package_name": crawl_job.package_name,
        "created_at": crawl_job.created_at.isoformat(),
        "expires_at": crawl_job.expires_at.isoformat(),
        "is_expired": crawl_job.is_expired(),
        "validated_urls": crawl_job.validated_urls,
        "source_url": crawl_job.source_url,
        "comment": crawl_job.comment,
        "auto_start": crawl_job.auto_start.value,
        "priority": crawl_job.priority.value,
    }
