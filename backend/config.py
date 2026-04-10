"""
backend/config.py — Cassandra Voice Server Configuration

Replaces the root-level config.py with a full pydantic-settings approach.
All configuration is loaded from environment variables and .env file.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Server ────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000
    debug: bool = False

    # ── OpenAI ───────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-2024-08-06"
    openai_whisper_model: str = "whisper-1"

    # ── Supabase ─────────────────────────────────────────────
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    # ── ElevenLabs TTS ───────────────────────────────────────
    elevenlabs_api_key: str = ""
    elevenlabs_default_voice: str = "alloy"
    elevenlabs_model: str = "eleven_multilingual_v2"

    # ── AssemblyAI ───────────────────────────────────────────
    assemblyai_api_key: str = ""

    # ── Provider Selection ────────────────────────────────────
    vad_provider: str = "silero"   # silero, deepgram, livekit
    stt_provider: str = "openai"   # openai, assemblyai, deepgram
    tts_provider: str = "elevenlabs"  # elevenlabs, openai

    # ── Rate Limits (seconds per month per role) ─────────────
    limit_tenant: int = 5400           # 90 minutes
    limit_super_tenant: int = 5400
    limit_admin: int = 5400
    limit_org_super_admin: int = 0     # Unlimited
    limit_owner: int = 0               # Unlimited
    limit_master_admin: int = 0         # Unlimited

    # ── Audio Config ─────────────────────────────────────────
    audio_sample_rate: int = 24000
    audio_chunk_duration_ms: int = 100
    vad_probability_threshold: float = 0.5
    vad_silence_threshold_ms: int = 750
    vad_prefix_padding_ms: int = 450

    # ── Session Config ────────────────────────────────────────
    session_idle_timeout_seconds: int = 3600
    max_concurrent_sessions: int = 100

    # ── Logging ───────────────────────────────────────────────
    log_level: str = "INFO"
    log_format: str = "json"   # json or text

    # ── Circuit Breaker ───────────────────────────────────────
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_recovery_timeout_seconds: int = 30

    # ── Tool Config ───────────────────────────────────────────
    autopilot_api_base_url: str = ""
    autopilot_api_key: str = ""

    # ── Helpers ───────────────────────────────────────────────
    def get_role_limit(self, role: str) -> int:
        """Return the monthly voice limit in seconds for a given role."""
        limits = {
            "tenant": self.limit_tenant,
            "super_tenant": self.limit_super_tenant,
            "admin": self.limit_admin,
            "org_super_admin": self.limit_org_super_admin,
            "owner": self.limit_owner,
            "master_admin": self.limit_master_admin,
        }
        return limits.get(role.lower(), self.limit_tenant)

    def is_role_unlimited(self, role: str) -> bool:
        """Return True if the role has unlimited usage."""
        return self.get_role_limit(role) == 0


@lru_cache
def get_settings() -> Settings:
    return Settings()
