"""
backend/core/audio_buffer.py — Rolling PCM16 audio buffer with speech segment extraction.

Designed for real-time voice processing:
- Accumulates incoming PCM16 chunks in a rolling buffer (max 30 seconds)
- Tracks speech start/end markers for segment extraction
- Computes RMS energy for VAD integration
- Extracts clean speech segments for STT processing
"""

import struct
from collections import deque
from dataclasses import dataclass, field
from typing import NamedTuple

import numpy as np


# Constants
MAX_BUFFER_SECONDS = 30
SAMPLES_PER_SECOND = 24000


@dataclass
class SpeechMarkers:
    """Marks the boundaries of a detected speech segment."""

    start_sample_index: int
    end_sample_index: int
    duration_ms: float

    def is_valid(self) -> bool:
        return self.end_sample_index > self.start_sample_index


class AudioBufferStats(NamedTuple):
    """Statistics about the current buffer state."""

    total_samples: int
    total_duration_ms: float
    speech_duration_ms: float
    current_rms: float
    has_active_speech: bool


class RollingAudioBuffer:
    """
    A rolling PCM16 audio buffer that accumulates samples and supports
    speech segment extraction.

    Thread-safe for use within a single asyncio task — not thread-safe
    for concurrent writes from multiple tasks.
    """

    def __init__(self, max_seconds: int = MAX_BUFFER_SECONDS):
        """
        Args:
            max_seconds: Maximum duration to keep in the buffer.
                         Older samples are dropped when exceeded.
        """
        self._max_samples = max_seconds * SAMPLES_PER_SECOND
        self._pcm_data: deque[bytes] = deque()
        self._total_bytes = 0

        # Speech markers
        self._speech_start: int | None = None
        self._speech_end: int | None = None
        self._sample_index = 0  # Total samples ever received

        # Rolling window for RMS computation
        self._rms_window_samples = SAMPLES_PER_SECOND  # 1 second window
        self._recent_pcm: list[int] = []

    # ── Core Operations ──────────────────────────────────────

    def append(self, pcm16_bytes: bytes) -> None:
        """
        Append a PCM16 chunk to the buffer.

        Args:
            pcm16_bytes: Raw PCM16 little-endian bytes.
        """
        chunk_samples = len(pcm16_bytes) // 2
        if chunk_samples == 0:
            return

        self._pcm_data.append(pcm16_bytes)
        self._total_bytes += len(pcm16_bytes)
        self._sample_index += chunk_samples

        # Trim if over max size
        while self._total_bytes > self._max_samples * 2:
            oldest = self._pcm_data.popleft()
            self._total_bytes -= len(oldest)

        # Update recent PCM for RMS
        pcm_ints = np.frombuffer(pcm16_bytes, dtype=np.int16).tolist()
        self._recent_pcm.extend(pcm_ints)
        if len(self._recent_pcm) > self._rms_window_samples:
            self._recent_pcm = self._recent_pcm[-self._rms_window_samples:]

    def mark_speech_start(self) -> None:
        """Mark the current position as the start of a speech segment."""
        self._speech_start = self._sample_index
        self._speech_end = None

    def mark_speech_end(self) -> None:
        """Mark the current position as the end of a speech segment."""
        if self._speech_start is not None:
            self._speech_end = self._sample_index

    def get_speech_segment(self) -> bytes | None:
        """
        Extract the current speech segment as PCM16 bytes.

        Returns:
            PCM16 bytes of the speech segment, or None if no valid segment.
        """
        if self._speech_start is None:
            return None

        end_index = self._speech_end or self._sample_index
        if end_index <= self._speech_start:
            return None

        return self._extract_range(self._speech_start, end_index)

    def get_latest_ms(self, ms: int) -> bytes:
        """
        Get the most recent N milliseconds of audio.

        Args:
            ms: Number of milliseconds to retrieve.

        Returns:
            PCM16 bytes of the most recent audio.
        """
        samples = (ms * SAMPLES_PER_SECOND) // 1000
        start_index = max(0, self._sample_index - samples)
        return self._extract_range(start_index, self._sample_index)

    def clear(self) -> None:
        """Clear all buffered audio and reset speech markers."""
        self._pcm_data.clear()
        self._total_bytes = 0
        self._sample_index = 0
        self._speech_start = None
        self._speech_end = None
        self._recent_pcm.clear()

    def clear_speech_markers(self) -> None:
        """Clear speech markers without clearing audio data."""
        self._speech_start = None
        self._speech_end = None

    # ── Analysis ──────────────────────────────────────────────

    def rms_energy(self) -> float:
        """
        Compute RMS energy of the most recent 1-second window.

        Returns:
            RMS value in range [0.0, 1.0] approximately.
        """
        if not self._recent_pcm:
            return 0.0
        arr = np.array(self._recent_pcm, dtype=np.float64)
        return float(np.sqrt(np.mean(arr ** 2)) / 32768.0)

    def stats(self) -> AudioBufferStats:
        """Return current buffer statistics."""
        total_samples = self._sample_index
        total_ms = (total_samples / SAMPLES_PER_SECOND) * 1000

        speech_ms = 0.0
        has_active = False
        if self._speech_start is not None:
            end_idx = self._speech_end or self._sample_index
            speech_samples = max(0, end_idx - self._speech_start)
            speech_ms = (speech_samples / SAMPLES_PER_SECOND) * 1000
            if self._speech_end is None:
                has_active = True

        return AudioBufferStats(
            total_samples=total_samples,
            total_duration_ms=total_ms,
            speech_duration_ms=speech_ms,
            current_rms=self.rms_energy(),
            has_active_speech=has_active,
        )

    # ── Serialization ───────────────────────────────────────

    def to_pcm16_bytes(self) -> bytes:
        """Return the full buffer as PCM16 bytes."""
        return b"".join(self._pcm_data)

    def to_float32(self) -> np.ndarray:
        """Return the full buffer as normalized Float32 array."""
        pcm_bytes = self.to_pcm16_bytes()
        if not pcm_bytes:
            return np.array([], dtype=np.float32)
        pcm_int = np.frombuffer(pcm_bytes, dtype=np.int16)
        return pcm_int.astype(np.float32) / 32768.0

    def to_base64(self) -> str:
        """Return the full buffer as base64-encoded PCM16."""
        import base64
        return base64.b64encode(self.to_pcm16_bytes()).decode("ascii")

    # ── Internal ────────────────────────────────────────────

    def _extract_range(self, start: int, end: int) -> bytes:
        """Extract PCM16 bytes for sample index range [start, end)."""
        result_parts = []
        remaining_start = start
        remaining_end = end

        for chunk_bytes in self._pcm_data:
            chunk_samples = len(chunk_bytes) // 2
            chunk_start = 0
            chunk_end = chunk_samples

            # This chunk's sample indices are relative to when it was added
            # We track cumulative index via the deque ordering
            # For simplicity, extract from full buffer
            pass

        # Simpler approach: reconstruct from full buffer
        full_pcm = self.to_pcm16_bytes()
        full_samples = len(full_pcm) // 2
        start = max(0, start)
        end = min(full_samples, end)

        if end <= start:
            return b""

        # Calculate byte offsets
        byte_start = start * 2
        byte_end = end * 2
        return full_pcm[byte_start:byte_end]

    def __len__(self) -> int:
        """Return the total number of samples in the buffer."""
        return self._sample_index

    def __bool__(self) -> bool:
        """Return True if the buffer has any audio."""
        return self._total_bytes > 0
