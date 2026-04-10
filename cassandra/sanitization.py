"""
T36: Input Sanitization

This module provides input sanitization and security:
- Prompt injection detection
- Transcript sanitization
- XSS prevention
- SQL injection detection

Features:
- Pattern-based detection
- Content filtering
- Safe output encoding
"""

import html
import json
import re
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Union
from enum import Enum

import structlog

logger = structlog.get_logger("cassandra.sanitization")


class ThreatType(str, Enum):
    """Types of detected threats."""
    PROMPT_INJECTION = "prompt_injection"
    XSS = "xss"
    SQL_INJECTION = "sql_injection"
    COMMAND_INJECTION = "command_injection"
    PII_LEAK = "pii_leak"
    SENSITIVE_DATA = "sensitive_data"


@dataclass
class SanitizationResult:
    """Result of sanitization check."""
    is_safe: bool
    sanitized_text: str
    threats_detected: List[Dict[str, Any]]
    original_length: int
    sanitized_length: int


class PromptInjectionDetector:
    """
    Detects prompt injection attempts.
    
    Looks for patterns that attempt to manipulate AI behavior
    or extract system information.
    """
    
    # Injection patterns
    INJECTION_PATTERNS = [
        # Ignore previous instructions
        r"ignore\s+(?:all\s+)?(?:previous|prior|earlier)\s+(?:instructions?|commands?|directives?)",
        r"disregard\s+(?:all\s+)?(?:previous|prior)\s+(?:instructions?|commands?)",
        
        # Role override
        r"you\s+(?:are|should)\s+now\s+(?:a|an)\s+",
        r"act\s+(?:as|like)\s+(?:a|an)\s+",
        r"pretend\s+(?:to\s+be|you\s+are)\s+",
        r"from\s+now\s+on\s+you\s+are\s+",
        
        # System prompt extraction
        r"(?:show|reveal|display|print)\s+(?:me\s+)?(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
        r"what\s+(?:are|were)\s+(?:your|the)\s+(?:system\s+)?(?:prompt|instructions?)",
        
        # Delimiter manipulation
        r"```\s*system",
        r"<\s*system\s*>",
        r"\[\s*system\s*\]",
        
        # Jailbreak attempts
        r"jailbreak",
        r"DAN\s*mode",
        r"developer\s*mode",
        r"ignore\s+ethical\s+constraints",
        
        # Instruction delimiter bypass
        r"--\s*-\s*-\s*",
        r"===+",
        r"\*\*\*+",
    ]
    
    def __init__(self):
        """Initialize detector."""
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.INJECTION_PATTERNS]
    
    def detect(self, text: str) -> List[Dict[str, Any]]:
        """
        Detect prompt injection attempts.
        
        Args:
            text: Input text to check
            
        Returns:
            List of detected threats
        """
        threats = []
        text_lower = text.lower()
        
        for i, pattern in enumerate(self.patterns):
            matches = pattern.finditer(text)
            for match in matches:
                threats.append({
                    "type": ThreatType.PROMPT_INJECTION,
                    "pattern_index": i,
                    "matched_text": match.group(),
                    "position": match.start(),
                    "severity": "high"
                })
        
        return threats


class XSSDetector:
    """Detects XSS attempts in input."""
    
    XSS_PATTERNS = [
        r"<\s*script\s*>",
        r"<\s*script\s+[^>]*>",
        r"javascript\s*:",
        r"on\w+\s*=\s*['\"]",
        r"<\s*iframe",
        r"<\s*object",
        r"<\s*embed",
        r"eval\s*\(",
        r"expression\s*\(",
    ]
    
    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.XSS_PATTERNS]
    
    def detect(self, text: str) -> List[Dict[str, Any]]:
        """Detect XSS attempts."""
        threats = []
        
        for i, pattern in enumerate(self.patterns):
            matches = pattern.finditer(text)
            for match in matches:
                threats.append({
                    "type": ThreatType.XSS,
                    "pattern_index": i,
                    "matched_text": match.group(),
                    "position": match.start(),
                    "severity": "critical"
                })
        
        return threats


class SQLInjectionDetector:
    """Detects SQL injection attempts."""
    
    SQL_PATTERNS = [
        r"(\%27)|(\')|(\-\-)|(\%23)|(#)",
        r"((\%3D)|(=))[^\n]*((\%27)|(\')|(\-\-)|(\%3B)|(;))",
        r"\w*((\%27)|(\'))((\%6F)|o|(\%4F))((\%72)|r|(\%52))",
        r"((\%27)|(\'))union",
        r"exec\s*\(\s*@",
        r"(\%27)|(\')\s*or\s*",
        r";\s*drop\s+table",
        r";\s*delete\s+from",
        r";\s*insert\s+into",
    ]
    
    def __init__(self):
        self.patterns = [re.compile(p, re.IGNORECASE) for p in self.SQL_PATTERNS]
    
    def detect(self, text: str) -> List[Dict[str, Any]]:
        """Detect SQL injection attempts."""
        threats = []
        
        for i, pattern in enumerate(self.patterns):
            matches = pattern.finditer(text)
            for match in matches:
                threats.append({
                    "type": ThreatType.SQL_INJECTION,
                    "pattern_index": i,
                    "matched_text": match.group(),
                    "position": match.start(),
                    "severity": "critical"
                })
        
        return threats


class PIIDetector:
    """Detects potential PII in text."""
    
    PII_PATTERNS = {
        "ssn": r"\b\d{3}-\d{2}-\d{4}\b",
        "credit_card": r"\b(?:\d{4}[-\s]?){3}\d{4}\b",
        "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b",
        "phone": r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b",
    }
    
    def __init__(self):
        self.patterns = {
            k: re.compile(v) for k, v in self.PII_PATTERNS.items()
        }
    
    def detect(self, text: str) -> List[Dict[str, Any]]:
        """Detect potential PII."""
        threats = []
        
        for pii_type, pattern in self.patterns.items():
            matches = pattern.finditer(text)
            for match in matches:
                threats.append({
                    "type": ThreatType.PII_LEAK,
                    "pii_type": pii_type,
                    "matched_text": match.group()[:10] + "...",
                    "position": match.start(),
                    "severity": "medium"
                })
        
        return threats


class InputSanitizer:
    """
    Main input sanitization class.
    
    Combines all detectors and provides sanitization.
    
    Usage:
        sanitizer = InputSanitizer()
        
        result = sanitizer.sanitize(user_input)
        if not result.is_safe:
            # Handle threats
            ...
    """
    
    def __init__(
        self,
        check_prompt_injection: bool = True,
        check_xss: bool = True,
        check_sql: bool = True,
        check_pii: bool = False  # Off by default, may have false positives
    ):
        """
        Initialize sanitizer.
        
        Args:
            check_prompt_injection: Check for prompt injection
            check_xss: Check for XSS
            check_sql: Check for SQL injection
            check_pii: Check for PII
        """
        self.detectors = []
        
        if check_prompt_injection:
            self.detectors.append(PromptInjectionDetector())
        if check_xss:
            self.detectors.append(XSSDetector())
        if check_sql:
            self.detectors.append(SQLInjectionDetector())
        if check_pii:
            self.detectors.append(PIIDetector())
    
    def sanitize(
        self,
        text: str,
        escape_html: bool = True,
        max_length: int = 10000
    ) -> SanitizationResult:
        """
        Sanitize input text.
        
        Args:
            text: Input text
            escape_html: Whether to escape HTML
            max_length: Maximum allowed length
            
        Returns:
            SanitizationResult
        """
        original_length = len(text)
        threats = []
        
        # Run all detectors
        for detector in self.detectors:
            threats.extend(detector.detect(text))
        
        # Sanitize text
        sanitized = text
        
        # Truncate if too long
        if len(sanitized) > max_length:
            sanitized = sanitized[:max_length]
            threats.append({
                "type": "length_exceeded",
                "severity": "low",
                "message": f"Text truncated from {original_length} to {max_length}"
            })
        
        # Escape HTML if requested
        if escape_html:
            sanitized = html.escape(sanitized)
        
        # Remove null bytes
        sanitized = sanitized.replace('\x00', '')
        
        # Remove control characters except newlines and tabs
        sanitized = ''.join(
            char for char in sanitized
            if ord(char) >= 32 or char in '\n\t\r'
        )
        
        is_safe = not any(
            t.get("severity") in ["high", "critical"]
            for t in threats
        )
        
        if threats:
            logger.warning(
                "threats_detected",
                threat_count=len(threats),
                threat_types=list(set(t.get("type") for t in threats))
            )
        
        return SanitizationResult(
            is_safe=is_safe,
            sanitized_text=sanitized,
            threats_detected=threats,
            original_length=original_length,
            sanitized_length=len(sanitized)
        )
    
    def sanitize_transcript(
        self,
        transcript: str,
        redact_pii: bool = True
    ) -> SanitizationResult:
        """
        Sanitize transcript text.
        
        Specialized for voice transcripts with optional PII redaction.
        
        Args:
            transcript: Transcript text
            redact_pii: Whether to redact detected PII
            
        Returns:
            SanitizationResult
        """
        result = self.sanitize(transcript, escape_html=False)
        
        if redact_pii:
            # Redact detected PII
            for threat in result.threats_detected:
                if threat.get("type") == ThreatType.PII_LEAK:
                    pii_type = threat.get("pii_type", "unknown")
                    result.sanitized_text = re.sub(
                        self.PII_PATTERNS.get(pii_type, ""),
                        f"[{pii_type.upper()}_REDACTED]",
                        result.sanitized_text
                    )
        
        return result


# =============================================================================
# FastAPI Integration
# =============================================================================

from fastapi import Request, HTTPException

async def sanitize_request_body(
    request: Request,
    sanitizer: Optional[InputSanitizer] = None
) -> Dict[str, Any]:
    """
    Sanitize request body.
    
    Args:
        request: FastAPI request
        sanitizer: InputSanitizer instance
        
    Returns:
        Sanitized body as dict
    """
    sanitizer = sanitizer or InputSanitizer()
    
    try:
        body = await request.json()
    except Exception:
        return {}
    
    def sanitize_value(value):
        if isinstance(value, str):
            result = sanitizer.sanitize(value)
            if not result.is_safe:
                logger.warning("unsafe_input_detected", threats=result.threats_detected)
            return result.sanitized_text
        elif isinstance(value, dict):
            return {k: sanitize_value(v) for k, v in value.items()}
        elif isinstance(value, list):
            return [sanitize_value(item) for item in value]
        return value
    
    return sanitize_value(body)


class SanitizationMiddleware:
    """FastAPI middleware for request sanitization."""
    
    def __init__(self, app, sanitizer: Optional[InputSanitizer] = None):
        self.app = app
        self.sanitizer = sanitizer or InputSanitizer()
    
    async def __call__(self, scope, receive, send):
        """Process request with sanitization."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        
        request = Request(scope, receive)
        
        # Sanitize query params
        for key, value in request.query_params.items():
            result = self.sanitizer.sanitize(value)
            if not result.is_safe:
                response = JSONResponse(
                    status_code=400,
                    content={"error": "Invalid input detected"}
                )
                await response(scope, receive, send)
                return
        
        await self.app(scope, receive, send)


# =============================================================================
# Convenience Functions
# =============================================================================

def sanitize_for_prompt(text: str) -> str:
    """
    Sanitize text for use in AI prompts.
    
    Removes potential prompt injection attempts.
    """
    sanitizer = InputSanitizer(
        check_prompt_injection=True,
        check_xss=False,
        check_sql=False,
        check_pii=False
    )
    
    result = sanitizer.sanitize(text, escape_html=False)
    return result.sanitized_text


def sanitize_for_display(text: str) -> str:
    """
    Sanitize text for display.
    
    Escapes HTML and removes dangerous content.
    """
    sanitizer = InputSanitizer(
        check_prompt_injection=False,
        check_xss=True,
        check_sql=False,
        check_pii=False
    )
    
    result = sanitizer.sanitize(text, escape_html=True)
    return result.sanitized_text
