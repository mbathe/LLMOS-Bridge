"use client";

import { useEffect, useRef, useCallback } from "react";
import { getToken } from "@/lib/auth/token";
import { useWSEventStore } from "@/stores/ws-events";
import { useNotificationStore } from "@/stores/notifications";
import type { WSMessage } from "@/types/events";

const DAEMON_URL =
  process.env.NEXT_PUBLIC_DAEMON_URL ?? "http://localhost:40000";

function wsUrl(path: string): string {
  const base = DAEMON_URL.replace(/^http/, "ws");
  const token = getToken();
  const sep = path.includes("?") ? "&" : "?";
  return token ? `${base}${path}${sep}token=${token}` : `${base}${path}`;
}

const BACKOFF_BASE = 1000;
const BACKOFF_MAX = 30000;

export function useWebSocket(path: string = "/ws/stream") {
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pushEvent = useWSEventStore((s) => s.pushEvent);
  const setConnected = useWSEventStore((s) => s.setConnected);
  const addNotification = useNotificationStore((s) => s.addNotification);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(wsUrl(path));
    wsRef.current = ws;

    ws.onopen = () => {
      retryRef.current = 0;
      setConnected(true);
    };

    ws.onmessage = (event) => {
      try {
        const msg: WSMessage = JSON.parse(event.data);
        if (msg.type === "pong") return;
        pushEvent(msg);

        if (msg.type === "approval_required") {
          addNotification({
            type: "warning",
            title: "Approval Required",
            message: `Plan ${(msg.payload as Record<string, string>).plan_id} needs approval`,
          });
        }
      } catch {
        // ignore non-JSON messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      const delay = Math.min(
        BACKOFF_BASE * Math.pow(2, retryRef.current),
        BACKOFF_MAX,
      );
      retryRef.current++;
      timerRef.current = setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [path, pushEvent, setConnected, addNotification]);

  useEffect(() => {
    connect();
    // Heartbeat: send "ping" every 30s
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send("ping");
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      if (timerRef.current) clearTimeout(timerRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  return { connected: useWSEventStore((s) => s.connected) };
}
