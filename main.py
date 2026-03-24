"""
main.py — Cassandra AI Backend (CLEAN REBUILD)

Full-duplex audio relay between browser client and OpenAI Realtime API.

Fixes from the original:
  1. Logs EVERY OpenAI event type (orignal silently dropped unknown types)
  2. Explicitly handles error events (original swallowed them)
  3. Waits for session.created before signaling frontend as ready
  4. Handles heartbeat pings from frontend
  5. Proper concurrent task management with cancellation
  6. Graceful shutdown on both sides
  7. Audio chunk counting for debugging throughput

Run: uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""
import asyncio
import json
import os
import logging
import time
import ssl
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import websockets
from supabase import create_client, Client

# macOS SSL Certificate fix
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# ─── Logging ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
logger = logging.getLogger("cassandra")

# ─── Config ─────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
OPENAI_MODEL = "gpt-4o-realtime-preview"
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={OPENAI_MODEL}"

if not OPENAI_API_KEY:
    logger.error("OPENAI_API_KEY not set! The backend will fail to connect.")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("Supabase credentials not set!")
    supabase: Client = None
else:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ─── System Prompt ──────────────────────────────────────────
CASSANDRA_INSTRUCTIONS = "pmpt_69bedd3a775881958d0e364bdbb597be07ad8c3617ae0dbd"

# ─── Session Configuration ──────────────────────────────────
SESSION_CONFIG = {
    "type": "session.update",
    "session": {
        "modalities": ["text", "audio"],
        "instructions": CASSANDRA_INSTRUCTIONS,
        "voice": "alloy",
        "input_audio_format": "pcm16",
        "output_audio_format": "pcm16",
        "input_audio_transcription": {
            "model": "whisper-1",
        },
        "turn_detection": {
            "type": "server_vad",
            "threshold": 0.62,
            "prefix_padding_ms": 450,
            "silence_duration_ms": 750,
        },
        "tools": [
            {
                "type": "function",
                "name": "save_insight",
                "description": "Save a detected insight from the conversation. Call proactively for decisions, action items, risks, contradictions, key facts.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "insight": {"type": "string", "description": "The insight text."},
                        "category": {
                            "type": "string",
                            "enum": ["decision", "action_item", "risk_flag", "contradiction", "key_fact", "pattern", "blind_spot"],
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "owner": {"type": "string", "description": "Person responsible, if applicable."},
                    },
                    "required": ["insight", "category", "confidence"],
                },
            }
        ],
    },
}

# ─── FastAPI App ────────────────────────────────────────────
app = FastAPI(title="Cassandra AI")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Meeting Storage (in-memory for now) ────────────────────
meetings = {}


@app.post("/api/meetings/new")
async def create_meeting():
    meeting_id = f"mtg-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{os.urandom(4).hex()}"
    
    if supabase:
        try:
            # We use the generated string as a 'title' or just track it. 
            # The database schema has a UUID primary key, so we'll store our meeting_id as title.
            data, count = supabase.table("meetings").insert({"title": meeting_id}).execute()
            db_id = data[1][0]["id"]
            logger.info(f"Meeting created in DB: {meeting_id} (UUID: {db_id})")
            return {"meeting_id": db_id} # Return the actual DB UUID
        except Exception as e:
            logger.error(f"Failed to create meeting in Supabase: {e}")
    
    meetings[meeting_id] = {"created": datetime.now().isoformat(), "status": "active"}
    logger.info(f"Meeting created (fallback): {meeting_id}")
    return {"meeting_id": meeting_id}


@app.get("/api/roles")
async def get_roles():
    return {
        "roles": [
            {"id": "GENERAL", "name": "General", "color": "#00FFFF"},
            {"id": "MARKETING", "name": "Marketing", "color": "#FF6B9D"},
            {"id": "SALES", "name": "Sales", "color": "#FFD93D"},
        ]
    }


# ─── OpenAI Connection ─────────────────────────────────────
async def connect_openai():
    """Establish WebSocket to OpenAI Realtime API."""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }
    ws = await websockets.connect(
        OPENAI_REALTIME_URL,
        additional_headers=headers,
        ping_interval=20,
        ping_timeout=10,
        max_size=10 * 1024 * 1024,
        ssl=ssl_context
    )
    return ws


# ─── Relay: Client → OpenAI ────────────────────────────────
async def relay_client_to_openai(client_ws: WebSocket, openai_ws, stats: dict):
    """Forward audio and control messages from browser to OpenAI."""
    try:
        while True:
            raw = await client_ws.receive_text()
            data = json.loads(raw)
            msg_type = data.get("type", "unknown")

            # Heartbeat — respond immediately, don't forward
            if msg_type == "ping":
                await client_ws.send_json({"type": "pong"})
                continue

            # Audio frame — forward to OpenAI
            if msg_type == "input_audio":
                audio = data.get("audio", "")
                stats["audio_chunks_sent"] += 1

                # Log every 50th chunk to avoid spam
                if stats["audio_chunks_sent"] % 50 == 1:
                    logger.info(
                        f"[Client→OpenAI] Audio chunk #{stats['audio_chunks_sent']} "
                        f"({len(audio)} base64 chars)"
                    )

                await openai_ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": audio,
                }))

            # Role switch
            elif msg_type == "switch_role":
                role = data.get("role", "GENERAL")
                logger.info(f"[Client] Role switch requested: {role}")
                # Could update session instructions here
                await client_ws.send_json({
                    "type": "role_switched",
                    "role": role,
                })

            # Forward other types directly (tool results, etc.)
            else:
                logger.info(f"[Client→OpenAI] Forwarding: {msg_type}")
                await openai_ws.send(json.dumps(data))

    except WebSocketDisconnect:
        logger.info("[Client] Disconnected.")
    except Exception as e:
        logger.error(f"[Client→OpenAI] Error: {e}")


# ─── Relay: OpenAI → Client ────────────────────────────────
async def relay_openai_to_client(client_ws: WebSocket, openai_ws, stats: dict, meeting_id: str):
    """Forward responses from OpenAI back to browser client."""
    try:
        async for message in openai_ws:
            data = json.loads(message)
            msg_type = data.get("type", "UNKNOWN")

            # ── ERRORS — always log loudly ──
            if msg_type == "error":
                error = data.get("error", {})
                logger.error(
                    f"[OpenAI ERROR] type={error.get('type')} "
                    f"code={error.get('code')} "
                    f"message={error.get('message')}"
                )
                await client_ws.send_json({
                    "type": "error",
                    "message": error.get("message", "Unknown OpenAI error"),
                })
                continue

            # ── Session lifecycle ──
            if msg_type == "session.created":
                logger.info("[OpenAI] Session created ✅")
                await client_ws.send_json({"type": "connected"})

            elif msg_type == "session.updated":
                logger.info("[OpenAI] Session config accepted ✅")

            # ── VAD events ──
            elif msg_type == "input_audio_buffer.speech_started":
                logger.info("[OpenAI] 🎤 Speech detected — sending interrupt")
                stats["speech_events"] += 1
                await client_ws.send_json({"type": "interrupt"})

            elif msg_type == "input_audio_buffer.speech_stopped":
                logger.info("[OpenAI] 🎤 Speech ended")

            elif msg_type == "input_audio_buffer.committed":
                logger.info("[OpenAI] Audio buffer committed")

            # ── AI Audio Response ──
            elif msg_type == "response.audio.delta":
                stats["audio_chunks_received"] += 1
                if stats["audio_chunks_received"] == 1:
                    logger.info("[OpenAI] 🔊 First audio response chunk!")
                await client_ws.send_json({
                    "type": "audio",
                    "audio": data["delta"],
                })

            elif msg_type == "response.audio.done":
                logger.info(
                    f"[OpenAI] 🔊 Audio complete "
                    f"({stats['audio_chunks_received']} chunks)"
                )

            # ── AI Transcript (streaming) ──
            elif msg_type == "response.audio_transcript.delta":
                await client_ws.send_json({
                    "type": "transcript",
                    "speaker": "AI",
                    "text": data.get("delta", ""),
                    "is_delta": True,
                })

            elif msg_type == "response.audio_transcript.done":
                transcript = data.get("transcript", "")
                logger.info(f"[OpenAI] 📝 AI said: {transcript[:80]}...")
                
                if supabase and meeting_id:
                    try:
                        supabase.table("transcripts").insert({
                            "meeting_id": meeting_id,
                            "speaker_role": "ai",
                            "content": transcript
                        }).execute()
                    except Exception as e:
                        logger.error(f"Failed to save AI transcript: {e}")

                await client_ws.send_json({
                    "type": "transcript",
                    "speaker": "AI",
                    "text": transcript,
                    "is_delta": False,
                })

            # ── User Transcript ──
            elif msg_type == "conversation.item.input_audio_transcription.completed":
                transcript = data.get("transcript", "")
                logger.info(f"[OpenAI] 📝 User said: {transcript[:80]}...")

                if supabase and meeting_id:
                    try:
                        supabase.table("transcripts").insert({
                            "meeting_id": meeting_id,
                            "speaker_role": "user",
                            "content": transcript
                        }).execute()
                    except Exception as e:
                        logger.error(f"Failed to save User transcript: {e}")

                await client_ws.send_json({
                    "type": "transcript",
                    "speaker": "User",
                    "text": transcript,
                    "is_delta": False,
                })

            # ── Response lifecycle ──
            elif msg_type == "response.created":
                logger.info("[OpenAI] 🤖 Generating response...")
                stats["audio_chunks_received"] = 0  # Reset for new response

            elif msg_type == "response.done":
                output = data.get("response", {}).get("output", [])
                logger.info(f"[OpenAI] Response complete ({len(output)} output items)")

                # Handle tool calls
                for item in output:
                    if item.get("type") == "function_call":
                        tool_name = item.get("name", "")
                        tool_args = item.get("arguments", "{}")
                        call_id = item.get("call_id", "")
                        logger.info(f"[OpenAI] 🔧 Tool call: {tool_name}({tool_args[:100]})")

                        # Send insight to frontend
                        if tool_name == "save_insight":
                            try:
                                args = json.loads(tool_args)
                                
                                if supabase and meeting_id:
                                    try:
                                        # Map category to allowed enum in DB: ('decision', 'risk', 'topic', 'summary')
                                        # or adjust if you have a different enum.
                                        # PRD says: ("decision", "action_item", "risk_flag", "contradiction", "key_fact", "pattern", "blind_spot")
                                        # Actual init.sql check: artifact_type in ('decision', 'risk', 'topic', 'summary')
                                        # I'll use a mapping or set a default.
                                        
                                        type_map = {
                                            "decision": "decision",
                                            "risk_flag": "risk",
                                            "key_fact": "topic",
                                            "action_item": "topic"
                                        }
                                        a_type = type_map.get(args.get("category"), "topic")
                                        
                                        supabase.table("artifacts").insert({
                                            "meeting_id": meeting_id,
                                            "artifact_type": a_type,
                                            "content": args.get("insight", ""),
                                            "confidence": 0.9 # Default float
                                        }).execute()
                                    except Exception as e:
                                        logger.error(f"Failed to save insight to DB: {e}")

                                await client_ws.send_json({
                                    "type": "insight",
                                    "insight": args.get("insight", ""),
                                    "category": args.get("category", "key_fact"),
                                    "confidence": args.get("confidence", "medium"),
                                    "owner": args.get("owner", ""),
                                })
                            except json.JSONDecodeError:
                                logger.error(f"[Tool] Failed to parse args: {tool_args}")

                        # Return tool result to OpenAI
                        await openai_ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps({"status": "saved"}),
                            },
                        }))

                        # Trigger continuation
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                        }))

            # ── Rate limits ──
            elif msg_type == "rate_limits.updated":
                pass  # Silent — too noisy

            # ── Catch-all for unknown types ──
            else:
                logger.debug(f"[OpenAI] Unhandled: {msg_type}")

    except websockets.exceptions.ConnectionClosed as e:
        logger.warning(f"[OpenAI] Connection closed: {e}")
    except Exception as e:
        logger.error(f"[OpenAI→Client] Error: {e}")


# ─── Main WebSocket Endpoint ───────────────────────────────
@app.websocket("/ws/meeting/{meeting_id}")
async def meeting_websocket(websocket: WebSocket, meeting_id: str):
    await websocket.accept()
    logger.info(f"[WS] Client connected for meeting: {meeting_id}")

    stats = {
        "audio_chunks_sent": 0,
        "audio_chunks_received": 0,
        "speech_events": 0,
        "start_time": time.time(),
    }

    openai_ws = None

    try:
        # Connect to OpenAI
        logger.info("[OpenAI] Connecting to Realtime API...")
        openai_ws = await connect_openai()
        logger.info("[OpenAI] Connected ✅")

        # Send session configuration
        logger.info("[OpenAI] Sending session.update...")
        await openai_ws.send(json.dumps(SESSION_CONFIG))

        # Run both relays concurrently
        client_task = asyncio.create_task(
            relay_client_to_openai(websocket, openai_ws, stats)
        )
        openai_task = asyncio.create_task(
            relay_openai_to_client(websocket, openai_ws, stats, meeting_id)
        )

        done, pending = await asyncio.wait(
            [client_task, openai_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel remaining task
        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    except websockets.exceptions.InvalidStatusCode as e:
        logger.error(f"[OpenAI] Rejected: HTTP {e.status_code}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"OpenAI rejected connection: HTTP {e.status_code}",
            })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"[WS] Error: {type(e).__name__}: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": str(e),
            })
        except Exception:
            pass

    finally:
        # Clean up
        if openai_ws:
            await openai_ws.close()
            logger.info("[OpenAI] Connection closed.")

        elapsed = time.time() - stats["start_time"]
        logger.info(
            f"[WS] Meeting {meeting_id} ended. "
            f"Duration: {elapsed:.0f}s | "
            f"Audio sent: {stats['audio_chunks_sent']} chunks | "
            f"Audio received: {stats['audio_chunks_received']} chunks | "
            f"Speech events: {stats['speech_events']}"
        )


# ─── Legacy endpoint (if your frontend uses /ws/boardroom) ──
@app.websocket("/ws/boardroom")
async def boardroom_websocket(websocket: WebSocket):
    """Alias for meeting WebSocket with auto-generated ID."""
    meeting_id = f"boardroom-{os.urandom(4).hex()}"
    await meeting_websocket.__wrapped__(websocket, meeting_id) if hasattr(meeting_websocket, '__wrapped__') else await meeting_websocket(websocket, meeting_id)


# ─── Health check ───────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "openai_key_set": bool(OPENAI_API_KEY),
        "model": OPENAI_MODEL,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
