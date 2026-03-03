"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { getToken } from "@/lib/auth/token";

const DAEMON_URL =
  process.env.NEXT_PUBLIC_DAEMON_URL ?? "http://localhost:40000";

export interface SSEHookOptions {
  onEvent?: (eventType: string, data: Record<string, unknown>) => void;
  enabled?: boolean;
}

export function useSSE(path: string, options: SSEHookOptions = {}) {
  const { onEvent, enabled = true } = options;
  const [connected, setConnected] = useState(false);
  const sourceRef = useRef<EventSource | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    const token = getToken();
    const url = new URL(path, DAEMON_URL);
    if (token) url.searchParams.set("token", token);

    const source = new EventSource(url.toString());
    sourceRef.current = source;

    source.onopen = () => setConnected(true);

    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onEventRef.current?.(event.type, data);
      } catch {
        // ignore
      }
    };

    // Listen to named events
    const eventTypes = [
      "action_started",
      "action_progress",
      "action_intermediate",
      "action_status",
      "action_result_ready",
      "plan_completed",
      "plan_failed",
    ];

    eventTypes.forEach((type) => {
      source.addEventListener(type, (event) => {
        try {
          const data = JSON.parse((event as MessageEvent).data);
          onEventRef.current?.(type, data);
        } catch {
          // ignore
        }
      });
    });

    source.onerror = () => {
      setConnected(false);
      // EventSource auto-reconnects
    };
  }, [path]);

  useEffect(() => {
    if (!enabled) return;
    connect();
    return () => {
      sourceRef.current?.close();
      setConnected(false);
    };
  }, [connect, enabled]);

  return { connected };
}
