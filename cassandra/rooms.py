"""
Room Management API for property-scoped ephemeral voice rooms.

Provides endpoints for:
- Creating rooms with enrolled participants
- Managing room participants
- Ending rooms and triggering post-session analysis
- Retrieving room analysis results
"""

import json
import uuid
from typing import Any, Dict, List, Optional
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
import structlog

from cassandra.auth import get_current_user, UserContext
from cassandra.supabase import get_supabase_client

logger = structlog.get_logger("cassandra.rooms")

router = APIRouter(prefix="/api/v1/properties", tags=["Rooms"])


# =============================================================================
# Pydantic Models
# =============================================================================

class CreateRoomRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    participant_ids: List[str] = Field(..., min_items=1, max_items=20)
    scheduled_start: Optional[str] = None


class PatchParticipantsRequest(BaseModel):
    add: List[str] = Field(default_factory=list)
    remove: List[str] = Field(default_factory=list)


class RoomParticipant(BaseModel):
    user_id: str
    display_name: str
    voice_profile_id: Optional[str] = None
    enrolled_at: Optional[str] = None


class RoomResponse(BaseModel):
    room_id: str
    name: str
    status: str
    property_id: str
    org_id: str
    participants: List[Dict[str, Any]]
    active_session_id: Optional[str] = None
    analysis_status: str
    created_at: str


# =============================================================================
# Helper Functions
# =============================================================================

def _generate_room_id(property_id: str) -> str:
    """Generate a human-readable room ID."""
    timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"room-{property_id}-{timestamp}-{short_uuid}"


async def _validate_property_belongs_to_org(property_id: str, org_id: str) -> bool:
    """Check that the property exists and belongs to the org."""
    client = get_supabase_client("service")
    result = client.table("properties").select("id").eq("id", property_id).eq("org_id", org_id).execute()
    return bool(result.data)


async def _validate_users_belong_to_org(user_ids: List[str], org_id: str) -> List[Dict[str, Any]]:
    """Fetch users and verify they all belong to the org."""
    client = get_supabase_client("service")
    result = client.table("users").select("id, email, org_id").in_("id", user_ids).eq("org_id", org_id).execute()
    found = {u["id"]: u for u in result.data}
    missing = [uid for uid in user_ids if uid not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Users not found in org: {missing}"
        )
    return list(found.values())


async def _fetch_voice_profiles_for_users(user_ids: List[str], org_id: str) -> Dict[str, Dict[str, Any]]:
    """Fetch voice profiles for the given users in the org."""
    client = get_supabase_client("service")
    result = client.table("voice_profiles").select("*").in_("user_id", user_ids).eq("org_id", org_id).eq("status", "active").execute()
    return {p["user_id"]: p for p in result.data}


async def _get_room_or_404(room_id: str, property_id: str, org_id: str) -> Dict[str, Any]:
    """Fetch room by ID with property/org validation."""
    client = get_supabase_client("service")
    result = client.table("rooms").select("*").eq("room_id", room_id).eq("property_id", property_id).eq("org_id", org_id).execute()
    if not result.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return result.data[0]


# =============================================================================
# Voice Enrollment Endpoints (moved/refactored here for clean API surface)
# =============================================================================

@router.post("/{property_id}/rooms", response_model=RoomResponse, status_code=status.HTTP_201_CREATED)
async def create_room(
    property_id: str,
    req: CreateRoomRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Create a new room at the specified property.
    All participants must have enrolled voice profiles.
    """
    org_id = user.org_id

    # Validate property belongs to org
    if not await _validate_property_belongs_to_org(property_id, org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found or does not belong to organization"
        )

    # Validate users belong to org
    org_users = await _validate_users_belong_to_org(req.participant_ids, org_id)

    # Fetch voice profiles for participants
    voice_profiles = await _fetch_voice_profiles_for_users(req.participant_ids, org_id)

    # Require all participants to have active voice profiles
    missing_profiles = [uid for uid in req.participant_ids if uid not in voice_profiles]
    if missing_profiles:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Participants missing voice enrollment: {missing_profiles}"
        )

    # Build participants JSONB snapshot
    participants = []
    for u in org_users:
        vp = voice_profiles.get(u["id"])
        participants.append({
            "user_id": u["id"],
            "display_name": u.get("email", u["id"]).split("@")[0],
            "voice_profile_id": vp["profile_id"] if vp else None,
            "enrolled_at": vp["enrolled_at"] if vp else None,
        })

    room_id = _generate_room_id(property_id)
    client = get_supabase_client("service")

    result = client.table("rooms").insert({
        "room_id": room_id,
        "org_id": org_id,
        "property_id": property_id,
        "name": req.name,
        "status": "waiting",
        "participants": participants,
        "active_session_id": None,
        "session_ids": [],
        "analysis_status": "pending",
        "created_by": user.user_id,
    }).execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create room")

    room = result.data[0]
    logger.info("room_created", room_id=room_id, org_id=org_id, property_id=property_id, participant_count=len(participants))

    return RoomResponse(
        room_id=room["room_id"],
        name=room["name"],
        status=room["status"],
        property_id=room["property_id"],
        org_id=room["org_id"],
        participants=room.get("participants", []),
        active_session_id=room.get("active_session_id"),
        analysis_status=room.get("analysis_status", "pending"),
        created_at=room["created_at"],
    )


@router.get("/{property_id}/rooms/{room_id}", response_model=RoomResponse)
async def get_room(
    property_id: str,
    room_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Get room details including participants and analysis status."""
    room = await _get_room_or_404(room_id, property_id, user.org_id)
    return RoomResponse(
        room_id=room["room_id"],
        name=room["name"],
        status=room["status"],
        property_id=room["property_id"],
        org_id=room["org_id"],
        participants=room.get("participants", []),
        active_session_id=room.get("active_session_id"),
        analysis_status=room.get("analysis_status", "pending"),
        created_at=room["created_at"],
    )


@router.patch("/{property_id}/rooms/{room_id}/participants", response_model=RoomResponse)
async def patch_room_participants(
    property_id: str,
    room_id: str,
    req: PatchParticipantsRequest,
    user: UserContext = Depends(get_current_user)
):
    """Add or remove participants from a room."""
    org_id = user.org_id
    room = await _get_room_or_404(room_id, property_id, org_id)

    current_participants = {p["user_id"]: p for p in room.get("participants", [])}

    # Handle removals
    for uid in req.remove:
        current_participants.pop(uid, None)

    # Handle additions
    if req.add:
        org_users = await _validate_users_belong_to_org(req.add, org_id)
        voice_profiles = await _fetch_voice_profiles_for_users(req.add, org_id)
        missing = [uid for uid in req.add if uid not in voice_profiles]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Added participants missing voice enrollment: {missing}"
            )
        for u in org_users:
            vp = voice_profiles.get(u["id"])
            current_participants[u["id"]] = {
                "user_id": u["id"],
                "display_name": u.get("email", u["id"]).split("@")[0],
                "voice_profile_id": vp["profile_id"] if vp else None,
                "enrolled_at": vp["enrolled_at"] if vp else None,
            }

    updated_participants = list(current_participants.values())

    client = get_supabase_client("service")
    result = client.table("rooms").update({
        "participants": updated_participants
    }).eq("room_id", room_id).eq("org_id", org_id).execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update room participants")

    updated_room = result.data[0]
    logger.info("room_participants_updated", room_id=room_id, org_id=org_id, added=len(req.add), removed=len(req.remove))

    return RoomResponse(
        room_id=updated_room["room_id"],
        name=updated_room["name"],
        status=updated_room["status"],
        property_id=updated_room["property_id"],
        org_id=updated_room["org_id"],
        participants=updated_room.get("participants", []),
        active_session_id=updated_room.get("active_session_id"),
        analysis_status=updated_room.get("analysis_status", "pending"),
        created_at=updated_room["created_at"],
    )


@router.post("/{property_id}/rooms/{room_id}/end")
async def end_room(
    property_id: str,
    room_id: str,
    user: UserContext = Depends(get_current_user)
):
    """
    End a room and trigger post-session analysis.
    Returns immediately; analysis runs in the background.
    """
    org_id = user.org_id
    room = await _get_room_or_404(room_id, property_id, org_id)

    client = get_supabase_client("service")
    result = client.table("rooms").update({
        "status": "ended",
        "ended_at": datetime.utcnow().isoformat(),
        "analysis_status": "running"
    }).eq("room_id", room_id).eq("org_id", org_id).execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to end room")

    # Trigger post-session analysis via background task (fire-and-forget)
    # The actual analyzer is invoked from the session end callback in main.py
    # This endpoint just marks the room as ended and sets analysis_status = running
    logger.info("room_ended", room_id=room_id, org_id=org_id)

    return {
        "room_id": room_id,
        "status": "ended",
        "analysis_status": "running"
    }


@router.get("/{property_id}/rooms/{room_id}/analysis")
async def get_room_analysis(
    property_id: str,
    room_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Get post-session analysis results for a room."""
    org_id = user.org_id
    room = await _get_room_or_404(room_id, property_id, org_id)

    client = get_supabase_client("service")

    # Fetch enriched transcripts
    transcript_result = client.table("enriched_transcripts").select("*").eq("room_id", room["id"]).order("start_ms").execute()

    # Fetch action items
    action_result = client.table("action_items").select("*").eq("room_id", room["id"]).order("created_at", desc=False).execute()

    # Fetch speaker analysis audit for quality flags
    audit_result = client.table("speaker_analysis_audit").select("*").eq("room_id", room["id"]).maybe_single().execute()
    audit = audit_result.data if audit_result.data else None

    # Determine if human review is recommended
    review_required = False
    review_reason: str | None = None
    if audit:
        if audit.get("has_unknowns"):
            review_required = True
            review_reason = "unidentified_speakers"
        elif (audit.get("mapping_confidence_avg") or 1.0) < 0.60:
            review_required = True
            review_reason = "low_mapping_confidence"
        elif audit.get("unmatched_speakers", 0) > 0:
            review_required = True
            review_reason = "partial_match"

    return {
        "room_id": room_id,
        "status": room.get("analysis_status", "pending"),
        "speaker_map": room.get("analysis_result", {}).get("speaker_map", {}),
        "enriched_transcript": transcript_result.data,
        "action_items": action_result.data,
        # Audit quality flags: tell the caller whether to trust these results
        "review_required": review_required,
        "review_reason": review_reason,
        "mapping_quality": {
            "pyannote_speakers": audit.get("pyannote_speakers") if audit else None,
            "assemblyai_speakers": audit.get("assemblyai_speakers") if audit else None,
            "unmatched_speakers": audit.get("unmatched_speakers") if audit else None,
            "avg_confidence": audit.get("mapping_confidence_avg") if audit else None,
            "high_confidence_matches": audit.get("high_confidence_matches") if audit else None,
            "unknown_labels": audit.get("unknown_speaker_labels") if audit else None,
        } if audit else None,
    }
