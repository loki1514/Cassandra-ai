import os
import json
import base64
import asyncio
import re
from typing import List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, File, UploadFile, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import websockets
from dotenv import load_dotenv

# Vector DB (Supabase)
import psycopg2
from sentence_transformers import SentenceTransformer

load_dotenv()

app = FastAPI(title="Cassandra: Revival Cortex")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Load Model
print("Loading Embedding Model...")
model = SentenceTransformer('all-MiniLM-L6-v2') 
print("Model Loaded.")

# DB Connection (SUPABASE)
def get_db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    )

# --- INGESTION PIPELINE ---

def clean_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    return text.strip()

def chunk_text(text: str, chunk_size: int = 400, overlap: int = 50) -> List[str]:
    words = text.split()
    chunks = []
    for i in range(0, len(words), chunk_size - overlap):
        chunk = words[i:i + chunk_size]
        if chunk:
            chunks.append(" ".join(chunk))
    return chunks

@app.post("/ingest")
async def ingest_document(file: UploadFile = File(...)):
    try:
        content = await file.read()
        text = content.decode("utf-8", errors="ignore")
        clean = clean_text(text)
        chunks = chunk_text(clean)
        if not chunks:
            return {"status": "skipped", "message": "No text found."}
        
        vectors = model.encode(chunks).tolist()
        conn = get_db()
        cur = conn.cursor()
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            cur.execute("""
                INSERT INTO documents (content, embedding, metadata)
                VALUES (%s, %s, %s)
            """, (chunk, vec, json.dumps({"source": file.filename, "chunk_index": i})))
        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success", "chunks": len(chunks)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# --- REALTIME VOICE PROXY ---

@app.websocket("/ws/boardroom")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    print("Frontend connected to Boardroom WebSocket")

    # Connect to OpenAI Realtime
    url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "OpenAI-Beta": "realtime=v1"
    }

    try:
        async with websockets.connect(url, extra_headers=headers) as openai_ws:
            print("Connected to OpenAI Realtime API")

            # Initialize Session with Tools
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": """You are Cassandra, a mission-control AI. 
                    You have access to a Cortex memory vault (Supabase).
                    When prominent decisions or risks are mentioned, use the 'save_insight' tool.
                    When the user asks for historical context, use the 'search_memory' tool.
                    Be concise and professional.""",
                    "voice": "alloy",
                    "input_audio_format": "pcm16",
                    "output_audio_format": "pcm16",
                    "turn_detection": {"type": "server_vad"},
                    "tools": [
                        {
                            "type": "function",
                            "name": "save_insight",
                            "description": "Store a decision, risk, or topic in the Cortex vault.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "insight_type": {"type": "string", "enum": ["decision", "risk", "topic"]},
                                    "content": {"type": "string"}
                                },
                                "required": ["insight_type", "content"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "search_memory",
                            "description": "Search the Cortex memory vault for historical context.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "query": {"type": "string"}
                                },
                                "required": ["query"]
                            }
                        }
                    ],
                    "tool_choice": "auto"
                }
            }
            await openai_ws.send(json.dumps(session_update))

            async def handle_tool_call(call):
                name = call.get("name")
                args = json.loads(call.get("arguments", "{}"))
                call_id = call.get("call_id")

                if name == "save_insight":
                    # Perform Ingestion
                    content = args["content"]
                    i_type = args["insight_type"]
                    try:
                        vec = model.encode([content])[0].tolist()
                        conn = get_db()
                        cur = conn.cursor()
                        cur.execute("""
                            INSERT INTO documents (content, embedding, metadata)
                            VALUES (%s, %s, %s)
                        """, (content, vec, json.dumps({"type": i_type, "triggered_by": "voice"})))
                        conn.commit()
                        cur.close()
                        conn.close()
                        
                        # UI Update
                        await websocket.send_json({
                            "type": "insight",
                            "insight_type": i_type,
                            "text": content,
                            "confidence": "1.0"
                        })
                        return {"status": "success", "message": f"Saved {i_type} to cortex."}
                    except Exception as e:
                        return {"status": "error", "message": str(e)}

                elif name == "search_memory":
                    query = args["query"]
                    try:
                        vec = model.encode([query])[0].tolist()
                        conn = get_db()
                        cur = conn.cursor()
                        cur.execute("""
                            SELECT content, metadata, 1 - (embedding <=> %s::vector) as similarity
                            FROM documents
                            ORDER BY embedding <=> %s::vector
                            LIMIT 3
                        """, (str(vec), str(vec)))
                        results = cur.fetchall()
                        cur.close()
                        conn.close()
                        
                        memory_text = "\n".join([f"- {r[0]}" for r in results])
                        return {"status": "success", "results": memory_text}
                    except Exception as e:
                        return {"status": "error", "message": str(e)}

                return {"status": "error", "message": "Unknown tool"}

            async def relay_from_openai():
                try:
                    async for message in openai_ws:
                        data = json.loads(message)
                        
                        # Handle tool calls
                        if data.get("type") == "response.done":
                            response = data.get("response", {})
                            output = response.get("output", [])
                            for item in output:
                                if item.get("type") == "function_call":
                                    result = await handle_tool_call(item)
                                    # Send back to OpenAI
                                    tool_response = {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "call_id": item["call_id"],
                                            "output": json.dumps(result)
                                        }
                                    }
                                    await openai_ws.send(json.dumps(tool_response))
                                    # Trigger another response
                                    await openai_ws.send(json.dumps({"type": "response.create"}))

                        # Handle transcript
                        elif data.get("type") == "response.audio_transcript.delta":
                            await websocket.send_json({
                                "type": "transcript",
                                "speaker": "AI",
                                "text": data["delta"]
                            })
                        
                        # Handle audio
                        elif data.get("type") == "response.audio.delta":
                            await websocket.send_json({
                                "type": "audio",
                                "audio": data["delta"]
                            })

                        # Handle interruption
                        elif data.get("type") == "input_audio_buffer.speech_started":
                            await websocket.send_json({"type": "interrupt"})

                except Exception as e:
                    print(f"Error in relay_from_openai: {e}")

            async def relay_from_frontend():
                try:
                    while True:
                        message = await websocket.receive_text()
                        data = json.loads(message)
                        
                        if data.get("type") == "input_audio":
                            audio_event = {
                                "type": "input_audio_buffer.append",
                                "audio": data["audio"]
                            }
                            await openai_ws.send(json.dumps(audio_event))
                except Exception as e:
                    print(f"Error in relay_from_frontend: {e}")

            await asyncio.gather(relay_from_openai(), relay_from_frontend())

    except WebSocketDisconnect:
        print("Frontend disconnected")
    except Exception as e:
        print(f"WebSocket Error: {e}")

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

