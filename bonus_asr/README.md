# Optional Bonus: Real-Lecture ASR Integration

Transcribes speech from a video file or live microphone using
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) and feeds captions
into the same `solution_server.py` and browser client used for the mandatory
assignment. No changes to the server or client are required.

## Setup

**1. Install system dependencies**

macOS:
```bash
brew install ffmpeg portaudio
```

Ubuntu:
```bash
sudo apt install -y ffmpeg portaudio19-dev
```

**2. Install Python packages (separate from mandatory requirements.txt)**

```bash
pip install -r bonus_asr/requirements_asr.txt
```

The first run downloads the Whisper model (~150 MB for `tiny`, ~300 MB for `base`).

## Usage

Start the LiveRecall server first:
```bash
python solution_server.py --port 8765
```

**Video file mode** — transcribes a lecture recording at 1× playback speed:
```bash
python bonus_asr/asr_source.py --port 8765 --video lecture.mp4
```

**Live microphone mode** — transcribes in real time:
```bash
python bonus_asr/asr_source.py --port 8765 --mic
```

Then open `client/index.html` in a browser and connect to `ws://localhost:8765`.
Captions appear live with provisional (gray italic) → final (dark bold) transitions.

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--model` | `tiny` | Whisper model: `tiny`, `base`, `small`, `medium` |
| `--language` | auto | Force language, e.g. `en` |
| `--port` | `8765` | Server port |

## How it works

Each 4-second audio chunk is transcribed by faster-whisper with word-level
timestamps. For each detected sentence:

1. Words are sent one-by-one as provisional `CAPTION_UPDATE` frames
   (`final=false`, revision increments per word).
2. The complete sentence is sent as a final `CAPTION_UPDATE` (`final=true`).

In video mode, sends are paced to match real audio timing using word timestamps
from faster-whisper, so captions appear in sync with the video. In mic mode,
captions appear within ~1–2 seconds of speech.

## Limitations

- **Latency:** faster-whisper processes audio in chunks (4 s default), so there
  is an inherent ~4-second delay before each chunk's captions appear.
- **Accuracy:** the `tiny` model trades accuracy for speed. Use `--model base`
  or `--model small` for better results at the cost of higher CPU usage.
- **Language:** auto-detection works well for English; use `--language en` to
  force it and avoid detection overhead.
- **Microphone device:** uses the system default input device. Change it via
  your OS sound settings if needed.
