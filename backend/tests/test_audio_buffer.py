"""
backend/tests/test_audio_buffer.py — Tests for RollingAudioBuffer
"""

import pytest
from backend.core.audio_buffer import RollingAudioBuffer


class TestRollingAudioBuffer:
    """Tests for the RollingAudioBuffer class."""

    def test_append_empty(self):
        buf = RollingAudioBuffer(max_seconds=5)
        buf.append(b"\x00\x00")
        assert len(buf) > 0

    def test_append_and_clear(self):
        buf = RollingAudioBuffer(max_seconds=5)
        buf.append(b"\x00\x01" * 1000)
        assert len(buf) > 0
        buf.clear()
        assert len(buf) == 0

    def test_rms_energy(self):
        buf = RollingAudioBuffer(max_seconds=5)
        # Silent audio
        buf.append(b"\x00\x00" * 4800)  # 100ms of silence at 24kHz
        rms = buf.rms_energy()
        assert 0.0 <= rms <= 0.1

    def test_speech_markers(self):
        buf = RollingAudioBuffer(max_seconds=5)
        buf.append(b"\x01\x00" * 4800)
        buf.mark_speech_start()
        buf.append(b"\x01\x00" * 4800)
        buf.mark_speech_end()
        segment = buf.get_speech_segment()
        assert segment is not None
        assert len(segment) > 0

    def test_stats(self):
        buf = RollingAudioBuffer(max_seconds=5)
        buf.append(b"\x00\x00" * 24000)  # 1 second
        stats = buf.stats()
        assert stats.total_samples == 24000
        assert stats.total_duration_ms == 1000.0
