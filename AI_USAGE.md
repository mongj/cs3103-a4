# AI / Coding-Assistant Usage

## Tools used

| Tool | Used for |
|---|---|
| Claude Code (CLI) | Server architecture design; `ClientOutbox` class and backpressure logic; ASR bonus feature (`asr_source.py`); debugging async timing issues; test-result analysis |

## One useful suggestion

**Suggestion:** Use a two-window sliding minimum for `rtt_min` instead of a plain running minimum.

Claude Code pointed out that a simple running minimum for the ping round-trip baseline would cause a client whose network latency *increases* permanently (e.g., handover from a fast to a slow link) to appear perpetually lagged, since every subsequent RTT would exceed the historic minimum. The fix was `_update_rtt_min` (`solution_server.py:222–229`), which keeps the minimum over the *previous* and *current* fixed-length windows (`RTT_MIN_WINDOW_SECONDS = 5 s`). After one window the stale minimum is replaced, so a lasting latency rise becomes the new baseline.

**Verification:** T3 (100 clients, 10 % slow at 500 ms) produces zero false evictions — slow clients are backpressured but not disconnected, confirming the baseline adapts correctly.

## One suggestion that was wrong, inefficient, or required modification

**Original suggestion (paraphrased):** To evict a slow client, call `await ws.close()`.

**Problem:** `websockets.close()` performs a clean WebSocket closing handshake, which means the server must drain the *entire* backlog through the connection before it closes — defeating the purpose of eviction. A client with 256 queued finals would hold the event loop until all of them flushed.

**Generated fragment:**
```python
async def evict(self, reason: str) -> None:
    self.close()
    await self.ws.close()   # wrong: drains the backlog first
```

**Correction:** Replace with `ws.transport.abort()` (`solution_server.py:263`), which tears down the TCP connection immediately. The client's reconnect logic fires and recovers cleanly via the HISTORY frame. The `evict()` docstring now explains this: *"Aborting the TCP connection discards its backlog at once, where a close handshake would wait behind it."*

## Responsibility statement

We understand that the team remains responsible for the submitted code, experimental results, and claims, and that every member may be asked to explain or modify relevant code during the viva.
