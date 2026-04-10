"""
backend/utils/audio.py — Audio format conversion utilities.

Handles conversions between:
- base64 strings
- PCM16 bytes (the format used by OpenAI Realtime API)
- Float32 normalized arrays (for processing)
- Int16 arrays (for scipy operations)

Also provides resampling for converting between sample rates
(e.g., 44.1kHz mobile audio → 24kHz backend format).
"""

import base64
import struct
from typing import Sequence

import numpy as np


# ── PCM16 Conversions ─────────────────────────────────────────

def base64_to_pcm16(b64_string: str) -> bytes:
    """
    Decode a base64 string to raw PCM16 bytes.

    Args:
        b64_string: base64-encoded PCM16 audio.

    Returns:
        Raw PCM16 little-endian bytes.
    """
    if not b64_string:
        return b""
    return base64.b64decode(b64_string)


def pcm16_to_base64(pcm16_bytes: bytes) -> str:
    """
    Encode raw PCM16 bytes to a base64 string.

    Args:
        pcm16_bytes: Raw PCM16 little-endian bytes.

    Returns:
        base64-encoded string.
    """
    if not pcm16_bytes:
        return ""
    return base64.b64encode(pcm16_bytes).decode("ascii")


def pcm16_to_int16(pcm16_bytes: bytes) -> np.ndarray:
    """
    Decode PCM16 bytes to an Int16 numpy array.

    Args:
        pcm16_bytes: Raw PCM16 little-endian bytes.

    Returns:
        numpy array of int16 samples.
    """
    if not pcm16_bytes:
        return np.array([], dtype=np.int16)
    return np.frombuffer(pcm16_bytes, dtype=np.int16)


def int16_to_pcm16(int16_array: np.ndarray) -> bytes:
    """
    Encode an Int16 numpy array to PCM16 bytes.

    Args:
        int16_array: numpy array of int16 samples.

    Returns:
        Raw PCM16 little-endian bytes.
    """
    return int16_array.astype(np.int16).tobytes()


# ── Float32 Conversions ─────────────────────────────────────────

def pcm16_to_float32(pcm16_bytes: bytes) -> np.ndarray:
    """
    Decode PCM16 bytes to a normalized Float32 array.

    Values are in range [-1.0, 1.0].

    Args:
        pcm16_bytes: Raw PCM16 little-endian bytes.

    Returns:
        numpy array of float32 samples in [-1.0, 1.0].
    """
    if not pcm16_bytes:
        return np.array([], dtype=np.float32)
    int16_arr = np.frombuffer(pcm16_bytes, dtype=np.int16)
    return int16_arr.astype(np.float32) / 32768.0


def float32_to_pcm16(float32_array: np.ndarray) -> bytes:
    """
    Encode a Float32 array to PCM16 bytes.

    Values should be in range [-1.0, 1.0]. Values are clipped.

    Args:
        float32_array: numpy array of float32 samples.

    Returns:
        Raw PCM16 little-endian bytes.
    """
    if len(float32_array) == 0:
        return b""

    # Clip to valid range
    clipped = np.clip(float32_array, -1.0, 1.0)
    int16_arr = (clipped * 32767.0).astype(np.int16)
    return int16_arr.tobytes()


def float32_to_base64(float32_array: np.ndarray) -> str:
    """Encode Float32 array to base64 PCM16."""
    return pcm16_to_base64(float32_to_pcm16(float32_array))


def base64_to_float32(b64_string: str) -> np.ndarray:
    """Decode base64 PCM16 to Float32 array."""
    return pcm16_to_float32(base64_to_pcm16(b64_string))


# ── RMS Energy ─────────────────────────────────────────────────

def compute_rms(pcm16_bytes: bytes) -> float:
    """
    Compute RMS energy of PCM16 audio.

    Args:
        pcm16_bytes: Raw PCM16 bytes.

    Returns:
        RMS value normalized to [0.0, 1.0] range.
    """
    if not pcm16_bytes:
        return 0.0
    float_arr = pcm16_to_float32(pcm16_bytes)
    return float(np.sqrt(np.mean(float_arr ** 2)))


def compute_rms_float32(float32_array: np.ndarray) -> float:
    """Compute RMS energy of Float32 audio."""
    if len(float32_array) == 0:
        return 0.0
    return float(np.sqrt(np.mean(float32_array ** 2)))


# ── Resampling ──────────────────────────────────────────────────

def resample_audio(
    pcm16_bytes: bytes,
    from_rate: int,
    to_rate: int,
) -> bytes:
    """
    Resample PCM16 audio from one sample rate to another.

    Uses scipy's signal resampling with linear interpolation.

    Args:
        pcm16_bytes: Raw PCM16 bytes at from_rate.
        from_rate: Source sample rate (e.g., 44100).
        to_rate: Target sample rate (e.g., 24000).

    Returns:
        Resampled PCM16 bytes at to_rate.
    """
    try:
        from scipy import signal
    except ImportError:
        # Fallback: simple decimation or zero-copy if same rate
        if from_rate == to_rate:
            return pcm16_bytes
        raise ImportError(
            "scipy is required for audio resampling. "
            "Install with: pip install scipy"
        )

    if from_rate == to_rate:
        return pcm16_bytes

    int16_arr = pcm16_to_int16(pcm16_bytes)
    float_arr = int16_arr.astype(np.float64) / 32767.0

    # Calculate new length
    num_samples = int(len(float_arr) * to_rate / from_rate)

    # Resample
    resampled = signal.resample(float_arr, num_samples)

    # Clip and convert back
    clipped = np.clip(resampled, -1.0, 1.0)
    result_int16 = (clipped * 32767.0).astype(np.int16)

    return int16_to_pcm16(result_int16)


# ── Validation ─────────────────────────────────────────────────

def validate_pcm16(pcm16_bytes: bytes) -> bool:
    """
    Validate that a byte string is valid PCM16 audio.

    Args:
        pcm16_bytes: Bytes to validate.

    Returns:
        True if the byte length is even (valid for int16 pairs).
    """
    return len(pcm16_bytes) % 2 == 0


def pcm16_chunk_count(pcm16_bytes: bytes) -> int:
    """Return the number of PCM16 samples in a byte string."""
    return len(pcm16_bytes) // 2


def pcm16_duration_ms(pcm16_bytes: bytes, sample_rate: int = 24000) -> float:
    """Return the duration of PCM16 audio in milliseconds."""
    samples = pcm16_chunk_count(pcm16_bytes)
    return (samples / sample_rate) * 1000.0


# ── Mixing & Processing ─────────────────────────────────────────

def mix_audio(audio_a: np.ndarray, audio_b: np.ndarray, gain_b: float = 1.0) -> np.ndarray:
    """
    Mix two Float32 audio arrays together.

    Args:
        audio_a: First audio array.
        audio_b: Second audio array (scaled by gain_b).
        gain_b: Gain to apply to the second audio.

    Returns:
        Mixed audio array (same length as audio_a).
    """
    min_len = min(len(audio_a), len(audio_b))
    result = audio_a.copy()
    result[:min_len] += audio_b[:min_len] * gain_b
    result = np.clip(result, -1.0, 1.0)
    return result


def apply_fade(
    float32_array: np.ndarray,
    fade_in_ms: float = 50.0,
    fade_out_ms: float = 50.0,
    sample_rate: int = 24000,
) -> np.ndarray:
    """
    Apply a linear fade in/out to audio.

    Args:
        float32_array: Audio to process.
        fade_in_ms: Fade-in duration in milliseconds.
        fade_out_ms: Fade-out duration in milliseconds.
        sample_rate: Sample rate of the audio.

    Returns:
        Audio with fades applied.
    """
    result = float32_array.copy()
    fade_in_samples = int(fade_in_ms * sample_rate / 1000)
    fade_out_samples = int(fade_out_ms * sample_rate / 1000)

    fade_in_samples = min(fade_in_samples, len(result))
    fade_out_samples = min(fade_out_samples, len(result))

    # Fade in
    if fade_in_samples > 0:
        envelope = np.linspace(0, 1, fade_in_samples)
        result[:fade_in_samples] *= envelope

    # Fade out
    if fade_out_samples > 0:
        envelope = np.linspace(1, 0, fade_out_samples)
        result[-fade_out_samples:] *= envelope

    return result
