"""
T07: Environment & Secrets Architecture

This module provides centralized configuration management using Pydantic Settings.
It handles environment variables, validation, and provides a type-safe config object.

Features:
- Pydantic Settings for all environment variables
- Strict validation of required variables
- Environment-specific defaults
- Secrets management best practices
"""

import os
from typing import Optional, List, Dict, Any
from functools import lru_cache
from pathlib import Path

from pydantic import Field, validator, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    """Database connection settings.

    Note: All actual database operations go through Supabase REST API
    (via cassandra/supabase.py), not direct Postgres connections.
    These settings exist for optional direct DB access if needed.
    """

    model_config = SettingsConfigDict(
        env_prefix="DB_",
        extra="ignore",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    host: str = Field(default="localhost", description="Database host")
    port: int = Field(default=5432, description="Database port")
    name: str = Field(default="cassandra", description="Database name")
    user: str = Field(default="postgres", description="Database user")
    password: str = Field(default="", description="Database password (optional — Supabase handles auth)")
    ssl_mode: str = Field(default="require", description="SSL mode for connection")
    pool_size: int = Field(default=10, description="Connection pool size")
    max_overflow: int = Field(default=20, description="Max pool overflow")
    
    @property
    def url(self) -> str:
        """Build PostgreSQL connection URL."""
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}?sslmode={self.ssl_mode}"
    
    @property
    def async_url(self) -> str:
        """Build async PostgreSQL connection URL."""
        return f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}?sslmode={self.ssl_mode}"


class SupabaseSettings(BaseSettings):
    """Supabase-specific settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="SUPABASE_",
        extra="ignore"
    )
    
    url: str = Field(..., description="Supabase project URL (required)")
    anon_key: str = Field(..., description="Supabase anon/public key (required)")
    service_role_key: str = Field(..., description="Supabase service role key (required)")
    jwt_secret: Optional[str] = Field(default=None, description="JWT secret for token verification")
    
    @field_validator('url')
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Ensure URL is valid."""
        if not v.startswith(('https://', 'http://')):
            raise ValueError('SUPABASE_URL must start with https:// or http://')
        return v.rstrip('/')


class AWSSettings(BaseSettings):
    """AWS service settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="AWS_",
        extra="ignore"
    )
    
    region: str = Field(default="us-east-1", description="AWS region")
    access_key_id: Optional[str] = Field(default=None, description="AWS access key ID")
    secret_access_key: Optional[str] = Field(default=None, description="AWS secret access key")
    kms_key_alias_prefix: str = Field(
        default="alias/cassandra-org",
        description="Prefix for KMS key aliases"
    )
    s3_bucket: Optional[str] = Field(default=None, description="S3 bucket for file storage")
    
    @property
    def use_iam_role(self) -> bool:
        """Check if using IAM role instead of explicit credentials."""
        return self.access_key_id is None or self.secret_access_key is None


class AssemblyAISettings(BaseSettings):
    """AssemblyAI transcription service settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="ASSEMBLYAI_",
        extra="ignore"
    )
    
    api_key: str = Field(..., description="AssemblyAI API key (required)")
    base_url: str = Field(
        default="https://api.assemblyai.com/v2",
        description="AssemblyAI API base URL"
    )
    webhook_secret: Optional[str] = Field(
        default=None,
        description="Webhook secret for verifying callbacks"
    )
    default_language: str = Field(default="en", description="Default transcription language")
    enable_speaker_diarization: bool = Field(
        default=True,
        description="Enable speaker diarization by default"
    )
    speakers_expected: Optional[int] = Field(
        default=None,
        description="Expected number of speakers"
    )


class ElevenLabsSettings(BaseSettings):
    """ElevenLabs TTS settings."""

    model_config = SettingsConfigDict(
        env_prefix="ELEVENLABS_",
        extra="ignore"
    )

    api_key: str = Field(default="", description="ElevenLabs API key")
    default_voice: str = Field(default="alloy", description="Default voice ID or name")
    model: str = Field(default="eleven_multilingual_v2", description="TTS model")

    @property
    def is_configured(self) -> bool:
        """Check if ElevenLabs is configured with a valid API key."""
        return bool(self.api_key)


class OpenAISettings(BaseSettings):
    """OpenAI API settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="OPENAI_",
        extra="ignore"
    )
    
    api_key: str = Field(..., description="OpenAI API key (required)")
    organization: Optional[str] = Field(default=None, description="OpenAI organization ID")
    model: str = Field(default="gpt-4", description="Default model for completions")
    embedding_model: str = Field(
        default="text-embedding-3-small",
        description="Model for embeddings"
    )
    max_tokens: int = Field(default=2000, description="Max tokens for completions")
    temperature: float = Field(default=0.7, description="Temperature for completions")


class SupermemorySettings(BaseSettings):
    """Supermemory conversational memory service settings."""

    model_config = SettingsConfigDict(
        env_prefix="SUPERMEMORY_",
        extra="ignore"
    )

    api_url: str = Field(
        default="https://api.supermemory.ai",
        description="Supermemory API base URL"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Supermemory API key"
    )
    org_id_header: str = Field(
        default="x-org-id",
        description="HTTP header name for org_id"
    )
    search_endpoint: str = Field(
        default="/search",
        description="Search endpoint path"
    )
    timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=60,
        description="Request timeout in seconds"
    )

    @property
    def is_configured(self) -> bool:
        """Check if Supermemory is configured with credentials."""
        return bool(self.api_key and self.api_url)


class AuthSettings(BaseSettings):
    """Authentication settings for Cassandra session tokens and FMS JWT verification."""

    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore"
    )

    cassandra_token_secret: str = Field(
        description="Generate: python3 -c \"import secrets; print(secrets.token_hex(64))\""
    )
    cassandra_token_expire_seconds: int = Field(default=21600, ge=60)
    fms_supabase_url: str = Field(
        description="FMS Supabase project URL (e.g. https://abc123.supabase.co). "
                    "This is the Supabase that authenticates the React console admin, "
                    "NOT the Cassandra database."
    )


class VectorStoreSettings(BaseSettings):
    """Vector store (Pinecone/Weaviate) settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="VECTOR_",
        extra="ignore"
    )
    
    provider: str = Field(default="pinecone", description="Vector store provider")
    api_key: str = Field(..., description="Vector store API key (required)")
    environment: Optional[str] = Field(default=None, description="Pinecone environment")
    index_name: str = Field(default="cassandra-memories", description="Index/collection name")
    dimension: int = Field(default=1536, description="Embedding dimension")
    metric: str = Field(default="cosine", description="Similarity metric")


class RedisSettings(BaseSettings):
    """Redis cache settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="REDIS_",
        extra="ignore"
    )
    
    host: str = Field(default="localhost", description="Redis host")
    port: int = Field(default=6379, description="Redis port")
    password: Optional[str] = Field(default=None, description="Redis password")
    db: int = Field(default=0, description="Redis database number")
    ssl: bool = Field(default=False, description="Use SSL connection")
    
    @property
    def url(self) -> str:
        """Build Redis connection URL."""
        protocol = "rediss" if self.ssl else "redis"
        auth = f":{self.password}@" if self.password else ""
        return f"{protocol}://{auth}{self.host}:{self.port}/{self.db}"


class SecuritySettings(BaseSettings):
    """Security-related settings."""
    
    model_config = SettingsConfigDict(
        env_prefix="SECURITY_",
        extra="ignore"
    )
    
    jwt_algorithm: str = Field(default="HS256", description="JWT signing algorithm")
    jwt_expiration_hours: int = Field(default=24, description="JWT expiration in hours")
    jwt_refresh_expiration_days: int = Field(default=7, description="Refresh token expiration")
    password_min_length: int = Field(default=8, description="Minimum password length")
    enable_cors: bool = Field(default=True, description="Enable CORS")
    allowed_origins: str = Field(
        default="",
        description="Allowed CORS origins as comma-separated string"
    )
    encryption_enabled: bool = Field(default=True, description="Enable KMS encryption")
    
    @property
    def allowed_origins_list(self) -> List[str]:
        """Parse comma-separated origins into a list."""
        if not self.allowed_origins:
            return []
        return [origin.strip() for origin in self.allowed_origins.split(',') if origin.strip()]


class LoggingSettings(BaseSettings):
    """Logging configuration."""
    
    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        extra="ignore"
    )
    
    level: str = Field(default="INFO", description="Logging level")
    format: str = Field(
        default="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format string"
    )
    json_format: bool = Field(default=False, description="Use JSON log format")
    file_path: Optional[str] = Field(default=None, description="Log file path")


class AppSettings(BaseSettings):
    """
    Main application settings.
    
    Combines all sub-settings and provides the root configuration object.
    """
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False
    )
    
    # Application
    app_name: str = Field(default="Cassandra AI", description="Application name")
    app_version: str = Field(default="0.1.0", description="Application version")
    debug: bool = Field(default=False, description="Debug mode")
    environment: str = Field(default="development", description="Environment (development/staging/production)")
    
    # Server
    host: str = Field(default="0.0.0.0", description="Server host")
    port: int = Field(default=8000, description="Server port")
    workers: int = Field(default=1, description="Number of worker processes")
    reload: bool = Field(default=False, description="Auto-reload on code changes")
    
    # Sub-settings
    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    supabase: SupabaseSettings = Field(default_factory=SupabaseSettings)
    aws: AWSSettings = Field(default_factory=AWSSettings)
    assemblyai: AssemblyAISettings = Field(default_factory=AssemblyAISettings)
    elevenlabs: ElevenLabsSettings = Field(default_factory=ElevenLabsSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    vector: VectorStoreSettings = Field(default_factory=VectorStoreSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    supermemory: SupermemorySettings = Field(default_factory=SupermemorySettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    
    @field_validator('environment')
    @classmethod
    def validate_environment(cls, v: str) -> str:
        """Validate environment value."""
        allowed = ['development', 'staging', 'production', 'test']
        if v.lower() not in allowed:
            raise ValueError(f'Environment must be one of: {", ".join(allowed)}')
        return v.lower()
    
    @property
    def is_development(self) -> bool:
        """Check if running in development mode."""
        return self.environment == 'development'
    
    @property
    def is_production(self) -> bool:
        """Check if running in production mode."""
        return self.environment == 'production'
    
    @property
    def is_test(self) -> bool:
        """Check if running in test mode."""
        return self.environment == 'test'


@lru_cache()
def get_settings() -> AppSettings:
    """
    Get cached application settings.
    
    Returns:
        AppSettings instance
        
    Raises:
        ValidationError: If required settings are missing
    """
    return AppSettings()


def reload_settings() -> AppSettings:
    """
    Force reload of settings (useful for testing).
    
    Returns:
        Fresh AppSettings instance
    """
    get_settings.cache_clear()
    return get_settings()


# Convenience exports
# Load .env file before settings to ensure all sub-models pick up env vars.
# Sub-models use env_prefix but don't inherit env_file from AppSettings in pydantic-settings v2.
from dotenv import load_dotenv
load_dotenv(override=True)
settings = get_settings()
