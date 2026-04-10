"""
backend/observability.py — Observability endpoints for Cassandra Voice Server.

Provides:
- /metrics: Prometheus-compatible metrics endpoint
- /api/admin/sessions: Active session list
- /api/admin/sessions/{id}: Session statistics
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException

from backend.core.session_manager import get_session_manager
from backend.utils.circuit_breaker import get_breaker_registry

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/metrics")
async def metrics():
    """
    Prometheus-compatible metrics endpoint.

    Exposes key metrics for monitoring:
    - Active sessions
    - Circuit breaker states
    - Audio processing stats
    """
    sm = get_session_manager()
    breaker_stats = get_breaker_registry().all_stats()

    # Build Prometheus text format
    lines = [
        "# HELP cassandra_active_sessions Number of active voice sessions",
        "# TYPE cassandra_active_sessions gauge",
        f"cassandra_active_sessions {await sm.get_active_session_count()}",
        "",
        "# HELP cassandra_circuit_breaker_state Circuit breaker state (0=closed, 1=open, 2=half_open)",
        "# TYPE cassandra_circuit_breaker_state gauge",
    ]

    state_map = {"closed": 0, "open": 1, "half_open": 2}
    for name, stats in breaker_stats.items():
        state_num = state_map.get(stats.state.value, 0)
        lines.append(
            f'cassandra_circuit_breaker_state{{name="{name}",state="{stats.state.value}"}} {state_num}'
        )
        lines.append(
            f'cassandra_circuit_breaker_calls_total{{name="{name}"}} {stats.total_calls}'
        )
        lines.append(
            f'cassandra_circuit_breaker_failures_total{{name="{name}"}} {stats.failed_calls}'
        )
        lines.append(
            f'cassandra_circuit_breaker_rejected_total{{name="{name}"}} {stats.rejected_calls}'
        )

    return "\n".join(lines)


@router.get("/sessions")
async def list_active_sessions():
    """List all active sessions with basic stats."""
    sm = get_session_manager()
    sessions = []

    for session_id in list(sm._sessions.keys()):
        stats = await sm.get_session_stats(session_id)
        if stats:
            sessions.append(stats)

    return {
        "count": len(sessions),
        "sessions": sessions,
    }


@router.get("/sessions/{session_id}")
async def get_session_detail(session_id: str):
    """Get detailed statistics for a specific session."""
    sm = get_session_manager()
    stats = await sm.get_session_stats(session_id)
    if not stats:
        raise HTTPException(status_code=404, detail="Session not found")
    return stats
