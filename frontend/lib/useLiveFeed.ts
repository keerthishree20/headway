"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  FeedConfig,
  FeedFilter,
  FeedStatus,
  FilterInfo,
  HistoryPoint,
  LiveEvent,
  Metrics,
  ServerMessage,
  Vehicle,
} from "./types";

const WS_URL =
  process.env.NEXT_PUBLIC_WS_URL ?? "ws://127.0.0.1:8100/ws";

const MAX_HISTORY = 240;
const MAX_EVENTS = 120;

export type LiveState = {
  connected: boolean;
  vehicles: Vehicle[];
  metrics: Metrics | null;
  history: HistoryPoint[];
  events: LiveEvent[];
  /** Severe events, kept in their own log so a busy peak cannot evict them. */
  alerts: LiveEvent[];
  status: FeedStatus | null;
  config: FeedConfig | null;
  filter: FilterInfo | null;
  /** Size of the last frame received, measured here rather than reported by
   *  the server -- a byte count cannot live inside the message it counts. */
  lastTickBytes: number | null;
  lastTick: number | null;
  feedAgeSec: number | null;
};

const EMPTY: LiveState = {
  connected: false,
  vehicles: [],
  metrics: null,
  history: [],
  events: [],
  alerts: [],
  status: null,
  config: null,
  filter: null,
  lastTickBytes: null,
  lastTick: null,
  feedAgeSec: null,
};

/**
 * Subscribes to the backend's WebSocket and keeps the newest snapshot plus a
 * rolling window of history and events. Reconnects with capped exponential
 * backoff so a backend restart heals on its own.
 *
 * The returned `setFilter` tells the server which routes and which map
 * viewport this browser is actually drawing. The server then sends only those
 * vehicles instead of the whole fleet -- the fleet array is by far the largest
 * part of a tick, and a page showing one route needs almost none of it.
 */
export function useLiveFeed(): LiveState & {
  setFilter: (filter: FeedFilter) => void;
} {
  const [state, setState] = useState<LiveState>(EMPTY);
  const socketRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);
  // The filter has to survive a reconnect: the new socket starts unfiltered,
  // so it is re-sent on open or the map silently fills back up with the whole
  // fleet after a backend restart.
  const filterRef = useRef<FeedFilter>({ routes: null, bbox: null });

  const sendFilter = useCallback(() => {
    const socket = socketRef.current;
    if (socket?.readyState !== WebSocket.OPEN) return;
    socket.send(JSON.stringify({ type: "subscribe", ...filterRef.current }));
  }, []);

  const setFilter = useCallback(
    (filter: FeedFilter) => {
      filterRef.current = filter;
      sendFilter();
    },
    [sendFilter],
  );

  const connect = useCallback(() => {
    if (closedRef.current) return;
    const socket = new WebSocket(WS_URL);
    socketRef.current = socket;

    socket.onopen = () => {
      attemptRef.current = 0;
      setState((s) => ({ ...s, connected: true }));
      if (filterRef.current.routes || filterRef.current.bbox) sendFilter();
    };

    socket.onmessage = (event) => {
      const raw = event.data as string;
      let msg: ServerMessage;
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      const frameBytes = new Blob([raw]).size;
      setState((prev) => {
        if (msg.type === "replay") {
          return {
            connected: true,
            vehicles: msg.vehicles,
            metrics: msg.metrics,
            history: msg.history.slice(-MAX_HISTORY),
            events: msg.events,
            alerts: msg.alerts ?? [],
            status: msg.status,
            config: msg.config,
            filter: msg.filter ?? null,
            lastTickBytes: frameBytes,
            lastTick: msg.ts,
            feedAgeSec: null,
          };
        }
        if (msg.type === "tick") {
          const point: HistoryPoint = {
            ts: msg.ts,
            feed_ts: msg.feed_ts,
            active: msg.metrics.active,
            bunched_pairs: msg.metrics.bunched_pairs,
            stale: msg.metrics.stale,
            avg_speed_mps: msg.metrics.avg_speed_mps,
          };
          return {
            ...prev,
            connected: true,
            vehicles: msg.vehicles,
            metrics: msg.metrics,
            history: [...prev.history, point].slice(-MAX_HISTORY),
            events: [...msg.events.slice().reverse(), ...prev.events].slice(
              0,
              MAX_EVENTS,
            ),
            alerts: msg.alerts ?? prev.alerts,
            status: msg.status,
            filter: msg.filter ?? prev.filter,
            lastTickBytes: frameBytes,
            lastTick: msg.ts,
            feedAgeSec: msg.feed_age_sec,
          };
        }
        // status: the feed broke; keep the last good positions on screen.
        return {
          ...prev,
          status: msg.status,
          events: [...msg.events.slice().reverse(), ...prev.events].slice(
            0,
            MAX_EVENTS,
          ),
          alerts: msg.alerts ?? prev.alerts,
        };
      });
    };

    const retry = () => {
      setState((s) => ({ ...s, connected: false }));
      if (closedRef.current) return;
      const delay = Math.min(1000 * 2 ** attemptRef.current, 15000);
      attemptRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    socket.onclose = retry;
    socket.onerror = () => socket.close();
  }, [sendFilter]);

  useEffect(() => {
    closedRef.current = false;
    connect();
    return () => {
      closedRef.current = true;
      if (timerRef.current) clearTimeout(timerRef.current);
      socketRef.current?.close();
    };
  }, [connect]);

  return { ...state, setFilter };
}
