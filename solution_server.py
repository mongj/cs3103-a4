#!/usr/bin/env python3
"""
CS3103 Assignment 4 - LiveRecall server.

It DOES NOT implement the graded networking mechanisms:
  - bounded per-client application queues
  - slow-client isolation
  - semantic coalescing / backpressure
  - constrained-bandwidth overload handling

Required evaluator command:
    python solution_server.py --port 8765
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import deque
from typing import Any

import websockets
from websockets.exceptions import ConnectionClosed

# Longer than the 60 s recovery requirement, so a client offline for a full
# 60 s can still recover the captions finalized while it was reconnecting.
HISTORY_WINDOW_SECONDS = 90

CLIENTS: set[asyncio.Queue[str]] = set()  # one outbox per connected client
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


async def handle_source(ws, hello: dict[str, Any]) -> None:
    global LATEST
    session = hello.get("session")
    print(f"source connected: session={session}", flush=True)

    # The source numbers segments from 1, so a new source is a new lecture.
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
                    for outbox in CLIENTS:
                        outbox.put_nowait(raw)
            elif msg_type == "SOURCE_DONE":
                # Test-control message. The specification does not require it
                # to be forwarded to clients.
                print("source finished", flush=True)
            else:
                # Forward compatibility: ignore unrecognized messages.
                continue
    finally:
        print("source disconnected", flush=True)


async def send_loop(ws, outbox: asyncio.Queue[str]) -> None:
    try:
        while True:
            await ws.send(await outbox.get())
    except ConnectionClosed:
        pass


async def handle_client(ws, hello: dict[str, Any]) -> None:
    client_id = hello.get("client_id")

    # No await until the client is in CLIENTS: every caption is therefore either
    # in this catch-up or broadcast to the outbox afterwards, never lost between.
    outbox: asyncio.Queue[str] = asyncio.Queue()
    outbox.put_nowait(json.dumps({"type": "HISTORY", "items": missed_finals(hello)}))
    if LATEST and not LATEST["final"]:
        outbox.put_nowait(json.dumps(LATEST))
    CLIENTS.add(outbox)

    sender = asyncio.create_task(send_loop(ws, outbox))
    print(f"client connected: id={client_id}; clients={len(CLIENTS)}", flush=True)
    try:
        # Keep the WebSocket open until the client disconnects.
        async for _ in ws:
            pass
    finally:
        sender.cancel()
        CLIENTS.discard(outbox)
        print(
            f"client disconnected: id={client_id}; clients={len(CLIENTS)}", flush=True
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
