# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

An offline-first, local Korean voice assistant ("home agent") intended to run on Ubuntu with an NVIDIA GPU. The pipeline — wake word → speech-to-text → intent handling → text-to-speech — runs entirely on local CPU/GPU. The one stage that can leave the machine is the optional LLM answer step: hermes keeps it local (127.0.0.1), while the Claude Code CLI skill calls the cloud and is off by default (see "Environment assumptions"). `main_agent.py` is the entry point (a thin orchestrator); the implementation lives in the `agent/` package.

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
python main_agent.py --debug-record     # 매 턴 녹음 원본을 debug_record.wav 로 저장 (진단용)
python main_agent.py --off-speaker "오늘 날씨는 어때?"   # 마이크/스피커 없이 문장 하나만 처리 (테스트용)
```

`--off-speaker "<문장>"` runs **one turn from text** and exits: it skips the wake word, the mic, STT, and TTS playback, feeding the sentence straight to `execute_command()` and printing the reply. Handled in `main()` before device resolution, so no PyAudio device is opened and neither whisper nor VITS is loaded — it starts instantly and works on a machine with no audio hardware (CI, SSH). Use it to test skill routing and LLM answers without talking to the mic. An empty string is rejected with a usage hint.

`SilentTextToSpeech` (`agent/tts.py`) is the stand-in passed to the skills: same `speak()` / `output_device_index` interface, but it prints `🔇 [무음 응답] …` instead of synthesizing. It also carries a `silent = True` marker, because two paths make sound **without going through `tts.speak()`** and must be suppressed explicitly:

- `timer` skill — the alarm plays in a `timer.py` subprocess, so `run_timer_script(…, silent=True)` logs what it would have run and skips the spawn.
- `claude_p` / `hermes_api` skills — the waiting sound plays via `BackgroundSound`, so they pass `enabled=not tts.silent`; `BackgroundSound.start()` then becomes a no-op while the `start()`/`stop()` pairing in the skill stays unchanged.

Both use `getattr(tts, "silent", False)`, so any other TTS object keeps working.

`--debug-record` (a plain `argparse` store-true flag, off by default; carried on `RunConfig.debug_record`) saves each turn's raw recording to `debug_record.wav` in the working directory — a diagnostic switch for when STT is slow or mis-transcribes (see "Diagnostics" below). Unlike `--list-devices` it does **not** exit; it runs the normal pipeline with the extra dump.

`--environment` (choices `dev`/`prod`, **default `dev`**) is parsed with `argparse` and selects a preset that drives the compute device, the microphone, and the speaker. Presets live in the `ENVIRONMENTS` dict in `agent/config.py`:

- `dev` — `device=cpu`, mic index `0`, 기본 스피커 (개발환경)
- `prod` — `device=cpu`, mic/speaker matched by **name** (`"USB"`) (운영환경)

STT/TTS both run on CPU in every environment: CPU was judged the better fit for these stages. GPU (cuda) is intentionally left unused for now, reserved for a future local LLM stage — when that stage lands it will get its own device setting. The selected environment is logged at startup (`[System] 실행 환경: ...`). STT `compute_type` is derived from the device automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU); since both presets are cpu, this is currently always `int8`.

### Device selection (why by name, not index)

PortAudio assigns device indices in enumeration order, so a USB mic/speaker's index **changes across reboots and re-plugs** — a hardcoded `2` breaks. Presets therefore carry `input_device_name` / `output_device_name`: a case-insensitive **prefix** of the device name, resolved to a live index at startup by `resolve_devices()` in `agent/audio_io.py` (called once in `main()`, before models load).

- No name (dev) → the preset's `input_device_index` / `output_device_index` is used as-is.
- Name matches → that index is used, and the match is logged (with which rule matched). Multiple matches → the first is picked and the rest are logged.
- Name matches nothing → warns, prints the device list, and falls back to the preset index (`None` = system default), so a missing USB device degrades instead of crashing.

**Matching rule (`find_device_by_name`), and why it isn't an exact match:** ALSA appends a `(hw:<card>,<device>)` tag to the device name, and on a server the **card number changes on every boot** — the same mic shows up as `USB PnP Sound Device: Audio (hw:1,0)` and then `(hw:2,0)`. Two things follow:

1. Comparison is **prefix**, not exact, so a pattern like `USB PnP Sound Device` matches regardless of what trails it.
2. Both the pattern and the device name are normalized by `_normalize_device_name()` (strip → drop a trailing `(hw:N,M)` → lowercase) before comparing, so a pattern pasted **with** a stale hw tag still matches. Prefix matching alone does not cover this case: the pattern would be longer than the current name and fail.

If no device matches by prefix, it falls back to the old **substring** match (logged as `부분일치 폴백`). That keeps the `prod` preset default `"USB"` working on machines whose device name carries it mid-string (`Generic USB Audio Device`), which a prefix-only rule would silently break.

Patterns are overridable via `.env` (`AUDIO_INPUT_NAME`, `AUDIO_OUTPUT_NAME`) so prod devices can change without touching code; an empty value falls back to the preset. Run `--list-devices` on the target machine to see the real names — the `(hw:N,M)` tail can be left off. The resolved index is passed to `open_input_stream(device_index)`; if opening still fails, the available input devices are listed to aid diagnosis.

Run standalone utilities:

```bash
python timer.py 30s   # timer alarm; accepts "<N>m" or "<N>s" (e.g. 1m, 30s)
python timer.py 30s --output-device 2   # play the alarm on a specific speaker index
python text_to_wav.py --name out.wav --text "안녕하세요"   # TTS text → wav file (no playback)
```

Install dependencies (see `README.md` for the full list):

```bash
pip3 install pyaudio numpy openwakeword faster-whisper requests torch transformers scipy uroman openai silero-vad
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

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`, `WAITING_SOUND_FILE`), the waiting-sound threshold (`WAITING_SOUND_DELAY_SECONDS`), the `ENVIRONMENTS` preset dict, `parse_device_args()` (the `--environment` / `--list-devices` / `--debug-record` / `--off-speaker` argparse logic, returns a `RunConfig` NamedTuple), and `load_env_file()` (reads the git-ignored `.env` into `os.environ`).
- `agent/audio_io.py` — PyAudio helpers: `open_input_stream(device_index=None)`, `MicStream`, `list_input_devices()`, `list_output_devices()`, `find_device_by_name()` / `resolve_device_index()` / `resolve_devices()` (name → index 해석), `record_until_silence()` (VAD 동적 녹음, 현재 파이프라인용), `play_wav_file(file_path, output_device_index=None, stop_event=None, loop=False)` (`stop_event` set 시 청크 경계에서 즉시 중단, `loop`=반복 재생), `save_pcm_wav(path, pcm_bytes, rate, channels)` (16-bit PCM → wav, `--debug-record` 진단용), `_convert_pcm16()` / `_downmix_to_mono()` (다채널→모노 다운믹스 공용), `_supports_format(audio, device_index, channels, rate, fmt, kind)` (입/출력 공용, 오픈 전 레이트 지원 조회로 ALSA 경고 회피), `list_input_devices()` / `list_output_devices()` 는 공용 `_list_devices(audio, kind)` 위임.
- `agent/backgroundsound.py` — `BackgroundSound` class: 지연 임계값 후 wav 를 백그라운드 스레드에서 반복 재생하고 `stop()` 으로 멈추는 헬퍼 (대기음용, hermes / claude CLI 스킬에서 사용). `enabled=False` 면 `start()` 가 no-op (무음 모드용). `play_wav_file` 을 `audio_io` 에서 가져다 쓰는 단방향 의존.
- `agent/wakeword.py` — `load_wakeword_model()` (openwakeword built-ins, "alexa"), `get_score()`.
- `agent/vad.py` — Silero VAD (발화 종료 감지/endpointing). `load_vad()` / `load_vad_model()` (pip `silero-vad`, jit 모델 번들 → **오프라인** 로드), `SileroVAD.is_speech()` / `speech_prob()` (512 샘플=32ms 고정 창), `WINDOW_SAMPLES`.
- `agent/stt.py` — `load_stt_model()` (faster-whisper, model size from `STT_MODEL_SIZE` in `config.py`, currently `medium`), `transcribe_pcm()` (int16 PCM bytes → Korean text).
- `agent/tts.py` — `TextToSpeech` class (`facebook/mms-tts-kor` VITS via `transformers` + `torch`); `synthesize_to_file()` and `speak()` (synthesize + play). Also `SilentTextToSpeech` — 모델 로드 없이 답변을 출력만 하는 `--off-speaker` 전용 대역 (`silent = True` 표식).
- `agent/skills/` — one module per skill, each exposing `handle(user_text, tts) -> bool`:
  - `agent/skills/timer.py` — `check_timer_intent()`, `extract_time_unit()`, `format_time_korean()`, `run_timer_script()`.
  - `agent/skills/claude_p.py` — Claude Code CLI(`claude --print`) 질의 (catch-all). `is_enabled()`, `resolve_model()` / `resolve_effort()` (`.env` 값 → CLI 인자 해석), `build_command()`, `ask()`, `strip_markdown()`; 응답 지연 시 `BackgroundSound` 로 대기음 재생 (아래 "LLM stage (Claude Code CLI)" 참고). 파일명이 `claude-p.py` 가 아닌 이유는 하이픈이 들어가면 `from agent.skills import claude-p` 가 문법 오류라 스킬 등록이 불가능하기 때문.
  - `agent/skills/hermes_api.py` — hermes gateway LLM 질의 (catch-all). `is_enabled()`, `ask()`, `strip_think()`; 응답 지연 시 `BackgroundSound` 로 대기음 재생 (아래 "LLM stage (hermes gateway)" 참고).

Models are loaded once inside `main()` (not at import time), so other scripts can import individual `agent` modules without pulling in the whole pipeline. Skills load no models themselves — `handle(user_text, tts)` receives the `TextToSpeech` instance from the caller.

`main_agent.py` runs an infinite loop:

1. **Wake word** — score each mic chunk; > 0.5 on "alexa" triggers a turn.
2. **Wake acknowledgment** — plays `res0.wav` (`WAKE_RESPONSE_FILE`) so the user knows the agent is listening, then starts recording.
3. **STT** — records **dynamically** with VAD endpointing (`record_until_silence()`, see below) instead of a fixed window, then transcribes with faster-whisper (Korean). If no speech was detected (user triggered the wake word but said nothing), the turn is skipped silently.
4. **Intent + action** — `process_user_command()` (see below).
5. Calls `oww_model.reset()` after each turn to clear wake-word state.

### Microphone capture (sample-rate handling)

The pipeline needs 16 kHz mono int16 (openwakeword and faster-whisper both assume 16 kHz). Most raw hardware ALSA devices (`hw:*`) do **not** support 16 kHz directly (PyAudio would fail with `-9997 Invalid sample rate`, and `-9999` on other mismatches). `open_input_stream()` therefore returns a `MicStream` wrapper instead of a bare PyAudio stream:

1. It resolves the target device to a concrete index (default input device if none given), then **probes** whether that device supports 16 kHz / mono via `_supports_format(…, "input")` (`PyAudio.is_format_supported`) — sound-server devices (`pulse`, `default`, `sysdefault`) support this via their own conversion. Only if supported does it actually open at 16 kHz / mono.
2. If the probe says unsupported, it opens the device at its **native** sample rate and channel count (e.g. USB-C Speaker = 48000 Hz stereo) and, on every `read()`, downmixes to mono and resamples to 16 kHz in software via `scipy.signal.resample_poly`. This path logs `[System] 마이크 네이티브 …Hz/…ch → 16000Hz/모노 소프트웨어 변환 사용` at startup.

**Why probe first, not try-then-catch:** an earlier version simply called `open()` at 16 kHz and caught the `OSError`. That works, but `Pa_OpenStream` reaches the ALSA stream-configure path before failing, and PortAudio prints C-level `paInvalidSampleRate` / `PaAlsaStream_Configure … failed` warnings **directly to stderr** — which Python's `try/except` cannot suppress. `Pa_IsFormatSupported` is a lighter hw-params probe that does not enter that path, so the unsupported case is detected silently and no failed-open warning is emitted.

`MicStream` exposes the same `read()`, `start_stream()`, `stop_stream()`, `close()`, and `get_read_available()` interface as a PyAudio stream, so `main_agent.py`, `record_until_silence()`, and `flush_input_stream()` use it unchanged. `scipy` is a required dependency for this resampling path.

### Speech capture (VAD endpointing)

The turn no longer records a fixed 5 s window. `record_until_silence()` (in `agent/audio_io.py`) records **until the user stops speaking**, using Silero VAD (`agent/vad.py`) to score each frame. This makes short commands respond in ~1–2 s and lets long commands run past the old 5 s cap without being cut off; because near-silence isn't captured, it also curbs Whisper's silence-region hallucinations. State machine (params in `agent/config.py`):

- Before speech starts: if no speech is seen within `STT_START_TIMEOUT_SECONDS` (6 s), returns `b''` → `main_agent.py` skips the turn silently ("호출만 하고 말 없음").
- After speech starts: `STT_SILENCE_MS` (800 ms) of continuous silence ends the utterance — but not before `STT_MIN_RECORD_SECONDS` (0.5 s) total, so a single noise blip can't end it instantly.
- Hard cap `STT_MAX_RECORD_SECONDS` (15 s) stops runaway recording in noisy rooms.
- `VAD_THRESHOLD` (0.5) is the speech-probability cutoff per frame.

**512-sample framing:** Silero at 16 kHz requires exactly 512-sample (32 ms) windows, which isn't a divisor of `CHUNK` (1280). `record_until_silence()` therefore buffers samples across reads and feeds the VAD in 512-sample slices, carrying the remainder to the next read. `SileroVAD.reset()` clears the model's recurrent state at the start of each utterance so the previous turn doesn't leak into the next.

**Offline:** `torch.hub` would download the model from GitHub; the pip `silero-vad` package bundles the jit model, so `load_vad()` loads with no network — the offline property holds. `torch` is already a dependency (TTS/STT), so the only new package is `silero-vad`.

### Playback (sample-rate handling)

Playback has the mirror-image problem: the wav files (`res0.wav`, the TTS `response.wav`, the timer alarm, the hermes `waiting.wav`) are all 16 kHz, but raw hardware output devices reject that rate. `play_wav_file()` handles it the same way, and — like the mic path — **probes before opening** to avoid PortAudio's stderr ALSA warnings (see "Why probe first" above; these warnings surfaced every turn because playback runs each turn, whereas the mic opens once at startup):

1. It resolves the target device to a concrete index (default output device if none given), then probes whether that device supports the wav's own rate / channel count via `_supports_format(…, "output")` (`PyAudio.is_format_supported`). If supported, it opens at the wav's rate directly.
2. If unsupported, it opens at the output device's **native** rate / channels and converts the 16-bit PCM in software via `_convert_pcm16()` — mono downmix → `scipy.signal.resample_poly` → duplicate up to the target channel count. This path logs `[System] 재생 네이티브 변환: …Hz/…ch → …Hz/…ch`.

`_convert_pcm16()` only handles 16-bit PCM (all wavs in this repo are 16-bit); a non-16-bit file that the device can't open natively is skipped with a message rather than crashing. `play_wav_file()` also accepts an optional `output_device_index` (defaults to the system default output device).

### Diagnostics (slow / wrong STT)

The main loop logs per-turn timing so a slow or wrong transcription can be triaged without guessing:

```
🛑 녹음 완료! (오디오 5.2초 / 녹음대기 5.2초) 생각 중...
[System] STT 전사 소요: 17.3초
```

- **오디오 N초** — actual captured speech length (`len(pcm_bytes)/2/RATE`). Pinned near `STT_MAX_RECORD_SECONDS` (15 s) means VAD never detected end-of-speech and recorded to the hard cap.
- **STT 전사 소요** — faster-whisper transcription wall time. Much larger than the audio length (e.g. 17 s for 5 s audio) is abnormal. The usual root cause is **bad input audio**, not STT settings: faster-whisper retries a low-confidence segment across a temperature-fallback ladder (up to ~6 decodes), so garbled audio makes it **slow and wrong at the same time** — the two symptoms share one cause.

`--debug-record` dumps each turn's raw mic capture to `debug_record.wav`. Play it back (`afplay debug_record.wav` on macOS) to check the audio itself first: garbled/noisy → mic path (native-conversion resample, wrong device); clean but mis-transcribed → STT model/params. A clean TTS-generated wav (`text_to_wav.py`) transcribed via `transcribe_pcm` on the same machine is a fast baseline — if that is fast and correct while the live turn is slow and wrong, the pipeline's audio is the culprit, not the model.

Note: ctranslate2 (faster-whisper's backend) has **no Metal/GPU support on Apple Silicon**, so on a Mac STT is always CPU-only regardless of `--environment`.

**Model size (`STT_MODEL_SIZE` in `config.py`, currently `medium`):** `small` transcribes a 5 s clip in ~1.3 s on an 8-core CPU but mis-hears conversational Korean ("수도 어디야" → "수돈어디아"); `medium` is noticeably more accurate at ~4–6 s for the same clip — still interactive, and the prod machine has ample headroom. `large-v3` is more accurate again but too slow for interactive use on CPU. The first run with a new size **downloads** the model (medium ≈ 1.5 GB, one-time ~2–3 min) and then caches it; steady-state load is fast.

### Intent handling (current behavior)

`main_agent.py` holds a `SKILLS` registry — a list of `handle(user_text, tts) -> bool` functions. `execute_command()` walks the list in order and stops at the first skill that returns `True` (meaning "I handled this"). Adding a feature = write a `handle` function and register it. If no skill handles the utterance, the fallback echoes the recognized text via TTS.

**Order matters.** The registry is currently `timer.handle → claude_p.handle → hermes_api.handle`. There are **two** catch-all (LLM) skills, and both must stay behind the specific skills like `timer`:

- `claude_p` runs first because Claude Code CLI can use web search, so it answers a wider range of questions than the local model.
- `hermes_api` stays last as the fallback for when `claude_p` is off or its CLI is missing.
- Both are gated by their own `.env` switch and return `False` when off, so the chain degrades: claude → hermes → echo fallback. A new specific skill goes **before** both.

The original design (documented in `GEMINI.md`) routed transcribed text to a local **Ollama** LLM (`qwen3:14b`). That was replaced by the two LLM skills below.

**timer skill** (`agent/skills/timer.py`):

- `check_timer_intent()` — keyword + regex heuristics to decide if the utterance is a timer/stopwatch request.
- `extract_time_unit()` — parses Korean time expressions ("1분 30초", "10초") into a normalized `"<N>m"` / `"<N>s"` string.
- If a timer intent with a valid time is found, `run_timer_script()` shells out via `subprocess` to `timer.py <time>`, which sleeps then plays an alarm. It forwards the agent's speaker index (`tts.output_device_index`) as `--output-device` so the alarm plays on the **same** speaker as the rest of the agent. Without this, the timer subprocess falls back to the system default output, which in prod (headless/nohup) is unrouted — flooding the log with ALSA/JACK fallback warnings and playing the alarm on the wrong (or no) device.

### LLM stage (hermes gateway)

`agent/skills/hermes_api.py` sends any utterance no other skill claimed to a **hermes gateway** OpenAI-compatible server (`hermes gateway`, port 8642, model `qwen3:8b`) and speaks the reply. It calls the `openai` SDK with only `base_url` swapped; `max_retries=0` so a dead gateway fails fast instead of stalling the voice turn. qwen3 can emit `<think>…</think>` blocks even with thinking disabled, so `strip_think()` removes them before TTS, and the system prompt demands one or two short plain-text Korean sentences (the answer is read aloud).

Config comes from a **git-ignored `.env`** in the project root — copy `.env.example` and fill it in. `load_env_file()` in `agent/config.py` parses it with the stdlib (no `python-dotenv` dependency): `KEY=VALUE` per line, `#` comments and blank lines skipped, surrounding quotes stripped, and **real environment variables always win** over `.env` values. Keys: `HERMES_ENABLED`, `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`.

`HERMES_ENABLED` is the explicit **on/off switch**, evaluated in `is_enabled()`: a truthy value (`1`/`true`/`yes`/`on`) turns the skill on (it still needs `HERMES_API_KEY`, since the OpenAI SDK requires the field even though hermes itself needs no auth), and a falsy value (`0`/`false`/`no`/`off`) turns it off. When off, `is_enabled()` returns `False` so `handle()` exits immediately — the hermes-calling code (the `BackgroundSound` waiting sound and `ask()`) never runs — and the echo fallback handles the turn. For backward compatibility, if `HERMES_ENABLED` is **unset**, the switch falls back to `HERMES_API_KEY` presence (the original behavior). An unrecognized `HERMES_ENABLED` value is treated as off (with a warning). With no `.env` the skill returns `False` and the echo fallback runs — so **dev works unchanged without a hermes server**, and prod enables the LLM by dropping in a `.env`. If the call fails or returns an empty body, the skill speaks a short apology and returns `True` (an echo would be confusing for what was clearly a question).

**Waiting sound (응답 지연 안내):** the hermes call is a blocking HTTP request that can take several seconds. To signal "still working, not stuck", `handle()` wraps `ask()` with a `BackgroundSound` (`agent/backgroundsound.py`) that loops `WAITING_SOUND_FILE` (`soundfile/waiting.wav`) on a background thread. Two behaviors matter: (1) it only starts after a `WAITING_SOUND_DELAY_SECONDS` (0.8 s) threshold, so replies faster than that get **no** sound and are not interrupted; (2) `stop()` is called **before** `tts.speak()` on every path (success, empty, exception) and joins the playback thread, so the waiting loop's output stream is fully closed before TTS opens the same device — no two-streams-on-one-device conflict. Because the mic is already stopped during command processing (`main_agent.py`), the waiting sound is never recorded and can't re-trigger the wake word. If `waiting.wav` is missing or playback fails, the thread swallows the error and the LLM turn proceeds normally.

### LLM stage (Claude Code CLI)

`agent/skills/claude_p.py` answers an unclaimed utterance by shelling out to the locally installed **Claude Code CLI** in non-interactive mode and speaking the reply. The command it builds (`build_command()`):

```
claude --print --output-format json [--model …] [--effort …] --append-system-prompt "…" --allowedTools "WebSearch,WebFetch"
```

- **Question via stdin, not argv** — `subprocess.run(cmd, input=question)`. Same reason as `test-claude-cli.py`: a sentence starting with `-` would be read as a CLI option, and a long prompt can hit argv length limits.
- **`--model` is always passed as a full model name** (`CLAUDE_CLI_MODEL`, empty = flag omitted = CLI default). The CLI accepts aliases (`opus`, `sonnet`, `haiku`, `fable`) too, but an alias means "the latest model in that family" — so a CLI upgrade silently swaps the model underneath, changing answer quality, latency, and cost with no config edit and nothing in the log to explain it. `resolve_model()` therefore expands the aliases in `MODEL_ALIASES` (`sonnet` → `claude-sonnet-5`, `opus` → `claude-opus-5`, `haiku` → `claude-haiku-4-5`, `fable` → `claude-fable-5`) before the flag is built; matching is case-insensitive.

  **Unknown values pass through unchanged, deliberately.** A model released after this table exists (`claude-opus-6`, a new alias) then works via `.env` alone with no skill edit — the cost of that is that a typo also reaches the CLI, where it fails loudly rather than silently picking a different model.
- **`--effort`** (`CLAUDE_CLI_EFFORT`) sets how much the model thinks: `low` / `medium` / `high` / `xhigh` / `max`. Lower is faster and cheaper; for a spoken reply the latency is felt immediately, so `low`–`medium` is the useful range and `.env.example` ships `medium`. An empty value omits the flag (CLI default). An unrecognized value is **warned once and ignored** rather than failing the turn (`resolve_effort()`, `_warned_effort`) — the same degrade-don't-crash posture as the other keys here.

  The resolved model and effort are printed once per process (`[System] claude CLI 모델: … / effort: …`, guarded by `_logged_options`), not per turn: they're needed to interpret a slow or low-quality answer, but they don't change between turns.
- **`--output-format json`** so `is_error` / `result` can be read explicitly. If the JSON can't be parsed (format change), the raw stdout is spoken instead of failing the turn. `ask()` also logs the payload's `num_turns`, which is the cheapest signal for whether tools ran: `1` = the model answered directly, `≥2` = it called a tool and answered from the result. A web-dependent question that keeps logging `1` means tools are off or being denied.
- **`--allowedTools "WebSearch,WebFetch"`** (overridable via `CLAUDE_CLI_ALLOWED_TOOLS`). This is the point of the skill — hermes' local `qwen3:8b` can't answer anything current.

  **Empty value ⇒ `--tools ""`, not "omit the flag".** These are not the same thing, and the difference is a silent failure. `--allowedTools` is a *permission allowlist*, not a tool-availability list: dropping the flag leaves WebSearch/WebFetch present in the model's toolset, so the model calls one and gets refused at runtime (`Claude requested permissions to use WebFetch, but you haven't granted it yet.`). The turn is wasted and the spoken answer becomes "권한이 없어서 확인하지 못했습니다" — and because `--output-format json` returns only the final text, nothing in the log says why. To actually run tool-free, the tool list itself must be emptied with `--tools ""` (what `test-claude-cli.py` does by default unless `--with-tools`), which `build_command()` now does for an empty/whitespace `CLAUDE_CLI_ALLOWED_TOOLS`.

  Note this is reachable by config alone: `load_env_file()` writes empty `.env` values into `os.environ`, and `os.environ.get(key, DEFAULT)` only falls back to the default when the key is **absent** — so `CLAUDE_CLI_ALLOWED_TOOLS=` (key present, value blank, as shipped in `.env.example`) yields `""`, never `DEFAULT_ALLOWED_TOOLS`.

  Separately, `--append-system-prompt`'s "one or two short sentences" instruction measurably *reduces* tool use (one `WebSearch` instead of a `WebSearch` + three `WebFetch` chain on the same question). That is intended for a spoken reply — fewer tool calls than a bare `claude -p` in the terminal is not by itself a bug.
- **`--append-system-prompt`** carries the same "one or two short plain-text Korean sentences" instruction as hermes, since the answer is read aloud. `strip_markdown()` then defensively removes links, list markers, code fences, and emphasis characters that web-search answers tend to include anyway.
- **`cwd` is a temp dir** (`WORK_DIR = tempfile.gettempdir()`), not the project root. Running `claude` inside this repo would load this very `CLAUDE.md` as project context, contaminating answers to ordinary questions ("서울 수도 어디야") with repo instructions.
- **Failure paths all return `True`** (never fall through to the echo, which would be confusing for a question): a timeout speaks `TIMEOUT_MESSAGE`, any other failure or an empty answer speaks `ERROR_MESSAGE`. The one exception is a **missing `claude` binary** (`shutil.which`), which returns `False` so the turn degrades to hermes/echo; the warning prints once (`_warned_missing`) instead of every turn.
- **Waiting sound** — identical contract to the hermes section above (delay threshold, `stop()` before `tts.speak()` on every path). It matters more here: a web-search turn can run well past hermes' latency.

Config keys in `.env`: `CLAUDE_CLI_ENABLED`, `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_EFFORT`, `CLAUDE_CLI_TIMEOUT` (default 60 s), `CLAUDE_CLI_ALLOWED_TOOLS`.

`CLAUDE_CLI_ENABLED` is **explicit opt-in — unset means off**, unlike `HERMES_ENABLED`'s backward-compatible key-presence fallback. The claude CLI needs no API key, so "installed ⇒ enabled" would silently route every dev utterance to the cloud. Only `1`/`true`/`yes`/`on` turns it on; anything else (including an unrecognized value, which warns) is off.

### TTS single source

`agent/tts.py` (`TextToSpeech`) is the only TTS implementation; `timer.py` and `text_to_wav.py` import it.

## Environment assumptions

- **`main_agent.py` environment is selectable** via `--environment dev|prod` (default `dev`); the preset drives the compute device (STT/TTS) and the audio devices (`dev`=cpu/mic index 0, `prod`=cpu/USB devices matched by name — see "Device selection" above). Both presets run STT/TTS on cpu; the GPU belongs to hermes (the LLM), which runs as a separate server process rather than in-process. `timer.py` auto-detects (`cuda` if available, else `cpu`).
- **The agent is no longer 100% offline when hermes is enabled** — but hermes runs locally (127.0.0.1), so no cloud APIs are involved and the offline property holds at the network boundary.
- **`CLAUDE_CLI_ENABLED=1` does break the offline property**, unlike hermes: `agent/skills/claude_p.py` shells out to Claude Code CLI, which calls the Anthropic API (and, with `--allowedTools "WebSearch,WebFetch"`, the open web). That is the point of the skill — answering questions a local model can't — but it means utterances leave the machine. Off by default.
- Both LLM stages are gated by `.env` (`CLAUDE_CLI_ENABLED`; `HERMES_ENABLED`, falling back to `HERMES_API_KEY` presence when unset), not by `--environment`. In practice that means prod-only, since dev has no `.env`.
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- A mic and a speaker are required for the normal loop, but **not** for `--off-speaker`, which opens no audio device at all.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — venv + pip setup.
- `GEMINI.md` — original project design doc (Korean). Describes an LLM-in-the-loop pipeline built on Ollama; the LLM stage now runs on hermes gateway instead (see above).
- `.env.example` — template for the git-ignored `.env` (hermes settings, Claude Code CLI settings, audio device name patterns).
- `test_hermes_api.py` — standalone hermes connectivity check (`python test_hermes_api.py "질문"`).
- `test-claude-cli.py` — standalone Claude Code CLI check, same "질문 → 응답 → 통계" shape as the hermes one so the two backends can be compared (`python test-claude-cli.py "질문"`; `--with-tools` to allow tools, `--model` to pick a model, `--effort` to set thinking depth).

  It **imports `resolve_model()` and `EFFORT_LEVELS` from `agent/skills/claude_p.py`** rather than restating them (the same reason `test_hermes_api.py` imports `hermes_api`'s switch constants): a second copy of the alias table drifts from the skill's, and then the script built to compare the two backends is quietly measuring a different model than the agent runs. `--model sonnet` therefore expands to `claude-sonnet-5` here too, and the startup line logs the **resolved** name so a saved measurement can be traced back to a model.

  One deliberate difference: an unrecognized `--effort` is rejected by `argparse` instead of the skill's warn-and-ignore. The skill reads a config file and must degrade rather than kill a voice turn; this is a flag a human typed, and silently ignoring `--effort xhgih` would report a timing for the CLI default while labelling it otherwise.
