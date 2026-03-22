import asyncio
import base64
import json
import math
import os
import struct
import time
import websockets
import ssl
from dotenv import load_dotenv

load_dotenv()

# Fix for macOS SSL Certificate issue
ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-4o-realtime-preview"
SAMPLE_RATE = 24000
DURATION_S = 2


def generate_speech_audio():
    """
    Generate 2 seconds of 440Hz sine wave as PCM16.
    This simulates actual speech-like audio that will trigger VAD.
    Pure silence won't trigger VAD — the threshold is never crossed.
    """
    num_samples = SAMPLE_RATE * DURATION_S
    audio = bytearray()

    for i in range(num_samples):
        t = i / SAMPLE_RATE
        # 440Hz tone with some amplitude variation to mimic speech energy
        amplitude = 0.3 * (1.0 + 0.3 * math.sin(2 * math.pi * 2 * t))
        sample = amplitude * math.sin(2 * math.pi * 440 * t)
        # Clamp and convert to PCM16 little-endian
        sample_int = max(-32768, min(32767, int(sample * 32767)))
        audio += struct.pack('<h', sample_int)

    return base64.b64encode(bytes(audio)).decode('utf-8')


def timestamp():
    return f"[{time.strftime('%H:%M:%S')}]"


async def test_realtime_api():
    url = f"wss://api.openai.com/v1/realtime?model={MODEL}"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1",
    }

    print(f"{timestamp()} Connecting to OpenAI Realtime API...")
    print(f"{timestamp()} Model: {MODEL}")
    print(f"{timestamp()} API Key: {str(OPENAI_API_KEY)[:8]}...{str(OPENAI_API_KEY)[-4:]}")
    print()

    try:
        async with websockets.connect(
            url,
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,
            ssl=ssl_context
        ) as ws:
            print(f"{timestamp()} ✅ WebSocket connected!")
            print()

            # ── Step 1: Send session.update ──
            session_config = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": "You are a test assistant. Say 'Hello, I can hear you!' when you receive audio input.",
                    "voice": "alloy",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 500,
                    },
                    "tools": [],
                },
            }
            print(f"{timestamp()} Sending session.update...")
            await ws.send(json.dumps(session_config))

            # ── Step 2: Wait for session.created ──
            session_ready = False
            print(f"{timestamp()} Waiting for session confirmation...")
            print()

            async def process_messages():
                audio_chunks_received = 0
                transcript_text = ""
                
                async for message in ws:
                    data = json.loads(message)
                    msg_type = data.get("type", "UNKNOWN")

                    # Format output based on message type
                    if msg_type == "error":
                        error = data.get("error", {})
                        print(f"{timestamp()} ❌ ERROR: {error.get('type', 'unknown')}")
                        print(f"           Message: {error.get('message', 'no message')}")
                        print(f"           Code: {error.get('code', 'none')}")
                        print()
                        return False

                    elif msg_type == "session.created":
                        session_ready = True
                        session = data.get("session", {})
                        print(f"{timestamp()} ✅ Session created!")
                        print(f"           ID: {session.get('id', 'unknown')}")
                        print(f"           Model: {session.get('model', 'unknown')}")
                        print(f"           Voice: {session.get('voice', 'unknown')}")
                        print()
                        # We don't return here so we can keep processing the messages
                        return "SESSION_CREATED"

                    elif msg_type == "session.updated":
                        print(f"{timestamp()} ✅ Session updated (config accepted)")
                        print()

                    elif msg_type == "input_audio_buffer.speech_started":
                        print(f"{timestamp()} 🎤 VAD: Speech detected!")

                    elif msg_type == "input_audio_buffer.speech_stopped":
                        print(f"{timestamp()} 🎤 VAD: Speech ended")

                    elif msg_type == "input_audio_buffer.committed":
                        print(f"{timestamp()} 📦 Audio buffer committed")

                    elif msg_type == "response.created":
                        print(f"{timestamp()} 🤖 Response generation started...")

                    elif msg_type == "response.audio.delta":
                        audio_chunks_received += 1
                        delta_len = len(data.get("delta", ""))
                        if audio_chunks_received == 1:
                            print(f"{timestamp()} 🔊 First audio chunk received! ({delta_len} chars base64)")
                        elif audio_chunks_received % 10 == 0:
                            print(f"{timestamp()} 🔊 Audio chunk #{audio_chunks_received}")

                    elif msg_type == "response.audio_transcript.delta":
                        transcript_text += data.get("delta", "")
                        # Print accumulated transcript on each delta
                        print(f"\r{timestamp()} 📝 AI: {transcript_text}", end="", flush=True)

                    elif msg_type == "response.audio_transcript.done":
                        print()  # Newline after streaming transcript
                        print(f"{timestamp()} 📝 AI transcript complete: {data.get('transcript', '')}")

                    elif msg_type == "conversation.item.input_audio_transcription.completed":
                        print(f"{timestamp()} 📝 User transcript: {data.get('transcript', '')}")

                    elif msg_type == "response.audio.done":
                        print(f"{timestamp()} 🔊 Audio response complete ({audio_chunks_received} chunks)")

                    elif msg_type == "response.done":
                        print()
                        print(f"{timestamp()} ✅ Full response cycle complete!")
                        print(f"           Audio chunks: {audio_chunks_received}")
                        print(f"           Transcript: {transcript_text}")
                        return "RESPONSE_DONE"

                    elif msg_type == "rate_limits.updated":
                        pass

                    else:
                        # Log any unknown types
                        preview = json.dumps(data)[:150]
                        print(f"{timestamp()} ℹ️  {msg_type}: {preview}")

            # Wait for session created
            while True:
                msg = await ws.recv()
                data = json.loads(msg)
                if data.get("type") == "session.created":
                    session = data.get("session", {})
                    print(f"{timestamp()} ✅ Session created!")
                    print(f"           ID: {session.get('id', 'unknown')}")
                    print(f"           Model: {session.get('model', 'unknown')}")
                    print(f"           Voice: {session.get('voice', 'unknown')}")
                    print()
                    break
                elif data.get("type") == "error":
                    print(f"{timestamp()} ❌ Session setup failed.")
                    print(json.dumps(data.get('error'), indent=2))
                    return

            # Read remaining init messages like session.updated
            # We'll just start an asyncio task to process everything continuously
            listener_task = asyncio.create_task(process_messages())

            # ── Step 3: Send audio ──
            print(f"{timestamp()} Generating {DURATION_S}s of test audio (440Hz tone)...")
            audio_b64 = generate_speech_audio()
            audio_bytes = SAMPLE_RATE * DURATION_S * 2
            print(f"{timestamp()} Audio size: {audio_bytes} bytes ({len(audio_b64)} base64 chars)")
            print()

            chunk_size = 4800  # 100ms
            chunks_sent = 0
            for i in range(0, len(audio_b64), chunk_size):
                chunk = audio_b64[i:i + chunk_size]
                await ws.send(json.dumps({
                    "type": "input_audio_buffer.append",
                    "audio": chunk,
                }))
                chunks_sent += 1
                await asyncio.sleep(0.05)

            print(f"{timestamp()} Sent {chunks_sent} audio chunks")

            # ── Step 4: Commit buffer and request response ──
            await asyncio.sleep(1.0)  

            print(f"{timestamp()} Manually committing audio buffer...")
            await ws.send(json.dumps({"type": "input_audio_buffer.commit"}))

            print(f"{timestamp()} Requesting response...")
            await ws.send(json.dumps({"type": "response.create"}))

            # ── Step 5: Listen for response ──
            print()
            print(f"{timestamp()} Waiting for AI response...")
            print("─" * 60)

            result = await listener_task

            print()
            print("─" * 60)
            print(f"{timestamp()} ✅ Test complete!")

    except Exception as e:
        print(f"{timestamp()} ❌ Error: {type(e).__name__}: {e}")

if __name__ == "__main__":
    if not OPENAI_API_KEY:
        print("❌ OPENAI_API_KEY not found in environment!")
    else:
        asyncio.run(test_realtime_api())
