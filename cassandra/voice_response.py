"""
T22: Voice Query Response

This module provides voice query handling with:
- Intent detection for questions
- TTS integration (ElevenLabs)
- Stream audio response

Features:
- Question intent classification
- Context-aware responses
- Streaming audio output
- Async/await for non-blocking I/O
"""

import asyncio
import io
import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Dict, Any, List, AsyncGenerator, Callable
from datetime import datetime

import httpx
import structlog
from pydantic import BaseModel, Field

from cassandra.config import settings
from cassandra.rag.memory_manager import MemoryManager, MemorySearchResult

logger = structlog.get_logger("cassandra.voice_response")


class QueryIntent(Enum):
    """Types of voice query intents."""
    QUESTION = "question"  # Information-seeking query
    COMMAND = "command"    # Action request
    GREETING = "greeting"  # Social greeting
    CONFIRMATION = "confirmation"  # Yes/no confirmation
    UNKNOWN = "unknown"    # Unclassified


@dataclass
class IntentClassification:
    """Result of intent classification."""
    intent: QueryIntent
    confidence: float
    entities: Dict[str, Any]
    original_text: str


@dataclass
class VoiceResponse:
    """Voice response data."""
    text: str
    audio_bytes: Optional[bytes] = None
    audio_format: str = "mp3"
    duration_ms: Optional[int] = None
    source: str = "ai"  # ai, memory, fallback


class QueryInput(BaseModel):
    """Input model for voice queries."""
    
    text: str = Field(..., description="Transcribed query text")
    org_id: str = Field(..., description="Organization ID")
    user_id: Optional[str] = Field(default=None)
    session_id: Optional[str] = Field(default=None)
    context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    
    class Config:
        json_schema_extra = {
            "example": {
                "text": "What was the resolution for ticket 1234?",
                "org_id": "org_12345",
                "user_id": "user_abc123"
            }
        }


class IntentDetector:
    """
    Detects intent from transcribed voice queries.
    
    Uses pattern matching and keyword analysis to classify
    user intent for appropriate response generation.
    """
    
    # Question patterns
    QUESTION_PATTERNS = [
        r"^(what|who|when|where|why|how)\s",
        r"^(is|are|was|were|do|does|did|can|could|will|would|should)\s",
        r"\?$",  # Ends with question mark
        r"(tell me about|explain|describe|what about|how about)",
    ]
    
    # Command patterns
    COMMAND_PATTERNS = [
        r"^(create|make|add|delete|remove|update|change|set|get|show|find|search)\s",
        r"^(please\s)?(can you|could you|would you)\s",
    ]
    
    # Greeting patterns
    GREETING_PATTERNS = [
        r"^(hi|hello|hey|good morning|good afternoon|good evening)\b",
        r"^(how are you|what's up|howdy)\b",
    ]
    
    # Confirmation patterns
    CONFIRMATION_PATTERNS = [
        r"^(yes|no|yeah|nope|sure|okay|ok|confirm|cancel)\b",
    ]
    
    def __init__(self):
        """Initialize intent detector."""
        self._compile_patterns()
        logger.info("intent_detector_initialized")
    
    def _compile_patterns(self):
        """Compile regex patterns."""
        self.question_regex = [re.compile(p, re.IGNORECASE) for p in self.QUESTION_PATTERNS]
        self.command_regex = [re.compile(p, re.IGNORECASE) for p in self.COMMAND_PATTERNS]
        self.greeting_regex = [re.compile(p, re.IGNORECASE) for p in self.GREETING_PATTERNS]
        self.confirmation_regex = [re.compile(p, re.IGNORECASE) for p in self.CONFIRMATION_PATTERNS]
    
    def classify(self, text: str) -> IntentClassification:
        """
        Classify intent from text.
        
        Args:
            text: Transcribed query text
            
        Returns:
            IntentClassification with intent and confidence
        """
        text_lower = text.strip().lower()
        
        # Check each intent type
        scores = {}
        
        # Question score
        scores[QueryIntent.QUESTION] = sum(
            1 for p in self.question_regex if p.search(text_lower)
        ) / len(self.question_regex)
        
        # Command score
        scores[QueryIntent.COMMAND] = sum(
            1 for p in self.command_regex if p.search(text_lower)
        ) / len(self.command_regex)
        
        # Greeting score
        scores[QueryIntent.GREETING] = sum(
            1 for p in self.greeting_regex if p.search(text_lower)
        ) / len(self.greeting_regex)
        
        # Confirmation score
        scores[QueryIntent.CONFIRMATION] = sum(
            1 for p in self.confirmation_regex if p.search(text_lower)
        ) / len(self.confirmation_regex)
        
        # Get highest scoring intent
        best_intent = max(scores, key=scores.get)
        best_score = scores[best_intent]
        
        # If no clear match, classify as unknown
        if best_score < 0.1:
            best_intent = QueryIntent.UNKNOWN
            best_score = 1.0
        
        # Extract entities (basic)
        entities = self._extract_entities(text)
        
        return IntentClassification(
            intent=best_intent,
            confidence=best_score,
            entities=entities,
            original_text=text
        )
    
    def _extract_entities(self, text: str) -> Dict[str, Any]:
        """Extract entities from text."""
        entities = {}
        
        # Extract ticket IDs
        ticket_patterns = [
            r"ticket\s*(?:#|number)?\s*(\d+)",
            r"ticket[-\s]?(\d+)",
            r"#(\d{3,})"
        ]
        
        for pattern in ticket_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entities["ticket_id"] = match.group(1)
                break
        
        # Extract dates
        date_patterns = [
            r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b",
            r"\b(yesterday|today|tomorrow|last week|next week)\b"
        ]
        
        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                entities["date_reference"] = match.group(1)
                break
        
        return entities


class ElevenLabsTTS:
    """
    ElevenLabs text-to-speech integration.
    
    Provides streaming audio generation for voice responses.
    """
    
    API_BASE_URL = "https://api.elevenlabs.io/v1"
    
    # Voice IDs
    VOICES = {
        "rachel": "21m00Tcm4TlvDq8ikWAM",
        "adam": "pNInz6obpgDQGcFmaJgB",
        "bella": "EXAVITQu4vr4xnSDxMaL",
        "antoni": "ErXwobaYiN019PkySvjV",
    }
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize ElevenLabs TTS.
        
        Args:
            api_key: ElevenLabs API key (defaults to env var)
        """
        self.api_key = api_key or settings.openai.api_key  # Fallback
        self._http_client: Optional[httpx.AsyncClient] = None
        
        logger.info("elevenlabs_tts_initialized")
    
    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                base_url=self.API_BASE_URL,
                timeout=60,
                headers={"xi-api-key": self.api_key}
            )
        return self._http_client
    
    async def generate_speech(
        self,
        text: str,
        voice_id: str = "rachel",
        model_id: str = "eleven_monolingual_v1",
        output_format: str = "mp3"
    ) -> bytes:
        """
        Generate speech from text.
        
        Args:
            text: Text to convert to speech
            voice_id: Voice ID or name
            model_id: Model ID
            output_format: Output audio format
            
        Returns:
            Audio bytes
        """
        # Resolve voice name to ID
        if voice_id in self.VOICES:
            voice_id = self.VOICES[voice_id]
        
        client = await self._get_http_client()
        
        payload = {
            "text": text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }
        
        logger.debug("generating_speech", text_length=len(text), voice=voice_id)
        
        try:
            response = await client.post(
                f"/text-to-speech/{voice_id}",
                json=payload,
                params={"output_format": output_format}
            )
            response.raise_for_status()
            
            audio_bytes = response.content
            
            logger.debug("speech_generated", audio_size=len(audio_bytes))
            
            return audio_bytes
            
        except httpx.HTTPError as e:
            logger.error("tts_generation_failed", error=str(e))
            raise TTSGenerationError(f"Failed to generate speech: {e}")
    
    async def stream_speech(
        self,
        text: str,
        voice_id: str = "rachel",
        chunk_size: int = 8192
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream speech generation.
        
        Args:
            text: Text to convert
            voice_id: Voice ID
            chunk_size: Audio chunk size
            
        Yields:
            Audio chunks
        """
        if voice_id in self.VOICES:
            voice_id = self.VOICES[voice_id]
        
        client = await self._get_http_client()
        
        payload = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.5
            }
        }
        
        logger.debug("streaming_speech", text_length=len(text))
        
        try:
            async with client.stream(
                "POST",
                f"/text-to-speech/{voice_id}/stream",
                json=payload
            ) as response:
                response.raise_for_status()
                
                async for chunk in response.aiter_bytes(chunk_size):
                    yield chunk
                    
        except httpx.HTTPError as e:
            logger.error("tts_streaming_failed", error=str(e))
            raise TTSGenerationError(f"Failed to stream speech: {e}")
    
    async def close(self):
        """Close HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()


class VoiceQueryHandler:
    """
    Main handler for voice queries.
    
    Orchestrates:
    - Intent detection
    - Context retrieval
    - Response generation
    - TTS conversion
    """
    
    def __init__(
        self,
        memory_manager: Optional[MemoryManager] = None,
        tts_client: Optional[ElevenLabsTTS] = None
    ):
        """
        Initialize voice query handler.
        
        Args:
            memory_manager: Memory manager for context retrieval
            tts_client: TTS client for audio generation
        """
        self.intent_detector = IntentDetector()
        self.memory_manager = memory_manager
        self.tts_client = tts_client or ElevenLabsTTS()
        
        logger.info("voice_query_handler_initialized")
    
    async def process_query(
        self,
        query: QueryInput,
        generate_audio: bool = True
    ) -> VoiceResponse:
        """
        Process a voice query and generate response.
        
        Args:
            query: Query input
            generate_audio: Whether to generate audio response
            
        Returns:
            VoiceResponse with text and optional audio
        """
        logger.info(
            "processing_voice_query",
            org_id=query.org_id,
            text_preview=query.text[:50]
        )
        
        # Step 1: Classify intent
        intent = self.intent_detector.classify(query.text)
        
        logger.debug(
            "intent_classified",
            intent=intent.intent.value,
            confidence=intent.confidence
        )
        
        # Step 2: Generate response based on intent
        response_text = await self._generate_response(query, intent)
        
        # Step 3: Generate audio if requested
        audio_bytes = None
        if generate_audio:
            try:
                audio_bytes = await self.tts_client.generate_speech(response_text)
            except Exception as e:
                logger.error("audio_generation_failed", error=str(e))
        
        return VoiceResponse(
            text=response_text,
            audio_bytes=audio_bytes,
            audio_format="mp3",
            source="ai"
        )
    
    async def _generate_response(
        self,
        query: QueryInput,
        intent: IntentClassification
    ) -> str:
        """Generate text response based on intent."""
        
        if intent.intent == QueryIntent.GREETING:
            return self._generate_greeting_response(query)
        
        elif intent.intent == QueryIntent.QUESTION:
            return await self._generate_question_response(query, intent)
        
        elif intent.intent == QueryIntent.COMMAND:
            return await self._generate_command_response(query, intent)
        
        elif intent.intent == QueryIntent.CONFIRMATION:
            return self._generate_confirmation_response(query, intent)
        
        else:
            return self._generate_fallback_response(query)
    
    def _generate_greeting_response(self, query: QueryInput) -> str:
        """Generate greeting response."""
        greetings = [
            "Hello! How can I help you today?",
            "Hi there! What can I do for you?",
            "Hey! Ready to assist you. What do you need?"
        ]
        import random
        return random.choice(greetings)
    
    async def _generate_question_response(
        self,
        query: QueryInput,
        intent: IntentClassification
    ) -> str:
        """Generate response to question."""
        
        # Check if asking about a specific ticket
        if "ticket_id" in intent.entities and self.memory_manager:
            ticket_id = intent.entities["ticket_id"]
            
            # Search for ticket-related memories
            memories = await self.memory_manager.search_memories(
                query=f"ticket {ticket_id}",
                org_id=query.org_id,
                limit=5
            )
            
            if memories:
                # Build response from memories
                context = "\n".join([
                    f"- {m.memory.content[:100]}"
                    for m in memories[:3]
                ])
                
                return (
                    f"Here's what I found about ticket {ticket_id}:\n"
                    f"{context}\n"
                    f"Would you like more details?"
                )
            else:
                return f"I couldn't find information about ticket {ticket_id}. Could you provide more details?"
        
        # General knowledge question
        return (
            "I understand you're asking about something. "
            "Let me search our knowledge base for the most relevant information. "
            "Could you provide more specific details about what you're looking for?"
        )
    
    async def _generate_command_response(
        self,
        query: QueryInput,
        intent: IntentClassification
    ) -> str:
        """Generate response to command."""
        return (
            "I understand you'd like me to take action. "
            "I'm currently processing your request. "
            "Please confirm the details, and I'll proceed."
        )
    
    def _generate_confirmation_response(
        self,
        query: QueryInput,
        intent: IntentClassification
    ) -> str:
        """Generate response to confirmation."""
        text_lower = query.text.lower()
        
        if any(word in text_lower for word in ["yes", "yeah", "sure", "okay", "ok"]):
            return "Great! I'll proceed with that."
        else:
            return "Understood. Let me know if you need anything else."
    
    def _generate_fallback_response(self, query: QueryInput) -> str:
        """Generate fallback response."""
        return (
            "I'm not quite sure what you're asking. "
            "Could you rephrase that? You can ask me about tickets, "
            "request actions, or just say hello!"
        )
    
    async def stream_response(
        self,
        query: QueryInput
    ) -> AsyncGenerator[bytes, None]:
        """
        Stream audio response for query.
        
        Args:
            query: Query input
            
        Yields:
            Audio chunks
        """
        # Process query to get response text
        response = await self.process_query(query, generate_audio=False)
        
        # Stream TTS
        async for chunk in self.tts_client.stream_speech(response.text):
            yield chunk
    
    async def close(self):
        """Close resources."""
        if self.tts_client:
            await self.tts_client.close()


class TTSGenerationError(Exception):
    """Raised when TTS generation fails."""
    pass


# =============================================================================
# FastAPI Endpoints
# =============================================================================

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/voice", tags=["Voice"])

# Global handler instance
_voice_handler: Optional[VoiceQueryHandler] = None


def get_voice_handler() -> VoiceQueryHandler:
    """Get or create voice handler instance."""
    global _voice_handler
    if _voice_handler is None:
        _voice_handler = VoiceQueryHandler()
    return _voice_handler


@router.post("/query")
async def voice_query(query: QueryInput):
    """
    Process a voice query and return text response.
    
    This endpoint accepts transcribed text and returns:
    - Detected intent
    - Generated response text
    - Optional audio response
    """
    try:
        handler = get_voice_handler()
        response = await handler.process_query(query, generate_audio=False)
        
        return {
            "success": True,
            "response_text": response.text,
            "source": response.source
        }
        
    except Exception as e:
        logger.error("voice_query_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query/audio")
async def voice_query_with_audio(query: QueryInput):
    """
    Process voice query and return audio response.
    
    Returns MP3 audio of the response.
    """
    try:
        handler = get_voice_handler()
        response = await handler.process_query(query, generate_audio=True)
        
        if not response.audio_bytes:
            raise HTTPException(status_code=500, detail="Audio generation failed")
        
        return StreamingResponse(
            io.BytesIO(response.audio_bytes),
            media_type="audio/mpeg",
            headers={
                "Content-Disposition": "attachment; filename=response.mp3",
                "X-Response-Text": response.text[:200]  # Truncated for header
            }
        )
        
    except Exception as e:
        logger.error("voice_query_audio_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/query/stream")
async def voice_query_websocket(websocket: WebSocket):
    """
    WebSocket endpoint for streaming voice queries.
    
    Protocol:
    - Client sends: {"text": "query text", "org_id": "..."}
    - Server responds with audio chunks
    """
    await websocket.accept()
    handler = get_voice_handler()
    
    try:
        while True:
            # Receive query
            message = await websocket.receive_json()
            
            query = QueryInput(**message)
            
            # Stream response
            async for chunk in handler.stream_response(query):
                await websocket.send_bytes(chunk)
            
            # Send completion signal
            await websocket.send_json({"type": "complete"})
            
    except WebSocketDisconnect:
        logger.info("voice_query_websocket_disconnected")
    except Exception as e:
        logger.error("voice_query_websocket_error", error=str(e))
        await websocket.close(code=1011)
