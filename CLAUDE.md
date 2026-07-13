# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A 100% offline, local Korean voice assistant ("home agent") intended to run on Ubuntu with an NVIDIA GPU. The full pipeline — wake word → speech-to-text → intent handling → text-to-speech — runs on local CPU/GPU with no cloud APIs. `main_agent.py` is the single entry point; the `test_*.py` files are standalone unit tests for individual pipeline stages.

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

Run individual stage tests:

```bash
python test_stt.py    # wake word + STT loop, forwards text to a local HTTP LLM endpoint
python test_tts.py    # synthesize a fixed Korean sentence and play it
python timer.py 30s   # timer alarm; accepts "<N>m" or "<N>s" (e.g. 1m, 30s)
```

Install dependencies (see `README.md` for the full list):

```bash
pip3 install pyaudio numpy openwakeword faster-whisper requests torch transformers scipy uroman
pip3 install nvidia-cublas-cu12 nvidia-cudnn-cu12
```

## Architecture

`main_agent.py` loads all models once at startup, then runs an infinite loop:

1. **Wake word** — `openwakeword` continuously scores the mic stream; a score > 0.5 on the built-in "alexa" model triggers a turn.
2. **STT** — records `RECORD_SECONDS` (5s) of audio and transcribes it with `faster-whisper` (`small` model, `device` from `--device`, Korean).
3. **Intent + action** — `process_user_command()` decides what to do with the transcribed text (see below).
4. **TTS** — `text_to_speech_and_play()` synthesizes Korean speech with the `facebook/mms-tts-kor` VITS model (`transformers` + `torch`) and plays it through PyAudio.

The loop calls `oww_model.reset()` after each turn to clear wake-word state.

### Intent handling (current behavior)

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That LLM path is **commented out** in the live `main_agent.py`. The active flow is timer-focused:

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `main_agent.py` shells out via `subprocess` to `timer.py <time>`, which sleeps then speaks an alarm. Otherwise it just echoes the recognized text back via TTS.

When re-enabling the LLM, uncomment the `ollama` import and the LLM block in the main loop; `messages_history` (with its Korean system prompt constraining answers to short spoken-style replies) is already set up for it.

### Cross-file duplication

`text_to_speech_and_play()` / TTS setup is copy-pasted across `main_agent.py`, `timer.py`, and `test_tts.py` (each re-loads the VITS model independently). A change to TTS behavior generally needs to be made in all three.

## Environment assumptions

- **`main_agent.py` device is selectable** via `--device cpu|cuda` (default `cpu`); the chosen device drives both STT and TTS. Note `test_stt.py` still hard-codes `WhisperModel(..., device="cuda", compute_type="float16")` and will fail without CUDA — it has not been updated to match `main_agent.py`.
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000`).
- `test_stt.py` posts to a local LLM endpoint at `http://localhost:8642/v1/chat/completions` — a separate service, not part of this repo.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes the intended LLM-in-the-loop pipeline, which differs from the current timer-only `main_agent.py`.
