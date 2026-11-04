#!/usr/bin/env python3
"""One load test whose conditions change over time, plotted per second.

Schedule: ramp clients up on an unshaped loopback, let them settle, throttle
loopback bandwidth in steps with tc, then remove the limit and keep going.
The plot shows how the server degrades (provisionals coalesced and dropped
before finals suffer, then evictions and HISTORY recovery) and recovers.

    python run_timeline.py run              # in the Ubuntu VM: needs tc and sudo
    python run_timeline.py plot <run dir>   # anywhere with matplotlib

Unlike load_clients.py, the clients here count per second instead of logging
every message, so 5000 of them do not make the client process the bottleneck.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import os
import random
import resource
import subprocess
import sys
import threading
import time
from array import array
from collections import Counter, defaultdict
from pathlib import Path

import websockets
from websockets.exceptions import WebSocketException

from run_demo import terminate_process, wait_for_port

PORT = 8767
URI = f"ws://127.0.0.1:{PORT}"
FRESH_MS = 500
HISTORY_SECONDS = 30

# What happens to each provisional revision, as seen by the client that should
# have received it. The order is the plot's stacking order.
PROVISIONAL_FATES = ("delivered", "coalesced", "dropped", "missed")

COLUMNS = [
    "t", "clients", "lo_mbit", "server_cpu", "client_cpu",
    "connects", "reconnects", "connect_errors", "evict_lag", "evict_backlog",
    "final_live", "final_recovered", *(f"provisional_{f}" for f in PROVISIONAL_FATES),
    "stale", "fresh_pct", "fresh_final_pct",
    "final_p50", "final_p95", "final_p99",
    "provisional_p50", "provisional_p95", "provisional_p99",
]


# Columns left blank when a second has no value for them: no live captions,
# or (second 0) no sample from the sampler yet.
METRICS = {"fresh_pct", "fresh_final_pct", *(f"{k}_p{p}" for k in ("final", "provisional") for p in (50, 95, 99)),
           "clients", "lo_mbit", "server_cpu", "client_cpu"}


class Stats:
    """What all clients received, bucketed by whole seconds since t0."""

    def __init__(self, t0: float) -> None:
        self.t0 = t0
        self.counts: defaultdict[int, Counter] = defaultdict(Counter)
        # (second, "final" | "provisional") -> latencies in ms of live frames
        self.latency: defaultdict[tuple[int, str], array] = defaultdict(lambda: array("f"))
        self.connected = 0

    def second(self, ts: float) -> int:
        return int(ts - self.t0)

    def count(self, ts: float, key: str, n: int = 1) -> None:
        self.counts[self.second(ts)][key] += n


class Client:
    """A client that resumes like load_clients.py, and classifies what became
    of every provisional revision of every segment it finalizes."""

    def __init__(self, client_id: int, stats: Stats) -> None:
        self.id = client_id
        self.stats = stats
        self.last_final = 0  # sent as last_segment on reconnect
        self.provisionals: dict[int, set[int]] = {}  # segment -> revisions received
        self.first_segment: int | None = None  # first live segment on this connection
        self.resumed = False  # this connection is a reconnect

    async def run(self, start_at: float) -> None:
        await asyncio.sleep(start_at - time.time())
        connected_before = False
        while True:
            opened = False
            try:
                async with websockets.connect(URI, max_size=2**20) as ws:
                    opened = True
                    self.resumed = connected_before
                    connected_before = True
                    self.stats.count(time.time(), "reconnects" if self.resumed else "connects")
                    self.first_segment = None
                    self.stats.connected += 1
                    try:
                        await ws.send(json.dumps({
                            "type": "CLIENT_HELLO",
                            "client_id": self.id,
                            "last_segment": self.last_final,
                            "history_seconds": HISTORY_SECONDS,
                        }))
                        async for raw in ws:
                            self.on_frame(raw)
                    finally:
                        self.stats.connected -= 1
            except (OSError, asyncio.TimeoutError, WebSocketException):
                if not opened:
                    self.stats.count(time.time(), "connect_errors")
            # Jittered, so that clients evicted together do not reconnect together.
            await asyncio.sleep(random.uniform(0.2, 0.6))

    def on_frame(self, raw: str) -> None:
        now = time.time()
        msg = json.loads(raw)
        if msg.get("type") == "HISTORY":
            for item in msg["items"]:
                if item["segment"] > self.last_final:
                    if self.resumed:
                        self.stats.count(now, "final_recovered")
                    self.finalize(item, now, sent_while_away=True)
            return

        segment, revision = msg["segment"], msg["revision"]
        received = self.provisionals.get(segment, ())
        if segment <= self.last_final or revision <= max(received, default=0):
            self.stats.count(now, "stale")
            return
        if self.first_segment is None:
            self.first_segment = segment

        kind = "final" if msg["final"] else "provisional"
        latency_ms = (now - msg["source_ts"]) * 1000
        self.stats.latency[(self.stats.second(now), kind)].append(latency_ms)
        if msg["final"]:
            self.stats.count(now, "final_live")
            # Some of the first segment's provisionals may predate this connection.
            self.finalize(msg, now, sent_while_away=segment == self.first_segment)
        else:
            self.stats.count(now, "provisional_delivered")
            self.provisionals.setdefault(segment, set()).add(revision)

    def finalize(self, final: dict, now: float, sent_while_away: bool) -> None:
        """Record a final, and count the provisionals of its segment that never
        arrived.

        The source numbers revisions from 1 and the final is the last, so the
        provisionals are 1 .. final-1. A missing one below the newest received
        was coalesced; one above it was dropped when the final erased it.

        If some were sent while the client was not connected, the missing ones
        count as missed, but only on a reconnect: on the first connection they
        were sent before the client existed, and the HISTORY frame is context.
        """
        segment = final["segment"]
        expected = final["revision"] - 1
        received = self.provisionals.pop(segment, set())
        newest = max(received, default=0)
        self.last_final = segment
        if not sent_while_away:
            self.stats.count(now, "provisional_coalesced", newest - len(received))
            self.stats.count(now, "provisional_dropped", expected - newest)
        elif self.resumed:
            self.stats.count(now, "provisional_missed", expected - len(received))


def build_schedule(args) -> tuple[list[tuple[float, str, float | None, list[str] | None]], float]:
    """Return [(time, label, limit Mbit/s or None, tc command or None)] and the end time."""
    schedule = [(0.0, f"ramp {args.start_clients}->{args.clients} clients", None, None)]
    t = args.ramp_seconds + args.settle_seconds
    for i, rate in enumerate(args.rates):
        # tbf needs a burst of at least rate/HZ; 10 ms of traffic covers that.
        burst = max(4096, int(rate * 1e6 / 8 * 0.010))
        tc = ["tc", "qdisc", "add" if i == 0 else "change", "dev", "lo", "root",
              "tbf", "rate", f"{rate:g}mbit", "burst", str(burst), "latency", "400ms"]
        schedule.append((t, f"{rate:g} Mbit/s", rate, tc))
        t += args.step_seconds
    schedule.append((t, "limit removed", None, ["tc", "qdisc", "del", "dev", "lo", "root"]))
    return schedule, t + args.recover_seconds


def cpu_seconds(pid: int) -> float:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")  # utime + stime


def lo_bytes() -> int:
    return int(Path("/sys/class/net/lo/statistics/tx_bytes").read_text())


async def sample(stats: Stats, server_pid: int, end: float, rows: dict[int, dict]) -> None:
    """Once a second, record connected clients, loopback throughput and CPU."""
    prev = (time.time(), lo_bytes(), cpu_seconds(server_pid), cpu_seconds(os.getpid()))
    while time.time() < end:
        await asyncio.sleep(1)
        cur = (time.time(), lo_bytes(), cpu_seconds(server_pid), cpu_seconds(os.getpid()))
        dt = cur[0] - prev[0]
        rows[stats.second(cur[0])] = {
            "clients": stats.connected,
            "lo_mbit": (cur[1] - prev[1]) * 8 / dt / 1e6,
            "server_cpu": 100 * (cur[2] - prev[2]) / dt,
            "client_cpu": 100 * (cur[3] - prev[3]) / dt,
        }
        prev = cur


async def follow_schedule(schedule, t0: float) -> None:
    for at, label, _, tc in schedule:
        await asyncio.sleep(t0 + at - time.time())
        print(f"[{at:5.0f}s] {label}", flush=True)
        if tc:
            subprocess.run(["sudo", *tc], check=True)


async def run_async(args, out: Path, server_pid: int):
    schedule, duration = build_schedule(args)
    t0 = time.time()
    stats = Stats(t0)
    end = t0 + duration
    rows: dict[int, dict] = {}

    with open(out / "source.log", "w") as source_log:
        source = await asyncio.create_subprocess_exec(
            sys.executable, "-u", "caption_source.py", "--uri", URI,
            "--duration", str(duration), "--quiet",
            stdout=source_log, stderr=subprocess.STDOUT,
        )
        added = args.clients - args.start_clients
        clients = [
            asyncio.create_task(Client(i, stats).run(
                t0 + args.ramp_seconds * max(0, i - args.start_clients) / max(1, added)
            ))
            for i in range(args.clients)
        ]
        await asyncio.gather(sample(stats, server_pid, end, rows), follow_schedule(schedule, t0))
        for task in clients:
            task.cancel()
        await asyncio.gather(*clients, return_exceptions=True)
        try:
            await asyncio.wait_for(source.wait(), 5)
        except asyncio.TimeoutError:
            source.terminate()
    return stats, rows, schedule


def log_with_timestamps(pipe, path: Path) -> None:
    with open(path, "w") as f:
        for line in pipe:
            f.write(f"{time.time():.3f} {line}")


def count_evictions(stats: Stats, server_log: Path) -> None:
    for line in server_log.read_text().splitlines():
        ts, _, text = line.partition(" ")
        if text.startswith("evicting client: lag"):
            stats.count(float(ts), "evict_lag")
        elif text.startswith("evicting client:"):
            stats.count(float(ts), "evict_backlog")


def percentile(sorted_xs, p: float) -> float | str:
    if not sorted_xs:
        return ""
    return sorted_xs[min(len(sorted_xs) - 1, int(p / 100 * len(sorted_xs)))]


def write_results(out: Path, stats: Stats, rows: dict[int, dict], schedule) -> None:
    with open(out / "events.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t", "label", "limit_mbit"])
        for at, label, limit, _ in schedule:
            w.writerow([at, label, "" if limit is None else limit])

    with open(out / "timeline.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        # Counted seconds too, not only sampled ones: the sampler's first row is
        # second 1, and second 0 holds the first connects.
        for s in sorted(set(rows) | set(stats.counts)):
            row = {"t": s, **rows.get(s, {}), **{k: stats.counts[s][k] for k in COLUMNS if k in stats.counts[s]}}
            finals = sorted(stats.latency[(s, "final")])
            provisionals = sorted(stats.latency[(s, "provisional")])
            live = len(finals) + len(provisionals)
            if live:
                fresh = sum(x <= FRESH_MS for x in finals) + sum(x <= FRESH_MS for x in provisionals)
                row["fresh_pct"] = 100 * fresh / live
            if finals:
                row["fresh_final_pct"] = 100 * sum(x <= FRESH_MS for x in finals) / len(finals)
            for kind, xs in (("final", finals), ("provisional", provisionals)):
                for p in (50, 95, 99):
                    row[f"{kind}_p{p}"] = percentile(xs, p)
            w.writerow({k: row.get(k, "" if k in METRICS else 0) for k in COLUMNS})

    totals = sum(stats.counts.values(), Counter())
    print("\nTotals:", flush=True)
    for key in ("connects", "reconnects", "connect_errors", "evict_lag", "evict_backlog",
                "final_live", "final_recovered", "stale",
                *(f"provisional_{f}" for f in PROVISIONAL_FATES)):
        print(f"  {key:24s} {totals[key]}", flush=True)


def run(args) -> None:
    out = Path("results") / f"timeline-{int(time.time())}"
    out.mkdir(parents=True)
    (out / "config.json").write_text(json.dumps(vars(args), indent=2))

    # One socket per client, plus the server's sockets in this VM.
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    reset = ["sudo", "tc", "qdisc", "del", "dev", "lo", "root"]
    subprocess.run(reset, stderr=subprocess.DEVNULL)

    server = subprocess.Popen(
        [sys.executable, "-u", "solution_server.py", "--port", str(PORT)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    logger = threading.Thread(target=log_with_timestamps, args=(server.stdout, out / "server.log"))
    logger.start()
    try:
        wait_for_port(PORT, server)
        stats, rows, schedule = asyncio.run(run_async(args, out, server.pid))
    finally:
        subprocess.run(reset, stderr=subprocess.DEVNULL)
        terminate_process(server, "solution_server.py")
        logger.join()

    count_evictions(stats, out / "server.log")
    write_results(out, stats, rows, schedule)
    print(f"\nResults in {out}", flush=True)
    try:
        plot(out)
    except ImportError:
        print(f"matplotlib not installed here; run: python run_timeline.py plot {out}")


def plot(out: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    with open(out / "timeline.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    with open(out / "events.csv", newline="") as f:
        events = [(float(e["t"]), e["label"], float(e["limit_mbit"]) if e["limit_mbit"] else None)
                  for e in csv.DictReader(f)]

    def col(key: str) -> list[float]:
        return [float(r[key]) if r[key] != "" else math.nan for r in rows]

    def rate(key: str) -> list[float]:
        """A per-second count, averaged over 3 s: a segment takes 0.75 s, so
        1 s buckets alternate between 1 and 2 segments."""
        xs = col(key)
        return [sum(xs[max(0, i - 1):i + 2]) / len(xs[max(0, i - 1):i + 2]) for i in range(len(xs))]

    t = col("t")
    limit = []
    for x in t:
        current = [lim for at, _, lim in events if at <= x]
        limit.append(current[-1] if current and current[-1] is not None else math.nan)

    fig, axes = plt.subplots(8, 1, sharex=True, figsize=(13, 26), constrained_layout=True)
    ax = iter(axes)

    a = next(ax)
    a.plot(t, col("clients"), color="tab:blue")
    a.set_ylabel("connected clients")
    for at, label, _ in events:
        a.annotate(label, (at, 1.02), xycoords=("data", "axes fraction"), fontsize=9, rotation=20)

    a = next(ax)
    a.plot(t, col("lo_mbit"), label="loopback throughput")
    a.step(t, limit, where="post", color="black", ls="--", label="tbf limit")
    a.set_ylabel("Mbit/s")
    a.legend(loc="upper left")

    a = next(ax)
    for kind, style in (("final", "-"), ("provisional", "--")):
        for p, color in ((50, "tab:green"), (95, "tab:orange"), (99, "tab:red")):
            a.plot(t, col(f"{kind}_p{p}"), ls=style, color=color, label=f"{kind} p{p}")
    a.set_yscale("log")
    a.set_ylabel("live caption age (ms)")
    a.legend(loc="upper left", ncol=2, fontsize=8)

    a = next(ax)
    a.plot(t, col("fresh_pct"), label="all live captions")
    a.plot(t, col("fresh_final_pct"), label="finals")
    a.set_ylim(-2, 102)
    a.set_ylabel(f"fresh (<= {FRESH_MS} ms) %")
    a.legend(loc="lower left")

    a = next(ax)
    a.stackplot(t, *(rate(f"provisional_{f}") for f in PROVISIONAL_FATES), labels=PROVISIONAL_FATES,
                colors=("tab:green", "tab:orange", "tab:red", "tab:gray"))
    a.set_ylabel("provisionals / s (3 s avg)")
    a.legend(loc="upper left")

    a = next(ax)
    a.stackplot(t, rate("final_live"), rate("final_recovered"), labels=("live", "recovered via HISTORY"),
                colors=("tab:blue", "tab:purple"))
    a.set_ylabel("finals / s (3 s avg)")
    a.legend(loc="upper left")

    a = next(ax)
    a.bar(t, col("evict_backlog"), width=1, label="evicted: pending finals", color="tab:red")
    a.bar(t, col("evict_lag"), width=1, bottom=col("evict_backlog"), label="evicted: lag", color="tab:orange")
    a.plot(t, col("reconnects"), color="tab:purple", label="reconnects")
    a.plot(t, col("connect_errors"), color="black", label="connect errors")
    a.set_ylabel("per second")
    a.legend(loc="upper left")

    a = next(ax)
    a.plot(t, col("server_cpu"), label="server")
    a.plot(t, col("client_cpu"), label="clients")
    a.set_ylabel("CPU % (100 = one core)")
    a.set_xlabel("seconds since start")
    a.legend(loc="upper left")

    for a in axes:
        a.grid(alpha=0.3)
        for at, _, _ in events:
            a.axvline(at, color="gray", ls=":", lw=1)
    fig.savefig(out / "timeline.png", dpi=110)
    print(f"Plot written to {out / 'timeline.png'}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--start-clients", type=int, default=100)
    r.add_argument("--clients", type=int, default=5000)
    r.add_argument("--ramp-seconds", type=float, default=90)
    r.add_argument("--settle-seconds", type=float, default=30)
    r.add_argument("--rates", type=lambda s: [float(x) for x in s.split(",")], default=[40, 20, 10, 5],
                   help="Comma-separated loopback limits in Mbit/s, applied in turn")
    r.add_argument("--step-seconds", type=float, default=40)
    r.add_argument("--recover-seconds", type=float, default=60)
    pl = sub.add_parser("plot")
    pl.add_argument("run_dir", type=Path)
    args = p.parse_args()

    if args.command == "run":
        run(args)
    else:
        plot(args.run_dir)


if __name__ == "__main__":
    main()
