"""
T42: Voice Enrollment (DB-backed, Supabase-powered)

This module provides voice enrollment functionality:
- Speaker enrollment flow
- Voice embedding storage in voice_profiles (VECTOR(512))
- Speaker verification
- Voice profile management

Features:
- Multi-sample enrollment
- Embedding extraction via Pyannote (async, background-only)
- Profile management via Supabase REST
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import numpy as np
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from cassandra.auth import get_current_user, UserContext
from cassandra.supabase import get_supabase_client

logger = structlog.get_logger("cassandra.voice_enrollment")

router = APIRouter(prefix="/api/v1/voice-enroll", tags=["Voice Enrollment"])


class EnrollmentStatus(str, Enum):
    """Enrollment status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class VoiceEnrollmentRequest(BaseModel):
    """Request body for voice enrollment."""
    audio_samples: List[bytes] = Field(..., min_items=1, max_items=10, description="3-5 audio samples, 5-10s each")


class VoiceEnrollmentResponse(BaseModel):
    """Response for voice enrollment."""
    success: bool
    profile_id: Optional[str] = None
    user_id: Optional[str] = None
    samples_processed: int = 0
    quality_score: float = 0.0
    status: str = "pending"
    errors: List[str] = []


class VoiceProfileStatusResponse(BaseModel):
    """Response for enrollment status check."""
    enrolled: bool
    profile_id: Optional[str] = None
    quality_score: Optional[float] = None
    sample_count: int = 0
    status: str = "inactive"


# =============================================================================
# Core Enrollment Logic
# =============================================================================

def _generate_profile_id(user_id: str) -> str:
    """Generate profile ID from user ID."""
    return f"vp_{hashlib.sha256(user_id.encode()).hexdigest()[:20]}"


async def _extract_embedding(audio_data: bytes) -> Optional[List[float]]:
    """Extract voice embedding from audio using pyannote."""
    try:
        from cassandra.speaker_id import extract_embedding
        embedding = await extract_embedding(audio_data)
        return embedding.tolist()
    except Exception as e:
        logger.error("embedding_extraction_failed", error=str(e))
        return None


async def _calculate_quality_score(audio_data: bytes) -> float:
    """Calculate audio quality score based on duration, RMS, and ZCR."""
    try:
        audio_array = np.frombuffer(audio_data, dtype=np.int16)
        duration_sec = len(audio_array) / 16000

        if duration_sec < 3:
            duration_score = 0.3
        elif duration_sec < 5:
            duration_score = 0.6
        elif duration_sec <= 10:
            duration_score = 1.0
        else:
            duration_score = 0.8

        rms = np.sqrt(np.mean(audio_array.astype(np.float32) ** 2))
        if rms < 100:
            rms_score = 0.2
        elif rms < 500:
            rms_score = 0.5
        elif rms <= 5000:
            rms_score = 1.0
        elif rms <= 10000:
            rms_score = 0.7
        else:
            rms_score = 0.3

        zero_crossings = np.sum(np.diff(np.signbit(audio_array).astype(int)) != 0)
        zcr = zero_crossings / len(audio_array)
        if zcr < 0.02:
            zcr_score = 0.4
        elif zcr <= 0.2:
            zcr_score = 1.0
        else:
            zcr_score = 0.5

        return round((duration_score * 0.4 + rms_score * 0.4 + zcr_score * 0.2), 2)
    except Exception as e:
        logger.error("quality_calculation_failed", error=str(e))
        return 0.5


async def _save_profile_to_supabase(
    profile_id: str,
    user_id: str,
    org_id: str,
    embedding: List[float],
    sample_count: int,
    quality_score: float,
    status: str = "active"
) -> bool:
    """Upsert voice profile into Supabase voice_profiles table."""
    client = get_supabase_client("service")
    try:
        result = client.table("voice_profiles").upsert({
            "profile_id": profile_id,
            "user_id": user_id,
            "org_id": org_id,
            "embedding": embedding,
            "status": status,
            "sample_count": sample_count,
            "quality_score": quality_score,
            "enrolled_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat(),
            "metadata": {
                "sample_count": sample_count,
                "avg_quality": quality_score
            }
        }).execute()
        return bool(result.data)
    except Exception as e:
        logger.error("save_profile_failed", error=str(e))
        return False


async def _get_profile_from_supabase(user_id: str, org_id: str) -> Optional[Dict[str, Any]]:
    """Fetch voice profile for a user from Supabase."""
    client = get_supabase_client("service")
    result = client.table("voice_profiles").select("*").eq("user_id", user_id).eq("org_id", org_id).maybe_single().execute()
    return result.data


# =============================================================================
# API Endpoints
# =============================================================================

@router.post("", response_model=VoiceEnrollmentResponse)
async def enroll_voice(
    req: VoiceEnrollmentRequest,
    user: UserContext = Depends(get_current_user)
):
    """
    Enroll the current user's voice profile.
    Upload 3-5 audio samples (5-10 seconds each) for embedding extraction.
    """
    user_id = user.user_id
    org_id = user.org_id
    num_samples = len(req.audio_samples)

    if num_samples < 1:
        raise HTTPException(status_code=400, detail="At least 1 audio sample required")

    profile_id = _generate_profile_id(user_id)
    logger.info("starting_enrollment", user_id=user_id, profile_id=profile_id, sample_count=num_samples)

    embeddings = []
    total_quality = 0.0
    errors = []

    for i, audio_data in enumerate(req.audio_samples):
        quality = await _calculate_quality_score(audio_data)
        total_quality += quality
        embedding = await _extract_embedding(audio_data)
        if embedding is None:
            errors.append(f"Failed to extract embedding from sample {i+1}")
            continue
        embeddings.append(embedding)

    if not embeddings:
        return VoiceEnrollmentResponse(success=False, errors=errors + ["No valid embeddings extracted"])

    avg_embedding = np.mean(embeddings, axis=0).tolist()
    quality_score = total_quality / len(embeddings)

    saved = await _save_profile_to_supabase(
        profile_id=profile_id,
        user_id=user_id,
        org_id=org_id,
        embedding=avg_embedding,
        sample_count=len(embeddings),
        quality_score=quality_score,
        status="active"
    )

    if not saved:
        return VoiceEnrollmentResponse(success=False, errors=errors + ["Failed to save profile to database"])

    logger.info("enrollment_completed", profile_id=profile_id, samples_processed=len(embeddings))

    return VoiceEnrollmentResponse(
        success=True,
        profile_id=profile_id,
        user_id=user_id,
        samples_processed=len(embeddings),
        quality_score=quality_score,
        status="completed",
        errors=errors
    )


@router.get("/status", response_model=VoiceProfileStatusResponse)
async def get_enrollment_status(
    user: UserContext = Depends(get_current_user)
):
    """Get the current user's voice enrollment status."""
    profile = await _get_profile_from_supabase(user.user_id, user.org_id)
    if not profile:
        return VoiceProfileStatusResponse(enrolled=False, status="inactive")

    return VoiceProfileStatusResponse(
        enrolled=profile.get("status") == "active",
        profile_id=profile.get("profile_id"),
        quality_score=profile.get("quality_score"),
        sample_count=profile.get("sample_count", 0),
        status=profile.get("status", "inactive")
    )


@router.delete("", status_code=status.HTTP_200_OK)
async def delete_enrollment(
    user: UserContext = Depends(get_current_user)
):
    """Soft-delete the current user's voice enrollment (set status to inactive)."""
    client = get_supabase_client("service")
    result = client.table("voice_profiles").update({
        "status": "inactive",
        "updated_at": datetime.utcnow().isoformat()
    }).eq("user_id", user.user_id).eq("org_id", user.org_id).execute()

    if not result.data:
        raise HTTPException(status_code=404, detail="No active voice enrollment found")

    logger.info("voice_enrollment_deleted", user_id=user.user_id, org_id=user.org_id)
    return {"deleted": True, "user_id": user.user_id}
