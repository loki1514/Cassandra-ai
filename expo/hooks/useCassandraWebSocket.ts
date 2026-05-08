/**
 * useCassandraWebSocket
 *
 * Production-grade WebSocket hook for Expo → Cassandra backend.
 *
 * Features:
 * - Exponential backoff retry with jitter
 * - Connection state tracking (connecting, open, closed, error)
 * - Auto-reconnect on unexpected disconnects
 * - Graceful close (no retry on intentional disconnect)
 * - Message queueing while offline
 * - Heartbeat / keepalive detection
 *
 * Usage:
 *   const { send, lastMessage, connectionState, connect, disconnect } =
 *     useCassandraWebSocket({ orgId, token, onAudioChunk });
 */

import { useCallback, useEffect, useRef, useState } from "react";

type ConnectionState = "idle" | "connecting" | "open" | "closing" | "closed" | "error";

interface UseCassandraWebSocketOptions {
  /** Cassandra backend WebSocket URL (e.g. ws://192.168.1.5:8000/ws/audio/org_123) */
  url: string;
  /** Callback for JSON text messages from server */
  onMessage?: (msg: any) => void;
  /** Callback for binary audio (MP3) chunks from server */
  onAudioChunk?: (chunk: ArrayBuffer) => void;
  /** Called whenever connection state changes */
  onStateChange?: (state: ConnectionState) => void;
  /** Max retry attempts before giving up (0 = infinite) */
  maxRetries?: number;
  /** Base delay in ms for exponential backoff */
  baseRetryDelayMs?: number;
  /** Max delay in ms between retries */
  maxRetryDelayMs?: number;
  /** Send heartbeat ping every N ms to keep connection alive */
  heartbeatIntervalMs?: number;
  /** If no message received in N ms, assume dead and reconnect */
  heartbeatTimeoutMs?: number;
}

interface UseCassandraWebSocketReturn {
  connectionState: ConnectionState;
  send: (data: string | ArrayBuffer | Blob) => void;
  connect: () => void;
  disconnect: () => void;
  retryCount: number;
}

export function useCassandraWebSocket(
  options: UseCassandraWebSocketOptions
): UseCassandraWebSocketReturn {
  const {
    url,
    onMessage,
    onAudioChunk,
    onStateChange,
    maxRetries = 10,
    baseRetryDelayMs = 1000,
    maxRetryDelayMs = 30000,
    heartbeatIntervalMs = 15000,
    heartbeatTimeoutMs = 35000,
  } = options;

  const [connectionState, setConnectionState] = useState<ConnectionState>("idle");
  const [retryCount, setRetryCount] = useState(0);

  const wsRef = useRef<WebSocket | null>(null);
  const intentionalCloseRef = useRef(false);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const heartbeatTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messageQueueRef = useRef<(string | ArrayBuffer | Blob)[]>([]);

  const updateState = useCallback(
    (state: ConnectionState) => {
      setConnectionState(state);
      onStateChange?.(state);
    },
    [onStateChange]
  );

  const clearTimers = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
    if (heartbeatTimeoutRef.current) {
      clearTimeout(heartbeatTimeoutRef.current);
      heartbeatTimeoutRef.current = null;
    }
  }, []);

  const resetHeartbeat = useCallback(() => {
    if (heartbeatTimeoutRef.current) {
      clearTimeout(heartbeatTimeoutRef.current);
    }
    heartbeatTimeoutRef.current = setTimeout(() => {
      console.warn("[CassandraWS] Heartbeat timeout — forcing reconnect");
      wsRef.current?.close();
    }, heartbeatTimeoutMs);
  }, [heartbeatTimeoutMs]);

  const flushQueue = useCallback(() => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    while (messageQueueRef.current.length > 0) {
      const msg = messageQueueRef.current.shift();
      if (msg !== undefined) {
        try {
          ws.send(msg);
        } catch (e) {
          console.error("[CassandraWS] Send failed, requeuing", e);
          messageQueueRef.current.unshift(msg);
          break;
        }
      }
    }
  }, []);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log("[CassandraWS] Already connected");
      return;
    }

    clearTimers();
    intentionalCloseRef.current = false;
    updateState("connecting");

    try {
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("[CassandraWS] Connected");
        setRetryCount(0);
        updateState("open");
        resetHeartbeat();
        heartbeatTimerRef.current = setInterval(() => {
          // Send lightweight status ping to keep connection alive
          try {
            ws.send(JSON.stringify({ action: "status" }));
          } catch {
            // Ignore ping failures
          }
        }, heartbeatIntervalMs);
        flushQueue();
      };

      ws.onmessage = (event: MessageEvent) => {
        resetHeartbeat();
        if (typeof event.data === "string") {
          try {
            const msg = JSON.parse(event.data);
            // Ignore heartbeats from server
            if (msg.type === "heartbeat") return;
            onMessage?.(msg);
          } catch {
            onMessage?.(event.data);
          }
        } else if (event.data instanceof ArrayBuffer) {
          onAudioChunk?.(event.data);
        } else if (event.data instanceof Blob) {
          event.data.arrayBuffer().then((buf) => onAudioChunk?.(buf));
        }
      };

      ws.onerror = (err) => {
        console.error("[CassandraWS] WebSocket error", err);
        updateState("error");
      };

      ws.onclose = (event) => {
        console.log(
          `[CassandraWS] Closed code=${event.code} wasClean=${event.wasClean}`
        );
        clearTimers();
        wsRef.current = null;
        updateState("closed");

        // Don't retry if the user explicitly called disconnect()
        if (intentionalCloseRef.current) {
          updateState("idle");
          return;
        }

        // Don't retry on authentication failure (4001) or policy violation (1008)
        if (event.code === 4001 || event.code === 1008) {
          console.error("[CassandraWS] Auth failed — not retrying");
          updateState("error");
          return;
        }

        if (maxRetries > 0 && retryCount >= maxRetries) {
          console.error("[CassandraWS] Max retries exceeded");
          updateState("error");
          return;
        }

        // Exponential backoff with jitter
        const delay = Math.min(
          baseRetryDelayMs * 2 ** retryCount + Math.random() * 1000,
          maxRetryDelayMs
        );
        console.log(`[CassandraWS] Reconnecting in ${Math.round(delay)}ms (attempt ${retryCount + 1})`);
        setRetryCount((c) => c + 1);
        reconnectTimerRef.current = setTimeout(() => {
          connect();
        }, delay);
      };
    } catch (e) {
      console.error("[CassandraWS] Failed to create WebSocket", e);
      updateState("error");
    }
  }, [
    url,
    maxRetries,
    baseRetryDelayMs,
    maxRetryDelayMs,
    heartbeatIntervalMs,
    retryCount,
    clearTimers,
    updateState,
    resetHeartbeat,
    flushQueue,
    onMessage,
    onAudioChunk,
  ]);

  const send = useCallback(
    (data: string | ArrayBuffer | Blob) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(data);
        } catch (e) {
          console.error("[CassandraWS] Send error, queuing", e);
          messageQueueRef.current.push(data);
        }
      } else {
        messageQueueRef.current.push(data);
        if (connectionState === "closed" || connectionState === "error") {
          console.log("[CassandraWS] Offline — queued message, triggering reconnect");
          connect();
        }
      }
    },
    [connect, connectionState]
  );

  const disconnect = useCallback(() => {
    intentionalCloseRef.current = true;
    clearTimers();
    if (wsRef.current) {
      updateState("closing");
      wsRef.current.close(1000, "Client disconnect");
      wsRef.current = null;
    }
    updateState("idle");
    setRetryCount(0);
    messageQueueRef.current = [];
  }, [clearTimers, updateState]);

  // Auto-connect on mount if url is provided
  useEffect(() => {
    if (url) {
      connect();
    }
    return () => {
      intentionalCloseRef.current = true;
      clearTimers();
      wsRef.current?.close(1000, "Component unmount");
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  return {
    connectionState,
    send,
    connect,
    disconnect,
    retryCount,
  };
}
