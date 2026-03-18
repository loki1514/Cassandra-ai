import os
import json
import asyncio
import websockets
from dotenv import load_dotenv
from config import config

load_dotenv()

# We will use the OpenAI Realtime API.
URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

async def connect_to_boardroom():
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1"
    }

    print("Connecting to OpenAI Realtime API...")
    async with websockets.connect(URL, extra_headers=headers) as ws:
        # 1. Initialize Session
        session_update = {
            "type": "session.update",
            "session": {
                "modalities": ["text", "audio"],
                "instructions": "You are the Boardroom AI Nervous System. Use a professional, executive tone. Identify any decisions or risks mentioned and articulate them clearly.",
                "voice": "alloy",
                "turn_detection": {"type": "server_vad"} # Enables Full Duplex interruptions
            }
        }
        await ws.send(json.dumps(session_update))

        print("Boardroom AI Nervous System: Online. Awaiting audio...")

        # 2. Handle Real-time Events
        async for message in ws:
            event = json.loads(message)
            
            if event["type"] == "response.audio.delta":
                # In Phase 3, we will stream this base64 audio Delta to the Frontend
                pass

            elif event["type"] == "input_audio_buffer.speech_started":
                print("Interruption detected. Silencing AI...")
                
            elif event["type"] == "response.done":
                # Here we could intercept the assistant's text response and check if a Decision was identified.
                # If so, write it to Supabase via services/memory.py logic.
                print("Response finished.")

            # Print events for debugging connection
            # print(event["type"])
