/**
 * frontend-react/src/hooks/useV2AudioPipeline.js
 *
 * V2 Protocol Audio Pipeline for Cassandra Voice Server.
 *
 * Supports TWO protocol modes:
 * 1. V1 (Legacy): session_start with no API key → OpenAI Realtime relay (backwards compat)
 * 2. V2 (Smart): session_start with API key → smart backend pipeline (VAD→STT→LLM→TTS)
 *
 * Key differences from V1 (useAudioPipeline.js):
 * - First message is session_start (not input_audio)
 * - Auth credentials (API key or JWT) sent at session start
 * - New message types: state_change, rate_limit_exceeded, memory_match
 * - Interrupt is client-initiated OR server-initiated
 * - Audio flows: TTS chunks via WebSocket (not from OpenAI Realtime)
 */

import { useRef, useCallback, useEffect } from 'react';

const SAMPLE_RATE = 24000;
const SEND_INTERVAL_MS = 100;
const HEARTBEAT_INTERVAL_MS = 15000;
const RECONNECT_BASE_MS = 1000;
const RECONNECT_MAX_MS = 30000;
const MAX_RECONNECT_ATTEMPTS = 10;

// ─── Audio Helpers ─────────────────────────────────────────

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

function calculateRMS(audioBuffer) {
  const data = audioBuffer.getChannelData(0);
  let sum = 0;
  for (let i = 0; i < data.length; i++) sum += data[i] * data[i];
  return Math.sqrt(sum / data.length);
}

// ─── The Hook ──────────────────────────────────────────────

/**
 * V2 Audio Pipeline Hook.
 *
 * @param {Object} options
 * @param {string} options.apiKey - API key for V2 smart backend authentication
 * @param {string} options.token - Alternative: Supabase JWT token
 * @param {string} options.sessionId - Optional session ID (server generates if omitted)
 * @param {string} options.meetingId - Associated meeting ID
 * @param {string} options.role - Initial agent role
 * @param {Function} options.onStateChange - (state) => void
 * @param {Function} options.onTranscript - (msg) => void
 * @param {Function} options.onInsight - (msg) => void
 * @param {Function} options.onConnected - () => void
 * @param {Function} options.onDisconnected - () => void
 * @param {Function} options.onRoleUpdate - (role, msg) => void
 * @param {Function} options.onMemoryMatch - (msg) => void
 * @param {Function} options.onRateLimitExceeded - (msg) => void
 * @param {string} options.selectedDeviceId - Microphone device ID
 */
export function useV2AudioPipeline({
  apiKey,
  token,
  sessionId,
  meetingId,
  role = 'GENERAL',
  onStateChange,
  onTranscript,
  onInsight,
  onConnected,
  onDisconnected,
  onRoleUpdate,
  onMemoryMatch,
  onRateLimitExceeded,
  selectedDeviceId,
}) {
  // ── Stable callback refs ──
  const onStateChangeRef = useRef(onStateChange);
  const onTranscriptRef = useRef(onTranscript);
  const onInsightRef = useRef(onInsight);
  const onConnectedRef = useRef(onConnected);
  const onDisconnectedRef = useRef(onDisconnected);
  const onRoleUpdateRef = useRef(onRoleUpdate);
  const onMemoryMatchRef = useRef(onMemoryMatch);
  const onRateLimitExceededRef = useRef(onRateLimitExceeded);
  const selectedDeviceIdRef = useRef(selectedDeviceId);

  useEffect(() => { onStateChangeRef.current = onStateChange; }, [onStateChange]);
  useEffect(() => { onTranscriptRef.current = onTranscript; }, [onTranscript]);
  useEffect(() => { onInsightRef.current = onInsight; }, [onInsight]);
  useEffect(() => { onConnectedRef.current = onConnected; }, [onConnected]);
  useEffect(() => { onDisconnectedRef.current = onDisconnected; }, [onDisconnected]);
  useEffect(() => { onRoleUpdateRef.current = onRoleUpdate; }, [onRoleUpdate]);
  useEffect(() => { onMemoryMatchRef.current = onMemoryMatch; }, [onMemoryMatch]);
  useEffect(() => { onRateLimitExceededRef.current = onRateLimitExceeded; }, [onRateLimitExceeded]);
  useEffect(() => { selectedDeviceIdRef.current = selectedDeviceId; }, [selectedDeviceId]);

  // ── Pipeline state refs ──
  const audioContextRef = useRef(null);
  const mediaStreamRef = useRef(null);
  const workletNodeRef = useRef(null);
  const analyserRef = useRef(null);
  const wsRef = useRef(null);

  const isPipelineActiveRef = useRef(false);
  const isStartingRef = useRef(false);
  const isIntentionalCloseRef = useRef(false);
  const protocolVersionRef = useRef('v2');

  const sendBufferRef = useRef([]);
  const sendIntervalRef = useRef(null);

  const playbackQueueRef = useRef([]);
  const scheduledSourcesRef = useRef([]);
  const nextPlayTimeRef = useRef(0);
  const isPlayingRef = useRef(false);
  const currentSourceRef = useRef(null);

  const reconnectDelayRef = useRef(RECONNECT_BASE_MS);
  const reconnectAttemptsRef = useRef(0);
  const reconnectTimeoutRef = useRef(null);
  const heartbeatRef = useRef(null);
  const activeSessionIdRef = useRef(sessionId);

  const isAISpeakingRef = useRef(false);
  const isUserSpeakingRef = useRef(false);

  // ── Audio energy (for orb) ──
  useEffect(() => {
    window.getAudioEnergy = () => ({
      rms: 0,
      isUserSpeaking: isUserSpeakingRef.current,
      isAISpeaking: isAISpeakingRef.current,
    });
    return () => { delete window.getAudioEnergy; };
  }, []);

  // ── State Management ─────────────────────────────────────

  const updateState = useCallback((state) => {
    window.orbState = state;
    isAISpeakingRef.current = state === 'speaking';
    onStateChangeRef.current?.(state);
  }, []);

  // ── Audio Capture ───────────────────────────────────────

  const startCapture = useCallback(async () => {
    if (!window.isSecureContext) {
      console.warn('[V2 Capture] Not a secure context');
    }

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error('AudioContext not supported');

    const ctx = new AudioContextClass({ sampleRate: SAMPLE_RATE });
    audioContextRef.current = ctx;

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
    if (!audioContextRef.current) {
      stream.getTracks().forEach(t => t.stop());
      return false;
    }

    mediaStreamRef.current = stream;

    if (audioContextRef.current?.state === 'suspended') {
      await audioContextRef.current.resume().catch(() => {});
    }
    if (!audioContextRef.current) return false;

    const source = ctx.createMediaStreamSource(stream);

    try {
      await audioContextRef.current?.audioWorklet?.addModule('/processors/recorder.js');
      if (!audioContextRef.current) return false;

      const workletNode = new AudioWorkletNode(ctx, 'recorder-processor');
      workletNodeRef.current = workletNode;

      workletNode.port.onmessage = (event) => {
        const floatData = event.data;
        sendBufferRef.current.push(float32ToPcm16Base64(floatData));
      };

      source.connect(workletNode);
      console.log('[V2 Capture] AudioWorklet connected');
    } catch (err) {
      console.warn('[V2 Capture] AudioWorklet failed, using fallback:', err.message);

      const scriptNode = ctx.createScriptProcessor(2048, 1, 1);
      scriptNode.onaudioprocess = (event) => {
        const inputData = event.inputBuffer.getChannelData(0);
        sendBufferRef.current.push(float32ToPcm16Base64(inputData));
      };
      source.connect(scriptNode);
      scriptNode.connect(ctx.destination);
    }

    return true;
  }, []);

  // ── Audio Playback ─────────────────────────────────────

  const schedulePlayback = useCallback((audioBuffer) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;

    const source = ctx.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(ctx.destination);

    const now = ctx.currentTime;
    if (nextPlayTimeRef.current < now) {
      nextPlayTimeRef.current = now;
    }

    source.start(nextPlayTimeRef.current);
    nextPlayTimeRef.current += audioBuffer.duration;

    scheduledSourcesRef.current.push(source);
    currentSourceRef.current = source;

    source.onended = () => {
      scheduledSourcesRef.current = scheduledSourcesRef.current.filter(s => s !== source);
      if (source === currentSourceRef.current) currentSourceRef.current = null;

      if (scheduledSourcesRef.current.length === 0 && playbackQueueRef.current.length === 0) {
        isPlayingRef.current = false;
        isAISpeakingRef.current = false;
        updateState('listening');
        workletNodeRef.current?.port.postMessage({ type: 'unmute' });
      }
    };
  }, [updateState]);

  const enqueueAudio = useCallback((base64Data) => {
    const ctx = audioContextRef.current;
    if (!ctx) return;

    try {
      const audioBuffer = base64ToAudioBuffer(base64Data, ctx);
      playbackQueueRef.current.push(audioBuffer);

      if (!isPlayingRef.current) {
        isPlayingRef.current = true;
        updateState('speaking');
        workletNodeRef.current?.port.postMessage({ type: 'mute' });
      }

      while (playbackQueueRef.current.length > 0) {
        schedulePlayback(playbackQueueRef.current.shift());
      }
    } catch (e) {
      console.error('[V2 Playback] Error:', e);
    }
  }, [schedulePlayback, updateState]);

  const interruptPlayback = useCallback(() => {
    for (const source of scheduledSourcesRef.current) {
      try { source.stop(0); } catch (_) {}
    }
    scheduledSourcesRef.current = [];
    currentSourceRef.current = null;
    playbackQueueRef.current = [];
    nextPlayTimeRef.current = 0;
    isPlayingRef.current = false;
    isAISpeakingRef.current = false;
    workletNodeRef.current?.port.postMessage({ type: 'unmute' });
    updateState('listening');
  }, [updateState]);

  // ── WebSocket ───────────────────────────────────────────

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

  const startSendLoop = useCallback(() => {
    if (sendIntervalRef.current) clearInterval(sendIntervalRef.current);
    sendIntervalRef.current = setInterval(() => {
      if (wsRef.current?.readyState !== WebSocket.OPEN) return;
      if (sendBufferRef.current.length === 0) return;

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

  // ── V2 Message Handler ──

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
        onConnectedRef.current?.();
        break;

      case 'state_change':
        // V2: server sends explicit state changes
        updateState(msg.state);
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
        // Server-initiated interrupt (VAD detected user speech)
        interruptPlayback();
        break;

      case 'role_switched':
        onRoleUpdateRef.current?.(msg.role, msg);
        break;

      case 'memory_match':
        // V2: retrieved context from institutional memory
        onMemoryMatchRef.current?.(msg);
        break;

      case 'rate_limit_exceeded':
        // V2: monthly limit reached
        onRateLimitExceededRef.current?.(msg);
        updateState('idle');
        break;

      case 'pong':
        break;

      case 'error':
        console.error('[V2 Transport] Server error:', msg.message);
        break;

      case 'meeting_ended':
        onDisconnectedRef.current?.();
        break;

      default:
        break;
    }
  }, [updateState, enqueueAudio, interruptPlayback]);

  // ── V2 Connection ──

  const connectWebSocket = useCallback((id) => {
    const wsUrl = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}`;
    const url = `${wsUrl}/ws/session/${id}`;

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close();
    }

    console.log(`[V2 Transport] Connecting to ${url}...`);
    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = async () => {
      console.log('[V2 Transport] Connected. Sending session_start...');

      // Send V2 session_start message
      const sessionStart = {
        type: 'session_start',
        api_key: apiKey || undefined,
        token: token || undefined,
        session_id: id,
        meeting_id: meetingId,
        role: role,
        client_type: 'web',
      };

      ws.send(JSON.stringify(sessionStart));

      reconnectDelayRef.current = RECONNECT_BASE_MS;
      reconnectAttemptsRef.current = 0;
      startHeartbeat();
      startSendLoop();
    };

    ws.onmessage = handleMessage;

    ws.onerror = (err) => {
      console.error('[V2 Transport] WebSocket error:', err);
    };

    ws.onclose = (event) => {
      console.log(`[V2 Transport] Disconnected (code: ${event.code})`);
      onDisconnectedRef.current?.();
      stopHeartbeat();
      stopSendLoop();

      if (isIntentionalCloseRef.current) return;

      if (reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS) {
        console.error('[V2 Transport] Max reconnect attempts.');
        updateState('error');
        return;
      }

      const delay = reconnectDelayRef.current;
      console.log(`[V2 Transport] Reconnecting in ${delay}ms...`);
      reconnectTimeoutRef.current = setTimeout(() => {
        reconnectAttemptsRef.current++;
        reconnectDelayRef.current = Math.min(delay * 2, RECONNECT_MAX_MS);
        connectWebSocket(activeSessionIdRef.current);
      }, delay);
    };
  }, [apiKey, token, meetingId, role, handleMessage, startHeartbeat, stopHeartbeat, startSendLoop, stopSendLoop, updateState]);

  // ── Public API ───────────────────────────────────────────

  const startPipeline = useCallback(async (id) => {
    if (isPipelineActiveRef.current) return;
    if (isStartingRef.current) return;
    if (audioContextRef.current) return;

    isStartingRef.current = true;
    isIntentionalCloseRef.current = false;
    activeSessionIdRef.current = id;
    protocolVersionRef.current = 'v2';
    updateState('connecting');

    try {
      const captureOk = await startCapture();
      if (!captureOk) throw new Error('Audio capture failed');

      connectWebSocket(id);
      isPipelineActiveRef.current = true;
      console.log('[V2 Pipeline] Started successfully.');
    } catch (err) {
      console.error('[V2 Pipeline] Failed:', err.message);
      updateState('error');
      stopPipelineInternal();
    } finally {
      isStartingRef.current = false;
    }
  }, [startCapture, connectWebSocket, updateState]);

  const stopPipelineInternal = useCallback(() => {
    isIntentionalCloseRef.current = true;

    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    stopSendLoop();
    stopHeartbeat();

    if (wsRef.current) {
      wsRef.current.onclose = null;
      wsRef.current.close(1000, 'Meeting ended');
      wsRef.current = null;
    }

    interruptPlayback();

    mediaStreamRef.current?.getTracks().forEach(t => t.stop());
    mediaStreamRef.current = null;
    workletNodeRef.current?.disconnect();
    workletNodeRef.current = null;
    analyserRef.current = null;
    audioContextRef.current?.close();
    audioContextRef.current = null;

    sendBufferRef.current = [];
    playbackQueueRef.current = [];
    isPipelineActiveRef.current = false;
    reconnectAttemptsRef.current = 0;
    reconnectDelayRef.current = RECONNECT_BASE_MS;
  }, [stopSendLoop, stopHeartbeat, interruptPlayback]);

  const stopPipeline = useCallback(() => {
    stopPipelineInternal();
    updateState('idle');
    console.log('[V2 Pipeline] Stopped.');
  }, [stopPipelineInternal, updateState]);

  const interrupt = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'interrupt' }));
    }
    interruptPlayback();
  }, [interruptPlayback]);

  const switchRole = useCallback((newRole) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'role_update', role: newRole }));
    }
  }, []);

  const injectContext = useCallback((query) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'context_inject', query }));
    }
  }, []);

  // ── Cleanup ─────────────────────────────────────────────

  useEffect(() => {
    return () => {
      isIntentionalCloseRef.current = true;
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (sendIntervalRef.current) clearInterval(sendIntervalRef.current);
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
      mediaStreamRef.current?.getTracks().forEach(t => t.stop());
      workletNodeRef.current?.disconnect();
      audioContextRef.current?.close();
      isPipelineActiveRef.current = false;
    };
  }, []);

  return {
    startPipeline,
    stopPipeline,
    switchRole,
    injectContext,
    interrupt,
    isConnecting: isStartingRef.current,
  };
}

export default useV2AudioPipeline;
