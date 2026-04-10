from openai import OpenAI
from infrastructure.database import get_supabase
from config import config
from schemas.models import ArtifactType

client = OpenAI(api_key=config.OPENAI_API_KEY)
supabase = get_supabase()

def generate_embedding(text: str) -> list[float]:
    """Generates embedding vector for storing institutional memory."""
    # Using small fast model for < 300ms embedding processing
    response = client.embeddings.create(
        model="text-embedding-3-small",
        input=text
    )
    return response.data[0].embedding

def store_meeting_insight(meeting_id: str, artifact_type: ArtifactType, content: str, confidence: float):
    """Generates embeddings and stores the artifact to Supabase."""
    embedding = generate_embedding(content)

    data = {
        "meeting_id": meeting_id,
        "artifact_type": artifact_type.value,
        "content": content,
        "confidence": confidence,
        "embedding": embedding
    }

    response = supabase.table("artifacts").insert(data).execute()
    return response.data

def save_artifact_with_embedding(meeting_id: str, artifact_type_str: str, content: str, confidence: float = 0.9):
    """
    Plain-string variant used by main.py event handlers.
    artifact_type_str must be one of: 'decision', 'risk', 'topic', 'summary'
    (matches the DB check constraint).
    """
    embedding = generate_embedding(content)

    data = {
        "meeting_id": meeting_id,
        "artifact_type": artifact_type_str,
        "content": content,
        "confidence": confidence,
        "embedding": embedding
    }

    response = supabase.table("artifacts").insert(data).execute()
    return response.data

def create_meeting(title: str = "Meeting Session"):
    """Creates a new meeting session."""
    data = {"title": title}
    response = supabase.table("meetings").insert(data).execute()
    return response.data[0]


def save_transcript(
    session_id: str,
    org_id: str,
    speaker: str,
    content: str,
    turn_index: int,
    chunk_index: int | None = None,
    is_final: bool = True,
    audio_start_ms: int | None = None,
    audio_end_ms: int | None = None,
    processing_latency_ms: float | None = None,
    vad_speech_ms: float | None = None,
) -> dict:
    """
    Save a transcript segment to the session_transcripts table.

    This is called after every STT result to build a searchable
    conversation history for Supermemory retrieval.

    Args:
        session_id: The Cassandra session UUID.
        org_id: Organization UUID.
        speaker: 'user' or 'ai'.
        content: Transcribed text.
        turn_index: Order of this turn in the session.
        chunk_index: Index within the turn (for partial transcripts).
        is_final: Whether this is the final transcript for this turn.
        audio_start_ms: Position in the audio stream.
        audio_end_ms: End position in the audio stream.
        processing_latency_ms: Time taken to process this segment.
        vad_speech_ms: Detected speech duration in this segment.

    Returns:
        The inserted record.
    """
    data = {
        "session_id": session_id,
        "org_id": org_id,
        "speaker": speaker,
        "content": content,
        "turn_index": turn_index,
        "chunk_index": chunk_index,
        "is_final": is_final,
        "audio_start_ms": audio_start_ms,
        "audio_end_ms": audio_end_ms,
        "processing_latency_ms": processing_latency_ms,
        "vad_speech_ms": vad_speech_ms,
    }

    # Remove None values
    data = {k: v for k, v in data.items() if v is not None}

    response = supabase.table("session_transcripts").insert(data).execute()
    return response.data[0] if response.data else {}


def log_tool_call(
    session_id: str | None,
    org_id: str,
    tool_name: str,
    tool_arguments: dict | None = None,
    tool_result: dict | None = None,
    status: str = "success",
    duration_ms: int = 0,
    error_message: str | None = None,
) -> dict:
    """
    Log a tool call execution to the tool_call_logs table.

    This is Cassandra's audit trail — every action she takes is logged.

    Args:
        session_id: Associated session UUID.
        org_id: Organization UUID.
        tool_name: Name of the tool executed.
        tool_arguments: Arguments passed to the tool.
        tool_result: Result returned by the tool.
        status: 'success', 'error', or 'timeout'.
        duration_ms: Execution time in milliseconds.
        error_message: Error message if status is 'error'.

    Returns:
        The inserted record.
    """
    data = {
        "session_id": session_id,
        "org_id": org_id,
        "tool_name": tool_name,
        "tool_arguments": tool_arguments,
        "tool_result": tool_result,
        "status": status,
        "duration_ms": duration_ms,
        "error_message": error_message,
        "completed_at": "now()",
    }
    data = {k: v for k, v in data.items() if v is not None}

    response = supabase.table("tool_call_logs").insert(data).execute()
    return response.data[0] if response.data else {}


def log_error(
    session_id: str | None,
    org_id: str | None,
    severity: str,
    error_type: str,
    message: str,
    context: dict | None = None,
    stack_trace: str | None = None,
    component: str | None = None,
    provider: str | None = None,
) -> dict:
    """
    Log an error to the error_logs table.

    This captures Cassandra's failures for debugging and improvement.

    Args:
        session_id: Associated session UUID.
        org_id: Organization UUID.
        severity: 'debug', 'info', 'warning', 'error', 'critical'.
        error_type: Exception class name.
        message: Error message.
        context: Additional structured context.
        stack_trace: Full stack trace.
        component: Component where error occurred (vad, stt, tts, llm, etc.).
        provider: Provider that caused the error.

    Returns:
        The inserted record.
    """
    data = {
        "session_id": session_id,
        "org_id": org_id,
        "severity": severity,
        "error_type": error_type,
        "message": message,
        "context": context,
        "stack_trace": stack_trace,
        "component": component,
        "provider": provider,
    }
    data = {k: v for k, v in data.items() if v is not None}

    response = supabase.table("error_logs").insert(data).execute()
    return response.data[0] if response.data else {}
