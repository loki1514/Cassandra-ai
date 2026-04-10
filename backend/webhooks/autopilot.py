"""
backend/webhooks/autopilot.py — Autopilot external API client.

This client handles all external API calls triggered by Cassandra's tool execution.
It provides a clean interface for calling the Autopilot platform's voice tools API.
"""

import asyncio
from dataclasses import dataclass

import httpx

from backend.config import get_settings
from backend.utils.circuit_breaker import get_breaker_registry
from backend.utils.logging_config import get_logger

logger = get_logger("cassandra.webhooks.autopilot")


@dataclass
class AutopilotResponse:
    """Response from the Autopilot API."""

    success: bool
    data: dict | None
    error: str | None
    status_code: int


class AutopilotClient:
    """
    Client for the Autopilot voice tools API.

    Autopilot provides domain-specific actions that Cassandra can trigger:
    - Create support tickets
    - Update CRM records
    - Send notifications
    - Schedule meetings
    - Query knowledge bases
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
    ):
        settings = get_settings()
        self._base_url = base_url or settings.autopilot_api_base_url
        self._api_key = api_key or settings.autopilot_api_key
        self._timeout = 30.0
        self._circuit_breaker = get_breaker_registry().get(
            "autopilot_api",
            failure_threshold=3,
            recovery_timeout=60.0,
        )

    @property
    def is_configured(self) -> bool:
        """Return True if the client is properly configured."""
        return bool(self._base_url and self._api_key)

    async def create_ticket(
        self,
        title: str,
        description: str,
        priority: str = "medium",
        assignee: str = "",
        tags: list[str] | None = None,
        session_id: str = "",
        org_id: str = "",
    ) -> AutopilotResponse:
        """
        Create a support ticket in the Autopilot system.

        Args:
            title: Ticket title.
            description: Detailed description.
            priority: low, medium, high, critical.
            assignee: Assigned person email.
            tags: List of tags.
            session_id: Cassandra session ID for tracking.
            org_id: Organization ID.

        Returns:
            AutopilotResponse with ticket creation result.
        """
        return await self._call(
            action="create-ticket",
            params={
                "title": title,
                "description": description,
                "priority": priority,
                "assignee": assignee,
                "tags": tags or [],
                "metadata": {
                    "source": "cassandra_voice",
                    "session_id": session_id,
                    "org_id": org_id,
                },
            },
        )

    async def send_notification(
        self,
        recipient: str,
        message: str,
        channel: str = "email",
        session_id: str = "",
        org_id: str = "",
    ) -> AutopilotResponse:
        """Send a notification via Autopilot."""
        return await self._call(
            action="send-notification",
            params={
                "recipient": recipient,
                "message": message,
                "channel": channel,
                "metadata": {
                    "source": "cassandra_voice",
                    "session_id": session_id,
                    "org_id": org_id,
                },
            },
        )

    async def update_crm_record(
        self,
        entity_type: str,
        entity_id: str,
        fields: dict,
        session_id: str = "",
        org_id: str = "",
    ) -> AutopilotResponse:
        """Update a CRM record."""
        return await self._call(
            action="update-crm-record",
            params={
                "entity_type": entity_type,
                "entity_id": entity_id,
                "fields": fields,
                "metadata": {
                    "source": "cassandra_voice",
                    "session_id": session_id,
                    "org_id": org_id,
                },
            },
        )

    async def schedule_meeting(
        self,
        title: str,
        participants: list[str],
        duration_minutes: int = 30,
        description: str = "",
        session_id: str = "",
        org_id: str = "",
    ) -> AutopilotResponse:
        """Schedule a meeting via Autopilot."""
        return await self._call(
            action="schedule-meeting",
            params={
                "title": title,
                "participants": participants,
                "duration_minutes": duration_minutes,
                "description": description,
                "metadata": {
                    "source": "cassandra_voice",
                    "session_id": session_id,
                    "org_id": org_id,
                },
            },
        )

    async def _call(
        self,
        action: str,
        params: dict,
    ) -> AutopilotResponse:
        """
        Make an API call to the Autopilot voice tools endpoint.

        Args:
            action: The action name (e.g., 'create-ticket').
            params: Action-specific parameters.

        Returns:
            AutopilotResponse with the result.
        """
        if not self.is_configured:
            logger.warning("autopilot_not_configured", action=action)
            return AutopilotResponse(
                success=False,
                data=None,
                error="Autopilot API not configured",
                status_code=0,
            )

        url = f"{self._base_url}/api/voice-tools/{action}"

        async def _do_request():
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json=params,
                )
                return response

        try:
            response = await self._circuit_breaker.call(_do_request)

            if response.status_code >= 200 and response.status_code < 300:
                return AutopilotResponse(
                    success=True,
                    data=response.json() if response.text else {},
                    error=None,
                    status_code=response.status_code,
                )
            else:
                logger.error(
                    "autopilot_api_error",
                    action=action,
                    status=response.status_code,
                    body=response.text[:200],
                )
                return AutopilotResponse(
                    success=False,
                    data=None,
                    error=f"HTTP {response.status_code}: {response.text[:200]}",
                    status_code=response.status_code,
                )

        except Exception as exc:
            logger.error("autopilot_call_failed", action=action, error=str(exc))
            return AutopilotResponse(
                success=False,
                data=None,
                error=str(exc),
                status_code=0,
            )


# Global client instance
_autopilot_client: AutopilotClient | None = None


def get_autopilot_client() -> AutopilotClient:
    """Get the global Autopilot client instance."""
    global _autopilot_client
    if _autopilot_client is None:
        _autopilot_client = AutopilotClient()
    return _autopilot_client
