/* LiveRecall browser client. See ARCHITECTURE.md for the design. */

const WS_URL = "ws://127.0.0.1:8765";
const RECONNECT_DELAY_MS = 1000;
const HISTORY_SECONDS = 60;  // the longest recall view
const KEEP_SECONDS = 90;

const statusEl = document.getElementById("status");
const liveEl = document.getElementById("live");
const recallEl = document.getElementById("recall");

// segment -> {msg, el}: one on-screen line per segment, oldest first.
const lines = new Map();
let lastFinal = { segment: 0, revision: 0 };

let ws;

function numericClientId() {
  const key = "liverecall-client-id";
  let value = sessionStorage.getItem(key);
  if (!value) {
    value = String(Math.floor(100000 + Math.random() * 900000));
    sessionStorage.setItem(key, value);
  }
  return Number(value);
}

function forgetOldLines() {
  const cutoff = Date.now() / 1000 - KEEP_SECONDS;
  for (const [segment, line] of lines) {
    if (line.msg.source_ts >= cutoff) break;
    line.el.remove();
    lines.delete(segment);
  }
}

function showCaption(msg) {
  let line = lines.get(msg.segment);
  if (line && msg.revision <= line.msg.revision) return;  // never regress a segment
  if (!line) {
    line = { el: liveEl.appendChild(document.createElement("div")) };
    lines.set(msg.segment, line);
  }
  line.msg = msg;
  line.el.textContent = msg.text;
  line.el.className = msg.final ? "final" : "provisional";
  if (msg.final) lastFinal = msg;
  forgetOldLines();
  liveEl.scrollTop = liveEl.scrollHeight;
}

function showRecall(seconds) {
  const cutoff = Date.now() / 1000 - seconds;
  const missed = [...lines.values()]
    .map((line) => line.msg)
    .filter((msg) => msg.final && msg.source_ts >= cutoff)
    .map((msg) => msg.text);
  recallEl.textContent = missed.join("\n") || `Nothing in the last ${seconds} seconds.`;
}

function connect() {
  statusEl.textContent = "connecting";
  ws = new WebSocket(WS_URL);

  ws.addEventListener("open", () => {
    statusEl.textContent = "connected";
    ws.send(JSON.stringify({
      type: "CLIENT_HELLO",
      client_id: numericClientId(),
      session: "L1",
      last_segment: lastFinal.segment,
      last_revision: lastFinal.revision,
      history_seconds: HISTORY_SECONDS
    }));
  });

  ws.addEventListener("message", (event) => {
    const msg = JSON.parse(event.data);
    if (msg.type === "CAPTION_UPDATE") {
      showCaption(msg);
    } else if (msg.type === "HISTORY") {
      msg.items.forEach(showCaption);
    }
  });

  ws.addEventListener("close", () => {
    statusEl.textContent = "reconnecting";
    setTimeout(connect, RECONNECT_DELAY_MS);
  });
}

for (const button of document.querySelectorAll("button[data-seconds]")) {
  button.addEventListener("click", () => showRecall(Number(button.dataset.seconds)));
}

connect();
