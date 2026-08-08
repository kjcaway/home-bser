# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

An offline-first, local Korean voice assistant ("home agent") for Ubuntu. The pipeline — wake word → STT → intent handling → TTS — runs entirely locally; the one stage that can leave the machine is the optional LLM answer step (hermes stays on 127.0.0.1, the Claude Code CLI skill calls the cloud and is off by default).

Two entry paths:

- `main_agent.py` — the voice loop. A thin orchestrator; the implementation lives in `agent/`.
- `batch/` — 정기 실행(배치) 잡 run from cron. It imports `agent/` but reads its **own `batch/.env`**, never the root one.

The repo root **is itself the venv** (`bin/`, `lib/`, `pyvenv.cfg` are git-ignored venv artifacts) with source alongside it — which is also why batch scripts could not live in `bin/`.

## Python 코드 규칙 (one class per file)

A `.py` file is **either** a class module **or** a function module — never both:

- **Class module** — exactly one top-level `class`, no top-level functions. Filename = that class in `snake_case` (`agent/mic_stream.py` → `MicStream`).
- **Function module** — any number of top-level functions, no `class`. Filename names the job, not a type (`agent/text_norm.py`).

Docstrings, imports, and constants are fine in both; the rule is about **top-level** `class` statements only.

**There is no exemption for "small" classes.** An exception (`BargeInCancelled`) and a `NamedTuple` (`RunConfig`) each get their own file — a rule that admits "this one is just a data holder" needs a judgement call at every commit.

Three consequences worth knowing before writing new code, because each is where the obvious move breaks the rule:

- **A class's factory becomes a `@classmethod`, not a module function** (`SileroVAD.load()`).
- **A helper shared by two class modules needs a third, lower module** — `agent/audio_format.py` is a leaf both sides import; putting it in either would make them import each other.
- **Splitting a function module is a cohesion judgement, not this rule** — `audio_io.py` was split because it had grown three unrelated jobs, not because it was long.

The rule applies to `batch/` too (currently all function modules). Check it mechanically:

```bash
./bin/python - <<'PY'
import ast, pathlib
for f in (sorted(pathlib.Path(".").glob("*.py"))
          + sorted(pathlib.Path("agent").rglob("*.py"))
          + sorted(pathlib.Path("batch").rglob("*.py"))):
    top = ast.parse(f.read_text(encoding="utf-8")).body
    cls = [n.name for n in top if isinstance(n, ast.ClassDef)]
    fns = [n.name for n in top if isinstance(n, ast.FunctionDef)]
    if len(cls) > 1 or (cls and fns):
        print(f"위반 {f}: class={cls} func={fns}")
PY
```

## Commands

Everything runs inside the venv (the repo root): `source bin/activate`

```bash
python main_agent.py                    # 기본: dev
python main_agent.py --environment prod # 운영환경 (USB 장치를 이름으로 탐색)
python main_agent.py --list-devices     # 장치 이름/인덱스 확인 후 종료
python main_agent.py --debug-record     # 매 턴 녹음 원본을 debug_record.wav 로 저장
python main_agent.py --off-speaker "오늘 날씨는 어때?"   # 마이크/스피커 없이 문장 하나만

python timer.py 30s [--output-device 2]               # 타이머 알람 ("<N>m" / "<N>s")
python text_to_wav.py --name out.wav --text "안녕하세요"  # TTS → wav (재생 없음)
python -m agent.text_norm "GPU 8개로 Python 실행"        # 발음 표기 미리보기 (모델 로드 없음)
```

`--off-speaker "<문장>"` runs **one turn from text** and exits: no wake word, mic, STT, or playback — the sentence goes straight to `execute_command()`. Handled before device resolution, so no PyAudio device is opened and no model is loaded; it starts instantly on a machine with no audio hardware (CI, SSH). `SilentTextToSpeech` stands in for the TTS object and carries a `silent = True` marker, because two paths make sound **without going through `tts.speak()`**: the `timer` skill (alarm plays in a subprocess → `run_timer_script(silent=True)` skips the spawn) and the LLM skills' `BackgroundSound` waiting sound (`enabled=not tts.silent`). Both read it via `getattr(tts, "silent", False)`, so any other TTS object keeps working.

Batch jobs (from the repo root; operational detail in `batch/README.md`):

```bash
cp batch/.env.example batch/.env                 # 최초 1회 (루트 .env 와 별개)
python -m batch.daily_briefing [--topic 주제] [--stdout] [--no-notify]
python -m batch.url_briefing [--url 주소] [--name 이름]
python -m batch.url_briefing_gemini              # 같은 프롬프트를 gemini CLI 로
python -m batch.discord_notify --file <경로> [--dry-run]
./batch/run.sh [잡이름] [인자…]                    # cron 래퍼 (cwd·PATH·로그)
```

Install dependencies. **`requirements.txt` is the single source of truth** — do not restate the package list in any doc (the two copies that used to exist had already drifted):

```bash
pip3 install -r requirements.txt
pip3 install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.20.*"   # GPU (Ubuntu + NVIDIA) 에서만
```

**cuDNN 버전 핀 필수**: a version newer than what torch was built against passes STT but kills TTS with `CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH`. Check the requirement with `python -c "import importlib.metadata as m; print([r for r in m.requires('torch') if 'cudnn' in r.lower()])"`.

## Architecture

`agent/` holds one module per pipeline stage (subdivided further by the one-class-per-file rule); `main_agent.py` only wires them together. `batch/` sits **on top of** the package — it imports `agent`, never the reverse.

| 모듈 | 역할 |
| --- | --- |
| `agent/config.py` | 오디오 상수, 출력 파일명, 임계값(`WAKE_THRESHOLD` 0.5, `BARGE_IN_*`), `ENVIRONMENTS` 프리셋, `parse_device_args()`, `load_env_file()` + `_env_bool()`/`_env_float()` (잘못된 값은 경고 후 기본값 — 나쁜 `.env` 한 줄이 음성 턴을 죽이지 않게) |
| `agent/run_config.py` | `RunConfig` NamedTuple — 실행 인자 + 프리셋 해석 결과 |
| `agent/audio_format.py` | `supports_format()` / `downmix_to_mono()` — 입출력이 공유하는 잎 |
| `agent/mic_stream.py` | `MicStream` — 장치가 16kHz/모노를 거부하면 네이티브로 열고 `read()` 마다 변환 |
| `agent/audio_device.py` | 장치 이름 → 인덱스 해석 (`find_device_by_name`, `resolve_devices`, 목록 출력) |
| `agent/audio_record.py` | `open_input_stream()`, `record_until_silence()`, `flush_input_stream()`, `save_pcm_wav()` |
| `agent/audio_play.py` | `play_wav_file(…, stop_event, loop)` 과 소프트웨어 변환 헬퍼 |
| `agent/background_sound.py` | `BackgroundSound` — 지연 임계값 후 대기음 반복 재생 (`enabled=False` 면 no-op) |
| `agent/barge_in_listener.py` | `BargeInListener` — 답변 중 호출어를 감시해 `stop_event` 를 세운다 |
| `agent/barge_in_cancelled.py` | `BargeInCancelled` 예외 |
| `agent/wait.py` | `wait_for_completion()` — 블로킹 호출을 워커에 맡기고 done/cancelled/timeout 반환 |
| `agent/wakeword.py` | openwakeword("alexa") 로드·점수·상태 리셋 |
| `agent/silero_vad.py` | `SileroVAD` (발화 종료 감지). `load()` 는 `@classmethod`, 512 샘플 고정 창 |
| `agent/stt.py` | faster-whisper 로드/전사 (`STT_MODEL_SIZE` = `medium`) |
| `agent/text_to_speech.py` | `TextToSpeech` (`facebook/mms-tts-kor` VITS). 합성 직전 `normalize_for_tts()` |
| `agent/silent_text_to_speech.py` | `--off-speaker` 전용 무음 대역 (`silent = True`) |
| `agent/text_norm.py` | TTS 입력 정규화 (기호 → 영문 → 숫자) |
| `agent/skills/timer.py` | 타이머 인텐트 판별·시간 파싱·`timer.py` 실행 |
| `agent/skills/claude_p.py` | Claude Code CLI 질의 (catch-all) |
| `agent/skills/hermes_api.py` | hermes gateway 질의 (catch-all, 폴백) |
| `batch/config.py` | `load_batch_env()`(설정 격리), `read_topics/url/site_name()`, `output_dir()`, `output_path(job, when)` |
| `batch/claude_query.py` | 배치용 claude CLI 질의 (`build_command`, `ask`) |
| `batch/gemini_query.py` | 배치용 gemini CLI 질의 — claude 판과 시그니처·반환값·실패 방식이 같은 교체용 부품 |
| `batch/markdown_fix.py` | `fix_bullets()` 만 있는 잎. 두 백엔드가 공유 |
| `batch/daily_briefing.py` | 정기 LLM 요약 잡 |
| `batch/url_briefing.py` | URL 브리핑 잡 |
| `batch/url_briefing_gemini.py` | 위 잡의 gemini 판 (결과물의 모양은 전부 import) |
| `batch/discord_notify.py` | Discord 웹훅 전송. 잡이 아니라 잡들이 쓰는 잎 |
| `batch/run.sh` | cron 래퍼 |

Models load once inside `main()` (not at import time), so other scripts can import individual `agent` modules without pulling in the whole pipeline. Skills load no models — `handle(user_text, tts)` receives the TTS instance from the caller.

The main loop is `while turn(): pass`: wake word (score > `WAKE_THRESHOLD`) → play `res0.wav` → record with VAD endpointing → transcribe → `execute_command()` → `reset_wakeword_state()`. `run_turn()` returns **whether the answer was cut off by a barge-in**, and an interrupted turn opens the next one immediately instead of returning to the wake-word wait — the user already said "알렉사" and must not be made to say it twice.

## 주요 동작

### Environment presets / device selection

`--environment dev|prod` (default `dev`) picks a preset from `ENVIRONMENTS` in `agent/config.py`: dev = cpu / mic index 0, prod = cpu / devices matched **by name** (`"USB"`). STT and TTS run on CPU in both — CPU was judged the better fit for these stages, and the GPU is reserved for a future local LLM stage. `compute_type` is derived from the device (cuda → float16, cpu → int8).

**Why by name, not index:** PortAudio indices change across reboots and re-plugs, so a hardcoded `2` breaks. `resolve_devices()` resolves names to live indices at startup. Matching is a **prefix** comparison, and both sides are normalized by `_normalize_device_name()` (strip → drop a trailing `(hw:N,M)` → lowercase) because ALSA's card number changes every boot — prefix matching alone would fail on a pattern pasted with a stale hw tag. If nothing matches by prefix it falls back to a **substring** match, which keeps prod's `"USB"` working on names carrying it mid-string (`Generic USB Audio Device`). No match at all → warn, print the device list, fall back to the preset index. Patterns are `.env`-overridable (`AUDIO_INPUT_NAME`, `AUDIO_OUTPUT_NAME`).

### Sample-rate handling (capture and playback)

The pipeline needs 16 kHz mono int16, but raw hardware ALSA devices (`hw:*`) usually reject 16 kHz. Both `MicStream` and `play_wav_file()` handle it the same way:

1. **Probe** with `supports_format()`; if supported, open at that rate directly.
2. Otherwise open at the device's native rate/channels and convert in software (`scipy.signal.resample_poly`, plus mono downmix / channel duplication).

**Why probe instead of try-then-catch:** opening at 16 kHz and catching the `OSError` works, but `Pa_OpenStream` reaches the ALSA configure path before failing and PortAudio prints C-level warnings **directly to stderr**, which Python's `try/except` cannot suppress. `Pa_IsFormatSupported` never enters that path. `_convert_pcm16()` only handles 16-bit PCM (all wavs here are); anything else is skipped with a message rather than crashing.

### Speech capture (VAD endpointing)

`record_until_silence()` records **until the user stops speaking** using Silero VAD instead of a fixed window: short commands respond in ~1–2 s, long ones aren't cut off, and skipping near-silence curbs Whisper's hallucinations. Params in `agent/config.py` — 6 s to start speaking (else `b''` and the turn is skipped silently), 800 ms of silence ends the utterance (but not before 0.5 s total), 15 s hard cap, `VAD_THRESHOLD` 0.5.

Silero requires exactly 512-sample windows at 16 kHz, which is not a divisor of `CHUNK` (1280), so samples are buffered across reads and fed in 512-sample slices with the remainder carried over. `reset()` clears the recurrent state each utterance. The pip `silero-vad` package **bundles** the jit model, so the offline property holds (`torch.hub` would download it).

### Barge-in (interrupting an answer)

The mic is stopped for most of a turn so speaker output isn't re-recorded — the cost being that a turn **could not be interrupted**. One `BargeInListener` (created in `main()`, hung on `tts.barge_in`) reopens it for the two windows that matter:

| 구간 | 여는 쪽 | `stop_event` 가 하는 일 |
| --- | --- | --- |
| LLM 응답 대기 | `claude_p.handle()` / `hermes_api.handle()` | `ask(cancel_event=…)` 가 `BargeInCancelled` 를 올린다 (claude 는 프로세스 kill) |
| 답변 합성·재생 | `TextToSpeech.speak()` | `play_wav_file()` 이 청크 경계에서 재생을 멈춘다 (~46 ms) |

- **Synthesis is inside the window** — VITS takes seconds, so a barge-in during it skips playback entirely rather than starting an answer the user already interrupted.
- **`triggered` persists for the whole turn** — a skill calling `speak()` twice would otherwise play its second sentence over the user's next command. `run_turn()` clears it with `reset()`.
- **The mic is opened per window, not for the whole turn** — leaving it running would make the listener chew through audio buffered seconds earlier, lagging detection by the length of the backlog.
- **`reset_wakeword_state()` on `start()` is required** — openwakeword's feature buffer still holds the "알렉사" that opened this turn, and without flushing it the listener re-detects it on the first chunk and cancels the answer instantly.

**Why a higher threshold than the wake word (0.7 vs 0.5):** there is no acoustic echo cancellation, so during playback the mic hears the agent's own voice and at the idle threshold it wakes itself up. The right value depends on mic/speaker placement, so both it and the switch are `.env`-overridable (`BARGE_IN_ENABLED`, `BARGE_IN_THRESHOLD`) and logged once at startup. Raise it if the agent interrupts itself, lower it if calling it doesn't take.

Cancellation differs per skill because only one is killable: **`claude_p`** uses `Popen` + a worker thread and actually kills the process (an abandoned `claude` keeps running web searches; polling `communicate(timeout=…)` is not an option — a second call raises `ValueError` after input has been sent). **`hermes_api`** has no cancel handle in the OpenAI SDK, so it runs in a daemon worker whose **result is discarded**; a local request capped at `max_tokens=256` finishes on its own, so abandoning it was cheaper than switching to `stream=True`. **The timer alarm is still not covered** — it plays from a subprocess nothing here holds a handle to, but it rings while the loop is idle, so the wake word already works during it.

### Intent handling (skill dispatch)

`main_agent.py` holds a `SKILLS` list of `handle(user_text, tts) -> bool` functions; `execute_command()` walks it and stops at the first `True`. Nothing handled it → the recognized text is echoed via TTS. Adding a feature = write a `handle` and register it.

**Order matters:** `timer.handle → claude_p.handle → hermes_api.handle`. There are **two** catch-alls and both must stay behind specific skills. `claude_p` runs first because it can use web search; `hermes_api` is the fallback for when claude is off or its CLI is missing. Both return `False` when their `.env` switch is off, so the chain degrades claude → hermes → echo. A new specific skill goes **before** both.

The `timer` skill forwards the agent's speaker index (`tts.output_device_index`) to `timer.py` as `--output-device`. Without it the subprocess falls back to the system default, which in prod (headless/nohup) is unrouted — flooding the log with ALSA/JACK warnings and playing the alarm on the wrong device.

### LLM stage

**hermes gateway** (`agent/skills/hermes_api.py`) — a local OpenAI-compatible server (port 8642, `qwen3:8b`). The `openai` SDK with only `base_url` swapped and `max_retries=0`, so a dead gateway fails fast instead of stalling the turn. qwen3 can emit `<think>` blocks even with thinking disabled → `strip_think()`. Keys: `HERMES_ENABLED` (falls back to `HERMES_API_KEY` presence when unset, for backward compatibility), `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`. With no `.env` the skill returns `False` and dev works unchanged without a hermes server.

**Claude Code CLI** (`agent/skills/claude_p.py`) — `claude --print --output-format json [--model …] [--effort …] --append-system-prompt "…" --allowedTools "WebSearch,WebFetch"`.

- **Question via stdin, not argv** — a sentence starting with `-` would be read as an option, and a long prompt can hit argv limits.
- **`--model` is always a full model name.** An alias means "the latest model in that family", so a CLI upgrade silently swaps the model with nothing in the log to explain it; `resolve_model()` expands `MODEL_ALIASES` first. **Unknown values pass through deliberately** — a model newer than the table then works via `.env` alone, at the cost of a typo reaching the CLI, where it fails loudly.
- **`--effort`** low/medium/high/xhigh/max. For a spoken reply latency is felt immediately, so low–medium is the useful range. An unrecognized value is **warned once and ignored** rather than failing the turn.
- **An empty `--allowedTools` means `--tools ""`, not "omit the flag".** The allowlist is a *permission* list: dropping the flag leaves the tools present, so the model calls one and gets refused at runtime ("권한이 없어서 확인하지 못했습니다") with nothing in the JSON output explaining why. Reachable by config alone, since `load_env_file()` writes empty values into `os.environ`.
- **`--output-format json`** so `is_error` / `result` can be read explicitly (unparseable JSON → speak raw stdout rather than fail). `num_turns` is logged as the cheapest signal for whether tools ran: `1` = answered from memory, `≥2` = a tool was called.
- **`cwd` is a temp dir** — running `claude` inside this repo would load this very `CLAUDE.md` as project context and contaminate ordinary answers.
- **Failure paths all return `True`** (an echo would be confusing for a question), except a **missing `claude` binary**, which returns `False` to degrade to hermes/echo and warns once.

Keys: `CLAUDE_CLI_ENABLED`, `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_EFFORT`, `CLAUDE_CLI_TIMEOUT` (60 s), `CLAUDE_CLI_ALLOWED_TOOLS`. `CLAUDE_CLI_ENABLED` is **explicit opt-in — unset means off**, unlike hermes: the CLI needs no API key, so "installed ⇒ enabled" would silently route every dev utterance to the cloud.

**Waiting sound**, shared by both skills: `BackgroundSound` starts only after `WAITING_SOUND_DELAY_SECONDS` (0.8 s), so fast replies get no sound, and `stop()` runs **before `tts.speak()` on every path** (success, empty, exception) — the waiting sound holds the output device and the listener holds the input device. Keeping that pairing intact is the rule. If `waiting.wav` is missing the thread swallows the error and the turn proceeds.

### Text normalization

The `mms-tts-kor` tokenizer's vocab is **26 tokens** (`abcdeghijklmnoprstuwy` plus quotes). Korean reaches it via uroman romanization; nothing else has a path in, so three things break silently: **digits** are dropped (`65도` → `do`), **English** is read with Korean romanization rules and f/q/v/x/z vanish entirely (`Netflix` → `netli`), **symbols** are dropped.

`agent/text_norm.py` rewrites all three **into Hangul** — that is the point, since the result then takes the same uroman path the model was trained on. Order is **symbols → English → numbers** and it matters (`°C` must be consumed before the English pass or it yields "도씨"; English before numbers or `MP3` splits).

`english_to_hangul()` tries four things: `_OVERRIDES` (conventional spellings and units — `python` → 파이썬, `km` → 킬로미터) → all-caps as letter names (`USB` → 유에스비, except `_ACRONYM_AS_WORD`) → CMU dict → ARPAbet → Hangul (camel case split first; overrides are checked on the **whole token before splitting**, or `YouTube` would never match) → OOV spelling rules (`anthropic` → 앤스로픽). `_arpabet_to_hangul()` follows 외래어 표기법 rather than transcribing phone-by-phone: only a short vowel + word-final/pre-consonant voiceless stop becomes a 받침 (`book` → 북 vs `desk` → 데스크), `[l]` before a vowel doubles to ㄹㄹ (`claude` → 클로드), coda `[r]` is not written (`car` → 카), `AO` is deliberately excluded from `_SHORT_VOWELS` (else `walk`/`blog` → 웍/블록), and only `[b]` is in `_FINAL_VOICED_STOPS` (`web` → 웹, but `tag` → 태그). `cmudict` is a required dependency that **bundles** its data (no runtime download), imported lazily on the first English word; if missing it warns once and falls back to the spelling rules.

Both LLM prompts also ask for Hangul transliteration, so most replies arrive converted. `text_norm` is the safety net — and the only thing covering the echo fallback and timer messages, which no prompt touches.

### Diagnostics (slow / wrong STT)

Each turn logs `🛑 녹음 완료! (오디오 5.2초 / …)` and `[System] STT 전사 소요: …`. Audio length pinned near the 15 s hard cap means VAD never detected end-of-speech. Transcription time much larger than the audio length (17 s for 5 s) is abnormal, and the usual cause is **bad input audio, not STT settings** — faster-whisper retries low-confidence segments across a temperature-fallback ladder (up to ~6 decodes), so garbled audio is **slow and wrong at the same time**.

`--debug-record` dumps the raw capture to `debug_record.wav`; play it first — garbled → mic path (resample, wrong device), clean but mis-transcribed → model/params. A clean `text_to_wav.py` wav transcribed on the same machine is a fast baseline.

**Model size** (`STT_MODEL_SIZE`, currently `medium`): `small` does a 5 s clip in ~1.3 s but mis-hears conversational Korean ("수도 어디야" → "수돈어디아"); `medium` is noticeably more accurate at 4–6 s, still interactive. `large-v3` is too slow on CPU. Changing the size downloads the model once (medium ≈ 1.5 GB). Note ctranslate2 has **no Metal/GPU support on Apple Silicon**, so STT on a Mac is always CPU-only.

### Batch jobs (`batch/`)

Work too slow for a voice turn, or that should run without anyone asking. Three jobs, each writing one markdown file per run and notifying Discord after the save: `daily_briefing` (topic summaries via web search), `url_briefing` (skim one community site), `url_briefing_gemini` (the same prompt through the gemini CLI). Operational detail lives in `batch/README.md`.

- **One config key, one directory per job.** All jobs share `BRIEFING_OUTPUT_DIR`, but `output_path(job_name, when)` picks `<root>/<job>/YYYY-MM-DD-HH.md`. The filename prefix this replaced only worked while one run overwrote the last; once files accumulate, a shared directory means eyeballing past another job's results.
- **The stamp stops at the hour**, so a retry overwrites while cron lines at different hours keep separate files. Both the stamp and the document's date come from one `datetime.now()` read in `main()` — a run crossing midnight would otherwise file `# 2026-08-02 브리핑` as `2026-08-03-00.md`.
- **Config isolation is one function, and it works by pre-emption.** `load_env_file()` parses only on its **first** call; `load_batch_env()` calls it with `batch/.env` first, so every later call from a skill is a no-op and the root `.env` is never read. The guard is set **before** the file-existence check, so a *missing* `batch/.env` also blocks the root one and the job exits `1` rather than quietly running on the voice agent's settings. Because the isolation is an ordering contract, it is called at the top of **every** batch entry function.
- **`python batch/daily_briefing.py` does not work** — `sys.path[0]` becomes `batch/` and `import agent` fails. `-m` puts the repo root on the path, and the same cwd matters for the relative paths in `agent/config.py`, which is why `batch/run.sh` `cd`s to the root first.

**Why `batch/claude_query.py` re-assembles the command instead of calling `claude_p.ask()`** — three things differ, all from "heard vs. read": the system prompt asks for a markdown list rather than three plain sentences, nothing strips markdown (it *is* the deliverable), and with no barge-in to watch a plain `subprocess.run` with a much longer timeout (300 s vs 60 s) replaces the `Popen`+thread dance. What it does **not** duplicate is `resolve_model()` / `resolve_effort()` / `WORK_DIR` — a second copy of the alias table drifts, and then the same `.env` value runs a different model in voice than in batch.

**The system prompt lives in the job, and the shared modules have no default for it.** The deliverable's shape *is* the job's identity, and everything derived from it (the header, the directory name, the advice `warn_if_too_long()` prints) already sits in the job module. A default would be the worse failure: a job that forgot its prompt would quietly render in the *other* job's shape, detectable only by reading the output, whereas a required argument fails at the call with `TypeError`.

**All three jobs deliver the same shape on purpose** — an overview sentence or two, a blank line, then exactly 6 `-` bullets whose title is a Discord masked link (`- [__"제목"__](<주소>) 설명`). The notifications land side by side in one channel, so a reader should learn one rule. The link syntax's three pieces are explained once, in `url_briefing.SYSTEM_PROMPT`: `[…](…)` renders only in **webhook/bot** messages, `<…>` suppresses the embed preview (without it six links attach six cards and bury the channel), and `__…__` is Discord's underline — at the cost of rendering as bold in the saved `.md`.

**The length is derived from the Discord budget (2000-character body), not from taste, and assumes one target per run.** Overview ≤150 characters plus 6 bullets with ≤80-character descriptions → a ~1360–1415 character document (41–47% headroom, measured). The limit counts the **raw content, not what the reader sees**, so masking a URL saves nothing — it costs 13 characters more than a bare one. That is what pushed descriptions from 180–200 down to 80, an acceptable trade because the link is right there. Title and URL length are set by the page and budgeted at averages (25 / 70), verified against a worst case. Raising the topic or URL count breaks this arithmetic, so a second cron line (which also gets its own notification) is the answer rather than a longer value. The instruction is best-effort, so each job's `warn_if_too_long()` logs the overflow — only when a webhook is configured, since warning daily about a limit that is never applied is how a warning gets ignored — and `truncate()` remains the backstop, cutting from the tail where the least important bullets are. The two copies of that function are not merged: what they share is three lines, what differs is the only useful part (which prompt to tighten).

`fix_bullets()` is the one piece of post-processing: the model intermittently drops the space in `-항목`, which then renders as literal text. It repairs `-` only — not `*` (line-initial `*` may be emphasis) and not `-` followed by a digit (`-5도` is a negative number). Missing a real bullet is cosmetic; corrupting a number is not.

**Failure posture: one topic, not the whole day.** Each topic is a separate CLI call, and a failure writes the reason into that section while the rest continue. Exit codes tell cron what happened — `0` all succeeded, `1` did nothing (switch off / no CLI / no target), `2` the document was written but a summary or the Discord send failed. There is no hermes fallback: web search is the point of the job. `url_briefing` uses the same codes but **has no partial success**; it still writes the failure document and still notifies, because "today's briefing broke" is exactly what should reach the channel when nobody is watching. Two extra `1` cases guard against a wasted 300-second call — an empty URL and one that fails `validate_url()`. The scheme check matters most: handed `www.example.com` the model does not fail, it web-searches for something similar and returns a plausible report, and a **wrong success is far harder to notice than an error**.

**`warn_if_tool_missing()` warns but does not block.** It is a substring match on a tool name, so a CLI-side rename would break the check before it breaks the job; failing hard there would make a correctly configured job unrunnable. The same condition surfaces from the other side in `턴 수: 1 (검색 미사용)`.

#### Gemini 백엔드

`gemini_query` is a deliberate **drop-in for `claude_query`** — same `ask(question, system_prompt, timeout=None) -> markdown`, same "failures raise" contract — because that symmetry is what lets a job swap backends by changing an import. `url_briefing_gemini` therefore **imports its shape** (`SYSTEM_PROMPT`, `build_prompt`, `render_document`, `warn_if_too_long`, `notify_document`, `validate_url`, `site_label`, exit codes) and keeps only what the backend changes. If the prompt were copied, one copy would get tuned and the job would stop comparing two models and start comparing two prompts.

It does not import `claude_query`, because that module drags in `agent.skills.claude_p` and with it pyaudio/scipy; the one piece genuinely shared (`fix_bullets`) was pulled out into `batch/markdown_fix.py`. Four CLI differences, all forced rather than chosen:

- **No system-prompt flag.** The CLI builds its input as `stdin + "\n\n" + --prompt`, so the split is **question → stdin, system prompt → `--prompt`**: the value that changes per run stays out of argv, and the model gets material first, instructions second. `--prompt` also switches headless mode on explicitly rather than relying on stdin not being a TTY.
- **No `--effort`**, so there is no `GEMINI_CLI_EFFORT` key either — a key for a flag that does not exist gets filled in and believed.
- **`--allowed-tools` means the opposite thing.** gemini **removes** approval-requiring tools (`web_fetch`, shell, edit) from the toolset in non-interactive mode and only rescues what this list names, so omitting one produces no error at all — the model just writes from memory. It is an array flag, so `resolve_allowed_tools()` splits the comma-separated value and passes it once per name.
- **stderr is noisy.** `clean_stderr()` drops node `DeprecationWarning`s, auth lines, and stack frames — the caller truncates the message to 300 characters, and an un-stripped stack pushes the actual reason out of the window. Only unambiguous lines are removed.

`log_usage()` prints the model that **actually answered**, since gemini can fall back to flash on quota. The document says nothing about which backend wrote it (the header shape is shared, and a Discord message carries no file path), so tell them apart with `DISCORD_USERNAME` per cron line; on disk `JOB_NAME` already separates them.

**Auth is the one thing config cannot fix on its own.** A CLI signed in as `oauth-personal` may be rejected (`IneligibleTierError`), and `GEMINI_API_KEY` alone is not enough: `validateNonInteractiveAuth` prefers `security.auth.selectedType` from `~/.gemini/settings.json` over the environment, so that value must become `"gemini-api-key"` or be removed. Separately, gemini usually lives under nvm — the node version is baked into that path, so it is left to the crontab's `PATH=` line rather than hardcoded in `run.sh`.

#### Discord 알림

`batch/discord_notify.py` is **not a job** — it is the shared part the jobs call, so it takes text and knows nothing about briefings. Keys: `DISCORD_WEBHOOK_URL`, `DISCORD_USERNAME`, `DISCORD_TIMEOUT`.

- **Message body, not an attachment**, so the 2000-character limit binds and `truncate()` appends `(...생략)`. It cuts at a **line boundary** (a bullet split mid-line reads as garbage; losing one whole line does not), falling back to a character cut when the last newline would throw away more than half the budget.
- **Length is measured in UTF-16 code units** — Python counts code points, but an emoji costs 2 in Discord's counter, so a body that measures 2000 by `len()` can be rejected with a 400.
- **Truncating, not splitting.** The full text stays in the source file; several messages per run is the failure mode that makes a channel unreadable, which defeats the alert.
- **The webhook URL is the switch** (`is_enabled()`), with no separate `DISCORD_ENABLED` — there is no state where sending is on but there is nowhere to send. The URL is **never printed**: it is itself the credential.
- **`send()` raises, `notify()` doesn't.** Jobs call `notify()` — a failed notification must not undo a briefing already on disk. A 429 is retried **once** after `retry_after` (capped at 5 s); a cron job should not hang for minutes on one alert.
- Jobs' `notify_document()` **re-reads the saved file** instead of sending the in-memory document: if the channel and the disk could differ, the alert stops being trustworthy. No webhook is not a failure (a job exiting `2` daily trains cron alerts to be ignored); a configured webhook that fails is `2`, since the exit code is the only way to learn the alert never went out.

## Environment assumptions

- **`main_agent.py`'s environment is selectable** via `--environment dev|prod`. Both presets run STT/TTS on cpu; the GPU belongs to hermes, which runs as a separate server process. `timer.py` auto-detects cuda/cpu.
- **hermes does not break the offline property** — it runs on 127.0.0.1, so nothing leaves the machine at the network boundary.
- **`CLAUDE_CLI_ENABLED=1` does break it** — the CLI calls the Anthropic API and, with tools allowed, the open web. That is the point of the skill, but utterances leave the machine, so it is off by default.
- Both LLM stages are gated by `.env`, not by `--environment` — in practice prod-only, since dev has no `.env`.
- **`batch/` reads `batch/.env`, never the root one** — enabling the claude CLI for batch does not enable it for voice, and vice versa. The same key names carry different values on purpose (voice keeps timeouts short because latency is felt; batch sets 300 s because nobody is waiting). Batch summaries are cloud + web calls by definition, and `DISCORD_WEBHOOK_URL` adds one more outbound call. `url_briefing` goes further: a named third-party site sees a request from this machine every time cron fires — and running the gemini job alongside it doubles that, with the second copy going to Google.
- Audio is fixed at 16 kHz mono 16-bit (`CHUNK=1280`, `RATE=16000`).
- `res0.wav` must exist in the working directory; if missing, the turn continues without it.
- A mic and speaker are required for the normal loop but **not** for `--off-speaker`, which opens no audio device. Batch jobs need neither.
- Both entry paths must run **from the repo root** (relative paths); `batch/run.sh` enforces this for cron.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — 한국어 사용자 문서. The split from this file is **what it does (README) vs. why it is that way (CLAUDE.md)**; a behavior change touches both.
- `requirements.txt` — the dependency source of truth. Do not copy the list into docs.
- `batch/README.md` — batch **operations only** (running, config keys, cron). The reasoning (budget arithmetic, why the backends are separate) lives here.
- `.env.example` / `batch/.env.example` — templates for the git-ignored voice and batch configs.
- `GEMINI.md` — the original design doc (Korean), describing an Ollama-based LLM pipeline since replaced by the two LLM skills.
- `test_hermes_api.py` / `test-claude-cli.py` — standalone checks for the two backends, same "질문 → 응답 → 통계" shape so they can be compared. The latter **imports** `resolve_model()` and `EFFORT_LEVELS` from the skill rather than restating them, or the script built to compare backends would quietly measure a different model than the agent runs. One deliberate difference: an unrecognized `--effort` is rejected by argparse instead of warned-and-ignored, because this is a flag a human typed and silently ignoring it would report a timing for the CLI default under the wrong label.
