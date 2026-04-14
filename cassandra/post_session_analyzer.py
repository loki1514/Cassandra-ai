"""
Post-Session Analyzer: Room-Based Voice Diarization Enrichment

This module runs the 8-step pipeline that converts "SPEAKER_00 said X"
into "John said X" using Pyannote embeddings matched against explicit
room participants.

Steps:
1. Load full audio + room participants
2. Pyannote diarization (batch — full audio)
3. Cluster by speaker_label, extract + average embeddings
4. Cosine match against room participant embeddings
5. Build speaker_map
6. Enrich transcript with names
7. Extract action items via LLM
8. Write to Supermemory
"""

import asyncio
from typing import Any, Dict, List, Optional
from datetime import datetime

import numpy as np
from numpy.linalg import norm
import structlog

from cassandra.speaker_id import diarize_audio, extract_embedding, DiarizationSegment
from cassandra.transcription import transcribe
from cassandra.extraction import extract_ticket_data
from cassandra.supabase import get_supabase_client

logger = structlog.get_logger("cassandra.post_session_analyzer")

SIMILARITY_THRESHOLD = 0.70
MIN_SEGMENT_MS = 50  # M-3 fix: lowered from 100ms to 50ms to avoid excluding short speakers


def _normalize_embedding(embedding: np.ndarray) -> np.ndarray:
    """Normalize embedding to unit vector."""
    n = norm(embedding)
    if n == 0:
        return embedding
    return embedding / n


def _cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two vectors."""
    return float(np.dot(a, b) / (norm(a) * norm(b) + 1e-8))


async def _load_room_context(room_db_id: str, org_id: str) -> Optional[Dict[str, Any]]:
    """Load room and participant voice profiles from Supabase."""
    client = get_supabase_client("service")
    room_result = client.table("rooms").select("*").eq("id", room_db_id).eq("org_id", org_id).maybe_single().execute()
    if not room_result.data:
        logger.error("room_not_found", room_db_id=room_db_id, org_id=org_id)
        return None
    return room_result.data


async def _update_room_analysis_status(
    room_db_id: str, org_id: str, status: str, result: Optional[Dict[str, Any]] = None
) -> None:
    """Update room analysis_status and analysis_result."""
    client = get_supabase_client("service")
    payload = {"analysis_status": status}
    if result is not None:
        payload["analysis_result"] = result
    try:
        client.table("rooms").update(payload).eq("id", room_db_id).eq("org_id", org_id).execute()
    except Exception as e:
        logger.error("room_analysis_status_update_failed", room_db_id=room_db_id, error=str(e))


async def _insert_enriched_transcripts(
    room_db_id: str, session_id: str, org_id: str, segments: List[Dict[str, Any]]
) -> None:
    """Bulk insert enriched transcript segments."""
    if not segments:
        return
    client = get_supabase_client("service")
    rows = []
    for seg in segments:
        rows.append({
            "room_id": room_db_id,
            "session_id": session_id,
            "org_id": org_id,
            "speaker_label": seg["speaker_label"],
            "speaker_name": seg["speaker_name"],
            "speaker_user_id": seg.get("speaker_user_id"),
            "confidence": seg["confidence"],
            "text": seg["text"],
            "start_ms": seg["start_ms"],
            "end_ms": seg["end_ms"],
        })
    try:
        client.table("enriched_transcripts").insert(rows).execute()
    except Exception as e:
        logger.error("enriched_transcripts_insert_failed", room_db_id=room_db_id, error=str(e))


async def _insert_action_items(
    room_db_id: str, org_id: str, action_items: List[Dict[str, Any]]
) -> None:
    """Bulk insert action items."""
    if not action_items:
        return
    client = get_supabase_client("service")
    rows = []
    for item in action_items:
        rows.append({
            "room_id": room_db_id,
            "org_id": org_id,
            "assignee_id": item.get("assignee_id"),
            "assignee_name": item.get("assignee_name"),
            "speaker_user_id": item.get("speaker_user_id"),
            "confidence": item.get("confidence"),
            "title": item["title"],
            "description": item.get("description"),
            "priority": item.get("priority", "medium"),
            "deadline": item.get("deadline"),
            "status": "open",
            "source_text": item.get("source_text"),
            "start_ms": item.get("start_ms"),
        })
    try:
        client.table("action_items").insert(rows).execute()
    except Exception as e:
        logger.error("action_items_insert_failed", room_db_id=room_db_id, error=str(e))


def _slice_audio(audio_bytes: bytes, start_ms: float, end_ms: float, sample_rate: int = 16000) -> bytes:
    """
    Slice PCM16 audio by time range.
    Assumes audio_bytes is continuous PCM16 little-endian mono.
    """
    bytes_per_sample = 2
    start_byte = int(start_ms / 1000 * sample_rate * bytes_per_sample)
    end_byte = int(end_ms / 1000 * sample_rate * bytes_per_sample)
    start_byte = max(0, start_byte)
    end_byte = min(len(audio_bytes), end_byte)
    return audio_bytes[start_byte:end_byte]


async def run_room_analysis(
    room_db_id: str,
    session_id: str,
    org_id: str,
    full_audio: bytes,
) -> bool:
    """
    Run the full post-session analysis pipeline for a room.

    Args:
        room_db_id: UUID primary key of the rooms row.
        session_id: Session ID that produced the audio.
        org_id: Organization ID.
        full_audio: Complete session recording as PCM16 bytes.

    Returns:
        True if analysis completed successfully.
    """
    logger.info(
        "room_analysis_started",
        room_db_id=room_db_id,
        session_id=session_id,
        org_id=org_id,
        audio_bytes=len(full_audio),
    )

    await _update_room_analysis_status(room_db_id, org_id, "running")

    # ── Step 1: Load room + participants ───────────────────────────────────────
    room = await _load_room_context(room_db_id, org_id)
    if not room:
        await _update_room_analysis_status(room_db_id, org_id, "failed")
        return False

    participants = room.get("participants", [])
    if not participants:
        logger.warning("room_has_no_participants", room_db_id=room_db_id)
        await _update_room_analysis_status(room_db_id, org_id, "completed", {"speaker_map": {}})
        return True

    # Fetch voice profile embeddings for all participants
    client = get_supabase_client("service")
    user_ids = [p["user_id"] for p in participants if p.get("user_id")]
    vp_result = client.table("voice_profiles").select("user_id, embedding").in_("user_id", user_ids).eq("org_id", org_id).eq("status", "active").execute()
    participant_embeddings = {}
    for vp in vp_result.data:
        participant_embeddings[vp["user_id"]] = np.array(vp["embedding"], dtype=np.float32)

    participant_info = {p["user_id"]: p for p in participants if p.get("user_id")}

    # ── Step 2: Pyannote diarization ──────────────────────────────────────────
    try:
        diarization_segments = await diarize_audio(full_audio)
        logger.info("diarization_complete", segments=len(diarization_segments))
    except Exception as e:
        logger.error("diarization_failed", error=str(e))
        await _update_room_analysis_status(room_db_id, org_id, "failed")
        return False

    # ── Step 3: Cluster by speaker_label, extract averaged embedding ──────────
    label_segments: Dict[str, List[DiarizationSegment]] = {}
    for seg in diarization_segments:
        label_segments.setdefault(seg.speaker_label, []).append(seg)

    label_embedding: Dict[str, np.ndarray] = {}
    bytes_per_sample = 2

    for label, segs in label_segments.items():
        embeddings = []
        for seg in segs:
            try:
                clip = _slice_audio(full_audio, seg.start_ms, seg.end_ms)
                # M-3 fix: lowered threshold to 50ms (was 100ms) to avoid excluding
                # late-joining speakers whose only utterance is brief.
                min_bytes = int(MIN_SEGMENT_MS / 1000 * 16000 * bytes_per_sample)
                if len(clip) < min_bytes:
                    logger.debug(
                        "short_segment_skipped",
                        label=label,
                        seg_ms=round(seg.end_ms - seg.start_ms, 1),
                    )
                    continue
                emb = await extract_embedding(clip)
                embeddings.append(emb)
            except Exception as e:
                logger.warning("embedding_extraction_failed_for_segment", label=label, error=str(e))

        if embeddings:
            avg_emb = np.mean(embeddings, axis=0)
            label_embedding[label] = _normalize_embedding(avg_emb)
        else:
            # No valid embeddings for this label — speaker will be marked Unknown.
            label_embedding[label] = None

    # ── Step 4: Match against room participants ───────────────────────────────
    speaker_map: Dict[str, Dict[str, Any]] = {}
    for label, emb in label_embedding.items():
        best_user_id = None
        best_name = "Unknown Speaker"
        best_conf = 0.0

        if emb is not None and participant_embeddings:
            for user_id, participant_emb in participant_embeddings.items():
                sim = _cosine_similarity(emb, participant_emb)
                if sim > best_conf:
                    best_conf = sim
                    best_user_id = user_id

        if best_conf >= SIMILARITY_THRESHOLD and best_user_id:
            best_name = participant_info.get(best_user_id, {}).get("display_name", "Unknown")
        else:
            best_user_id = None
            best_name = "Unknown Speaker"
            best_conf = 0.0

        speaker_map[label] = {
            "user_id": best_user_id,
            "name": best_name,
            "confidence": round(best_conf, 4),
        }

    logger.info("speaker_map_built", labels=list(speaker_map.keys()))

    # Build Pyannote segment index keyed by label for overlap lookup
    # CR-3 fix: Pyannote labels (SPEAKER_00/01/02) and AssemblyAI labels
    # (A/B/C) are independent numbering systems. We match by time overlap
    # instead of by label string.
    pyannote_by_label: Dict[str, List[DiarizationSegment]] = label_segments

    def _find_pyannote_label_for_segment(aai_start_ms: float, aai_end_ms: float) -> str | None:
        """Find which Pyannote label's segments overlap most with the given AAI segment."""
        best_label: str | None = None
        best_overlap_ms = 0.0

        for label, pseg_list in pyannote_by_label.items():
            for pseg in pseg_list:
                overlap_start = max(aai_start_ms, pseg.start_ms)
                overlap_end = min(aai_end_ms, pseg.end_ms)
                if overlap_start < overlap_end:
                    overlap_ms = overlap_end - overlap_start
                    if overlap_ms > best_overlap_ms:
                        best_overlap_ms = overlap_ms
                        best_label = label

        return best_label

    # ── Step 5: AssemblyAI transcript for content ─────────────────────────────
    try:
        aai_segments = await transcribe(full_audio, org_id=org_id)
    except Exception as e:
        logger.error("transcription_failed", error=str(e))
        aai_segments = []

    # Build enriched transcript segments
    # CR-3 fix: match AssemblyAI segments to Pyannote labels via time overlap,
    # then look up the speaker_map to get the matched name.
    enriched_segments = []
    for seg in aai_segments:
        pyannote_label = _find_pyannote_label_for_segment(seg.start_ms, seg.end_ms)
        if pyannote_label and pyannote_label in speaker_map:
            mapped = speaker_map[pyannote_label]
        else:
            # No Pyannote overlap — fall back to AssemblyAI label as unknown
            mapped = {"user_id": None, "name": seg.speaker_label, "confidence": 0.0}

        enriched_segments.append({
            "speaker_label": seg.speaker_label,
            "speaker_name": mapped["name"],
            "speaker_user_id": mapped["user_id"],
            "confidence": mapped["confidence"],
            "text": seg.text,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
        })

    await _insert_enriched_transcripts(room_db_id, session_id, org_id, enriched_segments)

    # ── Step 6: Extract action items via LLM ──────────────────────────────────
    action_items = []
    if enriched_segments:
        full_transcript_text = "\n".join([
            f"[{s['speaker_name']}]: {s['text']}" for s in enriched_segments
        ])

        # M-7 fix: build speaker_context from the speaker_map so the LLM gets
        # named speaker context rather than label strings.
        speaker_context = {
            label: info["name"]
            for label, info in speaker_map.items()
            if info["user_id"] is not None
        }

        try:
            extracted = await extract_ticket_data(full_transcript_text, speaker_context=speaker_context)
            # H-6 fix: iterate ALL extracted commitments, not just the first one.
            # extract_ticket_data returns a list of all commitments in
            # extracted["extracted_commitments"]. The top-level "title" field
            # is derived from the highest-confidence commitment only.
            commitments = extracted.get("extracted_commitments", [])
            if not commitments and extracted.get("title"):
                # Fallback: at least one commitment was found (top-level title)
                commitments = [{
                    "text": extracted["title"],
                    "speaker_id": extracted.get("assignee"),
                    "confidence": extracted.get("confidence", 0.5),
                }]

            # Build a reverse map: speaker_name → speaker_user_id from speaker_map
            name_to_user_id: Dict[str, str | None] = {}
            for label, info in speaker_map.items():
                if info["name"] != "Unknown Speaker":
                    name_to_user_id[info["name"]] = info["user_id"]

            for commitment in commitments:
                speaker_name = commitment.get("speaker_id", "unknown")
                action_items.append({
                    "title": commitment.get("text", extracted.get("title", "")),
                    "description": commitment.get("context", ""),
                    "assignee_id": name_to_user_id.get(speaker_name),
                    "assignee_name": speaker_name if speaker_name != "unknown" else None,
                    "speaker_user_id": name_to_user_id.get(speaker_name),
                    "confidence": commitment.get("confidence", 0.5),
                    "priority": extracted.get("priority", "medium"),
                    "deadline": extracted.get("deadline"),
                    "source_text": full_transcript_text[:500],
                    "start_ms": enriched_segments[0]["start_ms"] if enriched_segments else 0,
                })
        except Exception as e:
            logger.warning("action_item_extraction_failed", error=str(e))

    await _insert_action_items(room_db_id, org_id, action_items)

    # ── Step 7: Write to Supermemory ──────────────────────────────────────────
    try:
        from cassandra.rag.context_fetcher import write_session_memory
        await write_session_memory(
            session_id=session_id,
            org_id=org_id,
            user_id=None,
            session_stats={
                "duration_seconds": 0,
                "transcript_turns": len(enriched_segments),
            },
            room_id=room.get("room_id"),
            speaker_map=speaker_map,
            action_items=action_items,
        )
    except Exception as e:
        logger.error("supermemory_write_failed", room_db_id=room_db_id, error=str(e))

    # ── Step 8: Update room status ────────────────────────────────────────────
    analysis_result = {
        "speaker_map": speaker_map,
        "action_item_count": len(action_items),
        "transcript_segment_count": len(enriched_segments),
    }
    await _update_room_analysis_status(room_db_id, org_id, "completed", analysis_result)

    logger.info(
        "room_analysis_completed",
        room_db_id=room_db_id,
        session_id=session_id,
        transcript_segments=len(enriched_segments),
        action_items=len(action_items),
    )
    return True
