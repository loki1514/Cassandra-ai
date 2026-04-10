"""
Cassandra AI - Backend Architecture

A multi-tenant AI support system with:
- Supabase PostgreSQL backend
- AWS KMS encryption
- Row-Level Security (RLS)
- Soft-delete patterns
- AssemblyAI transcription
- Vector memory storage
"""

__version__ = "0.1.0"
__author__ = "Cassandra AI Team"

from cassandra.config import get_settings, settings, AppSettings
from cassandra.encryption import (
    encrypt,
    decrypt,
    generate_org_key,
    get_encryption_service,
    EncryptionService,
    KMSEncryptionError,
    KeyNotFoundError,
    EncryptionError,
    DecryptionError,
)
from cassandra.auth import (
    verify_jwt,
    get_current_user,
    get_current_user_optional,
    require_org_access,
    require_permissions,
    UserContext,
    AuthError,
    TokenExpiredError,
    InvalidTokenError,
    MissingTokenError,
)
from cassandra.transcription import (
    transcribe,
    TranscriptionResult,
    TranscriptionConfig
)
from cassandra.tools import (
    get_tool_registry,
    CreateTicketTool,
    CreateTicketInput,
    CreateTicketResult,
    AddMemoryTool,
    AddMemoryInput,
    AddMemoryResult,
    FetchContextTool,
    FetchContextInput,
    FetchContextResult,
    ToolRegistry,
    ToolMetadata
)

__all__ = [
    "__version__",
    "get_settings",
    "settings",
    "encrypt",
    "decrypt",
    "verify_jwt",
    "get_tool_registry",
    "CreateTicketTool",
    "AddMemoryTool",
    "FetchContextTool",
]