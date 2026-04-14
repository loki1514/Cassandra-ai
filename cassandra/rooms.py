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

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
import structlog

from cassandra.auth import get_current_user, UserContext
from cassandra.supabase import get_supabase_client

logger = structlog.get_logger("cassandra.rooms")

router = APIRouter(prefix="/api/v1", tags=["Rooms"])
properties_router = APIRouter(prefix="/api/v1/properties", tags=["Properties"])


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


class RoomSummary(BaseModel):
    """Lightweight room listing item."""
    room_id: str
    name: str
    status: str
    property_id: str
    analysis_status: str
    participant_count: int
    created_at: str
    ended_at: Optional[str] = None


class RoomListResponse(BaseModel):
    rooms: List[RoomSummary]
    total: int
    page: int
    page_size: int
    has_more: bool


class RoomDetailResponse(BaseModel):
    """Unified room detail: room + participants + action items + analysis + audit."""
    room_id: str
    name: str
    status: str
    property_id: str
    org_id: str
    participants: List[Dict[str, Any]]
    active_session_id: Optional[str] = None
    analysis_status: str
    created_at: str
    ended_at: Optional[str] = None
    # Analysis results (populated after room ends)
    speaker_map: Dict[str, Any] = Field(default_factory=dict)
    enriched_transcript: List[Dict[str, Any]] = Field(default_factory=list)
    action_items: List[Dict[str, Any]] = Field(default_factory=list)
    # Audit flags for Expo UI
    review_required: bool = False
    review_reason: Optional[str] = None
    mapping_quality: Optional[Dict[str, Any]] = None


class UpdateActionItemRequest(BaseModel):
    status: str = Field(
        ...,
        pattern="^(open|in_progress|completed|dismissed)$",
        description="New status for the action item"
    )
    assignee_id: Optional[str] = None
    deadline: Optional[str] = None


class CorrectTranscriptRequest(BaseModel):
    speaker_user_id: Optional[str] = Field(
        None,
        description="Correct user_id for the speaker. Pass null to mark as unknown."
    )
    speaker_name: Optional[str] = None
    reason: Optional[str] = Field(
        None,
        max_length=500,
        description="Optional explanation for the correction."
    )


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


async def _get_room_by_room_id_or_404(room_id: str, org_id: str) -> Dict[str, Any]:
    """Fetch room by room_id text field with org validation only."""
    client = get_supabase_client("service")
    result = client.table("rooms").select("*").eq("room_id", room_id).eq("org_id", org_id).execute()
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


# =============================================================================
# Property-scoped room listing (properties_router)
# =============================================================================

@properties_router.get("/{property_id}/rooms", response_model=RoomListResponse)
async def list_rooms(
    property_id: str,
    status_filter: Optional[str] = Query(
        None,
        alias="status",
        description="Filter by room status: waiting, active, ended",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    user: UserContext = Depends(get_current_user),
):
    """
    List all rooms at a property for the authenticated user's org.

    Supports pagination and optional status filtering. Returns lightweight
    summaries — use /rooms/{id}/full for the complete picture.
    """
    org_id = user.org_id

    if not await _validate_property_belongs_to_org(property_id, org_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Property not found or does not belong to organization",
        )

    client = get_supabase_client("service")

    # Build query
    query = client.table("rooms").select(
        "room_id, name, status, property_id, analysis_status, "
        "participants, created_at, ended_at"
    ).eq("property_id", property_id).eq("org_id", org_id)

    if status_filter:
        query = query.eq("status", status_filter)

    # Order by most recent first
    query = query.order("created_at", desc=True)

    # Pagination: offset-based
    offset = (page - 1) * page_size
    query = query.range(offset, offset + page_size - 1)

    result = query.execute()

    rooms = [
        RoomSummary(
            room_id=r["room_id"],
            name=r["name"],
            status=r["status"],
            property_id=r["property_id"],
            analysis_status=r.get("analysis_status", "pending"),
            participant_count=len(r.get("participants", [])),
            created_at=r["created_at"],
            ended_at=r.get("ended_at"),
        )
        for r in result.data
    ]

    return RoomListResponse(
        rooms=rooms,
        total=len(rooms),  # Supabase-js doesn't return total on range queries; clients should track total via has_more
        page=page,
        page_size=page_size,
        has_more=len(rooms) == page_size,
    )


@properties_router.get("/{property_id}/rooms/{room_id}/full", response_model=RoomDetailResponse)
async def get_room_full(
    property_id: str,
    room_id: str,
    user: UserContext = Depends(get_current_user),
):
    """
    Unified room detail endpoint for Expo.

    Returns everything needed for a room screen in a single call:
    - Room metadata + participants
    - Action items
    - Enriched transcript
    - Analysis audit flags (review_required, mapping_quality)
    """
    org_id = user.org_id
    room = await _get_room_or_404(room_id, property_id, org_id)

    client = get_supabase_client("service")

    # Fetch action items
    action_result = client.table("action_items").select("*").eq("room_id", room["id"]).order("created_at", desc=False).execute()

    # Fetch enriched transcript
    transcript_result = client.table("enriched_transcripts").select("*").eq("room_id", room["id"]).order("start_ms").execute()

    # Fetch audit
    audit_result = client.table("speaker_analysis_audit").select("*").eq("room_id", room["id"]).maybe_single().execute()
    audit = audit_result.data if audit_result.data else None

    # Compute review flags
    review_required = False
    review_reason: Optional[str] = None
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

    return RoomDetailResponse(
        room_id=room["room_id"],
        name=room["name"],
        status=room["status"],
        property_id=room["property_id"],
        org_id=room["org_id"],
        participants=room.get("participants", []),
        active_session_id=room.get("active_session_id"),
        analysis_status=room.get("analysis_status", "pending"),
        created_at=room["created_at"],
        ended_at=room.get("ended_at"),
        speaker_map=room.get("analysis_result", {}).get("speaker_map", {}),
        enriched_transcript=transcript_result.data,
        action_items=action_result.data,
        review_required=review_required,
        review_reason=review_reason,
        mapping_quality={
            "pyannote_speakers": audit.get("pyannote_speakers") if audit else None,
            "assemblyai_speakers": audit.get("assemblyai_speakers") if audit else None,
            "unmatched_speakers": audit.get("unmatched_speakers") if audit else None,
            "avg_confidence": audit.get("mapping_confidence_avg") if audit else None,
            "high_confidence_matches": audit.get("high_confidence_matches") if audit else None,
            "unknown_labels": audit.get("unknown_speaker_labels") if audit else None,
        } if audit else None,
    )


# =============================================================================
# Global room-scoped action endpoints (router — no property_id prefix)
# =============================================================================

@router.patch("/rooms/{room_id}/action-items/{action_item_id}")
async def update_action_item(
    room_id: str,
    action_item_id: str,
    req: UpdateActionItemRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    Update an action item's status, assignee, or deadline.

    Expo uses this to let users mark action items as completed,
    reassign them, or set a deadline.
    """
    org_id = user.org_id

    # Validate room belongs to org
    room = await _get_room_by_room_id_or_404(room_id, org_id)

    client = get_supabase_client("service")

    # Verify action item exists and belongs to this room + org
    existing = client.table("action_items").select("id, room_id, org_id").eq("id", action_item_id).eq("room_id", room["id"]).eq("org_id", org_id).maybe_single().execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Action item not found")

    payload: Dict[str, Any] = {
        "status": req.status,
        "updated_at": datetime.utcnow().isoformat(),
    }
    if req.assignee_id is not None:
        payload["assignee_id"] = req.assignee_id
    if req.deadline is not None:
        payload["deadline"] = req.deadline

    result = client.table("action_items").update(payload).eq("id", action_item_id).eq("org_id", org_id).execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update action item")

    logger.info(
        "action_item_updated",
        action_item_id=action_item_id,
        room_id=room_id,
        new_status=req.status,
        updated_by=user.user_id,
    )

    return {"success": True, "action_item": result.data[0]}


@router.patch("/rooms/{room_id}/transcripts/{segment_id}/correct")
async def correct_transcript_speaker(
    room_id: str,
    segment_id: str,
    req: CorrectTranscriptRequest,
    user: UserContext = Depends(get_current_user),
):
    """
    Correct a speaker attribution in an enriched transcript segment.

    This is the human correction loop: when review_required=true, the Expo UI
    shows an "Incorrect speaker?" button. When tapped, this endpoint updates
    the speaker_user_id and speaker_name for that segment.

    The original speaker is preserved in original_speaker_user_id if those
    columns exist (migration 021 adds them). The audit table's requires_review
    flag is cleared when all unknown speakers are corrected.
    """
    org_id = user.org_id
    room = await _get_room_by_room_id_or_404(room_id, org_id)

    client = get_supabase_client("service")

    # Verify segment exists and belongs to this room
    existing = client.table("enriched_transcripts").select("id, room_id, org_id").eq("id", segment_id).eq("room_id", room["id"]).maybe_single().execute()
    if not existing.data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transcript segment not found")

    # Build update payload
    payload: Dict[str, Any] = {
        "speaker_user_id": req.speaker_user_id,
        "speaker_name": req.speaker_name or (
            client.table("users").select("email").eq("id", req.speaker_user_id).maybe_single().execute().data["email"].split("@")[0]
            if req.speaker_user_id else "Unknown Speaker"
        ),
        "corrected_by": user.user_id,
        "corrected_at": datetime.utcnow().isoformat(),
    }

    result = client.table("enriched_transcripts").update(payload).eq("id", segment_id).eq("org_id", org_id).execute()

    if not result.data:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to correct transcript segment")

    logger.info(
        "transcript_speaker_corrected",
        segment_id=segment_id,
        room_id=room_id,
        new_speaker_user_id=req.speaker_user_id,
        corrected_by=user.user_id,
    )

    return {"success": True, "segment": result.data[0]}
