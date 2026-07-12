import { useEffect, useRef, useCallback, useState } from 'react';
import { useAppStore } from '@/store/useAppStore';
import type { WebSocketMessage } from '@/types';

// In dev, Vite proxies /ws to the backend. In production, nginx handles it.
const WS_URL = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws`;

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout>>();
  const reconnectAttempts = useRef(0);
  const mountedRef = useRef(true);
  const MAX_RECONNECT_ATTEMPTS = 5;
  const RECONNECT_DELAY = 3000;

  const { setWsConnected, updateIncident, updateAgent } = useAppStore();
  const [messages, setMessages] = useState<WebSocketMessage[]>([]);

  const connect = useCallback(() => {
    // Don't connect if already connected
    if (wsRef.current?.readyState === WebSocket.OPEN || wsRef.current?.readyState === WebSocket.CONNECTING) {
      return;
    }

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) return;
        console.debug('[WS] Connected');
        setWsConnected(true);
        reconnectAttempts.current = 0;
      };

      ws.onmessage = (event) => {
        if (!mountedRef.current) return;
        try {
          const message: WebSocketMessage = JSON.parse(event.data);
          setMessages((prev) => [...prev.slice(-50), message]);

          switch (message.type) {
            case 'incident_update': {
              const incident = message.payload as unknown as Parameters<typeof updateIncident>[0];
              updateIncident(incident);
              break;
            }
            case 'agent_status': {
              const agent = message.payload as unknown as Parameters<typeof updateAgent>[0];
              updateAgent(agent);
              break;
            }
            case 'heartbeat':
              break;
            default:
              break;
          }
        } catch (err) {
          console.warn('[WS] Failed to parse message:', err);
        }
      };

      ws.onerror = () => {
        // WebSocket errors are expected during reconnection — use warn level
        if (mountedRef.current) {
          console.warn('[WS] Connection error (will retry)');
        }
        setWsConnected(false);
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        console.debug('[WS] Disconnected');
        setWsConnected(false);
        wsRef.current = null;

        if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
          reconnectAttempts.current += 1;
          reconnectTimerRef.current = setTimeout(() => {
            if (mountedRef.current) {
              console.debug(`[WS] Reconnecting... Attempt ${reconnectAttempts.current}`);
              connect();
            }
          }, RECONNECT_DELAY * Math.min(reconnectAttempts.current, 3));
        } else {
          console.warn(`[WS] Max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}) reached`);
        }
      };
    } catch (err) {
      if (mountedRef.current) {
        console.warn('[WS] Connection setup failed:', err);
      }
      setWsConnected(false);
    }
  }, [setWsConnected, updateIncident, updateAgent]);

  const disconnect = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = undefined;
    }
    if (wsRef.current) {
      // Remove handlers to prevent reconnect attempts during intentional disconnect
      wsRef.current.onclose = null;
      wsRef.current.onerror = null;
      wsRef.current.close();
      wsRef.current = null;
    }
  }, []);

  const sendMessage = useCallback((message: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(message));
    } else {
      console.debug('[WS] Cannot send — not connected');
    }
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    // Small delay to avoid StrictMode double-mount race
    const initTimer = setTimeout(() => {
      if (mountedRef.current) {
        connect();
      }
    }, 100);

    return () => {
      mountedRef.current = false;
      clearTimeout(initTimer);
      disconnect();
    };
  }, [connect, disconnect]);

  return { messages, sendMessage, connect, disconnect };
}
