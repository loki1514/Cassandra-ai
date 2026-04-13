"""
T10: AssemblyAI Transcription Integration

This module provides transcription services using AssemblyAI API with:
- Speaker diarization enabled
- Exponential backoff with 3 retries
- Rate limit handling
- No PII in logs

Features:
- Async/await for non-blocking I/O
- Comprehensive error handling
- Structured logging (no sensitive data)
"""

import asyncio
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import timedelta

import assemblyai as aai
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)
import structlog

from cassandra.config import settings

logger = structlog.get_logger("cassandra.transcription")


# =============================================================================
# Data Models
# =============================================================================

@dataclass
class SpeakerSegment:
    """
    Represents a single speaker segment from transcription.
    
    Attributes:
        speaker_label: Speaker identifier (e.g., "A", "B", "C")
        text: Transcribed text for this segment
        start_ms: Start time in milliseconds
        end_ms: End time in milliseconds
        confidence: Transcription confidence score (0.0 - 1.0)
    """
    speaker_label: str
    text: str
    start_ms: int
    end_ms: int
    confidence: float


@dataclass
class TranscriptionResult:
    """
    Complete transcription result with all speaker segments.
    
    Attributes:
        segments: List of speaker segments
        duration_ms: Total audio duration in milliseconds
        language: Detected language code
        utterances: Raw utterance count from API
    """
    segments: List[SpeakerSegment]
    duration_ms: int
    language: str
    utterances: int


@dataclass
class TranscriptionConfig:
    """
    Configuration for the transcription pipeline.

    Used to configure audio format, speaker settings, and language options.
    This is a minimal stub — full configuration is passed directly to
    AssemblyAI via the AssemblyAIClient constructor.
    """
    sample_rate: int = 16000
    language_code: str = "en"
    speakers_expected: Optional[int] = None
    enable_diarization: bool = True
    confidence_threshold: float = 0.5


# =============================================================================
# Custom Exceptions
# =============================================================================

class TranscriptionError(Exception):
    """Base exception for transcription errors."""
    pass


class RateLimitError(TranscriptionError):
    """Raised when API rate limit is exceeded."""
    pass


class AuthenticationError(TranscriptionError):
    """Raised when API authentication fails."""
    pass


class AudioFormatError(TranscriptionError):
    """Raised when audio format is invalid."""
    pass


# =============================================================================
# AssemblyAI Client
# =============================================================================

class AssemblyAIClient:
    """
    Client for AssemblyAI transcription API.
    
    Features:
    - Speaker diarization
    - Automatic retries with exponential backoff
    - Rate limit handling
    - Secure credential management
    """
    
    MAX_RETRIES = 3
    RATE_LIMIT_STATUS = 429
    
    def __init__(self):
        self.api_key = settings.assemblyai.api_key
        self.base_url = settings.assemblyai.base_url
        
        # Configure AssemblyAI SDK
        aai.settings.api_key = self.api_key
        
        # Initialize transcriber with speaker diarization
        self.transcriber = aai.Transcriber()
        
        logger.info(
            "assemblyai_client_initialized",
            base_url=self.base_url,
            diarization_enabled=settings.assemblyai.enable_speaker_diarization
        )
    
    def _create_config(self) -> aai.TranscriptionConfig:
        """Create transcription configuration with speaker diarization."""
        config = aai.TranscriptionConfig(
            language_code=settings.assemblyai.default_language,
            speaker_labels=settings.assemblyai.enable_speaker_diarization,
            punctuate=True,
            format_text=True,
        )
        
        if settings.assemblyai.speakers_expected:
            config.speakers_expected = settings.assemblyai.speakers_expected
        
        return config
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((
            httpx.HTTPStatusError,
            httpx.NetworkError,
            httpx.TimeoutException,
            aai.TranscriptError
        )),
        before_sleep=before_sleep_log(logger, "warning")
    )
    async def transcribe(
        self,
        audio_bytes: bytes,
        org_id: Optional[str] = None
    ) -> TranscriptionResult:
        """
        Transcribe audio bytes with speaker diarization.
        
        Args:
            audio_bytes: Raw audio data (supports WAV, MP3, etc.)
            org_id: Organization ID for logging (no PII)
            
        Returns:
            TranscriptionResult with speaker segments
            
        Raises:
            TranscriptionError: On transcription failure
            RateLimitError: On rate limit exceeded
            AuthenticationError: On authentication failure
        """
        start_time = time.time()
        
        try:
            # Log transcription request (no audio content)
            logger.info(
                "transcription_started",
                audio_size_bytes=len(audio_bytes),
                org_id=org_id,
                diarization_enabled=settings.assemblyai.enable_speaker_diarization
            )
            
            # Create transcription config
            config = self._create_config()
            
            # Upload audio and transcribe
            # Note: AssemblyAI SDK is synchronous, run in thread pool
            loop = asyncio.get_event_loop()
            
            def _transcribe():
                # Upload audio
                upload_url = self.transcriber.upload_file(audio_bytes)
                
                # Submit transcription
                transcript = self.transcriber.transcribe(
                    audio_url=upload_url,
                    config=config
                )
                
                return transcript
            
            transcript = await loop.run_in_executor(None, _transcribe)
            
            # Check for errors
            if transcript.error:
                raise TranscriptionError(f"Transcription failed: {transcript.error}")
            
            # Process results
            result = self._process_transcript(transcript)
            
            duration = time.time() - start_time
            logger.info(
                "transcription_completed",
                duration_seconds=round(duration, 2),
                segments_count=len(result.segments),
                org_id=org_id,
                audio_duration_ms=result.duration_ms
            )
            
            return result
            
        except aai.AuthenticationError as e:
            logger.error(
                "transcription_auth_error",
                error_type="AuthenticationError",
                org_id=org_id
            )
            raise AuthenticationError("AssemblyAI authentication failed") from e
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == self.RATE_LIMIT_STATUS:
                logger.warning(
                    "transcription_rate_limited",
                    org_id=org_id,
                    retry_after=e.response.headers.get("Retry-After", "unknown")
                )
                raise RateLimitError("Rate limit exceeded. Please retry.") from e
            raise TranscriptionError(f"HTTP error: {e.response.status_code}") from e
            
        except Exception as e:
            logger.error(
                "transcription_error",
                error_type=type(e).__name__,
                org_id=org_id
            )
            raise TranscriptionError(f"Transcription failed: {str(e)}") from e
    
    def _process_transcript(self, transcript: aai.Transcript) -> TranscriptionResult:
        """
        Process AssemblyAI transcript into our data model.
        
        Args:
            transcript: AssemblyAI transcript object
            
        Returns:
            TranscriptionResult with speaker segments
        """
        segments: List[SpeakerSegment] = []
        
        if transcript.utterances:
            # Process utterances with speaker labels
            for utterance in transcript.utterances:
                segment = SpeakerSegment(
                    speaker_label=utterance.speaker or "UNKNOWN",
                    text=utterance.text or "",
                    start_ms=utterance.start,
                    end_ms=utterance.end,
                    confidence=getattr(utterance, 'confidence', 0.0) or 0.0
                )
                segments.append(segment)
        elif transcript.words:
            # Fallback: Process words without speaker labels
            # Group consecutive words
            current_text = []
            current_start = None
            current_end = None
            
            for word in transcript.words:
                if current_start is None:
                    current_start = word.start
                current_text.append(word.text)
                current_end = word.end
            
            if current_text:
                segment = SpeakerSegment(
                    speaker_label="UNKNOWN",
                    text=" ".join(current_text),
                    start_ms=current_start or 0,
                    end_ms=current_end or 0,
                    confidence=0.0
                )
                segments.append(segment)
        else:
            # No utterances or words - use full text
            segment = SpeakerSegment(
                speaker_label="UNKNOWN",
                text=transcript.text or "",
                start_ms=0,
                end_ms=transcript.audio_duration or 0,
                confidence=0.0
            )
            segments.append(segment)
        
        return TranscriptionResult(
            segments=segments,
            duration_ms=transcript.audio_duration or 0,
            language=transcript.language_code or settings.assemblyai.default_language,
            utterances=len(transcript.utterances) if transcript.utterances else 0
        )


# =============================================================================
# Convenience Functions
# =============================================================================

# Global client instance (lazy initialization)
_client: Optional[AssemblyAIClient] = None


def get_client() -> AssemblyAIClient:
    """Get or create AssemblyAI client instance."""
    global _client
    if _client is None:
        _client = AssemblyAIClient()
    return _client


async def transcribe(
    audio_bytes: bytes,
    org_id: Optional[str] = None
) -> List[SpeakerSegment]:
    """
    Transcribe audio bytes and return speaker segments.
    
    This is the main entry point for transcription.
    
    Args:
        audio_bytes: Raw audio data
        org_id: Organization ID for scoping (optional)
        
    Returns:
        List of SpeakerSegment objects
        
    Example:
        >>> segments = await transcribe(audio_bytes, org_id="org_123")
        >>> for seg in segments:
        ...     print(f"{seg.speaker_label}: {seg.text}")
    """
    client = get_client()
    result = await client.transcribe(audio_bytes, org_id)
    return result.segments


async def transcribe_with_metadata(
    audio_bytes: bytes,
    org_id: Optional[str] = None
) -> TranscriptionResult:
    """
    Transcribe audio bytes and return full result with metadata.
    
    Args:
        audio_bytes: Raw audio data
        org_id: Organization ID for scoping (optional)
        
    Returns:
        TranscriptionResult with segments and metadata
    """
    client = get_client()
    return await client.transcribe(audio_bytes, org_id)


# =============================================================================
# Batch Processing
# =============================================================================

async def transcribe_batch(
    audio_segments: List[bytes],
    org_id: Optional[str] = None,
    max_concurrent: int = 3
) -> List[List[SpeakerSegment]]:
    """
    Transcribe multiple audio segments concurrently.
    
    Args:
        audio_segments: List of audio byte arrays
        org_id: Organization ID for scoping
        max_concurrent: Maximum concurrent transcription requests
        
    Returns:
        List of speaker segment lists (one per input segment)
    """
    semaphore = asyncio.Semaphore(max_concurrent)
    
    async def _transcribe_with_limit(audio: bytes) -> List[SpeakerSegment]:
        async with semaphore:
            return await transcribe(audio, org_id)
    
    tasks = [_transcribe_with_limit(audio) for audio in audio_segments]
    return await asyncio.gather(*tasks, return_exceptions=True)


# =============================================================================
# Webhook Handler (for async transcription)
# =============================================================================

def verify_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Verify AssemblyAI webhook signature.
    
    Args:
        payload: Raw webhook payload bytes
        signature: Signature header value
        
    Returns:
        True if signature is valid
    """
    import hmac
    import hashlib
    
    webhook_secret = settings.assemblyai.webhook_secret
    if not webhook_secret:
        logger.warning("webhook_secret_not_configured")
        return False
    
    expected = hmac.new(
        webhook_secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(expected, signature)


def handle_webhook(payload: Dict[str, Any]) -> Optional[TranscriptionResult]:
    """
    Handle AssemblyAI webhook callback.
    
    Args:
        payload: Webhook payload dictionary
        
    Returns:
        TranscriptionResult if completed, None otherwise
    """
    status = payload.get("status")
    transcript_id = payload.get("transcript_id")
    
    logger.info(
        "webhook_received",
        transcript_id=transcript_id,
        status=status
    )
    
    if status == "completed":
        # Fetch full transcript
        client = get_client()
        transcript = aai.Transcript.get_by_id(transcript_id)
        return client._process_transcript(transcript)
    
    elif status == "error":
        error_msg = payload.get("error", "Unknown error")
        logger.error(
            "webhook_transcription_error",
            transcript_id=transcript_id,
            error=error_msg
        )
        raise TranscriptionError(f"Transcription failed: {error_msg}")
    
    return None
