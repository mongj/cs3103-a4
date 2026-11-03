#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall server.

Graded networking mechanisms implemented here:
  - Bounded per-client application outbox (ClientOutbox).
  - Slow-client isolation: a stalled receiver only grows its own outbox.
  - Semantic coalescing: at most one queued provisional revision per segment
    per client; the newest wins. Finals are durable and never dropped.
  - Finals-first drain: on wake, finals flush ahead of coalesced provisionals.
  - Rapid convergence: on each drain cycle only the latest per-segment state
    is sent, so a reader catching up skips stale revisions.

Required evaluator command:
    python solution_server.py --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import time
from collections import deque
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

# Shrink the per-socket kernel send buffer so that if a client falls behind,
# ws.send() back-pressures within a handful of unread messages instead of
# letting the kernel absorb hundreds in FIFO order. Without this, no amount of
# application-layer coalescing can take effect, because the kernel has already
# committed the stale provisionals to the wire.
SOCKET_SEND_BUFFER_BYTES = 4096

# Longer than the 60 s recovery requirement, so a client offline for a full
# 60 s can still recover the captions finalized while it was reconnecting.
HISTORY_WINDOW_SECONDS = 90

# Fixed upper bound on per-client pending finals. The evaluation guarantees
# clients can keep up with the final-only stream, so pending_finals should
# never grow near this ceiling under legitimate workloads. A client that
# exceeds it is pathologically slow — the server evicts the socket and lets
# the client reconnect via HISTORY (which replays everything inside the
# server-side 90 s FINALS window). This bounds per-client state and prevents
# any "long backlog drained only after source stops" behavior.
MAX_PENDING_FINALS = 256

FINALS: deque[dict[str, Any]] = deque()  # finalized captions, oldest first
LATEST: dict[str, Any] | None = None  # newest accepted caption


def rank(msg: dict[str, Any]) -> tuple[int, bool, int]:
    """Order captions so that a final outranks every revision of its segment."""
    return msg["segment"], msg["final"], msg["revision"]


def accept(msg: dict[str, Any]) -> bool:
    """Record a caption, or return False if it is stale or a duplicate."""
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
    """Select the finals for a HISTORY reply.

    A segment has one final, so last_segment alone identifies what the client
    holds. history_seconds only sizes the context of a client with no state: a
    resuming client gets everything it missed, or it would have a permanent gap.
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
    """Per-client coalescing outbox.

    Invariants:
      - At most one pending provisional per segment; newer revision replaces
        older. Value is (revision, raw_json).
      - A final erases any still-queued provisional for that same segment.
      - Finals are append-only and FIFO; the send loop drains them before any
        provisional, so durable state is never starved.
      - Values are pre-serialized JSON; the send loop does zero json.dumps.
    """

    __slots__ = (
        "ws",
        "_pending_provisional",
        "_pending_finals",
        "_wake",
        "_closed",
        "coalesced",
        "sent",
    )

    def __init__(self, ws) -> None:
        self.ws = ws
        self._pending_provisional: dict[int, tuple[int, str]] = {}
        self._pending_finals: deque[str] = deque()
        self._wake = asyncio.Event()
        self._closed = False
        self.coalesced = 0
        self.sent = 0

    def push(self, msg: dict[str, Any], raw: str) -> bool:
        """Enqueue a broadcast frame.

        Returns False only on the one call that causes eviction. If the outbox
        is already closed (from a previous eviction or disconnect) this is a
        silent no-op returning True — no further caller action required.
        """
        if self._closed:
            return True
        seg = msg["segment"]
        if msg["final"]:
            self._pending_finals.append(raw)
            self._pending_provisional.pop(seg, None)
            self._wake.set()
            if len(self._pending_finals) > MAX_PENDING_FINALS:
                return False
        else:
            rev = msg["revision"]
            curr = self._pending_provisional.get(seg)
            if curr is None:
                self._pending_provisional[seg] = (rev, raw)
            elif rev > curr[0]:
                self._pending_provisional[seg] = (rev, raw)
                self.coalesced += 1
            # else: incoming is older than what we already have queued; drop.
            self._wake.set()
        return True

    def seed_history(
        self,
        items: list[dict[str, Any]],
        latest_provisional: dict[str, Any] | None,
    ) -> None:
        """Seed the outbox before the client is added to CLIENTS.

        Caller invariant: no await between seed_history and CLIENTS.add. That
        guarantees every subsequent broadcast reaches this client, and every
        earlier final appears in HISTORY exactly once.
        """
        self._pending_finals.append(
            json.dumps({"type": "HISTORY", "items": items})
        )
        if latest_provisional is not None:
            raw = json.dumps(latest_provisional)
            self._pending_provisional[latest_provisional["segment"]] = (
                latest_provisional["revision"], raw
            )

    def close(self) -> None:
        self._closed = True
        self._wake.set()

    @property
    def closed(self) -> bool:
        return self._closed


CLIENTS: set[ClientOutbox] = set()


async def _close_ws(ws) -> None:
    """Best-effort WebSocket close used by the eviction path."""
    try:
        await ws.close(code=1013, reason="server-side backpressure eviction")
    except Exception:
        pass


async def send_loop(ws, outbox: ClientOutbox) -> None:
    """Drain the outbox: finals first, then latest-per-segment provisionals."""
    try:
        while not outbox.closed:
            if not outbox._pending_finals and not outbox._pending_provisional:
                await outbox._wake.wait()
                outbox._wake.clear()
                continue

            # 1) Finals: durable state, highest priority.
            while outbox._pending_finals:
                raw = outbox._pending_finals.popleft()
                await ws.send(raw)
                outbox.sent += 1

            # 2) Provisionals: latest revision per segment, in segment order.
            if outbox._pending_provisional:
                # Atomic swap. No await between the snapshot and the clear,
                # so a concurrent push() cannot lose data in asyncio.
                snapshot = sorted(outbox._pending_provisional.items())
                outbox._pending_provisional.clear()
                for _, (_, raw) in snapshot:
                    await ws.send(raw)
                    outbox.sent += 1
    except ConnectionClosed:
        pass
    finally:
        outbox._closed = True


async def handle_source(ws, hello: dict[str, Any]) -> None:
    global LATEST
    session = hello.get("session")
    print(f"source connected: session={session}", flush=True)

    # The source numbers segments from 1, so a new source is a new lecture.
    FINALS.clear()
    LATEST = None

    try:
        async for raw_in in ws:
            try:
                msg = json.loads(raw_in)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type")
            if msg_type == "CAPTION_UPDATE":
                if not accept(msg):
                    continue
                # Fan out the ORIGINAL source frame — avoids an N-client
                # json.dumps round-trip on every broadcast (CPU hot path).
                evicted: list[ClientOutbox] = []
                for outbox in CLIENTS:
                    if not outbox.push(msg, raw_in):
                        evicted.append(outbox)
                for outbox in evicted:
                    # Remove from CLIENTS so no further pushes grow the dead
                    # outbox; close() stops the send_loop. Also schedule the
                    # WebSocket close so the client immediately reconnects and
                    # catches up via HISTORY instead of waiting ~20 s for a
                    # ping timeout to notice the dead connection.
                    CLIENTS.discard(outbox)
                    outbox.close()
                    asyncio.create_task(_close_ws(outbox.ws))
            elif msg_type == "SOURCE_DONE":
                # Test-control message. Specification does not require it to
                # be forwarded to clients.
                print("source finished", flush=True)
            else:
                # Forward compatibility: ignore unrecognized messages.
                continue
    finally:
        print("source disconnected", flush=True)


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    # Narrow this socket's kernel send buffer so a slow receiver triggers
    # app-layer backpressure (ws.send blocks) rather than letting the kernel
    # queue up stale provisionals the coalescer can no longer touch.
    sock = ws.transport.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF,
                            SOCKET_SEND_BUFFER_BYTES)
        except OSError:
            pass

    # No await until the client is in CLIENTS: every subsequent broadcast is
    # either in this seed or arrives through push(), never between.
    outbox = ClientOutbox(ws)
    outbox.seed_history(
        missed_finals(hello),
        LATEST if (LATEST and not LATEST["final"]) else None,
    )
    CLIENTS.add(outbox)

    sender = asyncio.create_task(send_loop(ws, outbox))
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        async for _ in ws:
            # Clients do not send messages after HELLO. Idle until disconnect.
            pass
    finally:
        outbox.close()
        sender.cancel()
        CLIENTS.discard(outbox)
        print(
            f"client disconnected: id={client_id}; "
            f"clients={len(CLIENTS)}; "
            f"sent={outbox.sent}; coalesced={outbox.coalesced}",
            flush=True,
        )


async def handler(ws) -> None:
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        hello = json.loads(raw)
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
        # Small write buffer forces ws.send() to back-pressure at the asyncio
        # layer the moment a slow receiver falls behind, instead of silently
        # letting the kernel TCP buffer absorb hundreds of messages in FIFO
        # order. That is what gives the per-client outbox a real chance to
        # coalesce superseded provisionals and preserve finals.
        write_limit=1024,
        ping_interval=20,
        ping_timeout=20,
    ):
        print(f"LiveRecall listening on ws://0.0.0.0:{port}", flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        asyncio.run(main_async(args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
