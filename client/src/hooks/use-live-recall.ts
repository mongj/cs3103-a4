/* LiveRecall connection and caption state. See ARCHITECTURE.md for the design. */
import { LECTURES, type LectureId } from "@/lib/lectures";
import { useEffect, useRef, useState } from "react";

const RECONNECT_DELAY_MS = 1000;
const HISTORY_SECONDS = 60; // the longest recall view
const KEEP_SECONDS = 90;

export type Caption = {
  segment: number;
  revision: number;
  final: boolean;
  text: string;
  source_ts: number;
};

export type Status = "disconnected" | "connecting" | "connected" | "offline" | "reconnecting";

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
}

// State is per lecture: App remounts its user (key={lecture}) on a switch, so
// the refs below never carry one lecture's captions or resume point into another.
export function useLiveRecall(lecture: LectureId) {
  // segment -> latest revision, oldest segment first. The Map is the source of
  // truth; `captions` is its snapshot for rendering.
  const lines = useRef(new Map<number, Caption>());
  const lastFinal = useRef({ segment: 0, revision: 0 });
  const offlineRef = useRef(false);
  const ws = useRef<WebSocket | null>(null);

  const [captions, setCaptions] = useState<Caption[]>([]);
  const [status, setStatus] = useState<Status>("disconnected");
  const [offline, setOffline] = useState(false);
  const [clientId] = useState(numericClientId);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;

    function forgetOldLines() {
      const cutoff = Date.now() / 1000 - KEEP_SECONDS;
      for (const [segment, line] of lines.current) {
        if (line.source_ts >= cutoff) break;
        lines.current.delete(segment);
      }
    }

    function applyCaption(msg: Caption) {
      const line = lines.current.get(msg.segment);
      if (line && msg.revision <= line.revision) return; // never regress a segment
      lines.current.set(msg.segment, msg);
      if (msg.final) lastFinal.current = msg;
    }

    function render() {
      forgetOldLines();
      setCaptions([...lines.current.values()]);
    }

    function retry() {
      if (!stopped) timer = setTimeout(connect, RECONNECT_DELAY_MS);
    }

    function connect() {
      if (offlineRef.current) {
        setStatus("offline");
        retry();
        return;
      }
      setStatus("connecting");
      const socket = new WebSocket(`ws://${location.hostname || "127.0.0.1"}:${LECTURES[lecture].port}`);
      ws.current = socket;

      socket.addEventListener("open", () => {
        setStatus("connected");
        socket.send(JSON.stringify({
          type: "CLIENT_HELLO",
          client_id: clientId,
          session: lecture,
          last_segment: lastFinal.current.segment,
          last_revision: lastFinal.current.revision,
          history_seconds: HISTORY_SECONDS,
        }));
      });

      socket.addEventListener("message", (event) => {
        const msg = JSON.parse(event.data);
        if (msg.type === "CAPTION_UPDATE") {
          applyCaption(msg);
          render();
        } else if (msg.type === "HISTORY") {
          msg.items.forEach(applyCaption);
          render();
        }
      });

      socket.addEventListener("close", () => {
        if (stopped) return;
        setStatus(offlineRef.current ? "offline" : "reconnecting");
        retry();
      });
    }

    connect();
    return () => {
      stopped = true;
      clearTimeout(timer);
      ws.current?.close();
    };
  }, [clientId, lecture]);

  // Demo control: holds the client offline. The retry loop in connect() is the
  // only path back online, so a quick double click can't open a second socket.
  function toggleOffline() {
    offlineRef.current = !offlineRef.current;
    setOffline(offlineRef.current);
    if (offlineRef.current) ws.current?.close();
  }

  return { captions, status, offline, clientId, toggleOffline };
}
