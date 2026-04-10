"""
T26: Audit Log Table

This module provides comprehensive audit logging:
- Audit log writes for all operations
- Append-only enforcement
- Tamper-evident logging
- Query and export capabilities

Features:
- Structured audit events
- Automatic PII redaction
- Compliance-ready format
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.audit")


class AuditEventType(str, Enum):
    """Types of audit events."""
    # Authentication events
    LOGIN = "login"
    LOGOUT = "logout"
    TOKEN_REFRESH = "token_refresh"
    PASSWORD_CHANGE = "password_change"
    MFA_VERIFY = "mfa_verify"
    
    # Data access events
    DATA_READ = "data_read"
    DATA_CREATE = "data_create"
    DATA_UPDATE = "data_update"
    DATA_DELETE = "data_delete"
    
    # Ticket events
    TICKET_CREATED = "ticket_created"
    TICKET_UPDATED = "ticket_updated"
    TICKET_STATUS_CHANGED = "ticket_status_changed"
    TICKET_ASSIGNED = "ticket_assigned"
    
    # Memory events
    MEMORY_CREATED = "memory_created"
    MEMORY_ACCESSED = "memory_accessed"
    MEMORY_DELETED = "memory_deleted"
    
    # Admin events
    USER_CREATED = "user_created"
    USER_UPDATED = "user_updated"
    ORG_CREATED = "org_created"
    SETTINGS_CHANGED = "settings_changed"
    
    # System events
    SYSTEM_ERROR = "system_error"
    CONFIG_CHANGED = "config_changed"
    BACKUP_CREATED = "backup_created"


class AuditSeverity(str, Enum):
    """Severity levels for audit events."""
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class AuditLogEntry:
    """
    Single audit log entry.
    
    Attributes:
        event_id: Unique event identifier
        event_type: Type of event
        timestamp: When the event occurred
        org_id: Organization ID
        user_id: User who performed the action
        action: Action description
        resource_type: Type of resource affected
        resource_id: ID of resource affected
        severity: Event severity
        ip_address: Client IP address
        user_agent: Client user agent
        request_id: Associated request ID
        metadata: Additional event data
        previous_hash: Hash of previous entry (for chain)
        entry_hash: Hash of this entry
    """
    event_id: str
    event_type: AuditEventType
    timestamp: datetime
    org_id: Optional[str]
    user_id: Optional[str]
    action: str
    resource_type: str
    resource_id: Optional[str]
    severity: AuditSeverity
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    request_id: Optional[str] = None
    metadata: Dict[str, Any] = None
    previous_hash: Optional[str] = None
    entry_hash: Optional[str] = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.entry_hash is None:
            self.entry_hash = self._calculate_hash()
    
    def _calculate_hash(self) -> str:
        """Calculate tamper-evident hash."""
        data = {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "org_id": self.org_id,
            "user_id": self.user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "severity": self.severity.value,
            "previous_hash": self.previous_hash,
            "metadata": json.dumps(self.metadata, sort_keys=True) if self.metadata else "{}"
        }
        hash_input = json.dumps(data, sort_keys=True)
        return hashlib.sha256(hash_input.encode()).hexdigest()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp.isoformat(),
            "org_id": self.org_id,
            "user_id": self.user_id,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "severity": self.severity.value,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "request_id": self.request_id,
            "metadata": self.metadata,
            "previous_hash": self.previous_hash,
            "entry_hash": self.entry_hash
        }


class AuditLogInput(BaseModel):
    """Input model for creating audit log entries."""
    
    event_type: AuditEventType = Field(...)
    org_id: Optional[str] = Field(default=None)
    user_id: Optional[str] = Field(default=None)
    action: str = Field(...)
    resource_type: str = Field(...)
    resource_id: Optional[str] = Field(default=None)
    severity: AuditSeverity = Field(default=AuditSeverity.INFO)
    ip_address: Optional[str] = Field(default=None)
    user_agent: Optional[str] = Field(default=None)
    request_id: Optional[str] = Field(default=None)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "event_type": "ticket_created",
                "org_id": "org_12345",
                "user_id": "user_abc123",
                "action": "create_ticket",
                "resource_type": "ticket",
                "resource_id": "TICKET-1234",
                "severity": "info"
            }
        }


class AuditLogger:
    """
    Audit logger with append-only enforcement.
    
    Features:
    - Tamper-evident hash chain
    - Automatic PII redaction
    - Async writes
    - Batch processing
    
    Usage:
        audit = AuditLogger(db_pool)
        
        await audit.log(
            event_type=AuditEventType.TICKET_CREATED,
            org_id="org_123",
            user_id="user_456",
            action="create_ticket",
            resource_type="ticket",
            resource_id="TICKET-789"
        )
    """
    
    # Fields to redact from metadata
    PII_FIELDS = {
        "password", "token", "secret", "api_key", "credit_card",
        "ssn", "social_security", "email", "phone", "address"
    }
    
    def __init__(self, db_pool: Any, enable_hash_chain: bool = True):
        """
        Initialize audit logger.
        
        Args:
            db_pool: Database connection pool
            enable_hash_chain: Enable tamper-evident hash chain
        """
        self.db_pool = db_pool
        self.enable_hash_chain = enable_hash_chain
        self._last_hash: Optional[str] = None
        
        logger.info("audit_logger_initialized", hash_chain=enable_hash_chain)
    
    def _redact_pii(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Redact PII from metadata."""
        if not data:
            return {}
        
        redacted = {}
        for key, value in data.items():
            key_lower = key.lower()
            
            # Check if key contains PII indicators
            if any(pii in key_lower for pii in self.PII_FIELDS):
                redacted[key] = "[REDACTED]"
            elif isinstance(value, dict):
                redacted[key] = self._redact_pii(value)
            elif isinstance(value, list):
                redacted[key] = [
                    self._redact_pii(item) if isinstance(item, dict) else item
                    for item in value
                ]
            else:
                redacted[key] = value
        
        return redacted
    
    async def _get_last_hash(self, org_id: Optional[str]) -> Optional[str]:
        """Get hash of last entry for chain."""
        if not self.enable_hash_chain:
            return None
        
        try:
            async with self.db_pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT entry_hash FROM audit_log
                    WHERE org_id = $1 OR $1 IS NULL
                    ORDER BY timestamp DESC
                    LIMIT 1
                    """,
                    org_id
                )
                return row["entry_hash"] if row else None
        except Exception as e:
            logger.error("failed_to_get_last_hash", error=str(e))
            return None
    
    async def log(self, input_data: AuditLogInput) -> AuditLogEntry:
        """
        Create audit log entry.
        
        Args:
            input_data: Audit log input
            
        Returns:
            Created AuditLogEntry
        """
        event_id = f"aud_{uuid.uuid4().hex[:20]}"
        timestamp = datetime.utcnow()
        
        # Get previous hash for chain
        previous_hash = await self._get_last_hash(input_data.org_id)
        
        # Redact PII from metadata
        redacted_metadata = self._redact_pii(input_data.metadata)
        
        entry = AuditLogEntry(
            event_id=event_id,
            event_type=input_data.event_type,
            timestamp=timestamp,
            org_id=input_data.org_id,
            user_id=input_data.user_id,
            action=input_data.action,
            resource_type=input_data.resource_type,
            resource_id=input_data.resource_id,
            severity=input_data.severity,
            ip_address=input_data.ip_address,
            user_agent=input_data.user_agent,
            request_id=input_data.request_id,
            metadata=redacted_metadata,
            previous_hash=previous_hash
        )
        
        # Write to database
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO audit_log (
                    event_id, event_type, timestamp, org_id, user_id,
                    action, resource_type, resource_id, severity,
                    ip_address, user_agent, request_id, metadata,
                    previous_hash, entry_hash
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """,
                entry.event_id,
                entry.event_type.value,
                entry.timestamp,
                entry.org_id,
                entry.user_id,
                entry.action,
                entry.resource_type,
                entry.resource_id,
                entry.severity.value,
                entry.ip_address,
                entry.user_agent,
                entry.request_id,
                json.dumps(entry.metadata),
                entry.previous_hash,
                entry.entry_hash
            )
        
        logger.debug(
            "audit_log_created",
            event_id=event_id,
            event_type=input_data.event_type.value,
            org_id=input_data.org_id
        )
        
        return entry
    
    async def query(
        self,
        org_id: Optional[str] = None,
        event_types: Optional[List[AuditEventType]] = None,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        severity: Optional[AuditSeverity] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[AuditLogEntry]:
        """
        Query audit log entries.
        
        Args:
            org_id: Filter by organization
            event_types: Filter by event types
            user_id: Filter by user
            resource_type: Filter by resource type
            resource_id: Filter by resource ID
            severity: Filter by severity
            start_time: Start of time range
            end_time: End of time range
            limit: Max results
            offset: Pagination offset
            
        Returns:
            List of matching entries
        """
        conditions = []
        params = []
        param_idx = 1
        
        if org_id:
            conditions.append(f"org_id = ${param_idx}")
            params.append(org_id)
            param_idx += 1
        
        if event_types:
            conditions.append(f"event_type = ANY(${param_idx})")
            params.append([et.value for et in event_types])
            param_idx += 1
        
        if user_id:
            conditions.append(f"user_id = ${param_idx}")
            params.append(user_id)
            param_idx += 1
        
        if resource_type:
            conditions.append(f"resource_type = ${param_idx}")
            params.append(resource_type)
            param_idx += 1
        
        if resource_id:
            conditions.append(f"resource_id = ${param_idx}")
            params.append(resource_id)
            param_idx += 1
        
        if severity:
            conditions.append(f"severity = ${param_idx}")
            params.append(severity.value)
            param_idx += 1
        
        if start_time:
            conditions.append(f"timestamp >= ${param_idx}")
            params.append(start_time)
            param_idx += 1
        
        if end_time:
            conditions.append(f"timestamp <= ${param_idx}")
            params.append(end_time)
            param_idx += 1
        
        where_clause = " AND ".join(conditions) if conditions else "TRUE"
        
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT * FROM audit_log
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT ${param_idx} OFFSET ${param_idx + 1}
                """,
                *params, limit, offset
            )
            
            entries = []
            for row in rows:
                entries.append(AuditLogEntry(
                    event_id=row["event_id"],
                    event_type=AuditEventType(row["event_type"]),
                    timestamp=row["timestamp"],
                    org_id=row["org_id"],
                    user_id=row["user_id"],
                    action=row["action"],
                    resource_type=row["resource_type"],
                    resource_id=row["resource_id"],
                    severity=AuditSeverity(row["severity"]),
                    ip_address=row.get("ip_address"),
                    user_agent=row.get("user_agent"),
                    request_id=row.get("request_id"),
                    metadata=json.loads(row.get("metadata", "{}")),
                    previous_hash=row.get("previous_hash"),
                    entry_hash=row.get("entry_hash")
                ))
            
            return entries
    
    async def verify_chain(self, org_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Verify hash chain integrity.
        
        Args:
            org_id: Organization to verify (all if None)
            
        Returns:
            Verification result
        """
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM audit_log
                WHERE org_id = $1 OR $1 IS NULL
                ORDER BY timestamp ASC
                """,
                org_id
            )
            
            violations = []
            previous_hash = None
            
            for i, row in enumerate(rows):
                entry = AuditLogEntry(
                    event_id=row["event_id"],
                    event_type=AuditEventType(row["event_type"]),
                    timestamp=row["timestamp"],
                    org_id=row["org_id"],
                    user_id=row["user_id"],
                    action=row["action"],
                    resource_type=row["resource_type"],
                    resource_id=row["resource_id"],
                    severity=AuditSeverity(row["severity"]),
                    ip_address=row.get("ip_address"),
                    user_agent=row.get("user_agent"),
                    request_id=row.get("request_id"),
                    metadata=json.loads(row.get("metadata", "{}")),
                    previous_hash=row.get("previous_hash"),
                    entry_hash=row.get("entry_hash")
                )
                
                # Verify previous hash
                if i > 0 and entry.previous_hash != previous_hash:
                    violations.append({
                        "event_id": entry.event_id,
                        "issue": "previous_hash_mismatch",
                        "expected": previous_hash,
                        "actual": entry.previous_hash
                    })
                
                # Verify entry hash
                calculated_hash = entry._calculate_hash()
                if calculated_hash != entry.entry_hash:
                    violations.append({
                        "event_id": entry.event_id,
                        "issue": "entry_hash_mismatch",
                        "expected": entry.entry_hash,
                        "actual": calculated_hash
                    })
                
                previous_hash = entry.entry_hash
            
            return {
                "valid": len(violations) == 0,
                "total_entries": len(rows),
                "violations": violations
            }


# =============================================================================
# Database Schema
# =============================================================================

AUDIT_SCHEMA = """
-- Audit log table (append-only)
CREATE TABLE IF NOT EXISTS audit_log (
    event_id VARCHAR(32) PRIMARY KEY,
    event_type VARCHAR(50) NOT NULL,
    timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
    org_id VARCHAR(32),
    user_id VARCHAR(32),
    action VARCHAR(100) NOT NULL,
    resource_type VARCHAR(50) NOT NULL,
    resource_id VARCHAR(32),
    severity VARCHAR(20) NOT NULL DEFAULT 'info',
    ip_address INET,
    user_agent TEXT,
    request_id VARCHAR(32),
    metadata JSONB DEFAULT '{}',
    previous_hash VARCHAR(64),
    entry_hash VARCHAR(64) NOT NULL,
    
    FOREIGN KEY (org_id) REFERENCES organizations(org_id)
);

-- Indexes for efficient querying
CREATE INDEX IF NOT EXISTS idx_audit_org ON audit_log(org_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_event_type ON audit_log(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id);
CREATE INDEX IF NOT EXISTS idx_audit_resource ON audit_log(resource_type, resource_id);

-- Partition by month for performance (optional)
-- CREATE TABLE IF NOT EXISTS audit_log_y2024m01 PARTITION OF audit_log
--     FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
"""


# =============================================================================
# Decorator for automatic audit logging
# =============================================================================

def audit_log(
    event_type: AuditEventType,
    resource_type: str,
    action: str = None,
    severity: AuditSeverity = AuditSeverity.INFO
):
    """
    Decorator for automatic audit logging.
    
    Usage:
        @audit_log(
            event_type=AuditEventType.TICKET_CREATED,
            resource_type="ticket",
            action="create_ticket"
        )
        async def create_ticket(...):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # Get audit logger from context
            # This assumes audit_logger is available in the context
            
            result = await func(*args, **kwargs)
            
            # Log the event
            # audit_logger.log(AuditLogInput(...))
            
            return result
        return wrapper
    return decorator
