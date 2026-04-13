"""
T45: Features Router — FastAPI routes for all feature service modules.

This module mounts all 18 feature service classes as REST API endpoints
under /api/v1/features/. All endpoints require JWT authentication.

Architecture:
    Expo/Client → FastAPI → Feature Service → Supabase/Supermemory

Services are instantiated lazily on first request to avoid circular
import issues and to ensure settings are loaded before service init.
"""

from typing import Any, Dict, Optional
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from cassandra.auth import get_current_user, UserContext

logger_context = {"router": "features"}


# =============================================================================
# Pydantic Request/Response Models
# =============================================================================


class SpeakerContext(BaseModel):
    """Speaker context for voice command processing."""
    session_id: Optional[str] = None
    audio_available: bool = False
    transcript: Optional[str] = None


# --- AI ---
class PredictiveTicketsRequest(BaseModel):
    org_id: str


class PredictiveTicketsResponse(BaseModel):
    suggested_tickets: list[Dict[str, Any]]
    org_id: str


# --- BD Intelligence ---
class FeasibilityReportRequest(BaseModel):
    property_info: Dict[str, Any]
    org_id: str


# --- Chat / Research ---
class ResearchQueryRequest(BaseModel):
    query: str
    org_id: str


# --- AR Inspection ---
class ARInspectionRequest(BaseModel):
    image_data: str = Field(description="Base64-encoded image data")
    gps_lat: float
    gps_lng: float
    org_id: str


# --- Compliance Templates ---
class ComplianceTemplatesResponse(BaseModel):
    templates: Dict[str, Any]


# --- Drift Detection ---
class DriftCheckRequest(BaseModel):
    org_id: str


# --- Photo Evidence ---
class PhotoCaptureRequest(BaseModel):
    image_data: str = Field(description="Base64-encoded image data")
    checklist_item_id: str
    org_id: str


# --- Voice Checklist ---
class VoiceChecklistRequest(BaseModel):
    audio_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# --- OPEX Estimation ---
class OPEXEstimateRequest(BaseModel):
    sqft: int = Field(gt=0, description="Property square footage")
    property_type: str
    city: str
    org_id: str


# --- Integration Hub ---
class NotionPushRequest(BaseModel):
    data_type: str
    data: Dict[str, Any]
    org_id: str


# --- Operational Excellence ---
class QueueCommandRequest(BaseModel):
    command_text: str
    org_id: str
    audio_available: bool = False


# --- Reporting ---
class ReportGenerateRequest(BaseModel):
    report_type: str
    property_id: str
    period: str
    org_id: str


# --- Self-Healing ---
class QualityIssueRequest(BaseModel):
    query: str
    cassandra_answer: str
    correct_answer: str
    failure_category: str
    org_id: str


# --- Voice: Smart Queries ---
class SmartQueryRequest(BaseModel):
    query_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# --- Voice: Natural Language Tickets ---
class NLTicketRequest(BaseModel):
    audio_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# --- Voice: Batch Commands ---
class BatchCommandRequest(BaseModel):
    audio_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# --- Voice: Escalation ---
class EscalationRequest(BaseModel):
    audio_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# --- Voice: Snooze/Reschedule ---
class RescheduleRequest(BaseModel):
    audio_text: str
    org_id: str
    speaker_context: Optional[SpeakerContext] = None


# =============================================================================
# Feature Service Factories (lazy initialization)
# =============================================================================


@lru_cache()
def _get_ai_service() -> "AdvancedAIService":
    from features.ai_features.advanced_ai import AdvancedAIService
    from cassandra.supabase import get_supabase_client
    from cassandra.rag.context_fetcher import fetch_full_context

    db = get_supabase_client()
    return AdvancedAIService(
        db_client=db,
        llm_client=None,  # Uses fetch_full_context internally
        memory_manager=None,
        notification_service=None,
    )


@lru_cache()
def _get_bd_service() -> "BDIntelligenceService":
    from features.bd.bd_intelligence import BDIntelligenceService
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return BDIntelligenceService(
        perplexity_client=None,
        db_client=db,
        truth_ledger=None,
        analytics=None,
    )


@lru_cache()
def _get_chat_service() -> "PerplexityChatService":
    from features.chat.perplexity_chat import PerplexityChatService
    from cassandra.supabase import get_supabase_client
    from cassandra.rag.context_fetcher import fetch_full_context

    db = get_supabase_client()
    return PerplexityChatService(
        perplexity_client=None,
        notion_client=None,
        memory_manager=None,
        db_client=db,
    )


@lru_cache()
def _get_ar_service() -> "ARInspectionProcessor":
    from features.checklists.ar_inspection import ARInspectionProcessor
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return ARInspectionProcessor(
        ocr_service=None,
        db_client=db,
        storage_service=None,
        memory_manager=None,
    )


@lru_cache()
def _get_drift_service() -> "ChecklistDriftDetector":
    from features.checklists.drift_detection import ChecklistDriftDetector
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return ChecklistDriftDetector(
        db_client=db,
        notification_service=None,
        memory_manager=None,
        ticket_tool=None,
    )


@lru_cache()
def _get_photo_service() -> "PhotoEvidenceProcessor":
    from features.checklists.photo_evidence import PhotoEvidenceProcessor
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return PhotoEvidenceProcessor(
        storage_service=None,
        vision_client=None,
        db_client=db,
        memory_manager=None,
        ticket_tool=None,
    )


@lru_cache()
def _get_voice_checklist_service() -> "VoiceChecklistProcessor":
    from features.checklists.voice_checklist import VoiceChecklistProcessor
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return VoiceChecklistProcessor(
        db_client=db,
        memory_manager=None,
        fuzzy_matcher=None,
    )


@lru_cache()
def _get_opex_service() -> "OPEXEstimator":
    from features.facility.opex_estimation import OPEXEstimator
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return OPEXEstimator(
        perplexity_client=None,
        db_client=db,
        analytics_service=None,
    )


@lru_cache()
def _get_integration_service() -> "IntegrationHub":
    from features.integrations.integration_hub import IntegrationHub
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return IntegrationHub(
        notion_client=None,
        whatsapp_client=None,
        calendar_client=None,
        iot_client=None,
        erp_client=None,
        db_client=db,
        notification_service=None,
    )


@lru_cache()
def _get_ops_service() -> "OperationalExcellenceService":
    from features.operations.operational_excellence import OperationalExcellenceService
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return OperationalExcellenceService(
        db_client=db,
        storage_service=None,
        location_service=None,
        notification_service=None,
        memory_manager=None,
    )


@lru_cache()
def _get_reporting_service() -> "ReportingEngine":
    from features.reports.reporting_engine import ReportingEngine
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return ReportingEngine(
        db_client=db,
        pdf_engine=None,
        notion_client=None,
        analytics_service=None,
        llm_client=None,
    )


@lru_cache()
def _get_quality_service() -> "SelfHealingService":
    from features.self_healing.quality_loop import SelfHealingService
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return SelfHealingService(
        notion_client=None,
        db_client=db,
        memory_manager=None,
        llm_client=None,
    )


@lru_cache()
def _get_smart_query_service() -> "SmartStatusQueryProcessor":
    from features.voice.smart_queries import SmartStatusQueryProcessor
    from cassandra.supabase import get_supabase_client
    from cassandra.rag.context_fetcher import fetch_full_context

    db = get_supabase_client()
    return SmartStatusQueryProcessor(
        context_fetcher=fetch_full_context,
        memory_manager=None,
        db_client=db,
    )


@lru_cache()
def _get_nl_ticket_service() -> "NaturalLanguageTicketProcessor":
    from features.voice.natural_language_tickets import NaturalLanguageTicketProcessor
    from cassandra.rag.context_fetcher import fetch_full_context

    return NaturalLanguageTicketProcessor(
        llm_client=None,
        ticket_tool=None,
        speaker_id_service=None,
    )


@lru_cache()
def _get_batch_service() -> "BatchTicketProcessor":
    from features.voice.batch_commands import BatchTicketProcessor
    from cassandra.rag.context_fetcher import fetch_full_context

    return BatchTicketProcessor(
        nl_ticket_processor=None,
        ticket_tool=None,
    )


@lru_cache()
def _get_escalation_service() -> "VoiceEscalationProcessor":
    from features.voice.escalation_commands import VoiceEscalationProcessor
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return VoiceEscalationProcessor(
        backend_api=None,
        notification_service=None,
        memory_manager=None,
        audit_logger=None,
    )


@lru_cache()
def _get_reschedule_service() -> "VoiceRescheduleProcessor":
    from features.voice.snooze_reschedule import VoiceRescheduleProcessor
    from cassandra.supabase import get_supabase_client

    db = get_supabase_client()
    return VoiceRescheduleProcessor(
        backend_api=None,
        memory_manager=None,
        notification_service=None,
    )


# =============================================================================
# API Router
# =============================================================================

features_router = APIRouter(prefix="/api/v1/features", tags=["Features"])


# =============================================================================
# AI Endpoints
# =============================================================================


@features_router.post("/ai/predictive-tickets")
async def api_predictive_tickets(
    req: PredictiveTicketsRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Analyze patterns and suggest tickets before issues occur.
    Uses dual-read context from Supabase + Supermemory.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_ai_service()
    return await service.suggest_predictive_tickets(org_id=req.org_id)


# =============================================================================
# BD Intelligence Endpoints
# =============================================================================


@features_router.post("/bd/feasibility-report")
async def api_feasibility_report(
    req: FeasibilityReportRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate a property feasibility report for business development.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_bd_service()
    return await service.generate_feasibility_report(
        property_info=req.property_info,
        org_id=req.org_id,
    )


# =============================================================================
# Chat / Research Endpoints
# =============================================================================


@features_router.post("/chat/research")
async def api_research(
    req: ResearchQueryRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Research a query using Perplexity with context from memory.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_chat_service()
    return await service.research_query(
        query=req.query,
        org_id=req.org_id,
        user_id=user.user_id,
    )


# =============================================================================
# Checklist Endpoints
# =============================================================================


@features_router.post("/checklists/ar-process")
async def api_ar_inspection(
    req: ARInspectionRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process an AR (augmented reality) asset scan.
    Takes a base64-encoded image and GPS coordinates.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    import base64
    service = _get_ar_service()
    image_bytes = base64.b64decode(req.image_data)
    gps_location = {"lat": req.gps_lat, "lng": req.gps_lng}

    return await service.process_asset_scan(
        image_data=image_bytes,
        gps_location=gps_location,
        org_id=req.org_id,
        user_id=user.user_id,
    )


@features_router.get("/checklists/compliance-templates")
async def api_compliance_templates(
    user: UserContext = Depends(get_current_user),
) -> ComplianceTemplatesResponse:
    """
    Get available compliance checklist templates.
    """
    from features.checklists.compliance_templates import ComplianceTemplateGenerator
    return ComplianceTemplatesResponse(
        templates=ComplianceTemplateGenerator.TEMPLATE_LIBRARY
    )


@features_router.post("/checklists/drift-check")
async def api_drift_check(
    req: DriftCheckRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Run daily checklist completion drift detection for an organization.
    Detects zones where compliance is slipping and alerts.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_drift_service()
    return await service.run_daily_drift_check(org_id=req.org_id)


@features_router.post("/checklists/photo-capture")
async def api_photo_capture(
    req: PhotoCaptureRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Capture a completion photo for a checklist item.
    Performs defect detection using vision AI.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    import base64
    service = _get_photo_service()
    image_bytes = base64.b64decode(req.image_data)

    return await service.capture_completion_photo(
        image_data=image_bytes,
        checklist_item_id=req.checklist_item_id,
        user_id=user.user_id,
        org_id=req.org_id,
    )


@features_router.post("/checklists/voice-process")
async def api_voice_checklist(
    req: VoiceChecklistRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a voice command for checklist operations.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_voice_checklist_service()
    return await service.process_checklist_command(
        audio_text=req.audio_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )


# =============================================================================
# Facility Endpoints
# =============================================================================


@features_router.post("/facility/opex-estimate")
async def api_opex_estimate(
    req: OPEXEstimateRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Estimate operational expenses for a property using AI.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_opex_service()
    return await service.estimate_opex(
        sqft=req.sqft,
        property_type=req.property_type,
        city=req.city,
        org_id=req.org_id,
    )


# =============================================================================
# Integration Endpoints
# =============================================================================


@features_router.post("/integrations/notion")
async def api_push_notion(
    req: NotionPushRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Push data to Notion via the integration hub.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_integration_service()
    return await service.push_to_notion(
        data_type=req.data_type,
        data=req.data,
        org_id=req.org_id,
    )


# =============================================================================
# Operations Endpoints
# =============================================================================


@features_router.post("/operations/queue-command")
async def api_queue_command(
    req: QueueCommandRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Queue an offline voice command for execution when connectivity returns.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_ops_service()
    return await service.queue_offline_command(
        audio_data=b"",  # Not provided via REST; use audio_text instead
        command_text=req.command_text,
        user_id=user.user_id,
        org_id=req.org_id,
    )


# =============================================================================
# Reporting Endpoints
# =============================================================================


@features_router.post("/reports/generate")
async def api_generate_report(
    req: ReportGenerateRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Generate a report (PDF or voice summary) from voice command.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_reporting_service()
    return await service.generate_voice_report(
        report_type=req.report_type,
        property_id=req.property_id,
        period=req.period,
        org_id=req.org_id,
    )


# =============================================================================
# Self-Healing / Quality Loop Endpoints
# =============================================================================


@features_router.post("/quality/log-issue")
async def api_log_quality_issue(
    req: QualityIssueRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Log a quality issue when Cassandra gave a wrong answer.
    Used for continuous improvement of the LLM prompt.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_quality_service()
    return await service.log_quality_issue(
        query=req.query,
        cassandra_answer=req.cassandra_answer,
        correct_answer=req.correct_answer,
        failure_category=req.failure_category,
        org_id=req.org_id,
    )


@features_router.get("/quality/weekly-analysis")
async def api_weekly_quality_analysis(
    org_id: str,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Analyze weekly quality failures and propose prompt improvements.
    """
    if user.org_id != org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    service = _get_quality_service()
    return await service.analyze_weekly_failures(org_id=org_id)


# =============================================================================
# Voice Command Endpoints
# =============================================================================


@features_router.post("/voice/smart-query")
async def api_smart_query(
    req: SmartQueryRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a natural language status query (e.g. "What's the status of HVAC tickets?").
    Uses dual-read from Supabase + Supermemory.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_smart_query_service()
    return await service.process_status_query(
        query_text=req.query_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )


@features_router.post("/voice/ticket")
async def api_nl_ticket(
    req: NLTicketRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a natural language ticket creation command via voice.
    Extracts title, assignee, asset, deadline, priority from text.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_nl_ticket_service()
    return await service.process_voice_command(
        audio_text=req.audio_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )


@features_router.post("/voice/batch")
async def api_batch_commands(
    req: BatchCommandRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process batch voice commands (e.g. "Create tickets for the kitchen, the
    lobby, and the elevator"). Extracts multiple tickets from one utterance.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_batch_service()
    return await service.process_batch_command(
        audio_text=req.audio_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )


@features_router.post("/voice/escalate")
async def api_escalate(
    req: EscalationRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a voice escalation command.
    Supports LOW, MEDIUM, HIGH, CRITICAL escalation levels.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_escalation_service()
    return await service.process_escalation_command(
        audio_text=req.audio_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )


@features_router.post("/voice/snooze")
async def api_snooze(
    req: RescheduleRequest,
    user: UserContext = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Process a voice snooze or reschedule command.
    """
    if user.org_id != req.org_id:
        raise HTTPException(status_code=403, detail="Access denied for this organization")

    speaker_ctx = (req.speaker_context or SpeakerContext()).model_dump()
    service = _get_reschedule_service()
    return await service.process_reschedule_command(
        audio_text=req.audio_text,
        org_id=req.org_id,
        speaker_context=speaker_ctx,
    )
