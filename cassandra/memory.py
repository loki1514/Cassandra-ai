"""
Memory API for Expo — read and write to Supermemory via Cassandra.

Expo never writes to Supermemory directly. All writes go through this API
using the authenticated user's org context.
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import structlog

from cassandra.auth import get_current_user, UserContext
from cassandra.rag.context_fetcher import _fetch_supermemory

logger = structlog.get_logger("cassandra.memory")

router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])


# =============================================================================
# Pydantic Models
# =============================================================================

class MemorySearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    room_id: Optional[str] = None  # filter by room


class MemorySearchResult(BaseModel):
    content: str
    score: float
    source: str
    created_at: str


class MemorySearchResponse(BaseModel):
    results: List[MemorySearchResult]
    total: int
    query: str


class MemoryWriteRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)
    source: Optional[str] = None
    room_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None


class MemoryWriteResponse(BaseModel):
    success: bool
    memory_id: Optional[str] = None
    content_preview: str


# =============================================================================
# Endpoints
# =============================================================================

@router.post("/search", response_model=MemorySearchResponse)
async def search_memory(
    req: MemorySearchRequest,
    user: UserContext = Depends(get_current_user),
) -> MemorySearchResponse:
    """
    Search Supermemory for session context relevant to the query.

    Returns up to `top_k` memory chunks ranked by semantic similarity,
    scoped to the user's organization.
    """
    org_id = user.org_id

    chunks = await _fetch_supermemory(
        query=req.query,
        org_id=org_id,
        top_k=req.top_k,
    )

    # Optionally filter by room_id if the Supermemory payload supports it
    if req.room_id:
        chunks = [
            c for c in chunks
            if c.get("room_id") == req.room_id
            or req.room_id in c.get("source", "")
        ]

    results = [
        MemorySearchResult(
            content=r.get("content", ""),
            score=r.get("score", 0.0),
            source=r.get("source", "supermemory"),
            created_at=r.get("created_at", ""),
        )
        for r in chunks
    ]

    logger.info(
        "memory_search",
        org_id=org_id,
        query=req.query,
        results_count=len(results),
    )

    return MemorySearchResponse(
        results=results,
        total=len(results),
        query=req.query,
    )


@router.post("", response_model=MemoryWriteResponse)
async def write_memory(
    req: MemoryWriteRequest,
    user: UserContext = Depends(get_current_user),
) -> MemoryWriteResponse:
    """
    Write a memory entry to Supermemory.

    Use this for explicit user annotations or notes that should be
    persisted beyond the session lifecycle. Note: Cassandra already
    writes session summaries automatically on session end — use this
    only for supplementary content.

    Returns a preview of the written content.
    """
    org_id = user.org_id
    content_preview = req.content[:200] + ("..." if len(req.content) > 200 else "")

    try:
        from cassandra.config import settings
        import httpx

        if not settings.supermemory.is_configured:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Supermemory is not configured on this instance",
            )

        async with httpx.AsyncClient(
            timeout=settings.supermemory.timeout_seconds,
        ) as http:
            headers = {
                "Authorization": f"Bearer {settings.supermemory.api_key}",
                "Content-Type": "application/json",
            }
            org_header = settings.supermemory.org_id_header
            if org_header:
                headers[org_header] = org_id

            payload: Dict[str, Any] = {
                "content": req.content,
                "source": req.source or f"cassandra-expo/{user.user_id}",
                "url": f"cassandra://user/{user.user_id}",
                "org_id": org_id,
            }
            if req.room_id:
                payload["room_id"] = req.room_id
            if req.metadata:
                payload["metadata"] = req.metadata

            write_url = (
                f"{settings.supermemory.api_url.rstrip('/')}/add"
            )
            resp = await http.post(write_url, headers=headers, json=payload)
            resp.raise_for_status()

            # Try to extract an ID from the response
            memory_id = None
            try:
                data = resp.json()
                memory_id = (
                    data.get("id")
                    or data.get("memory_id")
                    or data.get("data", {}).get("id")
                )
            except Exception:
                pass

            logger.info(
                "memory_written",
                org_id=org_id,
                user_id=user.user_id,
                room_id=req.room_id,
                content_length=len(req.content),
            )

            return MemoryWriteResponse(
                success=True,
                memory_id=memory_id,
                content_preview=content_preview,
            )

    except HTTPException:
        raise
    except httpx.HTTPStatusError as e:
        logger.warning(
            "supermemory_write_http_error",
            status=e.response.status_code,
            body=str(e.response.text)[:200],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Supermemory upstream returned an error",
        )
    except Exception as e:
        logger.error("memory_write_failed", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to write memory",
        )
