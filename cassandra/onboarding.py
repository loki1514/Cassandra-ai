"""
T48: Onboarding Flow

This module provides onboarding functionality:
- Org setup wizard
- Team invitation
- Initial configuration
- Getting started guide

Features:
- Step-by-step wizard
- Team management
- Configuration templates
"""

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List

import structlog
from pydantic import BaseModel, Field, EmailStr

logger = structlog.get_logger("cassandra.onboarding")


class OnboardingStep(str, Enum):
    """Onboarding steps."""
    ORG_INFO = "org_info"
    TEAM_SETUP = "team_setup"
    INTEGRATIONS = "integrations"
    VOICE_SETUP = "voice_setup"
    COMPLETE = "complete"


class OnboardingStatus(str, Enum):
    """Onboarding status."""
    NOT_STARTED = "not_started"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass
class OnboardingProgress:
    """Onboarding progress tracking."""
    org_id: str
    current_step: OnboardingStep
    completed_steps: List[OnboardingStep]
    status: OnboardingStatus
    started_at: datetime
    completed_at: Optional[datetime]


class OrgSetupInput(BaseModel):
    """Organization setup input."""
    
    org_name: str = Field(..., min_length=2, max_length=100)
    org_slug: str = Field(..., min_length=2, max_length=50)
    timezone: str = Field(default="UTC")
    industry: Optional[str] = Field(default=None)
    team_size: Optional[str] = Field(default=None)
    
    class Config:
        json_schema_extra = {
            "example": {
                "org_name": "Acme Corporation",
                "org_slug": "acme-corp",
                "timezone": "America/New_York",
                "industry": "technology",
                "team_size": "10-50"
            }
        }


class TeamInvitationInput(BaseModel):
    """Team invitation input."""
    
    emails: List[EmailStr] = Field(..., min_items=1, max_items=20)
    role: str = Field(default="member", regex="^(admin|manager|member)$")
    message: Optional[str] = Field(default=None)
    
    class Config:
        json_schema_extra = {
            "example": {
                "emails": ["user1@example.com", "user2@example.com"],
                "role": "member",
                "message": "Join our team on Cassandra AI!"
            }
        }


class OnboardingWizard:
    """
    Onboarding wizard for new organizations.
    
    Usage:
        wizard = OnboardingWizard(db_pool)
        
        # Start onboarding
        progress = await wizard.start_onboarding(org_id="org_123")
        
        # Complete step
        await wizard.complete_step(
            org_id="org_123",
            step=OnboardingStep.ORG_INFO,
            data={"org_name": "Acme Corp"}
        )
    """
    
    def __init__(self, db_pool: Any, email_service: Optional[Any] = None):
        """
        Initialize onboarding wizard.
        
        Args:
            db_pool: Database connection pool
            email_service: Email service for invitations
        """
        self.db_pool = db_pool
        self.email_service = email_service
        
        logger.info("onboarding_wizard_initialized")
    
    async def start_onboarding(self, org_id: str) -> OnboardingProgress:
        """
        Start onboarding for organization.
        
        Args:
            org_id: Organization ID
            
        Returns:
            OnboardingProgress
        """
        progress = OnboardingProgress(
            org_id=org_id,
            current_step=OnboardingStep.ORG_INFO,
            completed_steps=[],
            status=OnboardingStatus.IN_PROGRESS,
            started_at=datetime.utcnow(),
            completed_at=None
        )
        
        # Save to database
        await self._save_progress(progress)
        
        logger.info("onboarding_started", org_id=org_id)
        
        return progress
    
    async def _save_progress(self, progress: OnboardingProgress):
        """Save onboarding progress."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO onboarding_progress (
                    org_id, current_step, completed_steps, status,
                    started_at, completed_at
                ) VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (org_id) DO UPDATE SET
                    current_step = EXCLUDED.current_step,
                    completed_steps = EXCLUDED.completed_steps,
                    status = EXCLUDED.status,
                    completed_at = EXCLUDED.completed_at
                """,
                progress.org_id,
                progress.current_step.value,
                [s.value for s in progress.completed_steps],
                progress.status.value,
                progress.started_at,
                progress.completed_at
            )
    
    async def complete_step(
        self,
        org_id: str,
        step: OnboardingStep,
        data: Dict[str, Any]
    ) -> OnboardingProgress:
        """
        Complete an onboarding step.
        
        Args:
            org_id: Organization ID
            step: Step completed
            data: Step data
            
        Returns:
            Updated progress
        """
        # Get current progress
        progress = await self._get_progress(org_id)
        
        if not progress:
            raise ValueError(f"Onboarding not started for org {org_id}")
        
        # Update progress
        if step not in progress.completed_steps:
            progress.completed_steps.append(step)
        
        # Determine next step
        step_order = [
            OnboardingStep.ORG_INFO,
            OnboardingStep.TEAM_SETUP,
            OnboardingStep.INTEGRATIONS,
            OnboardingStep.VOICE_SETUP,
            OnboardingStep.COMPLETE
        ]
        
        current_index = step_order.index(step)
        if current_index < len(step_order) - 1:
            progress.current_step = step_order[current_index + 1]
        else:
            progress.current_step = OnboardingStep.COMPLETE
            progress.status = OnboardingStatus.COMPLETED
            progress.completed_at = datetime.utcnow()
        
        # Save step data
        await self._save_step_data(org_id, step, data)
        
        # Save progress
        await self._save_progress(progress)
        
        logger.info(
            "onboarding_step_completed",
            org_id=org_id,
            step=step.value,
            next_step=progress.current_step.value
        )
        
        return progress
    
    async def _get_progress(self, org_id: str) -> Optional[OnboardingProgress]:
        """Get onboarding progress."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM onboarding_progress WHERE org_id = $1",
                org_id
            )
            
            if not row:
                return None
            
            return OnboardingProgress(
                org_id=row["org_id"],
                current_step=OnboardingStep(row["current_step"]),
                completed_steps=[OnboardingStep(s) for s in row["completed_steps"]],
                status=OnboardingStatus(row["status"]),
                started_at=row["started_at"],
                completed_at=row.get("completed_at")
            )
    
    async def _save_step_data(self, org_id: str, step: OnboardingStep, data: Dict[str, Any]):
        """Save step data."""
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO onboarding_data (org_id, step, data, created_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (org_id, step) DO UPDATE SET
                    data = EXCLUDED.data,
                    created_at = EXCLUDED.created_at
                """,
                org_id, step.value, json.dumps(data)
            )
    
    async def setup_organization(
        self,
        org_id: str,
        setup_data: OrgSetupInput
    ) -> Dict[str, Any]:
        """
        Setup organization.
        
        Args:
            org_id: Organization ID
            setup_data: Organization setup data
            
        Returns:
            Setup result
        """
        async with self.db_pool.acquire() as conn:
            # Update organization
            await conn.execute(
                """
                UPDATE organizations
                SET name = $1,
                    slug = $2,
                    timezone = $3,
                    industry = $4,
                    team_size = $5,
                    updated_at = NOW()
                WHERE org_id = $6
                """,
                setup_data.org_name,
                setup_data.org_slug,
                setup_data.timezone,
                setup_data.industry,
                setup_data.team_size,
                org_id
            )
            
            # Complete step
            await self.complete_step(
                org_id=org_id,
                step=OnboardingStep.ORG_INFO,
                data=setup_data.dict()
            )
            
            logger.info("organization_setup_complete", org_id=org_id)
            
            return {
                "success": True,
                "org_id": org_id,
                "name": setup_data.org_name
            }
    
    async def invite_team_members(
        self,
        org_id: str,
        invitation: TeamInvitationInput,
        invited_by: str
    ) -> Dict[str, Any]:
        """
        Invite team members.
        
        Args:
            org_id: Organization ID
            invitation: Invitation data
            invited_by: User ID who is inviting
            
        Returns:
            Invitation result
        """
        invitations_sent = []
        
        async with self.db_pool.acquire() as conn:
            for email in invitation.emails:
                # Generate invitation token
                import secrets
                token = secrets.token_urlsafe(32)
                
                # Save invitation
                await conn.execute(
                    """
                    INSERT INTO team_invitations (
                        invitation_id, org_id, email, role, token,
                        invited_by, message, created_at, expires_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW() + INTERVAL '7 days')
                    """,
                    f"inv_{secrets.token_hex(16)}",
                    org_id,
                    email,
                    invitation.role,
                    token,
                    invited_by,
                    invitation.message
                )
                
                invitations_sent.append({
                    "email": email,
                    "token": token
                })
                
                # Send email (would use email service)
                if self.email_service:
                    await self.email_service.send_invitation(
                        email=email,
                        token=token,
                        message=invitation.message
                    )
        
        # Complete step
        await self.complete_step(
            org_id=org_id,
            step=OnboardingStep.TEAM_SETUP,
            data={"invitations_sent": len(invitations_sent)}
        )
        
        logger.info(
            "team_invitations_sent",
            org_id=org_id,
            count=len(invitations_sent)
        )
        
        return {
            "success": True,
            "invitations_sent": len(invitations_sent),
            "invitations": invitations_sent
        }
    
    async def get_onboarding_state(self, org_id: str) -> Dict[str, Any]:
        """
        Get current onboarding state.
        
        Args:
            org_id: Organization ID
            
        Returns:
            Onboarding state
        """
        progress = await self._get_progress(org_id)
        
        if not progress:
            return {
                "status": OnboardingStatus.NOT_STARTED.value,
                "current_step": OnboardingStep.ORG_INFO.value,
                "completed_steps": [],
                "progress_percent": 0
            }
        
        # Calculate progress
        total_steps = 4  # ORG_INFO, TEAM_SETUP, INTEGRATIONS, VOICE_SETUP
        completed = len(progress.completed_steps)
        progress_percent = min(100, int((completed / total_steps) * 100))
        
        return {
            "status": progress.status.value,
            "current_step": progress.current_step.value,
            "completed_steps": [s.value for s in progress.completed_steps],
            "progress_percent": progress_percent,
            "started_at": progress.started_at.isoformat(),
            "completed_at": progress.completed_at.isoformat() if progress.completed_at else None
        }


# =============================================================================
# Database Schema
# =============================================================================

ONBOARDING_SCHEMA = """
-- Onboarding progress table
CREATE TABLE IF NOT EXISTS onboarding_progress (
    org_id VARCHAR(32) PRIMARY KEY,
    current_step VARCHAR(20) NOT NULL DEFAULT 'org_info',
    completed_steps TEXT[] DEFAULT '{}',
    status VARCHAR(20) NOT NULL DEFAULT 'not_started',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    
    FOREIGN KEY (org_id) REFERENCES organizations(org_id)
);

-- Onboarding data table
CREATE TABLE IF NOT EXISTS onboarding_data (
    org_id VARCHAR(32) NOT NULL,
    step VARCHAR(20) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    
    PRIMARY KEY (org_id, step),
    FOREIGN KEY (org_id) REFERENCES organizations(org_id)
);

-- Team invitations table
CREATE TABLE IF NOT EXISTS team_invitations (
    invitation_id VARCHAR(32) PRIMARY KEY,
    org_id VARCHAR(32) NOT NULL,
    email VARCHAR(255) NOT NULL,
    role VARCHAR(20) NOT NULL DEFAULT 'member',
    token VARCHAR(255) NOT NULL UNIQUE,
    invited_by VARCHAR(32) NOT NULL,
    message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMP NOT NULL,
    accepted_at TIMESTAMP,
    accepted_by VARCHAR(32),
    
    FOREIGN KEY (org_id) REFERENCES organizations(org_id),
    FOREIGN KEY (invited_by) REFERENCES users(user_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_team_invitations_org ON team_invitations(org_id);
CREATE INDEX IF NOT EXISTS idx_team_invitations_token ON team_invitations(token);
CREATE INDEX IF NOT EXISTS idx_team_invitations_email ON team_invitations(email);
"""


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, HTTPException, Depends
from cassandra.auth import get_current_user, UserContext

router = APIRouter(prefix="/onboarding", tags=["Onboarding"])

_onboarding_wizard: Optional[OnboardingWizard] = None


def get_onboarding_wizard() -> OnboardingWizard:
    """Get or create onboarding wizard."""
    global _onboarding_wizard
    if _onboarding_wizard is None:
        raise RuntimeError("Onboarding wizard not initialized")
    return _onboarding_wizard


@router.get("/state/{org_id}")
async def get_onboarding_state(
    org_id: str,
    user: UserContext = Depends(get_current_user)
):
    """Get onboarding state for organization."""
    try:
        wizard = get_onboarding_wizard()
        state = await wizard.get_onboarding_state(org_id)
        return state
    except Exception as e:
        logger.error("get_onboarding_state_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{org_id}/setup")
async def setup_organization(
    org_id: str,
    setup_data: OrgSetupInput,
    user: UserContext = Depends(get_current_user)
):
    """Setup organization information."""
    try:
        wizard = get_onboarding_wizard()
        result = await wizard.setup_organization(org_id, setup_data)
        return result
    except Exception as e:
        logger.error("setup_organization_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{org_id}/invite")
async def invite_team_members(
    org_id: str,
    invitation: TeamInvitationInput,
    user: UserContext = Depends(get_current_user)
):
    """Invite team members."""
    try:
        wizard = get_onboarding_wizard()
        result = await wizard.invite_team_members(
            org_id=org_id,
            invitation=invitation,
            invited_by=user.user_id
        )
        return result
    except Exception as e:
        logger.error("invite_team_members_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
