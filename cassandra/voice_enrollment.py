"""
T42: Voice Enrollment

This module provides voice enrollment functionality:
- Speaker enrollment flow
- Voice embedding storage
- Speaker verification
- Voice profile management

Features:
- Multi-sample enrollment
- Embedding extraction
- Profile management
"""

import hashlib
import io
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, Any, List

import numpy as np
import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.voice_enrollment")


class EnrollmentStatus(str, Enum):
    """Enrollment status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class VoiceSample:
    """Voice sample for enrollment."""
    sample_id: str
    audio_data: bytes
    duration_ms: int
    quality_score: float
    created_at: datetime


@dataclass
class VoiceProfile:
    """Speaker voice profile."""
    profile_id: str
    user_id: str
    org_id: str
    embedding: List[float]
    samples: List[VoiceSample]
    status: EnrollmentStatus
    created_at: datetime
    updated_at: datetime
    metadata: Dict[str, Any]


class VoiceEnrollmentInput(BaseModel):
    """Input for voice enrollment."""
    
    user_id: str = Field(..., description="User ID to enroll")
    org_id: str = Field(..., description="Organization ID")
    audio_samples: List[bytes] = Field(..., description="Audio samples for enrollment")
    min_samples: int = Field(default=3, description="Minimum samples required")
    max_samples: int = Field(default=5, description="Maximum samples allowed")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_abc123",
                "org_id": "org_12345",
                "min_samples": 3,
                "max_samples": 5
            }
        }


class VoiceEnrollmentResult(BaseModel):
    """Result of voice enrollment."""
    
    success: bool
    profile_id: Optional[str] = None
    user_id: Optional[str] = None
    samples_processed: int = 0
    quality_score: float = 0.0
    status: EnrollmentStatus = EnrollmentStatus.PENDING
    errors: List[str] = []


class VoiceEnrollmentManager:
    """
    Manages voice enrollment and speaker profiles.
    
    Usage:
        manager = VoiceEnrollmentManager(db_pool)
        
        # Enroll user
        result = await manager.enroll(
            user_id="user_123",
            org_id="org_456",
            audio_samples=[sample1, sample2, sample3]
        )
        
        # Verify speaker
        match = await manager.verify(
            profile_id="profile_789",
            audio_sample=test_sample
        )
    """
    
    def __init__(self, db_pool: Any):
        """
        Initialize enrollment manager.
        
        Args:
            db_pool: Database connection pool
        """
        self.db_pool = db_pool
        
        logger.info("voice_enrollment_manager_initialized")
    
    def _generate_profile_id(self, user_id: str) -> str:
        """Generate profile ID from user ID."""
        return f"vp_{hashlib.sha256(user_id.encode()).hexdigest()[:20]}"
    
    async def _extract_embedding(self, audio_data: bytes) -> Optional[List[float]]:
        """
        Extract voice embedding from audio.
        
        Args:
            audio_data: Audio bytes
            
        Returns:
            Embedding vector or None if extraction fails
        """
        # This would integrate with actual speaker embedding model
        # For now, return a placeholder
        
        try:
            # Placeholder: Generate random embedding
            # In production, use actual speaker embedding model
            embedding = np.random.randn(256).tolist()
            return embedding
        except Exception as e:
            logger.error("embedding_extraction_failed", error=str(e))
            return None
    
    async def _calculate_quality_score(self, audio_data: bytes) -> float:
        """
        Calculate audio quality score.
        
        Args:
            audio_data: Audio bytes
            
        Returns:
            Quality score (0.0 - 1.0)
        """
        # Placeholder quality calculation
        # In production, analyze SNR, clipping, background noise, etc.
        
        min_length = 16000 * 2 * 3  # 3 seconds at 16kHz, 16-bit
        
        if len(audio_data) < min_length:
            return 0.3  # Too short
        
        # Simulate quality check
        return 0.85
    
    async def enroll(self, input_data: VoiceEnrollmentInput) -> VoiceEnrollmentResult:
        """
        Enroll a user with voice samples.
        
        Args:
            input_data: Enrollment input
            
        Returns:
            Enrollment result
        """
        result = VoiceEnrollmentResult(success=False)
        
        # Validate sample count
        num_samples = len(input_data.audio_samples)
        
        if num_samples < input_data.min_samples:
            result.errors.append(
                f"Insufficient samples: {num_samples} < {input_data.min_samples}"
            )
            return result
        
        if num_samples > input_data.max_samples:
            result.errors.append(
                f"Too many samples: {num_samples} > {input_data.max_samples}"
            )
            return result
        
        profile_id = self._generate_profile_id(input_data.user_id)
        
        logger.info(
            "starting_enrollment",
            user_id=input_data.user_id,
            profile_id=profile_id,
            sample_count=num_samples
        )
        
        # Process samples
        samples = []
        embeddings = []
        total_quality = 0.0
        
        for i, audio_data in enumerate(input_data.audio_samples):
            # Calculate quality
            quality = await self._calculate_quality_score(audio_data)
            total_quality += quality
            
            # Extract embedding
            embedding = await self._extract_embedding(audio_data)
            
            if embedding is None:
                result.errors.append(f"Failed to extract embedding from sample {i+1}")
                continue
            
            # Create sample record
            sample = VoiceSample(
                sample_id=f"{profile_id}_sample_{i}",
                audio_data=audio_data,
                duration_ms=len(audio_data) // 32,  # Approximate
                quality_score=quality,
                created_at=datetime.utcnow()
            )
            
            samples.append(sample)
            embeddings.append(embedding)
        
        if len(embeddings) < input_data.min_samples:
            result.errors.append("Too few valid embeddings extracted")
            return result
        
        # Average embeddings
        avg_embedding = np.mean(embeddings, axis=0).tolist()
        
        # Create profile
        profile = VoiceProfile(
            profile_id=profile_id,
            user_id=input_data.user_id,
            org_id=input_data.org_id,
            embedding=avg_embedding,
            samples=samples,
            status=EnrollmentStatus.COMPLETED,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            metadata={
                "sample_count": len(samples),
                "avg_quality": total_quality / len(samples)
            }
        )
        
        # Save to database
        await self._save_profile(profile)
        
        result.success = True
        result.profile_id = profile_id
        result.user_id = input_data.user_id
        result.samples_processed = len(samples)
        result.quality_score = total_quality / len(samples)
        result.status = EnrollmentStatus.COMPLETED
        
        logger.info(
            "enrollment_completed",
            profile_id=profile_id,
            samples_processed=len(samples)
        )
        
        return result
    
    async def _save_profile(self, profile: VoiceProfile):
        """Save voice profile to database."""
        async with self.db_pool.acquire() as conn:
            # Save profile
            await conn.execute(
                """
                INSERT INTO voice_profiles (
                    profile_id, user_id, org_id, embedding, status,
                    created_at, updated_at, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (profile_id) DO UPDATE SET
                    embedding = EXCLUDED.embedding,
                    status = EXCLUDED.status,
                    updated_at = EXCLUDED.updated_at,
                    metadata = EXCLUDED.metadata
                """,
                profile.profile_id,
                profile.user_id,
                profile.org_id,
                profile.embedding,
                profile.status.value,
                profile.created_at,
                profile.updated_at,
                profile.metadata
            )
            
            # Save samples
            for sample in profile.samples:
                await conn.execute(
                    """
                    INSERT INTO voice_samples (
                        sample_id, profile_id, audio_data, duration_ms,
                        quality_score, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (sample_id) DO NOTHING
                    """,
                    sample.sample_id,
                    profile.profile_id,
                    sample.audio_data,
                    sample.duration_ms,
                    sample.quality_score,
                    sample.created_at
                )
    
    async def verify(
        self,
        profile_id: str,
        audio_sample: bytes,
        threshold: float = 0.7
    ) -> Dict[str, Any]:
        """
        Verify speaker against profile.
        
        Args:
            profile_id: Profile ID to verify against
            audio_sample: Audio sample to verify
            threshold: Similarity threshold (0.0 - 1.0)
            
        Returns:
            Verification result
        """
        # Get profile
        profile = await self._get_profile(profile_id)
        
        if not profile:
            return {
                "verified": False,
                "confidence": 0.0,
                "error": "Profile not found"
            }
        
        # Extract embedding from sample
        sample_embedding = await self._extract_embedding(audio_sample)
        
        if sample_embedding is None:
            return {
                "verified": False,
                "confidence": 0.0,
                "error": "Failed to extract embedding"
            }
        
        # Calculate similarity
        similarity = self._calculate_similarity(
            profile.embedding,
            sample_embedding
        )
        
        verified = similarity >= threshold
        
        return {
            "verified": verified,
            "confidence": similarity,
            "threshold": threshold,
            "profile_id": profile_id,
            "user_id": profile.user_id
        }
    
    def _calculate_similarity(
        self,
        embedding1: List[float],
        embedding2: List[float]
    ) -> float:
        """
        Calculate cosine similarity between embeddings.
        
        Args:
            embedding1: First embedding
            embedding2: Second embedding
            
        Returns:
            Similarity score (0.0 - 1.0)
        """
        vec1 = np.array(embedding1)
        vec2 = np.array(embedding2)
        
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        similarity = dot_product / (norm1 * norm2)
        
        # Normalize to 0-1 range
        return (similarity + 1) / 2
    
    async def _get_profile(self, profile_id: str) -> Optional[VoiceProfile]:
        """Get voice profile from database."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM voice_profiles WHERE profile_id = $1",
                profile_id
            )
            
            if not row:
                return None
            
            return VoiceProfile(
                profile_id=row["profile_id"],
                user_id=row["user_id"],
                org_id=row["org_id"],
                embedding=row["embedding"],
                samples=[],  # Would load samples if needed
                status=EnrollmentStatus(row["status"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                metadata=row.get("metadata", {})
            )
    
    async def delete_profile(self, profile_id: str) -> bool:
        """
        Delete voice profile.
        
        Args:
            profile_id: Profile ID to delete
            
        Returns:
            True if deleted
        """
        async with self.db_pool.acquire() as conn:
            # Delete samples first
            await conn.execute(
                "DELETE FROM voice_samples WHERE profile_id = $1",
                profile_id
            )
            
            # Delete profile
            result = await conn.execute(
                "DELETE FROM voice_profiles WHERE profile_id = $1",
                profile_id
            )
            
            deleted = result == "DELETE 1"
            
            if deleted:
                logger.info("profile_deleted", profile_id=profile_id)
            
            return deleted


# =============================================================================
# Database Schema
# =============================================================================

VOICE_ENROLLMENT_SCHEMA = """
-- Voice profiles table
CREATE TABLE IF NOT EXISTS voice_profiles (
    profile_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL,
    org_id VARCHAR(32) NOT NULL,
    embedding VECTOR(256),  -- Requires pgvector extension
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (org_id) REFERENCES organizations(org_id),
    UNIQUE (user_id)
);

-- Voice samples table
CREATE TABLE IF NOT EXISTS voice_samples (
    sample_id VARCHAR(32) PRIMARY KEY,
    profile_id VARCHAR(32) NOT NULL,
    audio_data BYTEA NOT NULL,
    duration_ms INTEGER NOT NULL,
    quality_score FLOAT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    FOREIGN KEY (profile_id) REFERENCES voice_profiles(profile_id) ON DELETE CASCADE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_voice_profiles_org ON voice_profiles(org_id);
CREATE INDEX IF NOT EXISTS idx_voice_profiles_user ON voice_profiles(user_id);
CREATE INDEX IF NOT EXISTS idx_voice_samples_profile ON voice_samples(profile_id);
"""
