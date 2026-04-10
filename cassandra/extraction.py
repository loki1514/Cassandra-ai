"""
T12: LLM Extraction — Commitments & Deadlines

This module provides commitment extraction from transcripts using LLM.

Features:
- Extract commitments with deadlines from conversation transcripts
- Structured output with confidence scores
- Flag low-confidence extractions for review
- Test with synthetic transcripts

Output Format:
{
    speaker_id: str,
    commitment_text: str,
    deadline_date: Optional[str],  # ISO format or None
    entity_type: str,  # 'commitment', 'action_item', 'deadline'
    confidence: float  # 0.0 - 1.0
}
"""

import json
import re
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from enum import Enum

import openai
import structlog
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

from cassandra.config import settings

logger = structlog.get_logger("cassandra.extraction")


# =============================================================================
# Data Models
# =============================================================================

class EntityType(str, Enum):
    """Types of extracted entities."""
    COMMITMENT = "commitment"
    ACTION_ITEM = "action_item"
    DEADLINE = "deadline"
    FOLLOW_UP = "follow_up"


@dataclass
class ExtractedCommitment:
    """
    Extracted commitment from transcript.
    
    Attributes:
        speaker_id: Speaker who made the commitment
        commitment_text: The commitment statement
        deadline_date: Deadline in ISO format (YYYY-MM-DD) or None
        entity_type: Type of entity extracted
        confidence: Confidence score (0.0 - 1.0)
        raw_text: Original transcript segment
        requires_review: True if confidence < 0.7
    """
    speaker_id: str
    commitment_text: str
    deadline_date: Optional[str]
    entity_type: str
    confidence: float
    raw_text: str
    requires_review: bool = False
    
    def __post_init__(self):
        """Set requires_review flag based on confidence."""
        self.requires_review = self.confidence < 0.7
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)


# =============================================================================
# Custom Exceptions
# =============================================================================

class ExtractionError(Exception):
    """Base exception for extraction errors."""
    pass


class LLMError(ExtractionError):
    """Raised when LLM call fails."""
    pass


class ParseError(ExtractionError):
    """Raised when response parsing fails."""
    pass


# =============================================================================
# LLM Client
# =============================================================================

class LLMClient:
    """
    Client for OpenAI LLM API.
    
    Features:
    - Structured JSON output
    - Retry with exponential backoff
    - Temperature control for consistency
    """
    
    def __init__(self):
        self.api_key = settings.openai.api_key
        self.model = settings.openai.model
        self.max_tokens = settings.openai.max_tokens
        self.temperature = settings.openai.temperature
        
        # Initialize OpenAI client
        self.client = openai.AsyncOpenAI(
            api_key=self.api_key,
            organization=settings.openai.organization
        )
        
        logger.info(
            "llm_client_initialized",
            model=self.model,
            max_tokens=self.max_tokens
        )
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((
            openai.RateLimitError,
            openai.APIError,
            openai.APITimeoutError
        ))
    )
    async def complete(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        response_format: Optional[Dict[str, str]] = None
    ) -> str:
        """
        Send completion request to LLM.
        
        Args:
            messages: List of message dicts with role and content
            temperature: Override default temperature
            response_format: Optional response format (e.g., {"type": "json_object"})
            
        Returns:
            LLM response content
        """
        try:
            params = {
                "model": self.model,
                "messages": messages,
                "max_tokens": self.max_tokens,
                "temperature": temperature or self.temperature
            }
            
            if response_format:
                params["response_format"] = response_format
            
            response = await self.client.chat.completions.create(**params)
            
            content = response.choices[0].message.content
            
            logger.debug(
                "llm_completion_success",
                tokens_used=response.usage.total_tokens if response.usage else 0
            )
            
            return content
            
        except openai.AuthenticationError as e:
            logger.error("llm_auth_error")
            raise LLMError("Authentication failed") from e
            
        except openai.RateLimitError as e:
            logger.warning("llm_rate_limited")
            raise
            
        except Exception as e:
            logger.error(
                "llm_completion_error",
                error_type=type(e).__name__
            )
            raise LLMError(f"LLM completion failed: {str(e)}") from e


# Global client instance
_llm_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """Get or create LLM client instance."""
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client


# =============================================================================
# Extraction Functions
# =============================================================================

EXTRACTION_PROMPT = """You are an expert at extracting commitments, action items, and deadlines from conversation transcripts.

Analyze the following transcript and extract all commitments made by speakers.

For each commitment, identify:
1. Who made the commitment (speaker_id)
2. What they committed to (commitment_text)
3. When it should be completed (deadline_date in YYYY-MM-DD format, or null if not specified)
4. The type of entity: "commitment" (formal promise), "action_item" (task), "deadline" (time-bound), or "follow_up" (needs follow-up)
5. Your confidence in this extraction (0.0 to 1.0)

Guidelines:
- Look for phrases like "I will", "I'll", "I promise", "I need to", "I have to", "let me", "I'll get back to you"
- Relative dates ("tomorrow", "next week") should be converted to absolute dates assuming today is {today}
- If no deadline is mentioned, set deadline_date to null
- Be precise with confidence scores: high (0.8-1.0) for clear commitments, medium (0.5-0.79) for ambiguous ones, low (<0.5) for unclear
- Confidence < 0.7 will be flagged for human review

Return ONLY a JSON object in this exact format:
{{
    "commitments": [
        {{
            "speaker_id": "A",
            "commitment_text": "I will send the report by Friday",
            "deadline_date": "2024-01-12",
            "entity_type": "commitment",
            "confidence": 0.92
        }}
    ]
}}

Transcript:
{transcript}
"""


async def extract_commitments(
    transcript: str,
    reference_date: Optional[datetime] = None
) -> List[ExtractedCommitment]:
    """
    Extract commitments from transcript using LLM.
    
    Args:
        transcript: Conversation transcript with speaker labels
        reference_date: Date for interpreting relative dates (default: today)
        
    Returns:
        List of ExtractedCommitment objects
        
    Example:
        >>> transcript = "A: I will send the report tomorrow.\\nB: Great, thanks!"
        >>> commitments = await extract_commitments(transcript)
        >>> for c in commitments:
        ...     print(f"{c.speaker_id}: {c.commitment_text} (confidence: {c.confidence})")
    """
    if not transcript or not transcript.strip():
        logger.info("empty_transcript")
        return []
    
    reference_date = reference_date or datetime.now()
    today_str = reference_date.strftime("%Y-%m-%d")
    
    # Build prompt
    prompt = EXTRACTION_PROMPT.format(
        today=today_str,
        transcript=transcript
    )
    
    messages = [
        {"role": "system", "content": "You are a precise commitment extraction assistant. Return only valid JSON."},
        {"role": "user", "content": prompt}
    ]
    
    try:
        # Call LLM
        client = get_llm_client()
        response = await client.complete(
            messages=messages,
            temperature=0.3,  # Lower temperature for consistency
            response_format={"type": "json_object"}
        )
        
        # Parse response
        data = json.loads(response)
        commitments_data = data.get("commitments", [])
        
        # Convert to ExtractedCommitment objects
        commitments = []
        for item in commitments_data:
            commitment = ExtractedCommitment(
                speaker_id=item.get("speaker_id", "UNKNOWN"),
                commitment_text=item.get("commitment_text", ""),
                deadline_date=_parse_date(item.get("deadline_date")),
                entity_type=item.get("entity_type", "commitment"),
                confidence=float(item.get("confidence", 0.0)),
                raw_text=transcript[:500]  # Store first 500 chars for context
            )
            commitments.append(commitment)
        
        # Log results
        review_count = sum(1 for c in commitments if c.requires_review)
        logger.info(
            "commitments_extracted",
            total=len(commitments),
            requires_review=review_count,
            avg_confidence=round(sum(c.confidence for c in commitments) / len(commitments), 2) if commitments else 0
        )
        
        return commitments
        
    except json.JSONDecodeError as e:
        logger.error(
            "commitment_parse_error",
            error=str(e),
            response_preview=response[:200] if 'response' in locals() else "N/A"
        )
        raise ParseError(f"Failed to parse LLM response: {str(e)}") from e
        
    except Exception as e:
        logger.error(
            "commitment_extraction_error",
            error_type=type(e).__name__
        )
        raise ExtractionError(f"Commitment extraction failed: {str(e)}") from e


def _parse_date(date_str: Optional[str]) -> Optional[str]:
    """
    Parse and validate date string.
    
    Args:
        date_str: Date string in various formats
        
    Returns:
        Validated ISO date string (YYYY-MM-DD) or None
    """
    if not date_str or date_str.lower() in ("null", "none", "", "n/a"):
        return None
    
    # Try to parse various formats
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%B %d, %Y",
        "%b %d, %Y"
    ]
    
    for fmt in formats:
        try:
            parsed = datetime.strptime(date_str, fmt)
            return parsed.strftime("%Y-%m-%d")
        except ValueError:
            continue
    
    # If parsing fails, return None
    logger.warning("date_parse_failed", date_str=date_str)
    return None


# =============================================================================
# Batch Processing
# =============================================================================

async def extract_commitments_batch(
    transcripts: List[str],
    reference_date: Optional[datetime] = None
) -> List[List[ExtractedCommitment]]:
    """
    Extract commitments from multiple transcripts.
    
    Args:
        transcripts: List of transcript strings
        reference_date: Reference date for relative dates
        
    Returns:
        List of commitment lists (one per transcript)
    """
    tasks = [extract_commitments(t, reference_date) for t in transcripts]
    return await asyncio.gather(*tasks, return_exceptions=True)


# =============================================================================
# Synthetic Test Data
# =============================================================================

SYNTHETIC_TRANSCRIPTS = [
    """
    A: Hi, thanks for joining the call today.
    B: No problem. So, about the project report - I'll have it ready by Friday.
    A: Great. And can you also review the design mockups?
    B: Sure, I'll take a look at them tomorrow morning.
    A: Perfect. I need to follow up with the client by next Wednesday.
    B: I'll send you my feedback by end of day today.
    """,
    """
    A: We need to finalize the budget for Q2.
    B: I'll compile the numbers and send them over by March 15th.
    A: Thanks. Also, I promised the team I'd schedule the offsite.
    B: When are you planning to do that?
    A: I'll send out a poll by the end of this week.
    C: I can help with the venue research. Let me know what you need.
    """,
    """
    A: The deployment failed last night.
    B: I'll investigate the issue and get back to you within 2 hours.
    A: We also need to update the documentation.
    B: I'll handle that tomorrow after we fix the deployment.
    A: Make sure to test everything in staging first.
    B: Absolutely. I'll run the full test suite before pushing to production.
    """
]


async def run_synthetic_tests() -> Dict[str, Any]:
    """
    Run extraction tests with synthetic transcripts.
    
    Returns:
        Test results with statistics
    """
    logger.info("running_synthetic_tests")
    
    results = []
    for i, transcript in enumerate(SYNTHETIC_TRANSCRIPTS):
        try:
            commitments = await extract_commitments(transcript)
            results.append({
                "test_id": i + 1,
                "success": True,
                "commitments": [c.to_dict() for c in commitments],
                "count": len(commitments)
            })
        except Exception as e:
            results.append({
                "test_id": i + 1,
                "success": False,
                "error": str(e)
            })
    
    # Calculate statistics
    total_commitments = sum(r.get("count", 0) for r in results if r["success"])
    success_count = sum(1 for r in results if r["success"])
    
    summary = {
        "total_tests": len(SYNTHETIC_TRANSCRIPTS),
        "successful": success_count,
        "failed": len(SYNTHETIC_TRANSCRIPTS) - success_count,
        "total_commitments_extracted": total_commitments,
        "results": results
    }
    
    logger.info("synthetic_tests_complete", summary=summary)
    return summary


# =============================================================================
# Utility Functions
# =============================================================================

def format_commitment_for_display(commitment: ExtractedCommitment) -> str:
    """Format commitment for human-readable display."""
    deadline = commitment.deadline_date or "No deadline"
    review_flag = " [REVIEW]" if commitment.requires_review else ""
    return f"[{commitment.speaker_id}] {commitment.commitment_text} (by {deadline}){review_flag}"


def filter_by_confidence(
    commitments: List[ExtractedCommitment],
    min_confidence: float = 0.7
) -> List[ExtractedCommitment]:
    """Filter commitments by minimum confidence."""
    return [c for c in commitments if c.confidence >= min_confidence]


def get_commitments_needing_review(
    commitments: List[ExtractedCommitment]
) -> List[ExtractedCommitment]:
    """Get commitments flagged for review."""
    return [c for c in commitments if c.requires_review]


# Import asyncio for batch processing
import asyncio
