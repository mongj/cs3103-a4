#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall server.

Required evaluator command:
    python solution_server.py --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import math
import resource
import socket
import time
from collections import deque
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

# Small kernel send buffer, so that a client that stops reading blocks
# ws.send() soon, and later frames wait in the outbox where they can still be
# coalesced. This alone does not bound a slow reader's lag (the client's own
# receive window still holds tens of KB); the lag check in send_loop does.
SOCKET_SEND_BUFFER_BYTES = 4096

# Longer than the 60 s recovery requirement, so a client that took the full
# 60 s to reconnect can still recover every final it missed.
HISTORY_WINDOW_SECONDS = 90

# A client can always keep up with the finals alone, so a backlog this large
# means it has stalled. It is evicted and recovers through HISTORY.
MAX_PENDING_FINALS = 256

# How long a client takes to read the frames sent before a ping (see
# ClientOutbox). Above this, the client is considered to be behind, and we
# will hold provisional segments until the ping returns. 500ms is chosen
# as that is the default for "freshness" metric as given in the brief.
LAG_TARGET_SECONDS = 0.5

# Pings take about a third of the server's CPU at 5000 clients, so the ping
# interval adapts per client between these bounds. It grows by STEP after each
# pong whose lag is at most STABLE_LAG, and drops straight to MIN after any
# other pong: pinging too often only costs CPU, while pinging too rarely lets
# a client fall behind unseen. From MIN to MAX takes 6 stable pongs, about
# 3.4 s. Only one ping is outstanding at a time, so the actual interval is
# max(interval, round trip).
PING_INTERVAL_MIN_SECONDS = 0.25
PING_INTERVAL_MAX_SECONDS = 1.0
PING_INTERVAL_STEP_SECONDS = 0.125

# Lag at most this is network jitter rather than a growing backlog. Comparing
# each lag with the previous one would mostly measure that jitter, so a client
# counts as falling behind once its lag rises above this level, well before it
# reaches LAG_TARGET.
STABLE_LAG_SECONDS = LAG_TARGET_SECONDS / 4

# Above this lag even finals arrive too late: the client is evicted and
# recovers through HISTORY.
EVICT_LAG_SECONDS = 10.0

# How fast the provisional gap shrinks back to 0 while the client keeps up.
# Slow on purpose: the client's WebSocket library buffers 16 messages that the
# server cannot see, so a faster recovery refills that buffer before lag shows.
BACKOFF_RECOVERY_PER_SECOND = 0.1

# rtt_min is the fastest pong in the last one or two windows of this length,
# so a lasting rise in network delay becomes the new baseline instead of
# counting as lag forever.
RTT_MIN_WINDOW_SECONDS = 5.0

# When the server is short of CPU, every pong waits for the busy event loop,
# so every client's lag rises although no client is slow. A probe sleeps this
# long and records how late it wakes; the worst of the last
# LOOP_DELAY_SAMPLES (one second) is the server's own share of a lag.
LOOP_PROBE_SECONDS = 0.1
LOOP_DELAY_SAMPLES = 10

FINALS: deque[dict[str, Any]] = deque()  # finals in the history window, oldest first
LATEST: dict[str, Any] | None = None  # newest accepted caption
CLIENTS: set[ClientOutbox] = set()  # connected clients that receive broadcasts
LOOP_DELAYS: deque[float] = deque([0.0], maxlen=LOOP_DELAY_SAMPLES)  # seconds late


def loop_delay() -> float:
    """The server's own recent share of every client's lag."""
    return max(LOOP_DELAYS)


async def probe_loop_delay() -> None:
    while True:
        start = time.monotonic()
        await asyncio.sleep(LOOP_PROBE_SECONDS)
        LOOP_DELAYS.append(time.monotonic() - start - LOOP_PROBE_SECONDS)


def rank(msg: dict[str, Any]) -> tuple[int, bool, int]:
    """Order captions so that a final outranks every revision of its segment."""
    return msg["segment"], msg["final"], msg["revision"]


def accept(msg: dict[str, Any]) -> bool:
    """Record a caption, or return False if it is stale or a duplicate.

    Accepted captions therefore have strictly increasing rank.
    """
    global LATEST
    if LATEST and rank(msg) <= rank(LATEST):
        return False
    LATEST = msg
    if msg["final"]:
        FINALS.append(msg)
        while FINALS[0]["source_ts"] < msg["source_ts"] - HISTORY_WINDOW_SECONDS:
            FINALS.popleft()
    return True


def missed_finals(hello: dict[str, Any]) -> list[dict[str, Any]]:
    """Select the finals for a client's HISTORY frame.

    A resuming client (last_segment > 0) gets every final after last_segment;
    limiting it to history_seconds would leave a permanent gap. A new client
    gets the finals of the last history_seconds as context.
    """
    last_segment = hello.get("last_segment", 0)
    cutoff = time.time() - hello.get("history_seconds", 30)
    return [
        final
        for final in FINALS
        if final["segment"] > last_segment
        and (last_segment or final["source_ts"] >= cutoff)
    ]


class ClientOutbox:
    """One client's pending frames, and how far behind it reads.

    Pending frames are pre-serialized JSON:
      - finals: FIFO, never dropped, always sent before provisionals.
      - provisionals: segment -> newest revision. A newer revision replaces
        the pending one (coalesced), and the segment's final erases it
        (dropped). Since accept() only passes increasing ranks, the dict's
        insertion order is segment order.

    The client's WebSocket library answers a ping only after reading every
    frame sent before it, so pings measure how far behind the client reads:
      - rtt_min: fastest recent round trip, taken as the network's share.
        It is the minimum over the previous and current RTT_MIN_WINDOW, so
        it follows the network both down and up.
      - lag: round trip minus rtt_min, the time spent reading the backlog.
        It includes the server's own delay (loop_delay()). What to send
        depends on the whole lag, since freshness does; ping cadence and
        eviction judge the client, so they use the lag less loop_delay().
      - ping_interval: minimum time between pings; see PING_INTERVAL_*.
      - backoff: minimum time between rounds of provisional sends, as of
        backoff_at. When a pong shows lag above LAG_TARGET, it jumps to that
        lag; it then shrinks back to 0 at BACKOFF_RECOVERY_PER_SECOND. A gap
        of 0 sends every revision; a gap longer than a segment lets each final
        erase the provisionals before it.
    """

    def __init__(self, ws) -> None:
        self.ws = ws
        self.finals: deque[str] = deque()
        self.provisionals: dict[int, str] = {}
        self.closed = False
        self._wake = asyncio.Event()  # set when there may be something to send

        self.rtt_min = math.inf
        self.rtt_min_next = math.inf  # fastest round trip in the current window
        self.rtt_window_at = 0.0  # monotonic time the current window started
        self.ping_at = -math.inf  # monotonic time the last ping was sent
        self.pong_due = False
        self.ping_interval = PING_INTERVAL_MIN_SECONDS
        self.backoff = 0.0
        self.backoff_at = 0.0
        self.last_provisional_at = 0.0

        # Logged on disconnect.
        self.sent = 0
        self.coalesced = 0
        self.dropped = 0

    def lag(self, now: float) -> float:
        """How far the outstanding ping is past rtt_min, or 0 if none.

        Before the first pong rtt_min is unknown, so a client that never reads
        is caught only by the websockets keepalive (ping_timeout).
        """
        if not self.pong_due or self.rtt_min == math.inf:
            return 0.0
        return now - self.ping_at - self.rtt_min

    def provisional_gap(self, now: float) -> float:
        """The backoff, less what it has recovered since backoff_at."""
        recovered = BACKOFF_RECOVERY_PER_SECOND * (now - self.backoff_at)
        return max(0.0, self.backoff - recovered)

    def on_pong(self, pong: asyncio.Future) -> None:
        if not pong.cancelled() and pong.exception() is None:
            rtt = pong.result()  # websockets resolves the future with the round trip
            now = time.monotonic()
            self._update_rtt_min(now, rtt)
            lag = rtt - self.rtt_min
            if lag - loop_delay() <= STABLE_LAG_SECONDS:
                self.ping_interval = min(
                    self.ping_interval + PING_INTERVAL_STEP_SECONDS,
                    PING_INTERVAL_MAX_SECONDS,
                )
            else:
                self.ping_interval = PING_INTERVAL_MIN_SECONDS
            if lag > LAG_TARGET_SECONDS:
                self.backoff = max(self.provisional_gap(now), lag)
                self.backoff_at = now
        self.pong_due = False
        self._wake.set()

    def _update_rtt_min(self, now: float, rtt: float) -> None:
        """Keep rtt_min as the minimum over the previous and current window."""
        if now - self.rtt_window_at >= RTT_MIN_WINDOW_SECONDS:
            self.rtt_min = self.rtt_min_next
            self.rtt_min_next = math.inf
            self.rtt_window_at = now
        self.rtt_min_next = min(self.rtt_min_next, rtt)
        self.rtt_min = min(self.rtt_min, rtt)

    def push(self, msg: dict[str, Any], raw: str) -> None:
        """Queue an accepted caption; evict the client if finals pile up."""
        if self.closed:
            return
        segment = msg["segment"]
        if msg["final"]:
            self.finals.append(raw)
            if self.provisionals.pop(segment, None) is not None:
                self.dropped += 1
            if len(self.finals) > MAX_PENDING_FINALS:
                self.evict(f"over {MAX_PENDING_FINALS} pending finals")
                return
        else:
            if segment in self.provisionals:
                self.coalesced += 1
            self.provisionals[segment] = raw
        self._wake.set()

    def close(self) -> None:
        self.closed = True
        CLIENTS.discard(self)
        self._wake.set()

    def evict(self, reason: str) -> None:
        """Disconnect a client that cannot keep up.

        Aborting the TCP connection discards its backlog at once, where a close
        handshake would wait behind it. The client reconnects and recovers
        through HISTORY.
        """
        print(f"evicting client: {reason}", flush=True)
        self.close()
        self.ws.transport.abort()

    def _take_frames(self, now: float, lag: float) -> tuple[list[str], float | None]:
        """Dequeue the frames to send now, in send order.

        Each frame is one caption's JSON, sent as its own WebSocket message.
        All pending finals come first, then the pending provisionals, unless
        the client is lagging or the provisional gap has not yet passed.

        Also returns how long until held provisionals are due, or None if
        none are waiting on the gap.
        """
        frames = list(self.finals)
        self.finals.clear()
        if not self.provisionals or lag > LAG_TARGET_SECONDS:
            return frames, None
        due = self.last_provisional_at + self.provisional_gap(now)
        if now < due:
            return frames, due - now
        frames += self.provisionals.values()
        self.provisionals.clear()
        self.last_provisional_at = now
        return frames, None

    async def _ping_if_due(self) -> None:
        if (
            not self.pong_due
            and time.monotonic() - self.ping_at >= self.ping_interval
        ):
            pong = await self.ws.ping()
            self.ping_at = time.monotonic()
            self.pong_due = True
            pong.add_done_callback(self.on_pong)

    async def send_loop(self) -> None:
        """Send finals as they arrive, and provisionals as fast as the client
        keeps up. A ping follows each round of sends, to keep the lag measurement current.

        push() runs between this loop's awaits, so each step from reading the
        queues to clearing _wake must not await, or a push could be missed.
        """
        try:
            while not self.closed:
                now = time.monotonic()
                lag = self.lag(now)
                if lag - loop_delay() > EVICT_LAG_SECONDS:
                    self.evict(f"lag {lag:.1f}s")
                    return

                frames, timeout = self._take_frames(now, lag)
                if frames:
                    for raw in frames:
                        await self.ws.send(raw)
                    self.sent += len(frames)
                    await self._ping_if_due()
                    continue

                if self.pong_due:
                    # Wake regularly to re-check the lag, so a client that
                    # stops reading is evicted even when no captions arrive.
                    timeout = min(timeout or LAG_TARGET_SECONDS, LAG_TARGET_SECONDS)
                self._wake.clear()
                if timeout is None:
                    # Most waits have no deadline; skip wait_for's timer.
                    await self._wake.wait()
                else:
                    with contextlib.suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), timeout)
        except ConnectionClosed:
            pass
        finally:
            self.close()


async def handle_source(ws, hello: dict[str, Any]) -> None:
    global LATEST
    print(f"source connected: session={hello.get('session')}", flush=True)

    # A new source numbers segments from 1 again, so it starts a new lecture.
    FINALS.clear()
    LATEST = None

    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            if msg_type == "CAPTION_UPDATE":
                if accept(msg):
                    # Forward the source's own frame, so each caption is
                    # serialized once rather than once per client. Iterate
                    # over a copy, as push() may evict a client.
                    for outbox in list(CLIENTS):
                        outbox.push(msg, raw)
            elif msg_type == "SOURCE_DONE":
                print("source finished", flush=True)
    finally:
        print("source disconnected", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    sock = ws.transport.get_extra_info("socket")
    if sock is not None:
        with contextlib.suppress(OSError):
            sock.setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, SOCKET_SEND_BUFFER_BYTES
            )

    # No await from here until the client is in CLIENTS, so every final is in
    # either this HISTORY frame or a later push(), exactly once.
    outbox = ClientOutbox(ws)
    outbox.finals.append(json.dumps({"type": "HISTORY", "items": missed_finals(hello)}))
    if LATEST and not LATEST["final"]:
        outbox.push(LATEST, json.dumps(LATEST))
    CLIENTS.add(outbox)

    sender = asyncio.create_task(outbox.send_loop())
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        async for _ in ws:
            pass  # Clients send nothing after CLIENT_HELLO.
    finally:
        outbox.close()
        sender.cancel()
        print(
            f"client disconnected: id={client_id}; clients={len(CLIENTS)}; "
            f"sent={outbox.sent}; coalesced={outbox.coalesced}; "
            f"dropped={outbox.dropped}; rtt_min={outbox.rtt_min:.3f}s",
            flush=True,
        )


async def handler(ws) -> None:
    try:
        hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
    except (asyncio.TimeoutError, json.JSONDecodeError, ConnectionClosed):
        await ws.close(code=1008, reason="Expected a valid hello message")
        return

    msg_type = hello.get("type")
    if msg_type == "SOURCE_HELLO":
        await handle_source(ws, hello)
    elif msg_type == "CLIENT_HELLO":
        await handle_client(ws, hello)
    else:
        await ws.close(code=1008, reason="Expected SOURCE_HELLO or CLIENT_HELLO")


async def main_async(port: int) -> None:
    async with websockets.serve(
        handler,
        "0.0.0.0",
        port,
        max_size=2**20,
        # Small asyncio write buffer, for the same reason as
        # SOCKET_SEND_BUFFER_BYTES.
        write_limit=1024,
        ping_interval=20,
        ping_timeout=20,
    ):
        print(f"LiveRecall listening on ws://0.0.0.0:{port}", flush=True)
        await probe_loop_delay()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    # Each client holds a file descriptor, and the usual soft limit of 1024
    # would cap the number of clients. macOS rejects raising the soft limit to
    # an unlimited hard limit, so there it keeps its default.
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    with contextlib.suppress(ValueError, OSError):
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async(args.port))


if __name__ == "__main__":
    main()
