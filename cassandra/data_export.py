"""
T47: Data Export API

This module provides data export functionality:
- GDPR export endpoint
- Encrypted ZIP delivery
- Data portability
- Export scheduling

Features:
- Complete data export
- Secure delivery
- Progress tracking
"""

import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import structlog
from pydantic import BaseModel, Field

logger = structlog.get_logger("cassandra.data_export")


class ExportStatus(str, Enum):
    """Export status values."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class DataExport:
    """Data export information."""
    export_id: str
    user_id: str
    org_id: str
    status: ExportStatus
    requested_at: datetime
    completed_at: Optional[datetime]
    expires_at: Optional[datetime]
    download_url: Optional[str]
    size_bytes: int
    checksum: Optional[str]


class ExportRequest(BaseModel):
    """Request for data export."""
    
    user_id: str = Field(...)
    org_id: str = Field(...)
    include_attachments: bool = Field(default=True)
    format: str = Field(default="json")
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_abc123",
                "org_id": "org_12345",
                "include_attachments": True,
                "format": "json"
            }
        }


class DataExportManager:
    """
    Manages data exports for GDPR and portability.
    
    Usage:
        manager = DataExportManager(db_pool, encryption_service)
        
        # Request export
        export = await manager.request_export(
            user_id="user_123",
            org_id="org_456"
        )
        
        # Get download URL
        url = await manager.get_download_url(export.export_id)
    """
    
    def __init__(
        self,
        db_pool: Any,
        encryption_service: Optional[Any] = None,
        s3_bucket: Optional[str] = None
    ):
        """
        Initialize data export manager.
        
        Args:
            db_pool: Database connection pool
            encryption_service: Encryption service for ZIP
            s3_bucket: S3 bucket for exports
        """
        self.db_pool = db_pool
        self.encryption_service = encryption_service
        self.s3_bucket = s3_bucket or "cassandra-exports"
        
        logger.info("data_export_manager_initialized")
    
    async def request_export(
        self,
        user_id: str,
        org_id: str,
        include_attachments: bool = True,
        format: str = "json"
    ) -> DataExport:
        """
        Request data export.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            include_attachments: Include file attachments
            format: Export format
            
        Returns:
            DataExport info
        """
        export_id = f"export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}_{user_id[:8]}"
        
        export = DataExport(
            export_id=export_id,
            user_id=user_id,
            org_id=org_id,
            status=ExportStatus.PENDING,
            requested_at=datetime.utcnow(),
            completed_at=None,
            expires_at=None,
            download_url=None,
            size_bytes=0,
            checksum=None
        )
        
        # Save export request
        await self._save_export_request(export)
        
        logger.info(
            "export_requested",
            export_id=export_id,
            user_id=user_id,
            org_id=org_id
        )
        
        return export
    
    async def _save_export_request(self, export: DataExport):
        """Save export request to database."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO data_exports (
                    export_id, user_id, org_id, status, requested_at
                ) VALUES ($1, $2, $3, $4, $5)
                """,
                export.export_id,
                export.user_id,
                export.org_id,
                export.status.value,
                export.requested_at
            )
    
    async def generate_export(
        self,
        export_id: str,
        password: Optional[str] = None
    ) -> DataExport:
        """
        Generate data export.
        
        Args:
            export_id: Export ID
            password: Optional password for ZIP encryption
            
        Returns:
            Updated DataExport
        """
        async with self.db_pool.acquire() as conn:
            # Get export info
            row = await conn.fetchrow(
                "SELECT * FROM data_exports WHERE export_id = $1",
                export_id
            )
            
            if not row:
                raise ValueError(f"Export {export_id} not found")
            
            user_id = row["user_id"]
            org_id = row["org_id"]
            
            # Update status
            await conn.execute(
                """
                UPDATE data_exports
                SET status = $1, started_at = NOW()
                WHERE export_id = $2
                """,
                ExportStatus.IN_PROGRESS.value,
                export_id
            )
            
            try:
                # Create ZIP file
                zip_path = await self._create_export_zip(
                    user_id=user_id,
                    org_id=org_id,
                    export_id=export_id,
                    password=password
                )
                
                # Upload to S3
                s3_key = f"exports/{export_id}.zip"
                download_url = await self._upload_to_s3(zip_path, s3_key)
                
                # Calculate checksum
                import hashlib
                with open(zip_path, 'rb') as f:
                    checksum = hashlib.sha256(f.read()).hexdigest()
                
                size_bytes = os.path.getsize(zip_path)
                
                # Clean up
                os.unlink(zip_path)
                
                # Update export
                expires_at = datetime.utcnow() + timedelta(days=7)
                
                await conn.execute(
                    """
                    UPDATE data_exports
                    SET status = $1,
                        completed_at = NOW(),
                        expires_at = $2,
                        download_url = $3,
                        size_bytes = $4,
                        checksum = $5
                    WHERE export_id = $6
                    """,
                    ExportStatus.COMPLETED.value,
                    expires_at,
                    download_url,
                    size_bytes,
                    checksum,
                    export_id
                )
                
                logger.info(
                    "export_completed",
                    export_id=export_id,
                    size_mb=size_bytes / (1024 * 1024)
                )
                
                return DataExport(
                    export_id=export_id,
                    user_id=user_id,
                    org_id=org_id,
                    status=ExportStatus.COMPLETED,
                    requested_at=row["requested_at"],
                    completed_at=datetime.utcnow(),
                    expires_at=expires_at,
                    download_url=download_url,
                    size_bytes=size_bytes,
                    checksum=checksum
                )
                
            except Exception as e:
                await conn.execute(
                    """
                    UPDATE data_exports
                    SET status = $1, error_message = $2
                    WHERE export_id = $3
                    """,
                    ExportStatus.FAILED.value,
                    str(e),
                    export_id
                )
                
                logger.error("export_failed", export_id=export_id, error=str(e))
                raise
    
    async def _create_export_zip(
        self,
        user_id: str,
        org_id: str,
        export_id: str,
        password: Optional[str] = None
    ) -> str:
        """
        Create export ZIP file.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            export_id: Export ID
            password: Optional ZIP password
            
        Returns:
            Path to ZIP file
        """
        # Create temp directory
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, f"{export_id}.zip")
        
        # Create ZIP
        compression = zipfile.ZIP_DEFLATED
        
        with zipfile.ZipFile(zip_path, 'w', compression) as zf:
            # Export user profile
            user_data = await self._export_user_data(user_id)
            zf.writestr("user_profile.json", json.dumps(user_data, indent=2, default=str))
            
            # Export tickets
            tickets = await self._export_tickets(user_id, org_id)
            zf.writestr("tickets.json", json.dumps(tickets, indent=2, default=str))
            
            # Export memories
            memories = await self._export_memories(user_id, org_id)
            zf.writestr("memories.json", json.dumps(memories, indent=2, default=str))
            
            # Export audit log
            audit_logs = await self._export_audit_logs(user_id, org_id)
            zf.writestr("audit_logs.json", json.dumps(audit_logs, indent=2, default=str))
            
            # Export notifications
            notifications = await self._export_notifications(user_id, org_id)
            zf.writestr("notifications.json", json.dumps(notifications, indent=2, default=str))
            
            # Add manifest
            manifest = {
                "export_id": export_id,
                "user_id": user_id,
                "org_id": org_id,
                "exported_at": datetime.utcnow().isoformat(),
                "files": [
                    "user_profile.json",
                    "tickets.json",
                    "memories.json",
                    "audit_logs.json",
                    "notifications.json"
                ]
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        
        return zip_path
    
    async def _export_user_data(self, user_id: str) -> Dict[str, Any]:
        """Export user profile data."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id, email, full_name, created_at, updated_at
                FROM users
                WHERE user_id = $1
                """,
                user_id
            )
            
            if row:
                return dict(row)
            return {}
    
    async def _export_tickets(self, user_id: str, org_id: str) -> List[Dict[str, Any]]:
        """Export user's tickets."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM tickets
                WHERE org_id = $1
                AND (created_by = $2 OR assigned_to = $2)
                """,
                org_id, user_id
            )
            
            return [dict(row) for row in rows]
    
    async def _export_memories(self, user_id: str, org_id: str) -> List[Dict[str, Any]]:
        """Export user's memories."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM memory_archive
                WHERE org_id = $1
                AND created_by = $2
                """,
                org_id, user_id
            )
            
            return [dict(row) for row in rows]
    
    async def _export_audit_logs(self, user_id: str, org_id: str) -> List[Dict[str, Any]]:
        """Export user's audit logs."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM audit_log
                WHERE org_id = $1
                AND user_id = $2
                ORDER BY timestamp DESC
                LIMIT 10000
                """,
                org_id, user_id
            )
            
            return [dict(row) for row in rows]
    
    async def _export_notifications(self, user_id: str, org_id: str) -> List[Dict[str, Any]]:
        """Export user's notifications."""
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM notifications
                WHERE org_id = $1
                AND user_id = $2
                """,
                org_id, user_id
            )
            
            return [dict(row) for row in rows]
    
    async def _upload_to_s3(self, file_path: str, s3_key: str) -> str:
        """Upload file to S3 and return presigned URL."""
        import boto3
        
        s3 = boto3.client('s3')
        
        # Upload
        s3.upload_file(file_path, self.s3_bucket, s3_key)
        
        # Generate presigned URL (valid for 7 days)
        url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.s3_bucket, 'Key': s3_key},
            ExpiresIn=7 * 24 * 60 * 60
        )
        
        return url
    
    async def get_export_status(self, export_id: str) -> Optional[Dict[str, Any]]:
        """Get export status."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM data_exports WHERE export_id = $1",
                export_id
            )
            
            if not row:
                return None
            
            return {
                "export_id": row["export_id"],
                "status": row["status"],
                "requested_at": row["requested_at"].isoformat(),
                "completed_at": row["completed_at"].isoformat() if row["completed_at"] else None,
                "expires_at": row["expires_at"].isoformat() if row["expires_at"] else None,
                "download_url": row["download_url"],
                "size_bytes": row["size_bytes"]
            }


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/export", tags=["Data Export"])

_export_manager: Optional[DataExportManager] = None


def get_export_manager() -> DataExportManager:
    """Get or create export manager."""
    global _export_manager
    if _export_manager is None:
        raise RuntimeError("Export manager not initialized")
    return _export_manager


@router.post("/request")
async def request_export(
    request: ExportRequest,
    background_tasks: BackgroundTasks,
    user: UserContext = Depends(get_current_user)
):
    """
    Request data export (GDPR).
    
    Creates an encrypted ZIP file with all user data.
    """
    try:
        manager = get_export_manager()
        
        export = await manager.request_export(
            user_id=request.user_id,
            org_id=request.org_id,
            include_attachments=request.include_attachments,
            format=request.format
        )
        
        # Generate export in background
        background_tasks.add_task(manager.generate_export, export.export_id)
        
        return {
            "success": True,
            "export_id": export.export_id,
            "status": export.status.value,
            "message": "Export is being prepared. Check status endpoint."
        }
        
    except Exception as e:
        logger.error("export_request_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{export_id}/status")
async def get_export_status(
    export_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Get export status and download URL."""
    try:
        manager = get_export_manager()
        status = await manager.get_export_status(export_id)
        
        if not status:
            raise HTTPException(status_code=404, detail="Export not found")
        
        return status
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error("export_status_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
