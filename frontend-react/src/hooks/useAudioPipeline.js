/**
 * useAudioPipeline.js — CLEAN REBUILD
 * 
 * Full-duplex audio pipeline for Cassandra AI.
 * 
 * Architecture:
 *   Mic → AudioWorklet → Rolling Buffer → WebSocket → Backend → OpenAI
 *   Speaker ← Audio Queue (gapless) ← WebSocket ← Backend ← OpenAI
 * 
 * Key design decisions:
 *   1. Single initialization lock (prevents StrictMode / double-click issues)
 *   2. Stable callback refs (no useCallback dependency cascades)
 *   3. Decoupled lifecycles (WebSocket reconnects don't restart mic)
 *   4. Gapless playback scheduling (no micro-gaps between chunks)
 *   5. Proper interrupt/barge-in (stops current source + flushes queue)
 *   6. Echo prevention (mutes mic during AI playback)
 *   7. Rolling buffer that never drops samples
 */
import { useRef, useCallback, useEffect } from 'react';

// ─── Constants ─────────────────────────────────────────────
const SAMPLE_RATE = 24000;
const SEND_INTERVAL_MS = 100;        // Drain buffer every 100ms
const HEARTBEAT_INTERVAL_MS = 15000; // Keep WebSocket alive
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const MAX_RECONNECT_ATTEMPTS = 10;

const getWsUrl = () => {
  if (import.meta.env.VITE_WS_URL) return import.meta.env.VITE_WS_URL;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}`;
};

// ─── Audio Helpers ─────────────────────────────────────────

/** Float32 → PCM16 base64 (OpenAI Realtime format) */
function float32ToPcm16Base64(float32) {
  const pcm16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
  }
  const bytes = new Uint8Array(pcm16.buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

/** Base64 PCM16 → Float32 AudioBuffer */
function base64ToAudioBuffer(base64, audioContext) {
  const binaryStr = atob(base64);
  const bytes = new Uint8Array(binaryStr.length);
  for (let i = 0; i < binaryStr.length; i++) {
    bytes[i] = binaryStr.charCodeAt(i);
  }
  const pcm16 = new Int16Array(bytes.buffer);
  const audioBuffer = audioContext.createBuffer(1, pcm16.length, SAMPLE_RATE);
  const channelData = audioBuffer.getChannelData(0);
  for (let i = 0; i < pcm16.length; i++) {
    channelData[i] = pcm16[i] / 32768.0;
  }
  return audioBuffer;
}

/** Calculate RMS energy from AudioBuffer */
function calculateRMS(audioBuffer) {
  const data = audioBuffer.getChannelData(0);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.sqrt(sum / data.length);
}

// ─── The Hook ──────────────────────────────────────────────

export function useAudioPipeline({
  onStateChange,
  onTranscript,
  onInsight,
  onConnected,
  onDisconnected,
  onRoleUpdate,
  selectedDeviceId,
}) {
  // ── Stable callback refs ──
  // These refs break the dependency cascade that was killing the pipeline.
  // The cleanup useEffect depends on stopPipeline, which depended on
  // updateState, which depended on onStateChange (an inline arrow).
  // Every render created a new onStateChange → new updateState →
  // new stopPipeline → useEffect cleanup fired → pipeline destroyed.
  // 
  // With refs, the callbacks are always current but never trigger re-renders.
  const onStateChangeRef = useRef(onStateChange);
  const onTranscriptRef = useRef(onTranscript);
  const onInsightRef = useRef(onInsight);
  const onConnectedRef = useRef(onConnected);
  const onDisconnectedRef = useRef(onDisconnected);
  const onRoleUpdateRef = useRef(onRoleUpdate);
  const selectedDeviceIdRef = useRef(selectedDeviceId);

  // Keep refs in sync with latest props (no re-renders triggered)
  useEffect(() => { onStateChangeRef.current = onStateChange; }, [onStateChange]);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onInsightRef.current = onInsight; }, [onInsight]);
  useEffect(() => { onConnectedRef.current = onConnected; }, [onConnected]);
  useEffect(() => { onDisconnectedRef.current = onDisconnected; }, [onDisconnected]);
  useEffect(() => { onRoleUpdateRef.current = onRoleUpdate; }, [onRoleUpdate]);
  useEffect(() => { selectedDeviceIdRef.current = selectedDeviceId; }, [selectedDeviceId]);

  // ── Pipeline state refs ──
  const audioContextRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const workletNodeRef = useRef(null);
  const analyserRef = useRef(null);
  const wsRef = useRef(null);

  // ── Lifecycle locks ──
  const isPipelineActiveRef = useRef(false);
  const isStartingRef = useRef(false);
  const isIntentionalCloseRef = useRef(false);

  // ── Audio send buffer ──
  const sendBufferRef = useRef([]);      // Array of base64 chunks ready to send
  const sendIntervalRef = useRef(null);

  // ── Playback state ──
  const playbackQueueRef = useRef([]);
  const scheduledSourcesRef = useRef([]);
  const nextPlayTimeRef = useRef(0);
  const isPlayingRef = useRef(false);
  const currentSourceRef = useRef(null);

  // ── Reconnection ──
  const reconnectDelayRef = useRef(RECONNECT_BASE_MS);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatRef = useRef(null);
  const meetingIdRef = useRef(null);

  // ── Audio energy (exposed for orb) ──
  const micRMSRef = useRef(0);
  const playbackRMSRef = useRef(0);
  const frequencyDataRef = useRef({ bass: 0, mid: 0, treble: 0 });
  const isUserSpeakingRef = useRef(false);
  const isAISpeakingRef = useRef(false);

  // ── Expose audio energy globally for orb ──
  useEffect(() => {
    window.getAudioEnergy = () => ({
      bass: frequencyDataRef.current.bass,
      mid: frequencyDataRef.current.mid,
      treble: frequencyDataRef.current.treble,
      rms: Math.max(playbackRMSRef.current, micRMSRef.current * 0.5),
      isUserSpeaking: isUserSpeakingRef.current,
      isAISpeaking: isAISpeakingRef.current,
    });
    return () => { delete window.getAudioEnergy; };
  }, []);

  // ═══════════════════════════════════════════════════════════
  // STATE MANAGEMENT
  // ═══════════════════════════════════════════════════════════

  const updateState = useCallback((state) => {
    window.orbState = state;
    isAISpeakingRef.current = state === 'speaking';
    isUserSpeakingRef.current = state === 'listening' && micRMSRef.current > 0.01;
    onStateChangeRef.current?.(state);
  }, []); // No deps — uses refs only

  // ═══════════════════════════════════════════════════════════
  // AUDIO CAPTURE (System 1)
  // ═══════════════════════════════════════════════════════════

  const startCapture = useCallback(async () => {
    // 1. Check prerequisites
    if (!window.isSecureContext) {
      console.warn('[Capture] Not a secure context — AudioWorklet may fail.');
    }

    // 2. Create AudioContext
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error('AudioContext not supported');

    const ctx = new AudioContextClass({ sampleRate: SAMPLE_RATE });
    audioContextRef.current = ctx;

    // 3. Get mic permission
    const constraints = {
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        ...(selectedDeviceIdRef.current && {
          deviceId: { exact: selectedDeviceIdRef.current },
        }),
      },
    };

    const stream = await navigator.mediaDevices.getUserMedia(constraints);

    // Guard: pipeline destroyed while waiting for mic permission
    if (!audioContextRef.current) {
      console.warn('[Capture] AudioContext destroyed during mic permission.');
      stream.getTracks().forEach((t) => t.stop());
      return false;
    }

    mediaStreamRef.current = stream;
    console.log('[Capture] Mic access granted.');

    // 4. Resume suspended context
    if (audioContextRef.current?.state === 'suspended') {
      await audioContextRef.current.resume().catch(() => {});
    }
    if (!audioContextRef.current) return false;

    // 5. Create analyser for frequency data
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 256;
    analyser.smoothingTimeConstant = 0.8;
    analyserRef.current = analyser;

    // 6. Start frequency analysis loop
    const freqData = new Uint8Array(analyser.frequencyBinCount);
    const analyseLoop = () => {
      if (!analyserRef.current) return;
      analyserRef.current.getByteFrequencyData(freqData);

      const nyquist = SAMPLE_RATE / 2;
      const binSize = nyquist / freqData.length;
      let bassSum = 0, bassN = 0, midSum = 0, midN = 0, trebleSum = 0, trebleN = 0;

      for (let i = 0; i < freqData.length; i++) {
        const freq = i * binSize;
        const val = freqData[i];
        if (freq < 250) { bassSum += val; bassN++; }
        else if (freq < 2000) { midSum += val; midN++; }
        else { trebleSum += val; trebleN++; }
      }

      frequencyDataRef.current = {
        bass: bassN > 0 ? (bassSum / bassN) * 6 : 0,
        mid: midN > 0 ? (midSum / midN) * 3 : 0,
        treble: trebleN > 0 ? (trebleSum / trebleN) * 2 : 0,
      };

      let rmsSum = 0;
      for (const v of freqData) { const n = v / 255; rmsSum += n * n; }
      micRMSRef.current = Math.sqrt(rmsSum / freqData.length);
      isUserSpeakingRef.current = micRMSRef.current > 0.05 && !isAISpeakingRef.current;

      requestAnimationFrame(analyseLoop);
    };
    analyseLoop();

    // 7. Load AudioWorklet and connect
    const source = ctx.createMediaStreamSource(stream);

    try {
      await audioContextRef.current?.audioWorklet?.addModule('/processors/recorder.js');
      if (!audioContextRef.current) return false;

      const workletNode = new AudioWorkletNode(ctx, 'recorder-processor');
      workletNodeRef.current = workletNode;

      // Worklet → rolling buffer (no samples dropped)
      workletNode.port.onmessage = (event) => {
        const floatData = event.data;
        sendBufferRef.current.push(float32ToPcm16Base64(floatData));
      };

      source.connect(workletNode);
      source.connect(analyser);
      console.log('[Capture] AudioWorklet connected.');
    } catch (err) {
      console.warn('[Capture] AudioWorklet failed, using ScriptProcessor:', err.message);

      // Fallback: ScriptProcessorNode
      const scriptNode = ctx.createScriptProcessor(2048, 1, 1);
      scriptNode.onaudioprocess = (event) => {
        const inputData = event.inputBuffer.getChannelData(0);
        sendBufferRef.current.push(float32ToPcm16Base64(inputData));
      };
      source.connect(scriptNode);
      scriptNode.connect(ctx.destination);
      source.connect(analyser);
      console.log('[Capture] ScriptProcessor fallback connected.');
    }

    return true;
  }, []); // No deps — uses refs only

  // ═══════════════════════════════════════════════════════════
  // AUDIO PLAYBACK (System 3)
  // ═══════════════════════════════════════════════════════════

  /** Schedule a buffer for gapless playback */
  const schedulePlayback = useCallback((audioBuffer) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    playbackRMSRef.current = calculateRMS(audioBuffer);

    const now = ctx.currentTime;
    if (nextPlayTimeRef.current < now) {
      nextPlayTimeRef.current = now;
    }

    source.start(nextPlayTimeRef.current);
    nextPlayTimeRef.current += audioBuffer.duration;

    scheduledSourcesRef.current.push(source);
    currentSourceRef.current = source;

    source.onended = () => {
      scheduledSourcesRef.current = scheduledSourcesRef.current.filter((s) => s !== source);
      if (source === currentSourceRef.current) currentSourceRef.current = null;

      if (scheduledSourcesRef.current.length === 0 && playbackQueueRef.current.length === 0) {
        isPlayingRef.current = false;
        playbackRMSRef.current = 0;
        isAISpeakingRef.current = false;
        updateState('listening');

        // Unmute mic when AI stops speaking
        workletNodeRef.current?.port.postMessage({ type: 'unmute' });
      }
    };
  }, [updateState]);

  /** Enqueue received AI audio for playback */
  const enqueueAudio = useCallback((base64Data) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;

    try {
      const audioBuffer = base64ToAudioBuffer(base64Data, ctx);
      playbackQueueRef.current.push(audioBuffer);

      if (!isPlayingRef.current) {
        isPlayingRef.current = true;
        updateState('speaking');

        // Mute mic to prevent echo feedback
        workletNodeRef.current?.port.postMessage({ type: 'mute' });
      }

      // Schedule all queued buffers
      while (playbackQueueRef.current.length > 0) {
        schedulePlayback(playbackQueueRef.current.shift());
      }
    } catch (e) {
      console.error('[Playback] Error:', e);
    }
  }, [schedulePlayback, updateState]);

  /** Interrupt: stop all playback immediately (barge-in) */
  const interruptPlayback = useCallback(() => {
    // Stop all scheduled sources
    for (const source of scheduledSourcesRef.current) {
      try { source.stop(0); } catch (_) {}
    }
    scheduledSourcesRef.current = [];
    currentSourceRef.current = null;

    // Flush queue
    playbackQueueRef.current = [];
    nextPlayTimeRef.current = 0;
    isPlayingRef.current = false;
    playbackRMSRef.current = 0;
    isAISpeakingRef.current = false;

    // Unmute mic
    workletNodeRef.current?.port.postMessage({ type: 'unmute' });

    updateState('listening');
  }, [updateState]);

  // ═══════════════════════════════════════════════════════════
  // WEBSOCKET TRANSPORT (System 2)
  // ═══════════════════════════════════════════════════════════

  const startHeartbeat = useCallback(() => {
    if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    heartbeatRef.current = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: 'ping' }));
      }
    }, HEARTBEAT_INTERVAL_MS);
  }, []);

  const stopHeartbeat = useCallback(() => {
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current);
      heartbeatRef.current = null;
    }
  }, []);

  /** Start draining send buffer over WebSocket */
  const startSendLoop = useCallback(() => {
    if (sendIntervalRef.current) clearInterval(sendIntervalRef.current);
    sendIntervalRef.current = setInterval(() => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      if (sendBufferRef.current.length === 0) return;

      // Drain all buffered chunks
      const chunks = sendBufferRef.current.splice(0);
      for (const chunk of chunks) {
        wsRef.current.send(JSON.stringify({
          type: 'input_audio',
          audio: chunk,
        }));
      }
    }, SEND_INTERVAL_MS);
  }, []);

  const stopSendLoop = useCallback(() => {
    if (sendIntervalRef.current) {
      clearInterval(sendIntervalRef.current);
      sendIntervalRef.current = null;
    }
  }, []);

  /** Handle incoming WebSocket messages */
  const handleMessage = useCallback((event) => {
    let msg;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return;
    }

    switch (msg.type) {
      case 'connected':
        updateState('listening');
        break;

      case 'audio':
        enqueueAudio(msg.audio);
        break;

      case 'transcript':
        onTranscriptRef.current?.(msg);
        break;

      case 'insight':
        onInsightRef.current?.(msg);
        break;

      case 'interrupt':
        interruptPlayback();
        break;

      case 'role_switched':
        onRoleUpdateRef.current?.(msg.role, msg);
        break;

      case 'pong':
        break;

      case 'error':
        console.error('[Transport] Server error:', msg.message);
        break;

      case 'meeting_ended':
        onDisconnectedRef.current?.();
        break;

      default:
        break;
    }
  }, [updateState, enqueueAudio, interruptPlayback]);

  /** Connect WebSocket with reconnection logic */
  const connectWebSocket = useCallback((id) => {
    const wsUrl = getWsUrl();
    const url = `${wsUrl}/ws/meeting/${id}`;

    // Clean up existing connection
    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    console.log(`[Transport] Connecting to ${url}...`);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('[Transport] Connected.');
      reconnectDelayRef.current = RECONNECT_BASE_MS;
      reconnectAttemptsRef.current = 0;
      onConnectedRef.current?.();
      startHeartbeat();
      startSendLoop();
    };

    ws.onmessage = handleMessage;

    ws.onerror = (err) => {
      console.error('[Transport] WebSocket error:', err);
    };

    ws.onclose = (event) => {
      console.log(`[Transport] Disconnected (code: ${event.code}).`);
      onDisconnectedRef.current?.();
      stopHeartbeat();
      stopSendLoop();

      // Don't reconnect if intentionally closed
      if (isIntentionalCloseRef.current) return;

      // Reconnect with exponential backoff
      if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        console.error('[Transport] Max reconnect attempts. Giving up.');
        updateState('error');
        return;
      }

      const delay = reconnectDelayRef.current;
      console.log(`[Transport] Reconnecting in ${delay}ms...`);
      reconnectTimeoutRef.current = setTimeout(() => {
        reconnectAttemptsRef.current++;
        reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
        connectWebSocket(meetingIdRef.current);
      }, delay);
    };
  }, [handleMessage, startHeartbeat, startSendLoop, stopHeartbeat, stopSendLoop, updateState]);

  // ═══════════════════════════════════════════════════════════
  // PUBLIC API
  // ═══════════════════════════════════════════════════════════

  /** Start the full pipeline — call from a user gesture (button click) */
  const startPipeline = useCallback(async (id) => {
    // Triple lock: already active, already starting, or context exists
    if (isPipelineActiveRef.current) {
      console.log('[Pipeline] Already active, skipping.');
      return;
    }
    if (isStartingRef.current) {
      console.log('[Pipeline] Already starting, skipping.');
      return;
    }
    if (audioContextRef.current) {
      console.log('[Pipeline] AudioContext exists, skipping.');
      return;
    }

    isStartingRef.current = true;
    isIntentionalCloseRef.current = false;
    meetingIdRef.current = id;
    updateState('connecting');

    try {
      // Step 1: Start audio capture
      const captureOk = await startCapture();
      if (!captureOk) {
        throw new Error('Audio capture failed');
      }

      // Step 2: Connect WebSocket
      connectWebSocket(id);

      isPipelineActiveRef.current = true;
      console.log('[Pipeline] Started successfully.');

    } catch (err) {
      console.error('[Pipeline] Failed:', err.message);
      updateState('error');
      // Clean up partial initialization
      stopPipelineInternal();
    } finally {
      isStartingRef.current = false;
    }
  }, [startCapture, connectWebSocket, updateState]);

  /** Internal cleanup — shared by stopPipeline and error recovery */
  const stopPipelineInternal = useCallback(() => {
    isIntentionalCloseRef.current = true;

    // Clear reconnect timer
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    // Stop send loop and heartbeat
    stopSendLoop();
    stopHeartbeat();

    // Close WebSocket
    if (wsRef.current) {
      wsRef.current.onclose = null; // Prevent reconnect trigger
      wsRef.current.close(1000, 'Meeting ended');
      wsRef.current = null;
    }

    // Stop playback
    interruptPlayback();

    // Stop mic tracks
    mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
    mediaStreamRef.current = null;

    // Disconnect worklet
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;

    // Clear analyser
    analyserRef.current = null;

    // Close AudioContext
    audioContextRef.current?.close();
    audioContextRef.current = null;

    // Clear buffers
    sendBufferRef.current = [];
    playbackQueueRef.current = [];

    // Reset state
    isPipelineActiveRef.current = false;
    isStartingRef.current = false;
    reconnectAttemptsRef.current = 0;
    reconnectDelayRef.current = RECONNECT_BASE_MS;
    meetingIdRef.current = null;
  }, [stopSendLoop, stopHeartbeat, interruptPlayback]);

  /** Public stop — also resets UI state */
  const stopPipeline = useCallback(() => {
    stopPipelineInternal();
    updateState('idle');
    console.log('[Pipeline] Stopped.');
  }, [stopPipelineInternal, updateState]);

  /** Switch agent role via WebSocket */
  const switchRole = useCallback((role) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'switch_role', role }));
    }
  }, []);

  // ── Cleanup on unmount ──
  // IMPORTANT: This useEffect has ZERO dependencies.
  // It captures stopPipelineInternal via closure at mount time.
  // This prevents the re-render → cleanup → destroy pipeline cascade.
  useEffect(() => {
    return () => {
      // Use the refs directly — they're always current
      isIntentionalCloseRef.current = true;

      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (sendIntervalRef.current) clearInterval(sendIntervalRef.current);
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);

      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }

      mediaStreamRef.current?.getTracks().forEach((t) => t.stop());
      workletNodeRef.current?.disconnect();
      audioContextRef.current?.close();

      isPipelineActiveRef.current = false;
    };
  }, []); // Empty deps — runs only on true unmount

  return {
    startPipeline,
    stopPipeline,
    switchRole,
    isConnecting: isStartingRef.current,
  };
}

export default useAudioPipeline;
