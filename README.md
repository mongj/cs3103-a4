# Team [8] - LiveRecall

## Team members


| Name           | Student ID | Main responsibility                                                                                                                                                                                                                                                  |
| -------------- | ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Zhang Ming Jun | A0282190H  | Browser client (revision replacement, recall UI, reconnect/offline toggle), server revision-state and history logic (accept(), missed_finals()), test infrastructure (run_demo.py, run_timeline.py), and server refinements (ping/lag handling, exception handling). |
| Cheng Lui Leng | A0281547X  | Server-side networking, the ClientOutbox class, per-client coalescing queues, slow-client isolation, and backpressure mechanism. Also implemented the optional ASR bonus feature.                                                                                    |


---



## Ubuntu setup

These instructions assume a fresh Ubuntu 22.04/24.04 environment. The server and
simulated clients must run inside the same Ubuntu VM for the `tc/netem`
network-impairment experiments.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iproute2 nodejs npm
npm install -g yarn
```

Verify:

```bash
python3 --version   # 3.10 or newer
tc -V
```



### Using Multipass (macOS / Windows host)

```bash
multipass launch --name liverecall --cpus 2 --memory 4G --disk 15G
multipass shell liverecall
# then run the apt commands above inside the VM
```

---



## Install

Clone or unzip the submission, then inside the project root:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Verify the server starts and listens:

```bash
python submission_check.py
```

---



## Required server

```bash
python solution_server.py --port 8765
```

The server binds to `0.0.0.0:<port>` and is ready within a few seconds. It
accepts connections from both caption sources and browser/load-test clients on
the same port.

---



## Browser client

In a second terminal, install and start the Vite dev server:

```bash
corepack enable
cd client
yarn install
yarn dev
```

Then open `http://127.0.0.1:8000` in a browser. The client connects automatically
to `ws://127.0.0.1:8765`.

> Note: `yarn dev` must stay running in the terminal while you use the browser.
> If port 8000 is already in use, free it first: `lsof -ti:8000 | xargs kill -9`

---



## Public deterministic tests

Run each test at least three times (the `--runs` flag restarts the server for
each run and reports the median).

### T1 — Basic correctness (20 clients, 20 s)

```bash
python run_demo.py --clients 20 --duration 20 --runs 3 --output-stem t1
```



### T2 — Public scale (100 clients, 40 s)

```bash
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem t2
```



### T3 — Slow clients (100 clients, 10 % slow at 500 ms)

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 \
  --slow-percent 10 --slow-ms 500 --output-stem t3
```



### T4 — Reconnect (100 clients, 20 % temporarily disconnected)

```bash
python run_demo.py --clients 100 --duration 60 --runs 3 \
  --disconnect-percent 20 --disconnect-at 15 --disconnect-seconds 5 \
  --output-stem t4
```



### T5 — Network impairment (tc/netem, Ubuntu only)

Apply impairment to the loopback interface before running T2 (requires root):

```bash
# example: 50 ms delay + 10 ms jitter + 1 % loss
sudo bash network_profiles/delay_loss.sh lo
python run_demo.py --clients 100 --duration 40 --runs 3 --output-stem t5_impaired
sudo bash network_profiles/reset.sh lo
```

Other profiles in `network_profiles/`: `delay_100ms.sh`, `loss_3pct.sh`,
`bandwidth_2mbit.sh`, `reorder.sh`.

---



## Mandatory deterministic application demo

Open **two** browser tabs at `http://127.0.0.1:8000`, then in a terminal:

```bash
python caption_source.py --duration 120
```

**What to demonstrate:**

1. **Live captions** — provisional captions appear in gray italic; each segment
  transitions to black bold when finalised.
2. **Revision replacement** — newer revisions of the same segment replace the
  line in-place; no duplicate lines appear.
3. **What did I miss?** — click "What did I miss? (30 s)" for the default view;
  click "15 s" or "60 s" for other windows. Under normal operation the panel
   reads from the client's local history without a server round-trip.
4. **Disconnect / reconnect** — click "Go offline" on one tab; wait 5–10 s;
  click "Go online". The status label shows "reconnecting" then "connected";
   only missed finals appear (no duplicates).
5. **Late join** — open a new browser tab while `caption_source.py` is running.
  The new client receives recent context immediately and then continues with
   live updates.

---



## Design notes



### Revision and deduplication state

The server maintains a single global `LATEST` variable (the highest-ranked
accepted caption) and rejects any incoming `CAPTION_UPDATE` whose
`(segment, final, revision)` tuple is not strictly greater. This prevents stale
or duplicate captions from reaching clients. Each `ClientOutbox` also guards
against regression: a final for segment N erases any pending provisional for N
before it is sent.

### Bounded server history

`FINALS` is a `deque` of all accepted final captions within the last 90 seconds
(`HISTORY_WINDOW_SECONDS = 90`). Entries older than the window are removed
whenever a new final arrives. This exceeds the 60-second recovery requirement
while remaining finite regardless of lecture length.

### Reconnect and late-join recovery

On `CLIENT_HELLO` the server calls `missed_finals()`:

- **Resuming client** (`last_segment > 0`): returns every final with
`segment > last_segment`, ignoring the time cutoff. This prevents a permanent
gap for a client that was offline for exactly the history window.
- **New / late-join client** (`last_segment = 0`): returns finals within the
requested `history_seconds` window.

The HISTORY frame is queued before the client is added to `CLIENTS`, so every
subsequent final appears in exactly one of the two.

### Per-client buffering and backpressure

Each client has a `ClientOutbox` with two structures:

- `finals` — a `deque` of serialised JSON strings, sent in FIFO order, never
dropped. Capped at `MAX_PENDING_FINALS = 256`; eviction triggers if this is
exceeded.
- `provisionals` — a `dict[segment → newest raw JSON]`. A newer revision
replaces the pending one (coalescing), and a final for the same segment
removes it (dropped). Only one provisional per active segment is ever pending.

Lag is measured by WebSocket pings. The client's library answers a ping only
after draining every frame sent before it, so the round-trip time minus the
network baseline (`rtt_min`) gives the true read-backlog delay. When lag exceeds
`LAG_TARGET_SECONDS = 0.5`, provisional sends are paused (backoff). When lag
exceeds `EVICT_LAG_SECONDS = 10`, the client is evicted via `transport.abort()`
so it reconnects cleanly through HISTORY instead of draining a stale queue.

The kernel send buffer (`SO_SNDBUF = 4 KB`) and websockets write limit (1 KB)
are kept small so that bandwidth-constrained sockets exert backpressure on the
application layer quickly, causing new provisionals to coalesce in the outbox
rather than queue stale revisions.

### Overload behaviour and trade-offs

Under overload (lag-driven backoff or bandwidth constraint), obsolete provisional
revisions are coalesced: only the newest revision of each active segment is
retained. Finals are always preserved and sent first. Under normal conditions
(`backoff = 0`, `lag ≤ LAG_TARGET`), all revisions are forwarded immediately,
satisfying the Normal Update Coverage requirement. The overload policy therefore
activates only under genuine backpressure and returns to full-coverage behaviour
when the client catches up.

---



## Optional ASR bonus

Transcribes speech from a video file or live microphone using
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and feeds captions
into the same `solution_server.py` and browser client used for the mandatory
assignment. No changes to the server or client are required.

### Setup

Install system dependencies separately (do **not** add them to `requirements.txt`):

```bash
# macOS
brew install ffmpeg portaudio

# Ubuntu
sudo apt install -y ffmpeg portaudio19-dev
```

Install Python packages:

```bash
pip install -r bonus_asr/requirements_asr.txt
```

The first run downloads the Whisper model (~150 MB for `tiny`, ~300 MB for `base`).

### Usage

Start the server first, then run the ASR source:

```bash
# video file (paced to real-time playback speed)
python bonus_asr/asr_source.py --port 8765 --video lecture.mp4

# live microphone
python bonus_asr/asr_source.py --port 8765 --mic
```

Then open `http://127.0.0.1:8000` in a browser as normal.

| Flag | Default | Description |
|---|---|---|
| `--model` | `tiny` | Whisper model: `tiny`, `base`, `small`, `medium` |
| `--language` | auto | Force language, e.g. `en` |
| `--port` | `8765` | Server port |

### How it works

Each 4-second audio chunk is transcribed by faster-whisper with word-level
timestamps. For each detected sentence, words are sent one-by-one as provisional
`CAPTION_UPDATE` frames (revision increments per word), then the complete sentence
is sent as a final `CAPTION_UPDATE`. In video mode, sends are paced to match real
audio timing so captions appear in sync with the video.

### Limitations

- **Latency:** faster-whisper processes audio in 4-second chunks, so there is an
  inherent ~4-second delay before each chunk's captions appear.
- **Accuracy:** the `tiny` model trades accuracy for speed. Use `--model base` or
  `--model small` for better results at the cost of higher CPU usage.
- **Language:** auto-detection works well for English; use `--language en` to
  force it and avoid detection overhead.
- **Microphone device:** uses the system default input device. Change it via your
  OS sound settings if needed.