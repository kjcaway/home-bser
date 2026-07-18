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
python main_agent.py --environment prod # 운영환경: cpu STT/TTS, USB 장치를 이름으로 탐색
python main_agent.py --list-devices     # 입출력 장치 이름/인덱스 확인
```

`--environment` (choices `dev`/`prod`, **default `dev`**) is parsed with `argparse` and selects a preset that drives the compute device, the microphone, and the speaker. Presets live in the `ENVIRONMENTS` dict in `agent/config.py`:

- `dev` — `device=cpu`, mic index `0`, 기본 스피커 (개발환경)
- `prod` — `device=cpu`, mic/speaker matched by **name** (`"USB"`) (운영환경)

STT/TTS both run on CPU in every environment: CPU was judged the better fit for these stages. GPU (cuda) is intentionally left unused for now, reserved for a future local LLM stage — when that stage lands it will get its own device setting. The selected environment is logged at startup (`[System] 실행 환경: ...`). STT `compute_type` is derived from the device automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU); since both presets are cpu, this is currently always `int8`.

### Device selection (why by name, not index)

PortAudio assigns device indices in enumeration order, so a USB mic/speaker's index **changes across reboots and re-plugs** — a hardcoded `2` breaks. Presets therefore carry `input_device_name` / `output_device_name`: a case-insensitive **substring** of the device name, resolved to a live index at startup by `resolve_devices()` in `agent/audio_io.py` (called once in `main()`, before models load).

- No name (dev) → the preset's `input_device_index` / `output_device_index` is used as-is.
- Name matches → that index is used, and the match is logged. Multiple matches → the first is picked and the rest are logged.
- Name matches nothing → warns, prints the device list, and falls back to the preset index (`None` = system default), so a missing USB device degrades instead of crashing.

Patterns are overridable via `.env` (`AUDIO_INPUT_NAME`, `AUDIO_OUTPUT_NAME`) so prod devices can change without touching code; an empty value falls back to the preset. Run `--list-devices` on the target machine to see the real names. The resolved index is passed to `open_input_stream(device_index)`; if opening still fails, the available input devices are listed to aid diagnosis.

Run standalone utilities:

```bash
python timer.py 30s   # timer alarm; accepts "<N>m" or "<N>s" (e.g. 1m, 30s)
python timer.py 30s --output-device 2   # play the alarm on a specific speaker index
python text_to_wav.py --name out.wav --text "안녕하세요"   # TTS text → wav file (no playback)
```

Install dependencies (see `README.md` for the full list):

```bash
pip3 install pyaudio numpy openwakeword faster-whisper requests torch transformers scipy uroman openai
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

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`), the `ENVIRONMENTS` preset dict, `parse_device_args()` (the `--environment` argparse logic, returns a `RunConfig` NamedTuple), and `load_env_file()` (reads the git-ignored `.env` into `os.environ`).
- `agent/audio_io.py` — PyAudio helpers: `open_input_stream(device_index=None)`, `MicStream`, `list_input_devices()`, `list_output_devices()`, `find_device_by_name()` / `resolve_device_index()` / `resolve_devices()` (name → index 해석), `record_frames()`, `play_wav_file(file_path, output_device_index=None)`, `_convert_pcm16()`, `_supports_input_format()` / `_supports_output_format()` (오픈 전 레이트 지원 조회로 ALSA 경고 회피).
- `agent/wakeword.py` — `load_wakeword_model()` (openwakeword built-ins, "alexa"), `get_score()`.
- `agent/stt.py` — `load_stt_model()` (faster-whisper `small`), `transcribe_pcm()` (int16 PCM bytes → Korean text).
- `agent/tts.py` — `TextToSpeech` class (`facebook/mms-tts-kor` VITS via `transformers` + `torch`); `synthesize_to_file()` and `speak()` (synthesize + play).
- `agent/skills/` — one module per skill, each exposing `handle(user_text, tts) -> bool`:
  - `agent/skills/timer.py` — `check_timer_intent()`, `extract_time_unit()`, `format_time_korean()`, `run_timer_script()`.
  - `agent/skills/hermes_api.py` — hermes gateway LLM 질의 (catch-all). `is_enabled()`, `ask()`, `strip_think()`.

Models are loaded once inside `main()` (not at import time), so other scripts can import individual `agent` modules without pulling in the whole pipeline. Skills load no models themselves — `handle(user_text, tts)` receives the `TextToSpeech` instance from the caller.

`main_agent.py` runs an infinite loop:

1. **Wake word** — score each mic chunk; > 0.5 on "alexa" triggers a turn.
2. **Wake acknowledgment** — plays `res0.wav` (`WAKE_RESPONSE_FILE`) so the user knows the agent is listening, then starts recording.
3. **STT** — records `RECORD_SECONDS` (5s), transcribes with faster-whisper (Korean).
4. **Intent + action** — `process_user_command()` (see below).
5. Calls `oww_model.reset()` after each turn to clear wake-word state.

### Microphone capture (sample-rate handling)

The pipeline needs 16 kHz mono int16 (openwakeword and faster-whisper both assume 16 kHz). Most raw hardware ALSA devices (`hw:*`) do **not** support 16 kHz directly (PyAudio would fail with `-9997 Invalid sample rate`, and `-9999` on other mismatches). `open_input_stream()` therefore returns a `MicStream` wrapper instead of a bare PyAudio stream:

1. It resolves the target device to a concrete index (default input device if none given), then **probes** whether that device supports 16 kHz / mono via `_supports_input_format()` (`PyAudio.is_format_supported`) — sound-server devices (`pulse`, `default`, `sysdefault`) support this via their own conversion. Only if supported does it actually open at 16 kHz / mono.
2. If the probe says unsupported, it opens the device at its **native** sample rate and channel count (e.g. USB-C Speaker = 48000 Hz stereo) and, on every `read()`, downmixes to mono and resamples to 16 kHz in software via `scipy.signal.resample_poly`. This path logs `[System] 마이크 네이티브 …Hz/…ch → 16000Hz/모노 소프트웨어 변환 사용` at startup.

**Why probe first, not try-then-catch:** an earlier version simply called `open()` at 16 kHz and caught the `OSError`. That works, but `Pa_OpenStream` reaches the ALSA stream-configure path before failing, and PortAudio prints C-level `paInvalidSampleRate` / `PaAlsaStream_Configure … failed` warnings **directly to stderr** — which Python's `try/except` cannot suppress. `Pa_IsFormatSupported` is a lighter hw-params probe that does not enter that path, so the unsupported case is detected silently and no failed-open warning is emitted.

`MicStream` exposes the same `read()`, `start_stream()`, `stop_stream()`, `close()`, and `get_read_available()` interface as a PyAudio stream, so `main_agent.py`, `record_frames()`, and `flush_input_stream()` use it unchanged. `scipy` is a required dependency for this resampling path.

### Playback (sample-rate handling)

Playback has the mirror-image problem: the wav files (`res0.wav`, the TTS `response.wav`, the timer alarm) are all 16 kHz, but raw hardware output devices reject that rate. `play_wav_file()` handles it the same way, and — like the mic path — **probes before opening** to avoid PortAudio's stderr ALSA warnings (see "Why probe first" above; these warnings surfaced every turn because playback runs each turn, whereas the mic opens once at startup):

1. It resolves the target device to a concrete index (default output device if none given), then probes whether that device supports the wav's own rate / channel count via `_supports_output_format()` (`PyAudio.is_format_supported`). If supported, it opens at the wav's rate directly.
2. If unsupported, it opens at the output device's **native** rate / channels and converts the 16-bit PCM in software via `_convert_pcm16()` — mono downmix → `scipy.signal.resample_poly` → duplicate up to the target channel count. This path logs `[System] 재생 네이티브 변환: …Hz/…ch → …Hz/…ch`.

`_convert_pcm16()` only handles 16-bit PCM (all wavs in this repo are 16-bit); a non-16-bit file that the device can't open natively is skipped with a message rather than crashing. `play_wav_file()` also accepts an optional `output_device_index` (defaults to the system default output device).

### Intent handling (current behavior)

`main_agent.py` holds a `SKILLS` registry — a list of `handle(user_text, tts) -> bool` functions. `execute_command()` walks the list in order and stops at the first skill that returns `True` (meaning "I handled this"). Adding a feature = write a `handle` function and register it. **Order matters**: `hermes_api.handle` is a catch-all and must stay last. If no skill handles the utterance, the fallback echoes the recognized text via TTS.

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That was replaced by the hermes gateway skill below.

**timer skill** (`agent/skills/timer.py`):

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `run_timer_script()` shells out via `subprocess` to `timer.py <time>`, which sleeps then plays an alarm. It forwards the agent's speaker index (`tts.output_device_index`) as `--output-device` so the alarm plays on the **same** speaker as the rest of the agent. Without this, the timer subprocess falls back to the system default output, which in prod (headless/nohup) is unrouted — flooding the log with ALSA/JACK fallback warnings and playing the alarm on the wrong (or no) device.

### LLM stage (hermes gateway)

`agent/skills/hermes_api.py` sends any utterance no other skill claimed to a **hermes gateway** OpenAI-compatible server (`hermes gateway`, port 8642, model `qwen3:8b`) and speaks the reply. It calls the `openai` SDK with only `base_url` swapped; `max_retries=0` so a dead gateway fails fast instead of stalling the voice turn. qwen3 can emit `<think>…</think>` blocks even with thinking disabled, so `strip_think()` removes them before TTS, and the system prompt demands one or two short plain-text Korean sentences (the answer is read aloud).

Config comes from a **git-ignored `.env`** in the project root — copy `.env.example` and fill it in. `load_env_file()` in `agent/config.py` parses it with the stdlib (no `python-dotenv` dependency): `KEY=VALUE` per line, `#` comments and blank lines skipped, surrounding quotes stripped, and **real environment variables always win** over `.env` values. Keys: `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`.

`HERMES_API_KEY` doubles as the **on/off switch**: hermes itself needs no auth, but the OpenAI SDK requires the field, and `is_enabled()` keys off it. With no `.env` the skill returns `False` and the echo fallback runs — so **dev works unchanged without a hermes server**, and prod enables the LLM by dropping in a `.env`. If the call fails or returns an empty body, the skill speaks a short apology and returns `True` (an echo would be confusing for what was clearly a question).

### TTS single source

`agent/tts.py` (`TextToSpeech`) is the only TTS implementation; `timer.py` and `text_to_wav.py` import it.

## Environment assumptions

- **`main_agent.py` environment is selectable** via `--environment dev|prod` (default `dev`); the preset drives the compute device (STT/TTS) and the audio devices (`dev`=cpu/mic index 0, `prod`=cpu/USB devices matched by name — see "Device selection" above). Both presets run STT/TTS on cpu; the GPU belongs to hermes (the LLM), which runs as a separate server process rather than in-process. `timer.py` auto-detects (`cuda` if available, else `cpu`).
- **The agent is no longer 100% offline when hermes is enabled** — but hermes runs locally (127.0.0.1), so no cloud APIs are involved and the offline property holds at the network boundary.
- The LLM stage is gated by the presence of a `.env` with `HERMES_API_KEY`, not by `--environment`. In practice that means prod-only, since dev has no `.env`.
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes an LLM-in-the-loop pipeline built on Ollama; the LLM stage now runs on hermes gateway instead (see above).
- `.env.example` — template for the git-ignored `.env` (hermes settings, audio device name patterns).
- `test_hermes_api.py` — standalone hermes connectivity check (`python test_hermes_api.py "질문"`).
