# Expo App → Cassandra Integration Guide

## Quick Reference

**Server URL:** `http://YOUR_SERVER_IP:8000`
**WebSocket:** `ws://YOUR_SERVER_IP:8000/ws/audio/{org_id}?token={jwt}`

---

## 1. Authentication

Cassandra uses Supabase JWTs. Your Expo app authenticates users via Supabase Auth, gets a JWT, and passes it to the WebSocket.

```typescript
// 1. User signs in with Supabase (existing auth flow)
const { data: { session } } = await supabase.auth.signInWithPassword({
  email: 'user@example.com',
  password: 'password'
})

const token = session.access_token
const orgId = session.user.user_metadata.org_id
```

---

## 2. WebSocket Connection

```typescript
// Connect to Cassandra
const wsUrl = `ws://localhost:8000/ws/audio/${orgId}?token=${encodeURIComponent(token)}`
const ws = new WebSocket(wsUrl)

ws.addEventListener('open', () => {
  console.log('Connected to Cassandra')
  // Send a test message
  ws.send(JSON.stringify({ action: 'status' }))
})

ws.addEventListener('message', (event) => {
  // Handle text messages (JSON)
  if (typeof event.data === 'string') {
    const msg = JSON.parse(event.data)
    handleCassandraMessage(msg)
  }
  // Handle binary messages (MP3 audio / voice response)
  else if (event.data instanceof Blob || event.data instanceof ArrayBuffer) {
    playAudioResponse(event.data)
  }
})

ws.addEventListener('close', (event) => {
  console.log('Disconnected:', event.code, event.reason)
})
```

---

## 3. Sending Audio

Send raw PCM16 audio (16kHz, mono, 16-bit). No encoding needed.

```typescript
// Start recording from microphone
const audioContext = new AudioContext({ sampleRate: 16000 })
const mediaStream = await navigator.mediaDevices.getUserMedia({ audio: true })
const source = audioContext.createMediaStreamSource(mediaStream)
const processor = new AudioWorkletNode(audioContext, 'pcm-processor')

source.connect(processor)
processor.connect(audioContext.destination)

// Receive PCM16 chunks and send to WebSocket
processor.port.onmessage = (event) => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(event.data)  // Send raw bytes
  }
}
```

**PCM16 format:** 16kHz sample rate, mono, 16-bit signed integers, little-endian.

### iOS (React Native)

```typescript
import { Audio } from 'expo-av'

// Configure audio recording
const recording = new Audio.Recording()
await recording.prepareToRecordAsync({
  android: { extension: '.raw', outputFormat: Audio.AndroidOutputFormat.DEFAULT },
  ios: { extension: '.raw', audioQuality: Audio.IOSAudioQuality.MAX },
  web: { bitsPerSecond: 256000 }
})

// Stream chunks to WebSocket
recording.setOnRecordingStatusUpdate((status) => {
  if (status.isRecording && status.recordingDuration > 0) {
    // Send audio chunks periodically
    ws.send(status.uri)  // Or use a stream reader
  }
})
```

---

## 4. Receiving Voice Responses

Cassandra sends two types of responses:

### JSON Messages (text)
```typescript
function handleCassandraMessage(msg: any) {
  switch (msg.type) {
    case 'connected':
      console.log('Cassandra ready:', msg.message)
      break

    case 'segment':
      console.log('Audio segment captured:', msg.duration_ms, 'ms')
      break

    case 'pipeline_result':
      console.log('Transcript:', msg.data.transcript)
      console.log('Tickets created:', msg.data.tickets_created)
      break

    case 'voice_response':
      console.log('Cassandra responded with voice:', msg.text)
      break

    case 'heartbeat':
      // Ignore — keep-alive from server
      break

    case 'error':
      console.error('Cassandra error:', msg.message)
      break

    case 'complete':
      console.log('Response complete')
      break
  }
}
```

### Binary Messages (MP3 audio)
```typescript
async function playAudioResponse(audioData: Blob | ArrayBuffer) {
  const blob = audioData instanceof Blob ? audioData : new Blob([audioData], { type: 'audio/mpeg' })
  const url = URL.createObjectURL(blob)
  const audio = new Audio(url)
  await audio.play()
}
```

---

## 5. Complete Voice Loop Example

```typescript
import { useEffect, useRef, useState } from 'react'
import { WebSocket } from 'expo-build-properties'

export function useCassandraConnection(orgId: string, token: string) {
  const wsRef = useRef<WebSocket | null>(null)
  const [isConnected, setIsConnected] = useState(false)
  const [lastTranscript, setLastTranscript] = useState<string>('')
  const [lastTickets, setLastTickets] = useState<any[]>([])

  useEffect(() => {
    const wsUrl = `ws://localhost:8000/ws/audio/${orgId}?token=${encodeURIComponent(token)}`
    const ws = new WebSocket(wsUrl)

    ws.onOpen = () => setIsConnected(true)
    ws.onClose = () => setIsConnected(false)
    ws.onError = (e) => console.error('WS Error:', e)

    ws.onMessage = (event) => {
      if (typeof event.data === 'string') {
        const msg = JSON.parse(event.data)
        if (msg.type === 'pipeline_result') {
          setLastTranscript(msg.data.transcript || '')
          setLastTickets(msg.data.tickets_created || [])
        }
      } else {
        // Binary audio — play it
        playAudio(event.data)
      }
    }

    wsRef.current = ws

    return () => ws.close()
  }, [orgId, token])

  function sendAudio(audioBytes: ArrayBuffer) {
    wsRef.current?.send(audioBytes)
  }

  return { isConnected, sendAudio, lastTranscript, lastTickets }
}
```

---

## 6. API Endpoints Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `WS` | `/ws/audio/{org_id}?token=JWT` | **Main voice WebSocket** — audio in, voice out |
| `WS` | `/ws/audio` | Unauthenticated audio buffering (no auth) |
| `POST` | `/voice/query` | Text query → text response |
| `POST` | `/voice/query/audio` | Text query → MP3 audio response |
| `WS` | `/voice/query/stream` | Text query → streaming audio |
| `POST` | `/api/v1/voice/process` | Audio bytes → full pipeline (transcription + ticket creation) |
| `GET` | `/health` | Basic health check |
| `GET` | `/health/dashboard` | Detailed health + system metrics |

---

## 7. Environment Variables Needed on the Server

The server (Cassandra) needs these from `.env`:

```bash
# Required
SUPABASE_URL=https://hapwbiteqgusvjifxium.supabase.co
SUPABASE_ANON_KEY=<your anon key>
SUPABASE_SERVICE_ROLE_KEY=<your service role key>
SUPABASE_JWT_SECRET=<your jwt secret>
OPENAI_API_KEY=<your openai key>
ASSEMBLYAI_API_KEY=<your assemblyai key>

# Required for orb to talk
ELEVENLABS_API_KEY=<your elevenlabs key>

# Optional but recommended
SECURITY_ALLOWED_ORIGINS=["exp://localhost:8081","exp://192.168.x.x:8081"]
```

For local development with Expo, add your machine's local IP:
```
SECURITY_ALLOWED_ORIGINS=["exp://localhost:8081","exp://192.168.1.x:8081"]
```

---

## 8. Starting the Server

```bash
cd /path/to/Cassandra-ai

# Development mode (auto-reload)
python -m cassandra.main

# Or
uvicorn cassandra.main:app --reload --host 0.0.0.0 --port 8000
```

Server will start on `http://0.0.0.0:8000`. Access docs at `http://localhost:8000/docs`.

---

## 9. Testing the Orb

Once connected, speak into the microphone. The expected flow:

1. You speak → audio sent as binary frames over WebSocket
2. Server detects silence → segment extracted
3. Server transcribes → `pipeline_result` JSON sent back with transcript
4. Server generates TTS response → MP3 bytes sent as binary message
5. Expo receives MP3 → plays audio → orb talks back

If the orb doesn't respond, check:
- `ELEVENLABS_API_KEY` is set in `.env`
- Supabase JWT is valid and not expired
- `org_id` in the URL matches the user's org
