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

