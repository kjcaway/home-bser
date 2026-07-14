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
python main_agent.py --environment prod # 운영환경: cpu STT/TTS, mic index 2
```

`--environment` (choices `dev`/`prod`, **default `dev`**) is parsed with `argparse` and selects a preset that drives both the compute device and the microphone. Presets live in the `ENVIRONMENTS` dict in `agent/config.py`:

- `dev` — `device=cpu`, `input-device=0` (개발환경)
- `prod` — `device=cpu`, `input-device=2` (운영환경)

STT/TTS both run on CPU in every environment: CPU was judged the better fit for these stages. GPU (cuda) is intentionally left unused for now, reserved for a future local LLM stage — when that stage lands it will get its own device setting. The selected environment is logged at startup (`[System] 실행 환경: ...`). STT `compute_type` is derived from the device automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU); since both presets are cpu, this is currently always `int8`. The mic index is passed to `open_input_stream(device_index)`; if opening fails, the available input devices are listed to aid diagnosis.

Run standalone utilities:

```bash
python timer.py 30s   # timer alarm; accepts "<N>m" or "<N>s" (e.g. 1m, 30s)
python text_to_wav.py --name out.wav --text "안녕하세요"   # TTS text → wav file (no playback)
```

Install dependencies (see `README.md` for the full list):

```bash
pip3 install pyaudio numpy openwakeword faster-whisper requests torch transformers scipy uroman
pip3 install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.20.*"
```

**cuDNN 버전 핀 필수**: `nvidia-cudnn-cu12` 는 torch 가 빌드된 cuDNN 버전과 일치해야 한다.
torch(예: cuDNN 9.20 = `torch.backends.cudnn.version()` → `92000`)보다 새 버전(예: 9.24)을
설치하면 STT(faster-whisper)는 통과하지만 TTS(VITS conv1d) 실행 시
`RuntimeError: CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH` 로 죽는다. 요구 버전은
`python -c "import importlib.metadata as m; print([r for r in m.requires('torch') if 'cudnn' in r.lower()])"`
로 확인해 핀을 맞춘다.

## Architecture

The code is split into an `agent/` package with one module per pipeline stage; `main_agent.py` only wires them together:

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`), the `ENVIRONMENTS` preset dict, and `parse_device_args()` (the `--environment` argparse logic; returns `(device, stt_compute_type, input_device_index)`).
- `agent/audio_io.py` — PyAudio helpers: `open_input_stream(device_index=None)`, `MicStream`, `list_input_devices()`, `record_frames()`, `play_wav_file(file_path, output_device_index=None)`, `_convert_pcm16()`.
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

### Playback (sample-rate handling)

Playback has the mirror-image problem: the wav files (`res0.wav`, the TTS `response.wav`, the timer alarm) are all 16 kHz, but raw hardware output devices reject that rate with `-9999`. `play_wav_file()` handles it the same way:

1. It first tries to open the output stream at the wav's own rate / channel count.
2. If that raises `OSError`, it reopens at the output device's **native** rate / channels and converts the 16-bit PCM in software via `_convert_pcm16()` — mono downmix → `scipy.signal.resample_poly` → duplicate up to the target channel count. This path logs `[System] 재생 네이티브 변환: …Hz/…ch → …Hz/…ch`.

`_convert_pcm16()` only handles 16-bit PCM (all wavs in this repo are 16-bit); a non-16-bit file that the device can't open natively is skipped with a message rather than crashing. `play_wav_file()` also accepts an optional `output_device_index` (defaults to the system default output device).

### Intent handling (current behavior)

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That LLM code has been **removed** pending a redesign; a future LLM stage would plug in where `process_user_command()` is called (after `user_text` is obtained). The active flow is timer-focused:

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `run_timer_script()` shells out via `subprocess` to `timer.py <time>`, which sleeps then speaks an alarm. Otherwise it just echoes the recognized text back via TTS.

### TTS single source

`agent/tts.py` (`TextToSpeech`) is the only TTS implementation; `timer.py` and `text_to_wav.py` import it.

## Environment assumptions

- **`main_agent.py` environment is selectable** via `--environment dev|prod` (default `dev`); the preset drives both the compute device (STT/TTS) and the mic input index (`dev`=cpu/mic 0, `prod`=cpu/mic 2). Both presets run STT/TTS on cpu; cuda is reserved for a future local LLM. `timer.py` auto-detects (`cuda` if available, else `cpu`).
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes the intended LLM-in-the-loop pipeline, which differs from the current timer-only `main_agent.py`.
