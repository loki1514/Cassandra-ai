"""
T45: Notifications

This module provides notification functionality:
- Push notification cron
- Expo Push API integration
- Email notifications
- In-app notifications

Features:
- Multi-channel notifications
- User preferences
- Delivery tracking
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import httpx
import structlog
from pydantic import BaseModel, Field

from cassandra.config import settings

logger = structlog.get_logger("cassandra.notifications")


class NotificationChannel(str, Enum):
    """Notification channels."""
    PUSH = "push"
    EMAIL = "email"
    SMS = "sms"
    IN_APP = "in_app"
    WEBHOOK = "webhook"


class NotificationPriority(str, Enum):
    """Notification priority levels."""
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


@dataclass
class Notification:
    """Notification data."""
    notification_id: str
    user_id: str
    org_id: str
    channel: NotificationChannel
    title: str
    body: str
    data: Dict[str, Any]
    priority: NotificationPriority
    created_at: datetime
    sent_at: Optional[datetime] = None
    read_at: Optional[datetime] = None


class PushNotificationInput(BaseModel):
    """Input for push notification."""
    
    user_id: str = Field(...)
    org_id: str = Field(...)
    title: str = Field(...)
    body: str = Field(...)
    data: Dict[str, Any] = Field(default_factory=dict)
    priority: NotificationPriority = Field(default=NotificationPriority.NORMAL)
    
    class Config:
        json_schema_extra = {
            "example": {
                "user_id": "user_abc123",
                "org_id": "org_12345",
                "title": "New Ticket Assigned",
                "body": "You have been assigned to ticket TICKET-1234",
                "priority": "normal"
            }
        }


class ExpoPushClient:
    """
    Expo Push Notification client.
    
    Features:
    - Batch push notifications
    - Delivery tracking
    - Error handling
    
    Usage:
        client = ExpoPushClient()
        
        await client.send_push(
            push_token="ExponentPushToken[...]",
            title="Hello",
            body="World"
        )
    """
    
    EXPO_PUSH_API = "https://exp.host/--/api/v2/push/send"
    
    def __init__(self, access_token: Optional[str] = None):
        """
        Initialize Expo push client.
        
        Args:
            access_token: Expo access token
        """
        self.access_token = access_token
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info("expo_push_client_initialized")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
                "Content-Type": "application/json"
            }
            
            if self.access_token:
                headers["Authorization"] = f"Bearer {self.access_token}"
            
            self._http_client = httpx.AsyncClient(
                timeout=30,
                headers=headers
            )
        
        return self._http_client
    
    async def send_push(
        self,
        push_token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        priority: str = "normal",
        sound: str = "default",
        badge: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Send push notification.
        
        Args:
            push_token: Expo push token
            title: Notification title
            body: Notification body
            data: Additional data
            priority: Priority level
            sound: Sound to play
            badge: Badge count
            
        Returns:
            API response
        """
        client = await self._get_http_client()
        
        payload = {
            "to": push_token,
            "title": title,
            "body": body,
            "sound": sound,
            "priority": priority
        }
        
        if data:
            payload["data"] = data
        
        if badge is not None:
            payload["badge"] = badge
        
        try:
            response = await client.post(
                self.EXPO_PUSH_API,
                json=payload
            )
            response.raise_for_status()
            
            result = response.json()
            
            logger.debug(
                "push_notification_sent",
                push_token=push_token[:20] + "...",
                status="success"
            )
            
            return {
                "success": True,
                "data": result.get("data", {})
            }
            
        except httpx.HTTPError as e:
            logger.error(
                "push_notification_failed",
                push_token=push_token[:20] + "...",
                error=str(e)
            )
            return {
                "success": False,
                "error": str(e)
            }
    
    async def send_batch(
        self,
        notifications: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Send batch push notifications.
        
        Args:
            notifications: List of notification payloads
            
        Returns:
            List of results
        """
        client = await self._get_http_client()
        
        # Expo supports up to 100 notifications per request
        batch_size = 100
        results = []
        
        for i in range(0, len(notifications), batch_size):
            batch = notifications[i:i + batch_size]
            
            try:
                response = await client.post(
                    self.EXPO_PUSH_API,
                    json=batch
                )
                response.raise_for_status()
                
                result = response.json()
                results.extend(result.get("data", []))
                
            except httpx.HTTPError as e:
                logger.error("batch_push_failed", error=str(e))
                results.extend([{"status": "error", "error": str(e)}] * len(batch))
        
        return results
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


class NotificationManager:
    """
    Manages all notifications.
    
    Usage:
        manager = NotificationManager(db_pool)
        
        # Send notification
        await manager.send(
            user_id="user_123",
            title="Hello",
            body="World",
            channel=NotificationChannel.PUSH
        )
        
        # Get user notifications
        notifications = await manager.get_user_notifications("user_123")
    """
    
    def __init__(
        self,
        db_pool: Any,
        expo_client: Optional[ExpoPushClient] = None
    ):
        """
        Initialize notification manager.
        
        Args:
            db_pool: Database connection pool
            expo_client: Expo push client
        """
        self.db_pool = db_pool
        self.expo_client = expo_client or ExpoPushClient()
        
        logger.info("notification_manager_initialized")
    
    async def send(
        self,
        user_id: str,
        org_id: str,
        title: str,
        body: str,
        channel: NotificationChannel = NotificationChannel.PUSH,
        data: Optional[Dict[str, Any]] = None,
        priority: NotificationPriority = NotificationPriority.NORMAL
    ) -> Dict[str, Any]:
        """
        Send notification to user.
        
        Args:
            user_id: User ID
            org_id: Organization ID
            title: Notification title
            body: Notification body
            channel: Notification channel
            data: Additional data
            priority: Priority level
            
        Returns:
            Send result
        """
        notification_id = f"notif_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
        
        # Save notification
        await self._save_notification(
            notification_id=notification_id,
            user_id=user_id,
            org_id=org_id,
            channel=channel,
            title=title,
            body=body,
            data=data or {},
            priority=priority
        )
        
        # Send based on channel
        if channel == NotificationChannel.PUSH:
            return await self._send_push(
                user_id=user_id,
                title=title,
                body=body,
                data=data,
                priority=priority
            )
        elif channel == NotificationChannel.IN_APP:
            # In-app notifications are just saved
            return {"success": True, "channel": "in_app"}
        else:
            return {"success": False, "error": f"Channel {channel} not implemented"}
    
    async def _save_notification(
        self,
        notification_id: str,
        user_id: str,
        org_id: str,
        channel: NotificationChannel,
        title: str,
        body: str,
        data: Dict[str, Any],
        priority: NotificationPriority
    ):
        """Save notification to database."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO notifications (
                    notification_id, user_id, org_id, channel, title, body,
                    data, priority, created_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
                """,
                notification_id, user_id, org_id, channel.value,
                title, body, json.dumps(data), priority.value
            )
    
    async def _send_push(
        self,
        user_id: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        priority: NotificationPriority
    ) -> Dict[str, Any]:
        """Send push notification."""
        # Get user's push token
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT push_token FROM user_devices WHERE user_id = $1 AND is_active = true",
                user_id
            )
            
            if not row or not row["push_token"]:
                return {"success": False, "error": "No push token found"}
            
            push_token = row["push_token"]
        
        # Send via Expo
        result = await self.expo_client.send_push(
            push_token=push_token,
            title=title,
            body=body,
            data=data,
            priority=priority.value
        )
        
        return result
    
    async def get_user_notifications(
        self,
        user_id: str,
        unread_only: bool = False,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get user notifications.
        
        Args:
            user_id: User ID
            unread_only: Only return unread
            limit: Max results
            
        Returns:
            List of notifications
        """
        async with self.db_pool.acquire() as conn:
            if unread_only:
                rows = await conn.fetch(
                    """
                    SELECT * FROM notifications
                    WHERE user_id = $1 AND read_at IS NULL
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id, limit
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT * FROM notifications
                    WHERE user_id = $1
                    ORDER BY created_at DESC
                    LIMIT $2
                    """,
                    user_id, limit
                )
            
            return [
                {
                    "notification_id": row["notification_id"],
                    "title": row["title"],
                    "body": row["body"],
                    "channel": row["channel"],
                    "priority": row["priority"],
                    "data": json.loads(row.get("data", "{}")),
                    "created_at": row["created_at"].isoformat(),
                    "read_at": row["read_at"].isoformat() if row["read_at"] else None
                }
                for row in rows
            ]
    
    async def mark_as_read(self, notification_id: str) -> bool:
        """
        Mark notification as read.
        
        Args:
            notification_id: Notification ID
            
        Returns:
            True if successful
        """
        async with self.db_pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE notifications
                SET read_at = NOW()
                WHERE notification_id = $1
                """,
                notification_id
            )
            
            return result == "UPDATE 1"


# =============================================================================
# Cron Job for Scheduled Notifications
# =============================================================================

async def run_notification_cron():
    """
    Cron job entry point for scheduled notifications.
    
    Sends pending notifications and processes scheduled ones.
    """
    logger.info("notification_cron_started")
    
    # This would:
    # 1. Get pending notifications from queue
    # 2. Send them via appropriate channels
    # 3. Update delivery status
    # 4. Handle retries
    
    logger.info("notification_cron_completed")


# =============================================================================
# Database Schema
# =============================================================================

NOTIFICATIONS_SCHEMA = """
-- Notifications table
CREATE TABLE IF NOT EXISTS notifications (
    notification_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL,
    org_id VARCHAR(32) NOT NULL,
    channel VARCHAR(20) NOT NULL,
    title VARCHAR(255) NOT NULL,
    body TEXT NOT NULL,
    data JSONB DEFAULT '{}',
    priority VARCHAR(20) NOT NULL DEFAULT 'normal',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    sent_at TIMESTAMP,
    read_at TIMESTAMP,
    
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (org_id) REFERENCES organizations(org_id)
);

-- User devices table (for push tokens)
CREATE TABLE IF NOT EXISTS user_devices (
    device_id VARCHAR(32) PRIMARY KEY,
    user_id VARCHAR(32) NOT NULL,
    push_token VARCHAR(255) NOT NULL,
    platform VARCHAR(20),  -- ios, android, web
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    UNIQUE (push_token)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id);
CREATE INDEX IF NOT EXISTS idx_notifications_org ON notifications(org_id);
CREATE INDEX IF NOT EXISTS idx_notifications_read ON notifications(user_id, read_at);
CREATE INDEX IF NOT EXISTS idx_user_devices_user ON user_devices(user_id);
"""
