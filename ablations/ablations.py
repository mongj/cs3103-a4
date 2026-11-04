#!/usr/bin/env python3
"""Leave-one-out ablations of solution_server.py, swept over an independent
variable each, for the report.

    python ablations/ablations.py build          # host: variant dirs from HEAD
    python ablations/ablations.py run [--reps N] # VM: every job, resumable
    python ablations/ablations.py plot           # host: line graphs

Each variant is HEAD's server with exactly one mechanism removed (see
VARIANTS), plus the starter kit. Every run records run_demo.py's summary,
per-role extras computed from the client CSV, and the server's CPU and peak
memory. A run whose result file exists is skipped, so `run` can be restarted.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import resource
import shutil
import statistics
import subprocess
import sys
import threading
import time
from array import array
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "results" / "ablations"
VARIANT_DIR = OUT / "variants"
DATA = OUT / "data"
PORT = 8780
HARNESS = ["run_demo.py", "load_clients.py", "caption_source.py", "analyse_results.py", "run_timeline.py"]
STARTER_COMMIT = "157b74c"

# name -> (description, [(old, new)]) applied to HEAD's solution_server.py.
VARIANTS: dict[str, tuple[str, list[tuple[str, str]]]] = {
    "head": ("full server (HEAD)", []),
    "b1_no_outbox": ("B1: no per-client outbox; source awaits every send", [
        ("""                if accept(msg):
                    # Forward the source's own frame, so each caption is
                    # serialized once rather than once per client. Iterate
                    # over a copy, as push() may evict a client.
                    for outbox in list(CLIENTS):
                        outbox.push(msg, raw)""",
         """                if accept(msg):
                    # ABLATION B1: no per-client outbox; the source waits for every send.
                    clients = list(CLIENTS)
                    results = await asyncio.gather(
                        *(o.ws.send(raw) for o in clients), return_exceptions=True
                    )
                    for o, r in zip(clients, results):
                        if isinstance(r, Exception):
                            CLIENTS.discard(o)"""),
        ("""    outbox = ClientOutbox(ws)
    outbox.finals.append(json.dumps({"type": "HISTORY", "items": missed_finals(hello)}))
    if LATEST and not LATEST["final"]:
        outbox.push(LATEST, json.dumps(LATEST))
    CLIENTS.add(outbox)

    sender = asyncio.create_task(outbox.send_loop())""",
         """    outbox = ClientOutbox(ws)
    history = json.dumps({"type": "HISTORY", "items": missed_finals(hello)})
    CLIENTS.add(outbox)
    # ABLATION B1: send HISTORY directly. websockets writes the frame before
    # its first await, so no broadcast can come between it and CLIENTS.add.
    await ws.send(history)
    sender = asyncio.create_task(asyncio.sleep(0))"""),
    ]),
    "b2_fifo": ("B2: one FIFO per client; no finals-first, coalescing or dropping", [
        ("""        segment = msg["segment"]
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
        self._wake.set()""",
         """        # ABLATION B2: one FIFO of every caption. The cap is 3x HEAD's, so it
        # holds as many finals (3 revisions per segment).
        self.finals.append(raw)
        if len(self.finals) > 3 * MAX_PENDING_FINALS:
            self.evict(f"over {3 * MAX_PENDING_FINALS} pending frames")
            return
        self._wake.set()"""),
    ]),
    "b3_big_buffers": ("B3: default socket and asyncio send buffers", [
        ("    if sock is not None:\n", "    if False:  # ABLATION B3: keep the kernel's default SO_SNDBUF\n"),
        ("        write_limit=1024,\n", ""),
    ]),
    "b4_no_lag": ("B4: no pings, so no lag measurement, provisional hold or lag eviction", [
        ("    async def _ping_if_due(self) -> None:\n",
         "    async def _ping_if_due(self) -> None:\n        return  # ABLATION B4: never ping, so lag stays 0\n"),
    ]),
    "b5_fixed_ping": ("B5: fixed 250 ms ping interval", [
        ("PING_INTERVAL_MAX_SECONDS = 1.0", "PING_INTERVAL_MAX_SECONDS = 0.25  # ABLATION B5"),
    ]),
    "b6_no_loop_comp": ("B6: no event-loop delay compensation", [
        ("    return max(LOOP_DELAYS)", "    return 0.0  # ABLATION B6"),
    ]),
    "b7_no_evict": ("B7: never evict", [
        ("EVICT_LAG_SECONDS = 10.0", "EVICT_LAG_SECONDS = math.inf  # ABLATION B7"),
        ("MAX_PENDING_FINALS = 256", "MAX_PENDING_FINALS = 10**9  # ABLATION B7"),
    ]),
    "b8_rtt_alltime": ("B8: rtt_min is the minimum ever seen", [
        ("RTT_MIN_WINDOW_SECONDS = 5.0", "RTT_MIN_WINDOW_SECONDS = math.inf  # ABLATION B8"),
    ]),
    "starter": ("starter kit (naive broadcaster)", []),
}

LABELS = {
    "head": "HEAD", "starter": "starter kit",
    "b1_no_outbox": "-B1 outbox", "b2_fifo": "-B2 finals-first/coalesce",
    "b3_big_buffers": "-B3 small buffers", "b4_no_lag": "-B4 lag measurement",
    "b5_fixed_ping": "-B5 adaptive ping", "b6_no_loop_comp": "-B6 loop-delay comp",
    "b7_no_evict": "-B7 eviction", "b8_rtt_alltime": "-B8 windowed rtt_min",
}


def tbf(kbit: int) -> list[list[str]]:
    burst = max(4096, kbit * 1000 // 8 // 100)  # at least 10 ms of traffic
    return [["tc", "qdisc", "add", "dev", "lo", "root", "tbf", "rate", f"{kbit}kbit",
             "burst", str(burst), "latency", "400ms"]]


def netem(delay_ms: int, loss_pct: float, limit: int | None = None) -> list[list[str]]:
    cmd = ["tc", "qdisc", "add", "dev", "lo", "root", "netem"]
    if limit:
        cmd += ["limit", str(limit)]
    if delay_ms:
        cmd += ["delay", f"{delay_ms}ms", f"{delay_ms // 4}ms", "distribution", "normal"]
    if loss_pct:
        cmd += ["loss", f"{loss_pct}%"]
    return [cmd] if any(k in cmd for k in ("delay", "loss")) else []


def netem_step(delay_ms: int, op: str = "add") -> list[list[str]]:
    """Fixed delay, no jitter, so rtt_min is exact; queue far above netem's 1000."""
    return [["tc", "qdisc", op, "dev", "lo", "root", "netem", "limit", "100000", "delay", f"{delay_ms}ms"]]


# Each sweep: one independent variable, the variants it is meant to separate,
# and its points as (x, run_demo args, tc commands).
SWEEPS = {
    "slow_reader": {
        "xlabel": "slow clients' time per message (ms), 10% of 100 clients",
        "variants": ["head", "b1_no_outbox", "b4_no_lag", "b7_no_evict", "starter"],
        "points": [(ms, f"--clients 100 --duration 60 --slow-percent 10 --slow-ms {ms}", [])
                   for ms in (0, 100, 250, 500, 750, 1000, 2000, 3000)],
    },
    "slow_reader_long": {
        "xlabel": "slow clients' time per message (ms), 10% of 100 clients, 180 s",
        "variants": ["head", "b4_no_lag", "b7_no_evict"],
        "reps": 1,  # 180 s runs; long enough for eviction and HISTORY to pay off
        "points": [(ms, f"--clients 100 --duration 180 --slow-percent 10 --slow-ms {ms}", [])
                   for ms in (750, 1000, 1500, 2000)],
    },
    "bandwidth": {
        "xlabel": "loopback bandwidth limit (Mbit/s), 200 clients",
        "xlog": True,
        # b4_no_lag: no pings, so it shows what the pings themselves cost in bandwidth.
        "variants": ["head", "b2_fifo", "b3_big_buffers", "b4_no_lag", "starter"],
        "points": [(64.0, "--clients 200 --duration 40", [])] + [
            (kbit / 1000, "--clients 200 --duration 40", tbf(kbit))
            for kbit in (4000, 2000, 1500, 1000, 750, 500, 350, 250)],
    },
    "scale": {
        "xlabel": "clients (4 messages/s)",
        "variants": ["head", "b5_fixed_ping", "starter"],
        "points": [(n, f"--clients {n} --duration 40", []) for n in (500, 1000, 2500, 5000, 7500, 10000)],
    },
    "rate": {
        "xlabel": "clients at 100 messages/s",
        "variants": ["head", "b6_no_loop_comp", "b5_fixed_ping"],
        "points": [(n, f"--clients {n} --duration 40 --revision-delay 0.01 --segment-gap 0.01", [])
                   for n in (250, 500, 1000, 1500, 2000, 2500, 3000)],
    },
    "loss": {
        "xlabel": "packet loss (%), 120 ms delay, 500 clients",
        "variants": ["head", "b4_no_lag", "b8_rtt_alltime", "starter"],
        "points": [(p, "--clients 500 --duration 40", netem(120, p)) for p in (0, 1, 3, 5, 8)],
    },
    # A route change: one-way delay steps from 20 ms to 20 + x ms at t = 20 s.
    # The windowed rtt_min re-learns the new base RTT; an all-time one (B8)
    # keeps reading the extra 2x ms of round trip as lag. netem's queue is raised
    # from its default 1000 packets, which otherwise drops packets at large RTTs
    # (see NOTES.md, delay_step_netem1000).
    "delay_step": {
        "xlabel": "one-way delay added at t = 20 s (ms), 500 clients, 60 s",
        "variants": ["head", "b8_rtt_alltime"],
        "points": [(ms, "--clients 500 --duration 60", netem_step(20))
                   for ms in (0, 100, 200, 300, 500, 800)],
        "step_at": 20,
        "step": lambda ms: netem_step(20 + ms, "change"),
    },
    # Clients behind a lossy, jittery link with netem's queue raised; compare
    # with the suite's net_delay_loss_* tests, which use netem's default queue.
    "delay_scale": {
        "xlabel": "clients, netem 120 ms +-30 ms, 3% loss, limit 100000",
        "variants": ["head", "starter"],
        "reps": 2,
        "points": [(n, f"--clients {n} --duration 40", netem(120, 3, limit=100000))
                   for n in (100, 500, 1000, 2000)],
    },
}

TIMELINE_VARIANTS = ["head", "b2_fifo", "b4_no_lag", "b7_no_evict"]


# ---------------------------------------------------------------- build (host)

def build() -> None:
    head = (ROOT / "solution_server.py").read_text()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True, check=True).stdout.strip()
    for name, (desc, patches) in VARIANTS.items():
        d = VARIANT_DIR / name
        d.mkdir(parents=True, exist_ok=True)
        if name == "starter":
            src = subprocess.run(["git", "show", f"{STARTER_COMMIT}:solution_server.py"], cwd=ROOT,
                                 capture_output=True, text=True, check=True).stdout
        else:
            src = head
            for old, new in patches:
                assert src.count(old) == 1, f"{name}: patch does not apply once: {old[:60]!r}"
                src = src.replace(old, new)
        (d / "solution_server.py").write_text(src)
        for f in HARNESS:
            shutil.copy(ROOT / f, d / f)
        compile(src, str(d / "solution_server.py"), "exec")
        print(f"built {name:16s} {desc}")
    (OUT / "build.json").write_text(json.dumps({"head_commit": commit, "starter_commit": STARTER_COMMIT,
                                                "variants": {k: v[0] for k, v in VARIANTS.items()}}, indent=2))


# ------------------------------------------------------------------ run (VM)

def sudo(cmd: list[str], check=True) -> None:
    subprocess.run(["sudo", *cmd], check=check, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def reset_tc() -> None:
    sudo(["tc", "qdisc", "del", "dev", "lo", "root"], check=False)


def kill_servers() -> None:
    subprocess.run(["pkill", "-f", f"solution_server.py --port {PORT}"], check=False)
    subprocess.run(["pkill", "-f", f"load_clients.py --uri ws://127.0.0.1:{PORT}"], check=False)


def proc_cpu_seconds(pid: str, tick: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    except OSError:
        return None
    return (int(stat[11]) + int(stat[12])) / tick


def proc_rss_mb(pid: str) -> tuple[float, float]:
    """(current, peak) resident memory in MB."""
    cur = peak = 0.0
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                cur = int(line.split()[1]) / 1024
            elif line.startswith("VmHWM:"):
                peak = int(line.split()[1]) / 1024
    except OSError:
        pass
    return cur, peak


class ProcSampler(threading.Thread):
    """Once a second: server CPU and memory, and the client process's CPU,
    like run_timeline.py's sampler. Also the whole-run server averages."""

    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.series: list[dict] = []
        self.first = self.last = None  # (time, server cpu seconds)
        self.peak_mb = 0.0

    @staticmethod
    def pid(pattern: str) -> str | None:
        pids = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
        return pids[0] if pids else None

    def run(self) -> None:
        tick = os.sysconf("SC_CLK_TCK")
        prev: dict[str, tuple[float, float]] = {}
        while not self.stop.wait(1.0):
            now = time.time()
            row = {"ts": now}
            for role, pattern in (("server", f"solution_server.py --port {PORT}"),
                                  ("client", f"load_clients.py --uri ws://127.0.0.1:{PORT}")):
                pid = self.pid(pattern)
                cpu = proc_cpu_seconds(pid, tick) if pid else None
                if cpu is None:
                    continue
                key = f"{role}:{pid}"
                if key in prev:
                    t0, c0 = prev[key]
                    row[f"{role}_cpu_pct"] = 100 * (cpu - c0) / (now - t0)
                prev[key] = (now, cpu)
                if role == "server":
                    rss, peak = proc_rss_mb(pid)
                    row["server_rss_mb"] = rss
                    self.peak_mb = max(self.peak_mb, peak)
                    self.first = self.first or (now, cpu)
                    self.last = (now, cpu)
            if len(row) > 1:
                self.series.append(row)

    def result(self) -> dict:
        cpu = None
        if self.first and self.last and self.last[0] > self.first[0]:
            cpu = 100 * (self.last[1] - self.first[1]) / (self.last[0] - self.first[0])
        client = [r["client_cpu_pct"] for r in self.series if "client_cpu_pct" in r]
        return {"server_cpu_pct": cpu, "server_peak_rss_mb": self.peak_mb,
                "client_cpu_pct": statistics.mean(client) if client else None,
                "proc_series": self.series}


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100 * len(xs)))]


def csv_extras(csv_path: Path, segments: int) -> dict:
    """From the client CSV: per role (slow/normal) finals completeness, live
    final age and reconnects; totals of captions read; and a per-second
    series of captions read, recoveries, connects, age and freshness."""
    finals = defaultdict(set)
    role = {}
    connects = defaultdict(int)
    final_age = {"0": array("f"), "1": array("f")}
    sec = defaultdict(lambda: {"final": 0, "provisional": 0, "recovered": 0, "connects": 0,
                               "ages": array("f"), "final_ages": array("f")})
    totals = {"finals_read": 0, "provisionals_read": 0, "history_items": 0, "connects": 0}
    t0 = None
    with open(csv_path, newline="") as f:
        for r in csv.DictReader(f):
            cid = r["client_id"]
            role[cid] = r["slow"]
            ts = float(r["receive_ts"]) if r["receive_ts"] else None
            if ts is not None:
                t0 = ts if t0 is None else min(t0, ts)
            b = sec[int(ts)] if ts is not None else None
            kind = r["event_type"]
            if kind == "CONNECT":
                connects[cid] += 1
                totals["connects"] += 1
                if b:
                    b["connects"] += 1
            elif kind == "HISTORY_ITEM":
                totals["history_items"] += 1
                if b and r["status"] == "recovered":
                    b["recovered"] += 1
            elif kind == "CAPTION_UPDATE" and r["status"] == "accepted":
                age = float(r["latency_ms"])
                if r["final"] == "1":
                    totals["finals_read"] += 1
                    final_age[r["slow"]].append(age)
                    b["final"] += 1
                    b["final_ages"].append(age)
                else:
                    totals["provisionals_read"] += 1
                    b["provisional"] += 1
                b["ages"].append(age)
            if r["final"] == "1" and r["status"] in ("accepted", "recovered"):
                finals[cid].add(r["segment"])
    out = {"totals": totals, "roles": {}}
    for flag, name in (("0", "normal"), ("1", "slow")):
        ids = [c for c, s in role.items() if s == flag]
        if not ids:
            continue
        ages = final_age[flag]
        out["roles"][name] = {
            "clients": len(ids),
            "final_completeness_pct": 100 * sum(len(finals[c]) for c in ids) / (segments * len(ids)),
            "final_age_p50_ms": percentile(ages, 50),
            "final_age_p95_ms": percentile(ages, 95),
            "fresh_final_pct": 100 * sum(x <= 500 for x in ages) / len(ages) if ages else None,
            "reconnects": sum(connects[c] for c in ids) - len(ids),
        }
    series = []
    for s in sorted(sec):
        b = sec[s]
        series.append({
            "t": s - int(t0), "ts": s, "finals": b["final"], "provisionals": b["provisional"],
            "recovered": b["recovered"], "connects": b["connects"],
            "age_p50_ms": percentile(b["ages"], 50), "age_p95_ms": percentile(b["ages"], 95),
            "final_age_p95_ms": percentile(b["final_ages"], 95),
            "fresh_pct": 100 * sum(x <= 500 for x in b["ages"]) / len(b["ages"]) if b["ages"] else None,
        })
    out["series"] = series
    return out


def result_path(sweep: str, variant: str, x, rep: int) -> Path:
    # No dots in the name: Path.with_suffix() would cut "0.75__rep1" at the dot.
    return DATA / sweep / variant / f"{str(x).replace('.', 'p')}__rep{rep}"


def run_point(sweep: str, variant: str, x, args: str, tc: list[list[str]], rep: int) -> None:
    stem = result_path(sweep, variant, x, rep)
    if stem.with_suffix(".result.json").exists():
        return
    stem.parent.mkdir(parents=True, exist_ok=True)
    duration = float(args.split("--duration ")[1].split()[0])
    reset_tc()
    kill_servers()
    for cmd in tc:
        sudo(cmd)
    sampler = ProcSampler()
    sampler.start()
    start = time.monotonic()
    record = {"sweep": sweep, "variant": variant, "x": x, "rep": rep, "args": args,
              "tc": tc, "started": time.time()}
    try:
        cfg = SWEEPS.get(sweep, {})
        step = None
        if "step" in cfg:
            # run_demo.py spends a few seconds starting clients; close enough.
            step = threading.Timer(cfg["step_at"], lambda: [sudo(c, check=False) for c in cfg["step"](x)])
            step.start()
            record["step"] = {"at": cfg["step_at"], "tc": cfg["step"](x)}
        try:
            with open(stem.with_suffix(".log"), "w") as log:
                proc = subprocess.run(
                    [sys.executable, "run_demo.py", "--port", str(PORT), "--quiet-server",
                     "--output-stem", str(stem), *args.split()],
                    cwd=VARIANT_DIR / variant, stdout=log, stderr=subprocess.STDOUT,
                    timeout=duration * 4 + 600,
                )
        finally:
            if step:
                step.cancel()
        record["returncode"] = proc.returncode
    except subprocess.TimeoutExpired:
        record["returncode"] = "timeout"
    finally:
        sampler.stop.set()
        sampler.join()
        reset_tc()
        kill_servers()
    record["wall_s"] = time.monotonic() - start
    record.update(sampler.result())
    summary_file = stem.with_suffix(".json")
    csv_file = stem.with_suffix(".csv")
    if summary_file.exists():
        record["summary"] = json.loads(summary_file.read_text())
        if csv_file.exists():
            segments = record["summary"]["expected_finals_total"] // max(1, record["summary"]["clients"])
            extras = csv_extras(csv_file, segments)
            record["roles"] = extras["roles"]
            record["totals"] = extras["totals"]
            record["series"] = extras["series"]
    if csv_file.exists() and csv_file.stat().st_size > 30 * 2**20:
        csv_file.unlink()  # the large ones are only needed for the summaries above
    stem.with_suffix(".result.json").write_text(json.dumps(record, indent=2))
    s = record.get("summary", {})
    print(f"{time.strftime('%H:%M:%S')} {sweep:12s} {variant:16s} x={x!s:7s} rep={rep} "
          f"rc={record['returncode']} wall={record['wall_s']:.0f}s finals={s.get('final_completeness_pct', float('nan')):.2f}% "
          f"p50={s.get('p50_ms', float('nan')):.0f} fresh={s.get('fresh_pct', float('nan')):.1f}% "
          f"cpu={record['server_cpu_pct'] or 0:.0f}% rss={record['server_peak_rss_mb']:.0f}MB", flush=True)


def run_timeline(variant: str) -> None:
    dest = OUT / "timelines" / variant
    if (dest / "timeline.csv").exists():
        return
    reset_tc()
    kill_servers()
    d = VARIANT_DIR / variant
    subprocess.run([sys.executable, "run_timeline.py", "run"], cwd=d, check=False,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1800)
    reset_tc()
    runs = sorted((d / "results").glob("timeline-*"))
    if runs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(runs[-1]), dest)
    print(f"{time.strftime('%H:%M:%S')} timeline {variant} -> {dest}", flush=True)


def run_suite(variant: str) -> None:
    """Every test in tests.json, once, against one variant."""
    tests = json.loads((ROOT / "tests.json").read_text())
    for test_id, test in tests.items():
        tc = []
        if test.get("network_profile"):
            tc = [["bash", str(ROOT / "network_profiles" / f"{test['network_profile']}.sh"), "lo"]]
        run_point("suite", variant, test_id, test["args"], tc, 1)


def wait_until_idle() -> None:
    """Do not overlap with run_tests.sh (port 8766) or a timeline (8767)."""
    while subprocess.run(["pgrep", "-f", "port 876[67]"], capture_output=True).returncode == 0:
        time.sleep(30)


def run(reps: int) -> None:
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    wait_until_idle()
    print(f"{time.strftime('%H:%M:%S')} starting", flush=True)

    def sweep_jobs(rep):
        # Variants alternate within each point, so slow drift affects them alike.
        for name, sweep in SWEEPS.items():
            if rep > sweep.get("reps", reps):
                continue
            for x, args, tc in sweep["points"]:
                for variant in sweep["variants"]:
                    yield name, variant, x, args, tc, rep

    # Priority: one pass of every sweep, then timelines and the starter suite,
    # then further passes for variance.
    for job in sweep_jobs(1):
        run_point(*job)
    for variant in TIMELINE_VARIANTS:
        run_timeline(variant)
    run_suite("starter")
    for rep in range(2, reps + 1):
        for job in sweep_jobs(rep):
            run_point(*job)
    print(f"{time.strftime('%H:%M:%S')} done", flush=True)


# ---------------------------------------------------------------- plot (host)

def load(sweep: str) -> dict[str, dict]:
    """variant -> x -> [records]"""
    out: dict[str, dict] = defaultdict(lambda: defaultdict(list))
    for f in (DATA / sweep).glob("*/*.result.json"):
        r = json.loads(f.read_text())
        if "summary" in r:
            out[r["variant"]][r["x"]].append(r)
    return out


def get(r: dict, path: str):
    cur = r
    for k in path.split("."):
        if not isinstance(cur, dict) or cur.get(k) is None:
            return None
        cur = cur[k]
    return cur


COLORS = {"head": "black", "starter": "tab:gray", "b1_no_outbox": "tab:red", "b2_fifo": "tab:orange",
          "b3_big_buffers": "tab:brown", "b4_no_lag": "tab:blue", "b5_fixed_ping": "tab:purple",
          "b6_no_loop_comp": "tab:green", "b7_no_evict": "tab:pink", "b8_rtt_alltime": "tab:olive"}

PLOTS = {
    "slow_reader": [
        ("summary.normal_clients.p95_ms", "normal clients: p95 caption age (ms)", True),
        ("summary.normal_clients.fresh_pct", "normal clients: fresh (%)", False),
        ("roles.slow.final_completeness_pct", "slow clients: finals received (%)", False),
        ("roles.slow.final_age_p50_ms", "slow clients: p50 age of live finals (ms)", True),
        ("summary.final_completeness_pct", "all clients: finals received (%)", False),
        ("server_peak_rss_mb", "server peak memory (MB)", False),
    ],
    "bandwidth": [
        ("summary.p50_ms", "p50 caption age (ms)", True),
        ("summary.p95_ms", "p95 caption age (ms)", True),
        ("summary.fresh_pct", "fresh captions (%)", False),
        ("summary.fresh_final_pct", "fresh finals (%)", False),
        ("summary.final_completeness_pct", "finals received (%)", False),
        ("summary.normal_update_coverage_pct", "revisions received (%)", False),
    ],
    "scale": [
        ("summary.p50_ms", "p50 caption age (ms)", True),
        ("summary.p95_ms", "p95 caption age (ms)", True),
        ("summary.fresh_pct", "fresh captions (%)", False),
        ("server_cpu_pct", "server CPU (% of a core, whole run)", False),
        ("summary.final_completeness_pct", "finals received (%)", False),
        ("server_peak_rss_mb", "server peak memory (MB)", False),
    ],
    "rate": [
        ("summary.final_completeness_pct", "finals received (%)", False),
        ("summary.p50_ms", "p50 caption age (ms)", True),
        ("summary.p95_ms", "p95 caption age (ms)", True),
        ("summary.fresh_pct", "fresh captions (%)", False),
        ("summary.normal_update_coverage_pct", "revisions received (%)", False),
        ("server_cpu_pct", "server CPU (% of a core, whole run)", False),
    ],
    "loss": [
        ("summary.fresh_pct", "fresh captions (%)", False),
        ("summary.normal_update_coverage_pct", "revisions received (%)", False),
        ("summary.p50_ms", "p50 caption age (ms)", True),
        ("summary.p95_ms", "p95 caption age (ms)", True),
        ("summary.final_completeness_pct", "finals received (%)", False),
        ("server_cpu_pct", "server CPU (% of a core, whole run)", False),
    ],
}


PLOTS["slow_reader_long"] = PLOTS["slow_reader"]
PLOTS["delay_step"] = [
    ("summary.normal_update_coverage_pct", "revisions received (%)", False),
    ("summary.fresh_pct", "fresh captions (%)", False),
    ("summary.p50_ms", "p50 caption age (ms)", True),
    ("summary.p95_ms", "p95 caption age (ms)", True),
    ("summary.final_completeness_pct", "finals received (%)", False),
    ("server_cpu_pct", "server CPU (% of a core, whole run)", False),
]
PLOTS["delay_scale"] = [
    ("summary.p50_ms", "p50 caption age (ms)", True),
    ("summary.p95_ms", "p95 caption age (ms)", True),
    ("summary.fresh_pct", "fresh captions (%)", False),
    ("summary.final_completeness_pct", "finals received (%)", False),
    ("summary.normal_update_coverage_pct", "revisions received (%)", False),
    ("server_cpu_pct", "server CPU (% of a core, whole run)", False),
]


def plot() -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir = OUT / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    table = {}
    for sweep, metrics in PLOTS.items():
        data = load(sweep)
        if not data:
            continue
        cfg = SWEEPS[sweep]
        fig, axes = plt.subplots(2, 3, figsize=(16, 9), constrained_layout=True)
        for ax, (path, ylabel, ylog) in zip(axes.flat, metrics):
            for variant in cfg["variants"]:
                xs, mean, lo, hi = [], [], [], []
                for x in sorted(data.get(variant, {})):
                    vals = [v for v in (get(r, path) for r in data[variant][x]) if v is not None]
                    if not vals:
                        continue
                    xs.append(x)
                    mean.append(statistics.mean(vals))
                    lo.append(min(vals))
                    hi.append(max(vals))
                    table.setdefault(sweep, {}).setdefault(variant, {}).setdefault(str(x), {})[path] = {
                        "mean": statistics.mean(vals), "n": len(vals)}
                if not xs:
                    continue
                style = "-" if variant == "head" else "--"
                ax.plot(xs, mean, style, marker="o", ms=4, color=COLORS[variant], label=LABELS[variant],
                        lw=2.2 if variant == "head" else 1.5)
                if any(h > l for h, l in zip(hi, lo)):
                    ax.fill_between(xs, lo, hi, color=COLORS[variant], alpha=0.15)
            ax.set_ylabel(ylabel)
            ax.set_xlabel(cfg["xlabel"])
            if cfg.get("xlog"):
                ax.set_xscale("log")
                ax.invert_xaxis()
            if ylog:
                ax.set_yscale("log")
            ax.grid(alpha=0.3)
        axes.flat[0].legend(fontsize=9)
        reps = max((len(rs) for v in data.values() for rs in v.values()), default=0)
        fig.suptitle(f"Ablation sweep: {sweep} (mean of up to {reps} runs; band = min-max)")
        fig.savefig(figdir / f"{sweep}.png", dpi=110)
        plt.close(fig)
        print(f"wrote {figdir / f'{sweep}.png'}")
    (OUT / "summary.json").write_text(json.dumps(table, indent=2))


# Points of each sweep whose per-second series are plotted, variants overlaid.
SERIES_POINTS = {
    "slow_reader": [1000, 3000],
    "slow_reader_long": [1000, 2000],
    "bandwidth": [1.0, 0.35],
    "scale": [5000, 10000],
    "rate": [1500, 2500],
    "loss": [3, 8],
    "delay_step": [300, 800],
}

SERIES_PANELS = [
    ("series", "finals", "finals read / s"),
    ("series", "provisionals", "provisionals read / s"),
    ("series", "recovered", "finals recovered via HISTORY / s"),
    ("series", "age_p95_ms", "p95 caption age (ms)"),
    ("series", "fresh_pct", "fresh captions (%)"),
    ("proc", "server_cpu_pct", "server CPU (%)"),
    ("proc", "server_rss_mb", "server memory (MB)"),
    ("proc", "client_cpu_pct", "client process CPU (%)"),
]


def plot_series() -> None:
    """Per-second series (like run_timeline.py) at selected points, rep 1."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    figdir = OUT / "figures"
    for sweep, points in SERIES_POINTS.items():
        data = load(sweep)
        if not data:
            continue
        fig, axes = plt.subplots(len(SERIES_PANELS), len(points), figsize=(7 * len(points), 2.6 * len(SERIES_PANELS)),
                                 sharex="col", constrained_layout=True, squeeze=False)
        for col, x in enumerate(points):
            for variant in SWEEPS[sweep]["variants"]:
                recs = [r for r in data.get(variant, {}).get(x, []) if r.get("series")]
                if not recs:
                    continue
                r = min(recs, key=lambda r: r["rep"])
                t0 = r["series"][0]["ts"] - r["series"][0]["t"]
                for row, (src, key, label) in enumerate(SERIES_PANELS):
                    pts = r["series"] if src == "series" else r.get("proc_series", [])
                    xs = [p["t"] if src == "series" else p["ts"] - t0 for p in pts if p.get(key) is not None]
                    ys = [p[key] for p in pts if p.get(key) is not None]
                    ax = axes[row][col]
                    ax.plot(xs, ys, color=COLORS[variant], lw=2 if variant == "head" else 1.2,
                            label=LABELS[variant])
                    ax.set_ylabel(label)
                    if key.endswith("_ms"):
                        ax.set_yscale("log")
                    ax.grid(alpha=0.3)
            axes[0][col].set_title(f"{SWEEPS[sweep]['xlabel']} = {x}")
            axes[-1][col].set_xlabel("seconds since clients started")
        axes[0][0].legend(fontsize=8)
        fig.savefig(figdir / f"{sweep}_series.png", dpi=100)
        plt.close(fig)
        print(f"wrote {figdir / f'{sweep}_series.png'}")


TIMELINE_PANELS = [
    ("final_p95", "finals: p95 age (ms)", True),
    ("provisional_p95", "provisionals: p95 age (ms)", True),
    ("fresh_pct", "fresh captions (%)", False),
    ("final_live", "finals read live / s", False),
    ("final_recovered", "finals recovered via HISTORY / s", False),
    ("provisional_delivered", "provisionals read / s", False),
    ("provisional_coalesced+provisional_dropped", "provisionals coalesced + dropped / s", False),
    ("evict_lag+evict_backlog", "evictions / s", False),
    ("server_cpu", "server CPU (% of a core)", False),
]


def plot_timelines() -> None:
    """The timelines of every variant, overlaid (5 s moving average)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    runs = {}
    for variant in TIMELINE_VARIANTS:
        path = OUT / "timelines" / variant / "timeline.csv"
        if path.exists():
            with open(path) as f:
                runs[variant] = list(csv.DictReader(f))
    if not runs:
        return
    events = list(csv.DictReader(open(OUT / "timelines" / next(iter(runs)) / "events.csv")))

    def value(row, key):
        try:
            return sum(float(row[k]) for k in key.split("+"))
        except (KeyError, ValueError):
            return math.nan

    fig, axes = plt.subplots(len(TIMELINE_PANELS), 1, figsize=(13, 3 * len(TIMELINE_PANELS)), sharex=True)
    for ax, (key, label, log) in zip(axes, TIMELINE_PANELS):
        for variant, rows in runs.items():
            t = [float(r["t"]) for r in rows]
            y = [value(r, key) for r in rows]
            smooth = []
            for i in range(len(y)):
                window = [v for v in y[max(0, i - 4):i + 1] if v == v]
                smooth.append(sum(window) / len(window) if window else math.nan)
            ax.plot(t, smooth, label=LABELS.get(variant, variant), color=COLORS.get(variant),
                    lw=2 if variant == "head" else 1.2)
        for e in events:
            ax.axvline(float(e["t"]), color="grey", ls=":", lw=0.8)
        if log:
            ax.set_yscale("log")
        ax.set_ylabel(label, fontsize=9)
        ax.grid(alpha=0.3)
    for e in events:
        axes[0].annotate(e["label"], (float(e["t"]), 1.02), xycoords=("data", "axes fraction"), fontsize=8)
    axes[0].legend(fontsize=9, loc="upper left")
    axes[-1].set_xlabel("time (s)")
    fig.suptitle("Timeline: ramp to 5000 clients, then loopback limits 40 -> 5 Mbit/s, then removed", y=0.995)
    fig.tight_layout()
    out = OUT / "figures" / "timelines.png"
    fig.savefig(out, dpi=100)
    plt.close(fig)
    print(f"wrote {out}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build")
    r = sub.add_parser("run")
    r.add_argument("--reps", type=int, default=3)
    sub.add_parser("plot")
    a = p.parse_args()
    if a.cmd == "plot":
        plot()
        plot_series()
        plot_timelines()
    else:
        {"build": build}.get(a.cmd, lambda: run(a.reps))()


if __name__ == "__main__":
    main()
