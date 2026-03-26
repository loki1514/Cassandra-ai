'use strict';
/**
 * pipeline.js — Cassandra AI Audio Pipeline v2
 *
 * System 1 — CaptureSystem  : AudioWorklet → RingBuffer (never restarts)
 * System 2 — TransportSystem: WebSocket drain + reconnect + heartbeat
 * System 3 — PlaybackSystem : Sample-accurate AI audio playback
 * + SileroVAD for instant local speech detection
 */

// ── Constants ────────────────────────────────────────────────
const BACKEND_HTTP  = 'http://localhost:8000';
const BACKEND_WS    = 'ws://localhost:8000';
const CAPTURE_RATE  = 24000;
const WS_CHUNK      = 2400;         // ~100ms at 24kHz
const WS_DRAIN_MS   = 50;
const HEARTBEAT_MS  = 15_000;
const MAX_BACKOFF   = 30_000;

// Silero VAD
const VAD_RATE      = 16000;
const VAD_FRAME_16K = 512;
const VAD_FRAME_24K = Math.round(VAD_FRAME_16K * CAPTURE_RATE / VAD_RATE); // 768 samples
const VAD_THRESHOLD = 0.5;
const VAD_MODEL_URL = 'https://cdn.jsdelivr.net/npm/@ricky0123/vad-web@0.0.7/dist/silero_vad.onnx';

// ── RingBuffer ───────────────────────────────────────────────
class RingBuffer {
    constructor(cap) {
        this._buf  = new Float32Array(cap);
        this._cap  = cap;
        this._head = 0;
        this._tail = 0;
        this._size = 0;
    }
    write(samples) {
        for (const s of samples) {
            if (this._size === this._cap) {
                this._tail = (this._tail + 1) % this._cap;
                this._size--;
            }
            this._buf[this._head] = s;
            this._head = (this._head + 1) % this._cap;
            this._size++;
        }
    }
    read(n) {
        const count = Math.min(n, this._size);
        const out = new Float32Array(count);
        for (let i = 0; i < count; i++) {
            out[i] = this._buf[this._tail];
            this._tail = (this._tail + 1) % this._cap;
        }
        this._size -= count;
        return out;
    }
    get available() { return this._size; }
}

// ── Helpers ──────────────────────────────────────────────────
function floatToPCM16Base64(f32) {
    const pcm = new Int16Array(f32.length);
    for (let i = 0; i < f32.length; i++) {
        const s = Math.max(-1, Math.min(1, f32[i]));
        pcm[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
    }
    const bytes = new Uint8Array(pcm.buffer);
    let bin = '';
    for (let i = 0; i < bytes.length; i += 8192)
        bin += String.fromCharCode(...bytes.subarray(i, i + 8192));
    return btoa(bin);
}

function base64ToFloat32(b64) {
    const bin = atob(b64);
    const u8  = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
    const i16 = new Int16Array(u8.buffer);
    const f32 = new Float32Array(i16.length);
    for (let i = 0; i < i16.length; i++) f32[i] = i16[i] / 32768.0;
    return f32;
}

// ── SileroVAD ────────────────────────────────────────────────
class SileroVAD {
    constructor({ onSpeechStart, onSpeechEnd } = {}) {
        this.onSpeechStart   = onSpeechStart;
        this.onSpeechEnd     = onSpeechEnd;
        this._session        = null;
        this._h = this._c   = null;
        this._speaking       = false;
        this._pending        = [];   // 24kHz sample accumulator
        this._silenceFrames  = 0;
        this.SILENCE_LIMIT   = 8;    // ~256ms at 32ms/frame
    }

    async load() {
        if (typeof ort === 'undefined') {
            console.warn('[VAD] onnxruntime-web not loaded — VAD disabled');
            return;
        }
        ort.env.wasm.wasmPaths =
            'https://cdn.jsdelivr.net/npm/onnxruntime-web@1.17.1/dist/';
        this._session = await ort.InferenceSession.create(VAD_MODEL_URL, {
            executionProviders: ['wasm'],
        });
        this._resetState();
        console.log('[VAD] Silero model loaded ✅');
    }

    _resetState() {
        this._h = new ort.Tensor('float32', new Float32Array(2 * 64), [2, 1, 64]);
        this._c = new ort.Tensor('float32', new Float32Array(2 * 64), [2, 1, 64]);
    }

    push(samples24k) {
        if (!this._session) return;
        for (const s of samples24k) this._pending.push(s);
        while (this._pending.length >= VAD_FRAME_24K) {
            const frame24 = new Float32Array(this._pending.splice(0, VAD_FRAME_24K));
            const frame16 = this._resample(frame24, VAD_FRAME_16K);
            this._infer(frame16); // async, non-blocking
        }
    }

    _resample(input, outLen) {
        const out   = new Float32Array(outLen);
        const ratio = (input.length - 1) / (outLen - 1);
        for (let i = 0; i < outLen; i++) {
            const pos  = i * ratio;
            const lo   = Math.floor(pos);
            const hi   = Math.min(lo + 1, input.length - 1);
            out[i] = input[lo] + (input[hi] - input[lo]) * (pos - lo);
        }
        return out;
    }

    async _infer(frame16k) {
        try {
            const feeds = {
                input: new ort.Tensor('float32', frame16k, [1, frame16k.length]),
                sr:    new ort.Tensor('int64', BigInt64Array.from([16000n]), [1]),
                h:     this._h,
                c:     this._c,
            };
            const out     = await this._session.run(feeds);
            this._h       = out.hn;
            this._c       = out.cn;
            const prob    = out.output.data[0];

            if (prob >= VAD_THRESHOLD) {
                this._silenceFrames = 0;
                if (!this._speaking) {
                    this._speaking = true;
                    this.onSpeechStart?.();
                }
            } else if (this._speaking) {
                this._silenceFrames++;
                if (this._silenceFrames >= this.SILENCE_LIMIT) {
                    this._speaking       = false;
                    this._silenceFrames  = 0;
                    this.onSpeechEnd?.();
                }
            }
        } catch (e) {
            console.warn('[VAD] Inference error:', e);
        }
    }
}

// ══════════════════════════════════════════════════════════════
// SYSTEM 1 — AUDIO CAPTURE
// ══════════════════════════════════════════════════════════════
const CaptureSystem = {
    audioCtx:   null,
    worklet:    null,
    ringBuffer: new RingBuffer(CAPTURE_RATE * 5), // 5s
    vad:        null,

    async start() {
        if (this.audioCtx) return; // guard: only init once

        this.audioCtx = new AudioContext({ sampleRate: CAPTURE_RATE });

        // Boot VAD in parallel — failures are non-fatal
        this.vad = new SileroVAD({
            onSpeechStart: () => {
                // Instant local feedback — no round-trip
                if (window.orbState !== 'SPEAKING') window.setOrbState('LISTENING');
                document.getElementById('sub-status').innerText = '🎤 Speech detected';
            },
            onSpeechEnd: () => {
                document.getElementById('sub-status').innerText = 'Processing…';
            },
        });
        this.vad.load(); // intentionally not awaited

        // Mic
        let stream;
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: CAPTURE_RATE, channelCount: 1,
                    echoCancellation: true, noiseSuppression: true, autoGainControl: true,
                },
            });
        } catch (e) {
            document.getElementById('status-text').innerText = 'SYSTEM ERROR: MIC DENIED';
            throw e;
        }

        // AudioWorklet
        await this.audioCtx.audioWorklet.addModule('audio-worklet.js');
        const source = this.audioCtx.createMediaStreamSource(stream);
        this.worklet = new AudioWorkletNode(this.audioCtx, 'audio-capture-processor');
        this.worklet.port.onmessage = ({ data }) => {
            this.ringBuffer.write(data); // → WebSocket drain
            this.vad?.push(data);        // → Silero VAD
        };
        source.connect(this.worklet);
        // worklet NOT connected to destination (avoid loopback)

        console.log('[Capture] AudioWorklet running ✅');
    },
};

// ══════════════════════════════════════════════════════════════
// SYSTEM 2 — WEBSOCKET TRANSPORT
// ══════════════════════════════════════════════════════════════
const TransportSystem = {
    ws:          null,
    meetingId:   null,
    _backoff:    1000,
    _drainTimer: null,
    _heartTimer: null,

    async connect() {
        // Create meeting once
        if (!this.meetingId) {
            try {
                const res     = await fetch(`${BACKEND_HTTP}/api/meetings/new`, { method: 'POST' });
                const { meeting_id } = await res.json();
                this.meetingId = meeting_id;
            } catch {
                this.meetingId = `local-${Date.now()}`;
            }
        }

        const url = `${BACKEND_WS}/ws/meeting/${this.meetingId}`;
        this.ws = new WebSocket(url);
        this.ws.onopen    = () => this._onOpen();
        this.ws.onclose   = () => this._onClose();
        this.ws.onerror   = e  => console.error('[WS]', e);
        this.ws.onmessage = e  => this._handle(JSON.parse(e.data));
    },

    _onOpen() {
        this._backoff = 1000;
        this._drainTimer = setInterval(() => this._drain(), WS_DRAIN_MS);
        this._heartTimer = setInterval(() => this._ping(),  HEARTBEAT_MS);
        document.getElementById('sub-status').innerText = 'CONNECTION ESTABLISHED';
        console.log('[Transport] WS open ✅ meeting:', this.meetingId);
    },

    _onClose() {
        clearInterval(this._drainTimer);
        clearInterval(this._heartTimer);
        document.getElementById('sub-status').innerText =
            `RECONNECTING in ${(this._backoff / 1000).toFixed(1)}s…`;
        setTimeout(() => this.connect(), this._backoff);
        this._backoff = Math.min(this._backoff * 2, MAX_BACKOFF);
    },

    _drain() {
        if (this.ws?.readyState !== WebSocket.OPEN) return;
        const ring = CaptureSystem.ringBuffer;
        while (ring.available >= WS_CHUNK) {
            const frame = ring.read(WS_CHUNK);
            this.ws.send(JSON.stringify({ type: 'input_audio', audio: floatToPCM16Base64(frame) }));
        }
    },

    _ping() {
        if (this.ws?.readyState === WebSocket.OPEN)
            this.ws.send(JSON.stringify({ type: 'ping' }));
    },

    send(payload) {
        if (this.ws?.readyState === WebSocket.OPEN)
            this.ws.send(JSON.stringify(payload));
    },

    _handle(msg) {
        switch (msg.type) {
            case 'connected':
                window.setOrbState('LISTENING');
                document.getElementById('sub-status').innerText = 'SESSION ACTIVE';
                break;
            case 'audio':
                PlaybackSystem.push(msg.audio);
                break;
            case 'interrupt':
                PlaybackSystem.interrupt();
                break;
            case 'transcript':
                if (!msg.is_delta) appendTranscript(msg.speaker, msg.text);
                break;
            case 'insight':
                appendInsight(msg.category, msg.insight, msg.confidence);
                break;
            case 'pong': break;
            case 'error':
                console.error('[Backend]', msg.message);
                break;
        }
    },
};

// ══════════════════════════════════════════════════════════════
// SYSTEM 3 — AUDIO PLAYBACK  (sample-accurate scheduling)
// ══════════════════════════════════════════════════════════════
const PlaybackSystem = {
    _queue:      [],
    _nextTime:   0,
    _active:     false,
    playbackRMS: 0,

    push(b64Audio) {
        const ctx = CaptureSystem.audioCtx;
        if (!ctx) return;

        const f32 = base64ToFloat32(b64Audio);
        const buf = ctx.createBuffer(1, f32.length, CAPTURE_RATE);
        buf.getChannelData(0).set(f32);

        let sq = 0;
        for (const s of f32) sq += s * s;
        this.playbackRMS = Math.sqrt(sq / f32.length);

        this._queue.push(buf);
        if (!this._active) this._flush();
    },

    _flush() {
        const ctx = CaptureSystem.audioCtx;
        if (!ctx || this._queue.length === 0) {
            this._active = false;
            this.playbackRMS = 0;
            window.setOrbState('LISTENING');
            return;
        }

        this._active = true;
        window.setOrbState('SPEAKING');

        const now = ctx.currentTime;
        if (this._nextTime < now + 0.01) this._nextTime = now + 0.01;

        // Schedule all queued buffers consecutively — gapless
        while (this._queue.length > 0) {
            const buf = this._queue.shift();
            const src = ctx.createBufferSource();
            src.buffer = buf;
            src.connect(ctx.destination);
            src.start(this._nextTime);
            this._nextTime += buf.duration;
        }

        // Wait until last scheduled buffer ends then recycle
        const waitMs = Math.max((this._nextTime - ctx.currentTime) * 1000 + 20, 20);
        setTimeout(() => this._flush(), waitMs);
    },

    interrupt() {
        this._queue      = [];
        this._nextTime   = 0;
        this._active     = false;
        this.playbackRMS = 0;
        window.setOrbState('LISTENING');
    },
};

// ── Orb energy hook (read by sketch.js) ─────────────────────
window.getAudioEnergy = () => ({
    bass:   PlaybackSystem.playbackRMS * 1500,
    mid:    PlaybackSystem.playbackRMS * 800,
    treble: PlaybackSystem.playbackRMS * 500,
});

// ══════════════════════════════════════════════════════════════
// ENTRY POINT  (called from sketch.js on orb click)
// ══════════════════════════════════════════════════════════════
window.initBackendPipeline = async function () {
    document.getElementById('status-text').innerText  = 'SYSTEM INITIATING…';
    document.getElementById('sub-status').innerText   = 'Starting audio capture…';
    try {
        await CaptureSystem.start();
        document.getElementById('sub-status').innerText = 'Connecting to backend…';
        await TransportSystem.connect();
    } catch (e) {
        console.error('[Pipeline] Init failed:', e);
        document.getElementById('status-text').innerText = 'SYSTEM ERROR';
    }
};

// ══════════════════════════════════════════════════════════════
// UI  helpers
// ══════════════════════════════════════════════════════════════
function appendTranscript(speaker, text) {
    if (!text?.trim()) return;
    const container = document.getElementById('transcript-content');
    const div = document.createElement('div');
    div.className = 'transcript-line';
    const isAI = ['ai', 'assistant'].includes(speaker?.toLowerCase());
    div.innerHTML =
        `<span class="transcript-speaker ${isAI ? 'transcript-ai' : ''}">${speaker}:</span> ` +
        `<span style="opacity:0.9">${text}</span>`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
}

function appendInsight(category, content, confidence) {
    // Map AI category → DOM panel id
    const typeMap = {
        decision: 'decisions', risk: 'risks', risk_flag: 'risks',
        topic: 'topics', key_fact: 'topics', action_item: 'topics',
    };
    const panelKey = typeMap[category] ?? 'topics';
    const el = document.getElementById(`${panelKey}-content`);
    if (!el) return;

    const div = document.createElement('div');
    div.className = `card ${panelKey.slice(0, -1)}`; // "decisions" → "decision"
    div.innerHTML =
        `<div class="card-title">${(category ?? '').toUpperCase()} · ${confidence ?? '?'}</div>` +
        `<div class="card-content">${content ?? ''}</div>`;
    el.appendChild(div);
    el.scrollTop = el.scrollHeight;
}

// ── DOM listeners ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('agent-mode-select')?.addEventListener('change', e =>
        window.setAgentMode(e.target.value)
    );

    document.getElementById('recall-btn')?.addEventListener('click', e => {
        e.stopPropagation();
        TransportSystem.send({
            type: 'inject_context',
            query: 'What were previous decisions or risks?',
        });
        window.setOrbState('MEMORY_RECALL');
    });
});
