#!/usr/bin/env python3
"""
LiveRecall Bonus ASR Source — connects faster-whisper to the LiveRecall server.

Feeds transcribed speech into the same solution_server.py and browser client
used for the mandatory assignment. No changes to the server or client required.

Setup:
    pip install -r bonus_asr/requirements_asr.txt
    brew install ffmpeg          # macOS (video mode)
    sudo apt install -y ffmpeg   # Ubuntu (video mode)

Usage:
    python bonus_asr/asr_source.py --port 8765 --video lecture.mp4
    python bonus_asr/asr_source.py --port 8765 --mic
    python bonus_asr/asr_source.py --port 8765 --mic --model small --language en
"""

from __future__ import annotations

import argparse
import asyncio
import json
import queue
import subprocess
import sys
import time
from typing import Iterator

import numpy as np
import websockets

SAMPLE_RATE = 16_000
CHUNK_SECONDS = 4


def load_model(model_size: str):
    from faster_whisper import WhisperModel
    print(f"Loading faster-whisper '{model_size}' model (downloads on first run)...",
          flush=True)
    return WhisperModel(model_size, device="cpu", compute_type="int8")


def audio_from_video(path: str) -> Iterator[np.ndarray]:
    """Yield CHUNK_SECONDS-long float32 chunks from a video/audio file via ffmpeg."""
    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    chunk_bytes = chunk_samples * 2  # int16 = 2 bytes per sample
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", path,
        "-vn",                          # strip video
        "-acodec", "pcm_s16le",
        "-ar", str(SAMPLE_RATE),
        "-ac", "1",                     # mono
        "-f", "s16le", "pipe:1",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        while True:
            raw = proc.stdout.read(chunk_bytes)
            if not raw:
                break
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            yield audio
    finally:
        proc.kill()


def audio_from_mic() -> Iterator[np.ndarray]:
    """Yield CHUNK_SECONDS-long float32 chunks from the default microphone."""
    import sounddevice as sd  # imported here so video mode works without sounddevice

    chunk_samples = CHUNK_SECONDS * SAMPLE_RATE
    q: queue.Queue[np.ndarray] = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(f"mic: {status}", file=sys.stderr)
        q.put(indata[:, 0].copy())

    print("Listening on microphone... (Ctrl+C to stop)", flush=True)
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=SAMPLE_RATE // 4,  # 250 ms blocks
                        callback=callback):
        buf: list[np.ndarray] = []
        accumulated = 0
        while True:
            chunk = q.get()
            buf.append(chunk)
            accumulated += len(chunk)
            if accumulated >= chunk_samples:
                audio = np.concatenate(buf)
                yield audio[:chunk_samples]
                leftover = audio[chunk_samples:]
                buf = [leftover] if len(leftover) > 0 else []
                accumulated = len(leftover)


async def run(args: argparse.Namespace) -> None:
    model = load_model(args.model)

    uri = f"ws://127.0.0.1:{args.port}"
    print(f"Connecting to {uri} ...", flush=True)

    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"type": "SOURCE_HELLO", "session": "BONUS"}))
        print("Connected. Transcription starting.\n", flush=True)

        segment_num = 0
        start_wall = time.time()
        source = audio_from_video(args.video) if args.video else audio_from_mic()

        loop = asyncio.get_event_loop()

        for chunk_idx, audio in enumerate(source):
            chunk_wall_start = start_wall + chunk_idx * CHUNK_SECONDS

            # Run transcription in a thread so the event loop stays alive for
            # WebSocket pings during the CPU-intensive whisper decode.
            def transcribe(a=audio):
                segs, info = model.transcribe(
                    a,
                    language=args.language or None,
                    word_timestamps=True,
                    vad_filter=True,
                )
                return list(segs), info  # consume generator inside the thread

            result_segs, _ = await loop.run_in_executor(None, transcribe)

            for seg in result_segs:
                words = [w for w in (seg.words or []) if w.word.strip()]
                if not words:
                    continue

                segment_num += 1
                accumulated: list[str] = []

                # Send one provisional revision per word.
                for rev, word in enumerate(words, start=1):
                    accumulated.append(word.word.strip())

                    # Video mode: pace sends to match real audio playback timing.
                    if args.video:
                        fire_at = chunk_wall_start + word.start
                        delay = fire_at - time.time()
                        if delay > 0:
                            await asyncio.sleep(delay)

                    await ws.send(json.dumps({
                        "type": "CAPTION_UPDATE",
                        "session": "BONUS",
                        "segment": segment_num,
                        "revision": rev,
                        "source_ts": time.time(),
                        "final": False,
                        "text": " ".join(accumulated),
                    }))

                # Final revision — complete sentence text, final=True.
                await ws.send(json.dumps({
                    "type": "CAPTION_UPDATE",
                    "session": "BONUS",
                    "segment": segment_num,
                    "revision": len(words) + 1,
                    "source_ts": time.time(),
                    "final": True,
                    "text": seg.text.strip(),
                }))
                print(f"[{segment_num}] {seg.text.strip()}", flush=True)

            # Video mode: wait until real-time catches up to the chunk boundary
            # before reading the next chunk, preserving 1x playback speed.
            if args.video:
                next_boundary = start_wall + (chunk_idx + 1) * CHUNK_SECONDS
                wait = next_boundary - time.time()
                if wait > 0:
                    await asyncio.sleep(wait)

        await ws.send(json.dumps({"type": "SOURCE_DONE", "session": "BONUS"}))
        print("\nTranscription complete.", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LiveRecall ASR bonus source using faster-whisper"
    )
    parser.add_argument("--port", type=int, default=8765,
                        help="LiveRecall server port (default: 8765)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video", metavar="PATH",
                       help="Path to a video or audio file")
    group.add_argument("--mic", action="store_true",
                       help="Capture live audio from the default microphone")
    parser.add_argument("--model", default="tiny",
                        choices=["tiny", "base", "small", "medium"],
                        help="Whisper model size (default: tiny, ~150 MB)")
    parser.add_argument("--language", default=None,
                        help="Force language, e.g. 'en' (default: auto-detect)")
    args = parser.parse_args()

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)


if __name__ == "__main__":
    main()
