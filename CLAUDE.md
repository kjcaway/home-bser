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

Run the full agent (needs a mic and speaker; environment selected via `--environment`):

```bash
python main_agent.py                    # default: dev
python main_agent.py --environment dev  # 개발환경: cpu, mic index 0
python main_agent.py --environment prod # 운영환경: cuda GPU, mic index 1
```

`--environment` (choices `dev`/`prod`, **default `dev`**) is parsed with `argparse` and selects a preset that drives both the compute device and the microphone. Presets live in the `ENVIRONMENTS` dict in `agent/config.py`:

- `dev` — `device=cpu`, `input-device=0` (개발환경)
- `prod` — `device=cuda`, `input-device=1` (운영환경)

The selected environment is logged at startup (`[System] 실행 환경: ...`). STT `compute_type` is derived from the device automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU). Selecting `prod` without a working CUDA setup raises an error; there is no automatic CPU fallback. The mic index is passed to `open_input_stream(device_index)`; if opening fails, the available input devices are listed to aid diagnosis.

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

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`), the `ENVIRONMENTS` preset dict, and `parse_device_args()` (the `--environment` argparse logic; returns `(device, stt_compute_type, input_device_index)`).
- `agent/audio_io.py` — PyAudio helpers: `open_input_stream(device_index=None)`, `MicStream`, `list_input_devices()`, `record_frames()`, `play_wav_file()`.
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

### Microphone capture (sample-rate handling)

The pipeline needs 16 kHz mono int16 (openwakeword and faster-whisper both assume 16 kHz). Most raw hardware ALSA devices (`hw:*`) do **not** support 16 kHz directly, so PyAudio fails with `-9997 Invalid sample rate` (and `-9999` on other mismatches). `open_input_stream()` therefore returns a `MicStream` wrapper instead of a bare PyAudio stream:

1. It first tries to open the device at 16 kHz / mono directly — sound-server devices (`pulse`, `default`, `sysdefault`) support this via their own conversion.
2. If that raises `OSError`, it reopens the device at its **native** sample rate and channel count (e.g. USB-C Speaker = 48000 Hz stereo) and, on every `read()`, downmixes to mono and resamples to 16 kHz in software via `scipy.signal.resample_poly`. This path logs `[System] 마이크 네이티브 …Hz/…ch → 16000Hz/모노 소프트웨어 변환 사용` at startup.

`MicStream` exposes the same `read()`, `start_stream()`, `stop_stream()`, `close()`, and `get_read_available()` interface as a PyAudio stream, so `main_agent.py`, `record_frames()`, and `flush_input_stream()` use it unchanged. `scipy` is a required dependency for this resampling path.

### Intent handling (current behavior)

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That LLM code has been **removed** pending a redesign; a future LLM stage would plug in where `process_user_command()` is called (after `user_text` is obtained). The active flow is timer-focused:

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `run_timer_script()` shells out via `subprocess` to `timer.py <time>`, which sleeps then speaks an alarm. Otherwise it just echoes the recognized text back via TTS.

### TTS single source

`agent/tts.py` (`TextToSpeech`) is the only TTS implementation; `timer.py` and `text_to_wav.py` import it.

## Environment assumptions

- **`main_agent.py` environment is selectable** via `--environment dev|prod` (default `dev`); the preset drives both the compute device (STT/TTS) and the mic input index (`dev`=cpu/mic 0, `prod`=cuda/mic 1). `timer.py` auto-detects (`cuda` if available, else `cpu`).
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes the intended LLM-in-the-loop pipeline, which differs from the current timer-only `main_agent.py`.
