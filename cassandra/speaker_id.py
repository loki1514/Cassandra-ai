"""
T11: Pyannote Diarization + Voice Embedding Match

This module provides speaker identification using:
- Pyannote.audio for speaker diarization and embedding extraction
- Cosine similarity for speaker matching
- Threshold-based unknown speaker detection
- Lazy loading of models for memory efficiency

Features:
- 512-dim embedding vectors
- Cosine similarity matching
- Confidence threshold: < 0.85 = unknown_speaker
- Lazy model loading
"""

import asyncio
import hashlib
from typing import Optional, List, Dict, Tuple, Any
from dataclasses import dataclass
from pathlib import Path
import io
import tempfile
import os

import numpy as np
from numpy.linalg import norm
import structlog

logger = structlog.get_logger("cassandra.speaker_id")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class SpeakerMatch:
    """
    Result of speaker matching.
    
    Attributes:
        speaker_id: Matched speaker ID or 'unknown_speaker'
        confidence: Match confidence score (0.0 - 1.0)
        embedding: The 512-dim embedding vector
        matches: List of top matches with scores
    """
    speaker_id: str
    confidence: float
    embedding: np.ndarray
    matches: List[Dict[str, Any]]


@dataclass
class DiarizationSegment:
    """
    Speaker diarization segment.
    
    Attributes:
        speaker_label: Speaker label (e.g., "SPEAKER_00")
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        embedding: 512-dim speaker embedding
    """
    speaker_label: str
    start_ms: float
    end_ms: float
    embedding: Optional[np.ndarray] = None


# =============================================================================
# Custom Exceptions
# =============================================================================

class SpeakerIDError(Exception):
    """Base exception for speaker identification errors."""
    pass


class ModelLoadError(SpeakerIDError):
    """Raised when model loading fails."""
    pass


class EmbeddingError(SpeakerIDError):
    """Raised when embedding extraction fails."""
    pass


# =============================================================================
# Model Manager (Lazy Loading)
# =============================================================================

class ModelManager:
    """
    Manages lazy loading of Pyannote models.
    
    Models are loaded on first use to minimize memory footprint
    and startup time.
    """
    
    _instance: Optional["ModelManager"] = None
    _lock = asyncio.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._pipeline = None
        self._embedding_model = None
        self._models_loaded = False
        self._initialized = True
        
        logger.info("model_manager_initialized")
    
    @property
    def is_loaded(self) -> bool:
        """Check if models are loaded."""
        return self._models_loaded
    
    async def load_models(self):
        """Load Pyannote models (thread-safe)."""
        if self._models_loaded:
            return
        
        async with self._lock:
            if self._models_loaded:
                return
            
            try:
                logger.info("loading_pyannote_models")
                
                # Run model loading in thread pool (CPU intensive)
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_models_sync)
                
                self._models_loaded = True
                logger.info("pyannote_models_loaded")
                
            except Exception as e:
                logger.error(
                    "model_load_error",
                    error_type=type(e).__name__,
                    error_message=str(e)
                )
                raise ModelLoadError(f"Failed to load models: {str(e)}") from e
    
    def _load_models_sync(self):
        """Synchronous model loading (runs in thread pool)."""
        from pyannote.audio import Pipeline
        from pyannote.audio import Model as EmbeddingModel
        
        # Load speaker diarization pipeline
        # Uses pyannote/speaker-diarization-3.1 by default
        self._pipeline = Pipeline.from_pretrained(
            "pyannote/speaker-diarization-3.1",
            use_auth_token=os.getenv("HUGGINGFACE_TOKEN")
        )
        
        # Load embedding model
        # Uses pyannote/wespeaker-voxceleb-resnet34-LM for 512-dim embeddings
        self._embedding_model = EmbeddingModel.from_pretrained(
            "pyannote/wespeaker-voxceleb-resnet34-LM",
            use_auth_token=os.getenv("HUGGINGFACE_TOKEN")
        )
    
    def get_pipeline(self):
        """Get the diarization pipeline (loads if needed)."""
        if not self._models_loaded:
            raise ModelLoadError("Models not loaded. Call load_models() first.")
        return self._pipeline
    
    def get_embedding_model(self):
        """Get the embedding model (loads if needed)."""
        if not self._models_loaded:
            raise ModelLoadError("Models not loaded. Call load_models() first.")
        return self._embedding_model


# Global model manager instance
model_manager = ModelManager()


# =============================================================================
# Embedding Extraction
# =============================================================================

async def extract_embedding(audio_segment: bytes) -> np.ndarray:
    """
    Extract 512-dimensional speaker embedding from audio segment.
    
    Args:
        audio_segment: Raw audio bytes (WAV format preferred)
        
    Returns:
        512-dimensional numpy array (normalized)
        
    Raises:
        EmbeddingError: If extraction fails
        ModelLoadError: If models not loaded
        
    Example:
        >>> embedding = await extract_embedding(audio_bytes)
        >>> print(embedding.shape)  # (512,)
        >>> print(embedding.dtype)  # float32
    """
    # Ensure models are loaded
    if not model_manager.is_loaded:
        await model_manager.load_models()
    
    try:
        # Write audio to temporary file (pyannote requires file path)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(audio_segment)
        
        try:
            # Extract embedding in thread pool (CPU intensive)
            loop = asyncio.get_event_loop()
            embedding = await loop.run_in_executor(
                None,
                lambda: model_manager.get_embedding_model()(tmp_path)
            )
            
            # Convert to numpy and flatten
            if hasattr(embedding, 'numpy'):
                embedding = embedding.numpy()
            embedding = np.array(embedding).flatten()
            
            # Ensure 512 dimensions
            if embedding.shape[0] != 512:
                logger.warning(
                    "unexpected_embedding_dimension",
                    expected=512,
                    actual=embedding.shape[0]
                )
                # Pad or truncate to 512
                if embedding.shape[0] < 512:
                    embedding = np.pad(embedding, (0, 512 - embedding.shape[0]))
                else:
                    embedding = embedding[:512]
            
            # Normalize to unit vector
            embedding = embedding / (norm(embedding) + 1e-8)
            
            logger.debug(
                "embedding_extracted",
                dimension=embedding.shape[0],
                norm=float(norm(embedding))
            )
            
            return embedding.astype(np.float32)
            
        finally:
            # Cleanup temporary file
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    except Exception as e:
        logger.error(
            "embedding_extraction_error",
            error_type=type(e).__name__,
            audio_size=len(audio_segment)
        )
        raise EmbeddingError(f"Failed to extract embedding: {str(e)}") from e


def extract_embedding_sync(audio_segment: bytes) -> np.ndarray:
    """
    Synchronous version of extract_embedding for batch processing.
    
    Args:
        audio_segment: Raw audio bytes
        
    Returns:
        512-dimensional numpy array
    """
    import asyncio
    return asyncio.run(extract_embedding(audio_segment))


# =============================================================================
# Speaker Matching
# =============================================================================

# Default similarity threshold
SIMILARITY_THRESHOLD = 0.85

# Database-backed speaker storage
# Uses Supabase for persistence across restarts and horizontal scaling
from cassera.config import settings


class SpeakerDatabase:
    """
    Database-backed speaker storage using Supabase.
    
    Replaces in-memory storage for production use.
    Supports horizontal scaling and persistence across restarts.
    """
    
    def __init__(self):
        self.supabase_url = settings.SUPABASE_URL
        self.supabase_key = settings.SUPABASE_SERVICE_ROLE_KEY
        self._client = None
    
    @property
    def client(self):
        """Lazy-load Supabase client."""
        if self._client is None:
            from supabase import create_client
            self._client = create_client(self.supabase_url, self.supabase_key)
        return self._client
    
    async def get_speakers(self, org_id: str) -> Dict[str, np.ndarray]:
        """Get all speakers for an organization."""
        try:
            result = self.client.table("speaker_embeddings")\
                .select("speaker_id, embedding")\
                .eq("org_id", org_id)\
                .execute()
            
            speakers = {}
            for row in result.data:
                embedding = np.array(row["embedding"], dtype=np.float32)
                speakers[row["speaker_id"]] = embedding
            
            return speakers
        except Exception as e:
            logger.error("failed_to_load_speakers", org_id=org_id, error=str(e))
            return {}
    
    async def store_speaker(self, org_id: str, speaker_id: str, 
                           embedding: np.ndarray) -> bool:
        """Store or update a speaker embedding."""
        try:
            self.client.table("speaker_embeddings").upsert({
                "org_id": org_id,
                "speaker_id": speaker_id,
                "embedding": embedding.tolist(),
                "updated_at": datetime.now().isoformat()
            }).execute()
            return True
        except Exception as e:
            logger.error("failed_to_store_speaker", 
                        org_id=org_id, speaker_id=speaker_id, error=str(e))
            return False


# Global speaker database instance
_speaker_db = SpeakerDatabase()


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    Calculate cosine similarity between two vectors.
    
    Args:
        a: First vector
        b: Second vector
        
    Returns:
        Cosine similarity score (0.0 - 1.0)
    """
    return float(np.dot(a, b) / (norm(a) * norm(b) + 1e-8))


async def match_speaker(
    embedding: np.ndarray,
    org_id: str,
    threshold: float = SIMILARITY_THRESHOLD
) -> SpeakerMatch:
    """
    Match speaker embedding against known speakers in organization.
    
    Args:
        embedding: 512-dim speaker embedding
        org_id: Organization ID for scoping
        threshold: Minimum similarity for match (default: 0.85)
        
    Returns:
        SpeakerMatch with speaker_id and confidence
        
    Example:
        >>> match = await match_speaker(embedding, org_id="org_123")
        >>> if match.confidence >= 0.85:
        ...     print(f"Matched: {match.speaker_id}")
        ... else:
        ...     print("Unknown speaker")
    """
    # SECURITY: Enforce minimum threshold to prevent "match everything" attacks
    MINIMUM_THRESHOLD = 0.5
    if threshold < MINIMUM_THRESHOLD:
        logger.warning("threshold_too_low_enforced", 
                      requested=threshold, 
                      enforced=MINIMUM_THRESHOLD)
        threshold = MINIMUM_THRESHOLD
    
    # Get organization's speaker database from persistent storage
    org_speakers = await _speaker_db.get_speakers(org_id)
    
    if not org_speakers:
        logger.info(
            "no_speakers_in_org",
            org_id=org_id
        )
        return SpeakerMatch(
            speaker_id="unknown_speaker",
            confidence=0.0,
            embedding=embedding,
            matches=[]
        )
    
    # Calculate similarities
    matches = []
    for speaker_id, stored_embedding in org_speakers.items():
        similarity = cosine_similarity(embedding, stored_embedding)
        matches.append({
            "speaker_id": speaker_id,
            "similarity": similarity
        })
    
    # Sort by similarity (descending)
    matches.sort(key=lambda x: x["similarity"], reverse=True)
    
    # Get best match
    best_match = matches[0] if matches else {"speaker_id": "unknown_speaker", "similarity": 0.0}
    
    # Apply threshold
    if best_match["similarity"] >= threshold:
        speaker_id = best_match["speaker_id"]
        confidence = best_match["similarity"]
    else:
        speaker_id = "unknown_speaker"
        confidence = best_match["similarity"]
    
    logger.info(
        "speaker_match_result",
        org_id=org_id,
        matched_speaker=speaker_id,
        confidence=round(confidence, 4),
        threshold=threshold,
        candidates=len(matches)
    )
    
    return SpeakerMatch(
        speaker_id=speaker_id,
        confidence=confidence,
        embedding=embedding,
        matches=matches[:5]  # Return top 5 matches
    )


async def register_speaker(
    speaker_id: str,
    embedding: np.ndarray,
    org_id: str
) -> bool:
    """
    Register a new speaker in the organization's database.
    
    Args:
        speaker_id: Unique speaker identifier
        embedding: 512-dim speaker embedding
        org_id: Organization ID for scoping
        
    Returns:
        True if registration successful
    """
    if org_id not in _speaker_db:
        _speaker_db[org_id] = {}
    
    # Store normalized embedding
    normalized = embedding / (norm(embedding) + 1e-8)
    _speaker_db[org_id][speaker_id] = normalized.astype(np.float32)
    
    logger.info(
        "speaker_registered",
        speaker_id=speaker_id,
        org_id=org_id,
        total_speakers=len(_speaker_db[org_id])
    )
    
    return True


async def unregister_speaker(speaker_id: str, org_id: str) -> bool:
    """
    Remove a speaker from the organization's database.
    
    Args:
        speaker_id: Speaker identifier to remove
        org_id: Organization ID for scoping
        
    Returns:
        True if removal successful
    """
    if org_id in _speaker_db and speaker_id in _speaker_db[org_id]:
        del _speaker_db[org_id][speaker_id]
        logger.info(
            "speaker_unregistered",
            speaker_id=speaker_id,
            org_id=org_id
        )
        return True
    return False


# =============================================================================
# Diarization
# =============================================================================

async def diarize_audio(audio_bytes: bytes) -> List[DiarizationSegment]:
    """
    Perform speaker diarization on audio.
    
    Args:
        audio_bytes: Raw audio bytes (WAV format)
        
    Returns:
        List of DiarizationSegment with speaker labels and embeddings
    """
    # Ensure models are loaded
    if not model_manager.is_loaded:
        await model_manager.load_models()
    
    try:
        # Write audio to temporary file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            tmp.write(audio_bytes)
        
        try:
            # Run diarization in thread pool
            loop = asyncio.get_event_loop()
            diarization = await loop.run_in_executor(
                None,
                lambda: model_manager.get_pipeline()(tmp_path)
            )
            
            # Convert to segments
            segments = []
            for turn, _, speaker in diarization.itertracks(yield_label=True):
                segment = DiarizationSegment(
                    speaker_label=speaker,
                    start_ms=turn.start * 1000,  # Convert to ms
                    end_ms=turn.end * 1000,
                    embedding=None  # Will be extracted separately
                )
                segments.append(segment)
            
            logger.info(
                "diarization_complete",
                segments=len(segments),
                unique_speakers=len(set(s.speaker_label for s in segments))
            )
            
            return segments
            
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
                
    except Exception as e:
        logger.error(
            "diarization_error",
            error_type=type(e).__name__,
            audio_size=len(audio_bytes)
        )
        raise SpeakerIDError(f"Diarization failed: {str(e)}") from e


# =============================================================================
# Utility Functions
# =============================================================================

def get_embedding_hash(embedding: np.ndarray) -> str:
    """
    Generate a hash for an embedding (for caching/comparison).
    
    Args:
        embedding: 512-dim embedding vector
        
    Returns:
        Hex string hash
    """
    # Round to reduce sensitivity to small variations
    rounded = np.round(embedding, decimals=4)
    return hashlib.sha256(rounded.tobytes()).hexdigest()[:16]


def get_org_speakers(org_id: str) -> List[str]:
    """Get list of registered speaker IDs for organization."""
    return list(_speaker_db.get(org_id, {}).keys())


def clear_org_speakers(org_id: str) -> int:
    """Clear all speakers for an organization. Returns count removed."""
    if org_id in _speaker_db:
        count = len(_speaker_db[org_id])
        _speaker_db[org_id] = {}
        logger.info("org_speakers_cleared", org_id=org_id, count=count)
        return count
    return 0
