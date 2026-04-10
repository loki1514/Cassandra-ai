"""
Cassandra AI Mobile Module

Provides mobile client functionality including:
- WebSocket audio streaming client
- Reconnection with exponential backoff
- JWT authentication
"""

from .audio_client import AudioStreamClient, AudioStreamConfig

__all__ = ["AudioStreamClient", "AudioStreamConfig"]
