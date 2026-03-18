# Product Requirements Document

## CASSANDRA — AI Voice Assistant Orb Interface (Boardroom Edition)

**Version:** 1.0  
**Date:** March 5, 2026  
**Status:** Draft  
**Author:** Product Team

---

## 1. Overview

Cassandra is a real-time, voice-activated AI assistant presented through an immersive 3D orb visualization. The orb serves as the visual embodiment of the AI — it listens, thinks, speaks, and idles, with each state reflected through distinct animations, colors, and behaviors.

The core principle is that the orb *is* the interface. Users interact entirely through voice, and Cassandra responds through voice and visual state changes.

---

## 2. Goals and Success Criteria

**Product Goals**

- Deliver a hands-free, voice-first AI assistant experience with zero traditional UI chrome.
- Create an emotionally resonant visual interface that communicates system state intuitively.
- Achieve sub-4-second end-to-end response latency (wake → first audio output) for simple queries, leveraging OpenAI's Realtime API.

**Success Metrics**

- Full Duplex communication with server VAD (Voice Activity Detection) enabling seamless interruptions.
- Minimum latency: skipped STT/TTS pipeline overhead, averaging < 500ms natively.
- Frame rate ≥ 30 FPS on mid-range hardware during all orb states.

---

## 3. Target Users

**Primary:** Enterprise teams exploring branded AI avatar interfaces for boardrooms, kiosks, reception desks, or internal tools.

---

## 4. System Architecture

```
User Voice Input
       │
       ▼
┌──────────────┐
│  FastAPI     │  WebSocket Bridge Proxy
│  Backend     │
└──────┬───────┘
       │ (PCM16 Audio Stream via WebSocket)
       ▼
┌──────────────┐
│  OpenAI      │  Realtime API (gpt-4o-realtime-preview)
│  Realtime    │  Handles STT + LLM Reasoning + TTS
└──────┬───────┘
       │ (Response Audio + JSON Events)
       ▼
┌──────────────┐
│  Orb Render  │  p5.js WebGL — state-driven visualization
│  Engine      │
└──────────────┘
```

The system is highly unified: the OpenAI Realtime API natively handles speech-to-speech, removing the need for separate Whisper (STT) and ElevenLabs (TTS) services.

---

## 5. Orb State Machine

The orb operates as a finite state machine with primary states:

### 5.1 State Definitions

| State | Trigger | Visual Behavior | Color Palette | Audio Behavior |
|---|---|---|---|---|
| **Dormant / Idle** | Default / timeout | Ultra-slow rotation, minimal particle size, dim glow | Deep indigo `[20, 0, 60]` → black `[5, 0, 15]` | Connected, waiting for input |
| **Listening** | User speaking (VAD) | Gentle breathing pulse synced to voice amplitude | Cyan `[0, 200, 255]` → teal `[0, 150, 180]` | OpenAI server detects speech |
| **Speaking** | TTS audio arrives | Strong rhythmic pulses synced to output audio FFT | Magenta `[255, 50, 255]` → violet `[160, 0, 200]` | OpenAI Streams Audio Back |

*(Transitions are fluid as OpenAI's Realtime WebSocket controls the back-and-forth)*

---

## 6. Feature Requirements

### 6.1 Audio I/O Integration — P0 (Must Have)

**Requirement:** The frontend must stream microphone audio to the backend and play returned audio seamlessly.

**Specifications:**
- Audio captured via browser Web Audio API. 
- Sent as base64-encoded `pcm16` chunks over WebSockets to the FastAPI proxy.
- Incoming audio chunks from OpenAI (via proxy) are queued and played sequentially.

### 6.2 3D Orb Visualization — P0

**Requirement:** Render a real-time 3D particle orb using p5.js WebGL that visually represents Cassandra's state.

**Specifications:**
- Golden spiral point distribution rendered using WEBGL in `p5.js`.
- Base energy / amplitude drives particle size pulse.
- Smooth transitions between state color palettes.

### 6.3 Semantic Memory Injection — P1

- Users can click a "Retrieve Context" button or use command triggers to search Supabase via the `/api/chat` fallback or an injected `conversation.item.create` event.
- Decisions from past meetings are injected directly into the AI's running memory so the Orb can reference them conversationally.

---

## 7. Technical Stack

| Layer | Technology |
|---|---|
| Rendering | p5.js with WEBGL mode |
| Audio analysis | Web Audio API / p5.FFT |
| Microphone input | Browser `navigator.mediaDevices.getUserMedia` |
| Core Brain | OpenAI Realtime API (`gpt-4o-realtime-preview`) |
| Memory Storage | Supabase (Postgres + pgvector) |
| Backend | FastAPI (Python) serving as WebSocket Proxy |

---

## 8. Privacy and Security

- API keys for OpenAI and Supabase are stored server-side in the `.env` file, never exposed to the client.
- The browser requires explicit microphone permission.

---
