# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A 100% offline, local Korean voice assistant ("home agent") intended to run on Ubuntu with an NVIDIA GPU. The full pipeline — wake word → speech-to-text → intent handling → text-to-speech — runs on local CPU/GPU with no cloud APIs. `main_agent.py` is the entry point (a thin orchestrator); the implementation lives in the `agent/` package.

Note: the repo root **is itself the Python venv** (`bin/`, `include/`, `lib/`, `pyvenv.cfg` are venv artifacts, git-ignored). Source lives directly at the root alongside them.

## Commands

All commands must run inside the venv (the repo root). Activate first:

```bash
source bin/activate
```

Run the full agent (needs a mic and speaker; GPU optional via `--device`):

```bash
python main_agent.py                 # default: cpu
python main_agent.py --device cuda   # run STT/TTS on CUDA GPU
python main_agent.py --device cpu    # explicit cpu
```

`--device` (choices `cpu`/`cuda`, **default `cpu`**) is parsed with `argparse` and drives both STT and TTS. The selected device is logged at startup (`[System] 실행 디바이스: ...`). STT `compute_type` is derived from it automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU). Passing `--device cuda` without a working CUDA setup raises an error; there is no automatic CPU fallback.

Run standalone utilities:

```bash
python timer.py 30s   # timer alarm; accepts "<N>m" or "<N>s" (e.g. 1m, 30s)
python text_to_wav.py --name out.wav --text "안녕하세요"   # TTS text → wav file (no playback)
```

Install dependencies (see `README.md` for the full list):

```bash
pip3 install pyaudio numpy openwakeword faster-whisper requests torch transformers scipy uroman
pip3 install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## Architecture

The code is split into an `agent/` package with one module per pipeline stage; `main_agent.py` only wires them together:

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`), and `parse_device_args()` (the `--device` argparse logic).
- `agent/audio_io.py` — PyAudio helpers: `open_input_stream()`, `record_frames()`, `play_wav_file()`.
- `agent/wakeword.py` — `load_wakeword_model()` (openwakeword built-ins, "alexa"), `get_score()`.
- `agent/stt.py` — `load_stt_model()` (faster-whisper `small`), `transcribe_pcm()` (int16 PCM bytes → Korean text).
- `agent/tts.py` — `TextToSpeech` class (`facebook/mms-tts-kor` VITS via `transformers` + `torch`); `synthesize_to_file()` and `speak()` (synthesize + play).
- `agent/intent.py` — `check_timer_intent()`, `extract_time_unit()`, `run_timer_script()`, `process_user_command()`.

Models are loaded once inside `main()` (not at import time), so other scripts can import individual `agent` modules without pulling in the whole pipeline. `agent/intent.py` does not load any model itself — `process_user_command(user_text, tts)` receives the `TextToSpeech` instance from the caller.

`main_agent.py` runs an infinite loop:

1. **Wake word** — score each mic chunk; > 0.5 on "alexa" triggers a turn.
2. **Wake acknowledgment** — plays `res0.wav` (`WAKE_RESPONSE_FILE`) so the user knows the agent is listening, then starts recording.
3. **STT** — records `RECORD_SECONDS` (5s), transcribes with faster-whisper (Korean).
4. **Intent + action** — `process_user_command()` (see below).
5. Calls `oww_model.reset()` after each turn to clear wake-word state.

### Intent handling (current behavior)

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That LLM code has been **removed** pending a redesign; a future LLM stage would plug in where `process_user_command()` is called (after `user_text` is obtained). The active flow is timer-focused:

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `run_timer_script()` shells out via `subprocess` to `timer.py <time>`, which sleeps then speaks an alarm. Otherwise it just echoes the recognized text back via TTS.

### TTS single source

`agent/tts.py` (`TextToSpeech`) is the only TTS implementation; `timer.py` and `text_to_wav.py` import it.

## Environment assumptions

- **`main_agent.py` device is selectable** via `--device cpu|cuda` (default `cpu`); the chosen device drives both STT and TTS. `timer.py` auto-detects (`cuda` if available, else `cpu`).
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes the intended LLM-in-the-loop pipeline, which differs from the current timer-only `main_agent.py`.
