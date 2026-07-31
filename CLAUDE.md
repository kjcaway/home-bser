# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

An offline-first, local Korean voice assistant ("home agent") intended to run on Ubuntu with an NVIDIA GPU. The pipeline — wake word → speech-to-text → intent handling → text-to-speech — runs entirely on local CPU/GPU. The one stage that can leave the machine is the optional LLM answer step: hermes keeps it local (127.0.0.1), while the Claude Code CLI skill calls the cloud and is off by default (see "Environment assumptions"). `main_agent.py` is the entry point (a thin orchestrator); the implementation lives in the `agent/` package.

Alongside the voice loop there is a second, unattended entry path: `batch/` holds **정기 실행(배치) 잡** — work too slow to do inside a voice turn, run from cron instead. It shares the `agent/` package but reads its **own `batch/.env`**, never the root one (see "Batch jobs" below).

Note: the repo root **is itself the Python venv** (`bin/`, `include/`, `lib/`, `pyvenv.cfg` are venv artifacts, git-ignored). Source lives directly at the root alongside them. This is also why batch scripts could not go in `bin/` — the venv owns that name.

## Python 코드 규칙 (one class per file)

A `.py` file is **either** a class module **or** a function module — never a mix of both:

- **Class module** — exactly **one** top-level `class`, and **no** top-level functions. The filename is that class name in `snake_case`: `agent/mic_stream.py` → `MicStream`, `agent/barge_in_listener.py` → `BargeInListener`, `agent/silent_text_to_speech.py` → `SilentTextToSpeech`.
- **Function module** — any number of top-level functions, and **no** `class` at all. The filename names the job, not a type: `agent/audio_record.py`, `agent/text_norm.py`, `agent/wait.py`.

Module docstrings, imports, and module-level constants are fine in both. The rule is about **top-level** `class` statements only — methods, nested classes, and closures inside a function are unaffected.

**There is no exemption for "small" or "not really a" classes.** An exception (`BargeInCancelled`) and a `NamedTuple` (`RunConfig`) each get their own file too, even at ~10 lines. A rule that admits "this one is just a data holder" is a rule that needs a judgement call at every commit, and that judgement is exactly what lets a module drift back into a grab bag of one class plus fifteen functions.

Three consequences worth knowing **before** writing new code, because each one is a place where the obvious move breaks the rule:

- **A class's factory function becomes a `@classmethod`, not a module function.** `load_vad()` sitting next to `SileroVAD` violates the rule, and exiling it to another module hides where the object gets made. `SileroVAD.load()` keeps the loader with its class and the file compliant.
- **A helper shared by two class modules needs a third, lower module.** `MicStream` (capture) and `play_wav_file` (playback) both need `supports_format()` / `downmix_to_mono()`. They live in `agent/audio_format.py`, a leaf that imports no other audio module — putting them in either caller would make the two import each other.
- **Splitting a function module is a cohesion judgement, not this rule.** The rule never forces a function module to split. `agent/audio_io.py` was split into `audio_device` / `audio_record` / `audio_play` because it had grown three unrelated jobs, not because it was long.

The rule applies to `batch/` too — those modules are currently all function modules.

Check compliance mechanically (no judgement, no grep guessing):

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

`SilentTextToSpeech` (`agent/silent_text_to_speech.py`) is the stand-in passed to the skills: same `speak()` / `output_device_index` interface, but it prints `🔇 [무음 응답] …` instead of synthesizing. It also carries a `silent = True` marker, because two paths make sound **without going through `tts.speak()`** and must be suppressed explicitly:

- `timer` skill — the alarm plays in a `timer.py` subprocess, so `run_timer_script(…, silent=True)` logs what it would have run and skips the spawn.
- `claude_p` / `hermes_api` skills — the waiting sound plays via `BackgroundSound`, so they pass `enabled=not tts.silent`; `BackgroundSound.start()` then becomes a no-op while the `start()`/`stop()` pairing in the skill stays unchanged.

Both use `getattr(tts, "silent", False)`, so any other TTS object keeps working.

`--debug-record` (a plain `argparse` store-true flag, off by default; carried on `RunConfig.debug_record`) saves each turn's raw recording to `debug_record.wav` in the working directory — a diagnostic switch for when STT is slow or mis-transcribes (see "Diagnostics" below). Unlike `--list-devices` it does **not** exit; it runs the normal pipeline with the extra dump.

`--environment` (choices `dev`/`prod`, **default `dev`**) is parsed with `argparse` and selects a preset that drives the compute device, the microphone, and the speaker. Presets live in the `ENVIRONMENTS` dict in `agent/config.py`:

- `dev` — `device=cpu`, mic index `0`, 기본 스피커 (개발환경)
- `prod` — `device=cpu`, mic/speaker matched by **name** (`"USB"`) (운영환경)

STT/TTS both run on CPU in every environment: CPU was judged the better fit for these stages. GPU (cuda) is intentionally left unused for now, reserved for a future local LLM stage — when that stage lands it will get its own device setting. The selected environment is logged at startup (`[System] 실행 환경: ...`). STT `compute_type` is derived from the device automatically — `float16` for cuda, `int8` for cpu (faster-whisper does not support `float16` on CPU); since both presets are cpu, this is currently always `int8`.

### Device selection (why by name, not index)

PortAudio assigns device indices in enumeration order, so a USB mic/speaker's index **changes across reboots and re-plugs** — a hardcoded `2` breaks. Presets therefore carry `input_device_name` / `output_device_name`: a case-insensitive **prefix** of the device name, resolved to a live index at startup by `resolve_devices()` in `agent/audio_device.py` (called once in `main()`, before models load).

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
python -m agent.text_norm "GPU 8개로 Python 실행"   # TTS 발음 표기만 미리보기 (모델 로드 없음)
```

`python -m agent.text_norm "<문장>"` prints what the TTS will actually pronounce (`지피유 팔개로 파이썬 실행`) without loading VITS — the fastest way to check a mispronunciation. See "Text normalization" below.

Run batch jobs (from the repo root; see "Batch jobs" below):

```bash
cp batch/.env.example batch/.env                 # 최초 1회 (루트 .env 와 별개)
python -m batch.daily_briefing                   # 정기 LLM 요약 → batch/output/YYYY-MM-DD.md
python -m batch.daily_briefing --topic "AI 동향"   # 주제 하나만 (반복 지정 가능)
python -m batch.daily_briefing --stdout          # 파일 저장 없이 출력만
python -m batch.daily_briefing --no-notify       # 저장만, Discord 전송 생략

python -m batch.url_briefing                     # URL 브리핑 → batch/output/url-YYYY-MM-DD.md
python -m batch.url_briefing --url https://…/board   # .env 의 URL_BRIEFING_URL 대신 이 주소
python -m batch.url_briefing --stdout            # 파일 저장 없이 출력만
python -m batch.url_briefing --no-notify         # 저장만, Discord 전송 생략

./batch/run.sh                                   # cron 래퍼 (cwd·PATH·로그 처리, 기본 daily_briefing)
./batch/run.sh url_briefing                      # 잡 이름을 첫 인자로 (래퍼 수정 불필요)

python -m batch.discord_notify --file batch/output/2026-07-30.md   # 결과 파일을 Discord 로 전송
python -m batch.discord_notify "테스트 메시지"                       # 텍스트 직접 전송
python -m batch.discord_notify --file … --dry-run                  # 보내지 않고 잘린 본문만 확인
```

Install dependencies. **`requirements.txt` is the single source of truth** — the package list is not restated in `README.md` or here, because the two copies that used to exist had already drifted (README's was missing `openai`, the pinned file was missing `silero-vad`). Add a package there, nowhere else:

```bash
pip3 install -r requirements.txt
pip3 install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.20.*"   # GPU (Ubuntu + NVIDIA) 에서만
```

**cuDNN 버전 핀 필수**: `nvidia-cudnn-cu12` 는 torch 가 빌드된 cuDNN 버전과 일치해야 한다.
torch(예: cuDNN 9.20 = `torch.backends.cudnn.version()` → `92000`)보다 새 버전(예: 9.24)을
설치하면 STT(faster-whisper)는 통과하지만 TTS(VITS conv1d) 실행 시
`RuntimeError: CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH` 로 죽는다. 요구 버전은
`python -c "import importlib.metadata as m; print([r for r in m.requires('torch') if 'cudnn' in r.lower()])"`
로 확인해 핀을 맞춘다.

## Architecture

The code is split into an `agent/` package with one module per pipeline stage — subdivided further by the one-class-per-file rule above, so a stage that has both a class and functions occupies two files; `main_agent.py` only wires them together. `batch/` sits **on top of** this package (it imports `agent`, never the reverse) and is listed separately at the end:

- `agent/config.py` — audio constants (`CHUNK`, `RATE`, …), output-file names (`TTS_OUTPUT_FILE`, `WAKE_RESPONSE_FILE`, `WAITING_SOUND_FILE`), the waiting-sound threshold (`WAITING_SOUND_DELAY_SECONDS`), the wake-word thresholds (`WAKE_THRESHOLD`, `BARGE_IN_ENABLED` / `BARGE_IN_THRESHOLD`), the `ENVIRONMENTS` preset dict, `parse_device_args()` (the `--environment` / `--list-devices` / `--debug-record` / `--off-speaker` argparse logic, returns a `RunConfig`), `load_env_file()` (reads the git-ignored `.env` into `os.environ`), and its `_env_bool()` / `_env_float()` readers (empty or unparseable value → warn and keep the default, so a bad `.env` line can't kill a voice turn).
- `agent/run_config.py` — `RunConfig` NamedTuple: 실행 인자 + 환경 프리셋을 해석한 결과 (`device`, `stt_compute_type`, 장치 이름/인덱스, `list_devices`, `debug_record`, `off_speaker`, `barge_in_*`). 만드는 쪽은 `config.parse_device_args()` — 값 객체만 따로 두는 이유는 위의 코드 규칙(클래스 파일 하나에 클래스 하나) 때문이다.
- `agent/audio_format.py` — 입력/출력이 공통으로 쓰는 포맷 헬퍼 두 개: `supports_format(audio, device_index, channels, rate, fmt, kind)` (오픈 전 레이트 지원 조회로 ALSA 경고 회피), `downmix_to_mono(samples, channels)`. 오디오 모듈 중 아무 것도 import 하지 않는 **잎(leaf)** — `mic_stream` 과 `audio_play` 양쪽이 여기에 기대므로, 어느 한쪽에 두면 순환 import 가 된다.
- `agent/mic_stream.py` — `MicStream` class: 장치가 16kHz/모노를 거부하면 네이티브 레이트로 열어 `read()` 마다 16kHz 모노로 변환하는 래퍼 (아래 "Microphone capture" 참고). PyAudio 스트림과 같은 `read` / `start_stream` / `stop_stream` / `close` / `get_read_available` 인터페이스.
- `agent/audio_device.py` — 장치 이름 → 인덱스 해석 전용: `find_device_by_name()` / `resolve_device_index()` / `resolve_devices()`, `list_input_devices()` / `list_output_devices()` (공용 `_list_devices(audio, kind)` 위임), `_normalize_device_name()`.
- `agent/audio_record.py` — 마이크 입력 경로: `open_input_stream(device_index=None)` (→ `MicStream`, 실패 시 입력 장치 목록 출력), `record_until_silence()` (VAD 동적 녹음, 현재 파이프라인용), `flush_input_stream()`, `save_pcm_wav(path, pcm_bytes, rate, channels)` (16-bit PCM → wav, `--debug-record` 진단용).
- `agent/audio_play.py` — 재생 경로: `play_wav_file(file_path, output_device_index=None, stop_event=None, loop=False)` (`stop_event` set 시 청크 경계에서 즉시 중단, `loop`=반복 재생)과 그 소프트웨어 변환 헬퍼 `_convert_pcm16()`.
- `agent/background_sound.py` — `BackgroundSound` class: 지연 임계값 후 wav 를 백그라운드 스레드에서 반복 재생하고 `stop()` 으로 멈추는 헬퍼 (대기음용, hermes / claude CLI 스킬에서 사용). `enabled=False` 면 `start()` 가 no-op (무음 모드용). `play_wav_file` 을 `audio_play` 에서 가져다 쓰는 단방향 의존.
- `agent/barge_in_listener.py` — `BargeInListener` class: 답변을 만들고 들려주는 동안 마이크를 다시 열어 별도 스레드에서 호출어를 감시하고, 감지되면 `stop_event` 를 세우는 헬퍼. `reset()` / `start()` / `stop()` / `triggered`. `audio_record` 의 `flush_input_stream` 과 `wakeword` 의 `get_score` / `reset_wakeword_state` 를 가져다 쓰는 단방향 의존 (아래 "Barge-in" 참고).
- `agent/barge_in_cancelled.py` — `BargeInCancelled` 예외: 기다리던 작업을 끼어들기로 취소했음을 알린다. LLM 스킬의 `ask()` 가 올리고 `handle()` 이 받는다.
- `agent/wait.py` — `wait_for_completion(done_event, cancel_event, deadline)`: LLM 대기 구간처럼 밖에서 끊을 수 없는 블로킹 호출을 워커 스레드에 맡기고 감시해 `"done"` / `"cancelled"` / `"timeout"` 을 반환한다 (뒤처리는 호출자 몫 — 아래 "Barge-in" 참고).
- `agent/wakeword.py` — `load_wakeword_model()` (openwakeword built-ins, "alexa"), `get_score()`, `reset_wakeword_state()` (특징 버퍼에 무음을 흘려 넣어 직전 호출어의 잔상을 지운 뒤 `Model.reset()`).
- `agent/silero_vad.py` — `SileroVAD` class (발화 종료 감지/endpointing): `SileroVAD.load()` 팩토리 (pip `silero-vad`, jit 모델 번들 → **오프라인** 로드), `is_speech()` / `speech_prob()` (512 샘플=32ms 고정 창), 모듈 상수 `WINDOW_SAMPLES`. 로더가 모듈 함수(`load_vad()`)가 아니라 `@classmethod` 인 이유는 위의 코드 규칙 때문이다.
- `agent/stt.py` — `load_stt_model()` (faster-whisper, model size from `STT_MODEL_SIZE` in `config.py`, currently `medium`), `transcribe_pcm()` (int16 PCM bytes → Korean text).
- `agent/text_to_speech.py` — `TextToSpeech` class (`facebook/mms-tts-kor` VITS via `transformers` + `torch`); `synthesize_to_file()` and `speak()` (synthesize + play). 합성 직전 `normalize_for_tts()` 로 텍스트를 정규화한다. `barge_in` 속성(`BargeInListener`, 기본 `None`)이 붙어 있으면 `speak()` 가 합성·재생 구간을 그 리스너로 감싼다.
- `agent/silent_text_to_speech.py` — `SilentTextToSpeech` class: 모델 로드 없이 답변을 출력만 하는 `--off-speaker` 전용 대역 (`silent = True` 표식); 정규화 결과가 원문과 다르면 `🔡 [읽기]` 줄로 함께 출력한다.
- `agent/text_norm.py` — TTS 입력 정규화. `normalize_for_tts()` (기호 → 영문 → 숫자 순), `normalize_english()` / `english_to_hangul()` (영문 → 한글 음차), `normalize_numbers()` (숫자 → 한자어 읽기, 소수·자릿수 쉼표 포함), `normalize_symbols()`. 모델을 로드하지 않으므로 `python -m agent.text_norm "문장"` 으로 단독 실행해 발음을 미리 볼 수 있다. 아래 "Text normalization" 참고.
- `agent/skills/` — one module per skill, each exposing `handle(user_text, tts) -> bool`:
  - `agent/skills/timer.py` — `check_timer_intent()`, `extract_time_unit()`, `format_time_korean()`, `run_timer_script()`.
  - `agent/skills/claude_p.py` — Claude Code CLI(`claude --print`) 질의 (catch-all). `is_enabled()`, `resolve_model()` / `resolve_effort()` (`.env` 값 → CLI 인자 해석), `build_command()`, `ask(question, cancel_event=None)` (`Popen` + 워커 스레드, 취소 시 프로세스 kill), `strip_markdown()`; 응답 지연 시 `BackgroundSound` 로 대기음 재생 + 호출어 감시 (아래 "LLM stage (Claude Code CLI)" 참고). 파일명이 `claude-p.py` 가 아닌 이유는 하이픈이 들어가면 `from agent.skills import claude-p` 가 문법 오류라 스킬 등록이 불가능하기 때문.
  - `agent/skills/hermes_api.py` — hermes gateway LLM 질의 (catch-all). `is_enabled()`, `ask(question, cancel_event=None)` (워커 스레드, 취소 시 결과 폐기), `strip_think()`; 응답 지연 시 `BackgroundSound` 로 대기음 재생 + 호출어 감시 (아래 "LLM stage (hermes gateway)" 참고).
- `batch/` — 정기 실행(배치) 잡. 모듈 하나가 잡 하나이며, 오디오도 모델도 건드리지 않는다 (아래 "Batch jobs" 참고):
  - `batch/config.py` — `BATCH_DIR`, `DEFAULT_OUTPUT_DIR`, `load_batch_env()` (배치 전용 `.env` 적재 — 루트 `.env` 와의 분리를 담당하는 유일한 장치), `read_topics()`, `read_url()` (`URL_BRIEFING_URL`, 하나만), `read_site_name()` (`URL_BRIEFING_NAME`, 링크 라벨용), `output_dir()` (모든 잡이 공유 — 충돌 회피는 파일명 접두어 쪽).
  - `batch/claude_query.py` — 배치용 claude CLI 질의: `build_command(system_prompt=None)`, `ask(question, timeout=None, system_prompt=None)` (`subprocess.run`, 기본 300초), `fix_bullets()`, `describe_options()`. 모델/effort 해석과 실행 디렉터리는 `claude_p` 에서 import 한다. `SYSTEM_PROMPT` 는 정기 요약용 **기본값**이고, 잡마다 다른 것은 이 프롬프트뿐이라 인자로만 열려 있다 (아래 참고).
  - `batch/daily_briefing.py` — 정기 LLM 요약 잡: `main()`, `parse_args()`, `build_prompt()`, `summarize_topic()`, `render_document()`, `warn_if_too_long()` (Discord 예산 초과 경고), `notify_document()` (저장 직후 Discord 전송, 실패 시 `True`).
  - `batch/url_briefing.py` — URL 브리핑 잡 (커뮤니티 사이트 하나를 훑어 요약): `main()`, `parse_args()`, `validate_url()`, `warn_if_tool_missing()` (`WebFetch` 누락 경고), `build_prompt()`, `summarize_url()`, `site_label()` (대상 링크 라벨 — 이름 없으면 호스트명, 대괄호 제거), `render_document()`, `warn_if_too_long()`, `notify_document()`. 자체 `SYSTEM_PROMPT` 를 `claude_query.ask(system_prompt=…)` 로 넘긴다. 파일명 접두어는 `FILENAME_PREFIX`(`url-`).
  - `batch/discord_notify.py` — Discord 웹훅 전송. **잡이 아니라 잡들이 가져다 쓰는 공용 부품**이다: `webhook_url()` / `is_enabled()`, `content_length()` / `truncate()`, `send()` (실패는 예외), `notify()` (실패를 삼키고 bool), `main()` (단독 실행 CLI). `agent/` 는 물론 다른 배치 모듈도 import 하지 않는 잎(leaf)이라 어느 잡에서든 끌어다 쓸 수 있다 (아래 "Discord 알림" 참고).
  - `batch/run.sh` — cron 래퍼 (cwd → 저장소 루트, PATH 보강, `batch/logs/` 로 로깅). Python 이 아니므로 코드 규칙과 무관.

Models are loaded once inside `main()` (not at import time), so other scripts can import individual `agent` modules without pulling in the whole pipeline. Skills load no models themselves — `handle(user_text, tts)` receives the `TextToSpeech` instance from the caller.

`main_agent.py` runs an infinite loop:

1. **Wake word** — score each mic chunk; a score above `WAKE_THRESHOLD` (0.5) on "alexa" triggers a turn.
2. **Wake acknowledgment** — plays `res0.wav` (`WAKE_RESPONSE_FILE`) so the user knows the agent is listening, then starts recording.
3. **STT** — records **dynamically** with VAD endpointing (`record_until_silence()`, see below) instead of a fixed window, then transcribes with faster-whisper (Korean). If no speech was detected (user triggered the wake word but said nothing), the turn is skipped silently.
4. **Intent + action** — `execute_command()` (see below). The mic is stopped for the STT/LLM part of this step and reopened only while an answer is spoken (see "Barge-in").
5. Calls `reset_wakeword_state()` after each turn to clear wake-word state.

`run_turn()` returns **whether the answer was cut off by a barge-in**, and the loop is `while turn(): pass` — an interrupted turn opens the next one immediately instead of returning to the wake-word wait, because the user has already said "알렉사" and must not be made to say it twice.

### Microphone capture (sample-rate handling)

The pipeline needs 16 kHz mono int16 (openwakeword and faster-whisper both assume 16 kHz). Most raw hardware ALSA devices (`hw:*`) do **not** support 16 kHz directly (PyAudio would fail with `-9997 Invalid sample rate`, and `-9999` on other mismatches). `open_input_stream()` therefore returns a `MicStream` wrapper instead of a bare PyAudio stream:

1. It resolves the target device to a concrete index (default input device if none given), then **probes** whether that device supports 16 kHz / mono via `supports_format(…, "input")` (`agent/audio_format.py`, wrapping `PyAudio.is_format_supported`) — sound-server devices (`pulse`, `default`, `sysdefault`) support this via their own conversion. Only if supported does it actually open at 16 kHz / mono.
2. If the probe says unsupported, it opens the device at its **native** sample rate and channel count (e.g. USB-C Speaker = 48000 Hz stereo) and, on every `read()`, downmixes to mono and resamples to 16 kHz in software via `scipy.signal.resample_poly`. This path logs `[System] 마이크 네이티브 …Hz/…ch → 16000Hz/모노 소프트웨어 변환 사용` at startup.

**Why probe first, not try-then-catch:** an earlier version simply called `open()` at 16 kHz and caught the `OSError`. That works, but `Pa_OpenStream` reaches the ALSA stream-configure path before failing, and PortAudio prints C-level `paInvalidSampleRate` / `PaAlsaStream_Configure … failed` warnings **directly to stderr** — which Python's `try/except` cannot suppress. `Pa_IsFormatSupported` is a lighter hw-params probe that does not enter that path, so the unsupported case is detected silently and no failed-open warning is emitted.

`MicStream` exposes the same `read()`, `start_stream()`, `stop_stream()`, `close()`, and `get_read_available()` interface as a PyAudio stream, so `main_agent.py`, `record_until_silence()`, and `flush_input_stream()` use it unchanged. `scipy` is a required dependency for this resampling path.

### Speech capture (VAD endpointing)

The turn no longer records a fixed 5 s window. `record_until_silence()` (in `agent/audio_record.py`) records **until the user stops speaking**, using Silero VAD (`agent/silero_vad.py`) to score each frame. This makes short commands respond in ~1–2 s and lets long commands run past the old 5 s cap without being cut off; because near-silence isn't captured, it also curbs Whisper's silence-region hallucinations. State machine (params in `agent/config.py`):

- Before speech starts: if no speech is seen within `STT_START_TIMEOUT_SECONDS` (6 s), returns `b''` → `main_agent.py` skips the turn silently ("호출만 하고 말 없음").
- After speech starts: `STT_SILENCE_MS` (800 ms) of continuous silence ends the utterance — but not before `STT_MIN_RECORD_SECONDS` (0.5 s) total, so a single noise blip can't end it instantly.
- Hard cap `STT_MAX_RECORD_SECONDS` (15 s) stops runaway recording in noisy rooms.
- `VAD_THRESHOLD` (0.5) is the speech-probability cutoff per frame.

**512-sample framing:** Silero at 16 kHz requires exactly 512-sample (32 ms) windows, which isn't a divisor of `CHUNK` (1280). `record_until_silence()` therefore buffers samples across reads and feeds the VAD in 512-sample slices, carrying the remainder to the next read. `SileroVAD.reset()` clears the model's recurrent state at the start of each utterance so the previous turn doesn't leak into the next.

**Offline:** `torch.hub` would download the model from GitHub; the pip `silero-vad` package bundles the jit model, so `SileroVAD.load()` loads with no network — the offline property holds. `torch` is already a dependency (TTS/STT), so the only new package is `silero-vad`.

### Playback (sample-rate handling)

Playback has the mirror-image problem: the wav files (`res0.wav`, the TTS `response.wav`, the timer alarm, the hermes `waiting.wav`) are all 16 kHz, but raw hardware output devices reject that rate. `play_wav_file()` handles it the same way, and — like the mic path — **probes before opening** to avoid PortAudio's stderr ALSA warnings (see "Why probe first" above; these warnings surfaced every turn because playback runs each turn, whereas the mic opens once at startup):

1. It resolves the target device to a concrete index (default output device if none given), then probes whether that device supports the wav's own rate / channel count via `supports_format(…, "output")` (the same `agent/audio_format.py` helper the mic path uses). If supported, it opens at the wav's rate directly.
2. If unsupported, it opens at the output device's **native** rate / channels and converts the 16-bit PCM in software via `_convert_pcm16()` — mono downmix → `scipy.signal.resample_poly` → duplicate up to the target channel count. This path logs `[System] 재생 네이티브 변환: …Hz/…ch → …Hz/…ch`.

`_convert_pcm16()` only handles 16-bit PCM (all wavs in this repo are 16-bit); a non-16-bit file that the device can't open natively is skipped with a message rather than crashing. `play_wav_file()` also accepts an optional `output_device_index` (defaults to the system default output device).

### Barge-in (interrupting an answer)

The mic is stopped for most of a turn (`stream.stop_stream()` right after recording) so speaker output isn't re-recorded into the wake word. The cost of that was that **a turn could not be interrupted** — a wrong answer had to be listened to in full, and a slow LLM had to be waited out. `agent/barge_in_listener.py` reopens the mic for the two windows where it matters: **the LLM wait** and **the answer playback**.

One `BargeInListener` (created in `main()`, hung on `tts.barge_in`) serves both. It starts the stream, scores each chunk on a background thread, and on a hit sets `stop_event`. What that event does depends on who is waiting on it:

| Window | Started by | `stop_event` does |
| --- | --- | --- |
| LLM 응답 대기 | `claude_p.handle()` / `hermes_api.handle()` | `ask(cancel_event=…)` 가 보고 `BargeInCancelled` 를 올린다 (claude 는 프로세스 kill) |
| 답변 합성·재생 | `TextToSpeech.speak()` | `play_wav_file()` 이 청크 경계에서 보고 재생을 멈춘다 (~46 ms) |

`stop()` joins the thread and stops the stream again, leaving the rest of the turn exactly as it was. Both windows are opt-in via `getattr(tts, "barge_in", None)`, so `SilentTextToSpeech` (`--off-speaker`) and any other TTS object keep working untouched.

- **Synthesis is inside the window, not just playback.** VITS synthesis takes seconds; a barge-in during it skips playback entirely (`if not listener.triggered` before `play_wav_file`) rather than starting an answer the user already interrupted.
- **`triggered` persists for the whole turn.** A skill that calls `speak()` twice would otherwise play its second sentence over the user's next command; the second call logs `✋ 끼어들기 이후이므로…` and returns. `run_turn()` clears the flag with `reset()` at the start of each turn.
- **The mic is opened per window, not for the whole turn.** It stays off through recording-to-STT and is reopened by whoever owns the next window; each `start()` re-flushes. Leaving it running across a window it isn't being read in would make the listener chew through audio buffered seconds earlier, so detection would lag real time by the length of the backlog.
- **`reset_wakeword_state()` on `start()` is required, not defensive.** openwakeword's feature buffer still holds the "알렉사" that opened this turn; without flushing it the listener re-detects that same utterance on its first chunk and cancels the answer instantly.

**Why a separate, higher threshold (`BARGE_IN_THRESHOLD` = 0.7 vs `WAKE_THRESHOLD` = 0.5):** there is no acoustic echo cancellation, so during playback the mic hears the agent's own voice. At the idle threshold the agent wakes itself up on its own answer. The right value depends on how close the mic and speaker sit, so both it and the on/off switch are `.env`-overridable (`BARGE_IN_ENABLED`, `BARGE_IN_THRESHOLD`) and are read through `_env_bool()` / `_env_float()`; the resolved values are logged once at startup (`[System] 재생 중 끼어들기(barge-in): 켜짐 (임계값 0.70)`). Raise it if the agent interrupts itself, lower it if calling it doesn't take. `BARGE_IN_ENABLED=0` restores the old listen-to-the-end behavior.

**Cancelling the LLM wait.** Both skills wrap `ask()` the same way the waiting sound is wrapped: start the listener, pass `listener.stop_event` in as `cancel_event`, and stop both through one local `stop_waiting()` helper that must run **before** any `tts.speak()` (the waiting sound holds the output device, the listener holds the input device). On `BargeInCancelled` the skill speaks nothing and returns `True` — the turn is consumed, and `run_turn()` sees `triggered` and opens the next one.

The blocking call itself is cancelled differently in each skill, because only one of them can be killed:

- **`claude_p`** — `subprocess.run` became `Popen` + a worker thread doing `communicate()`, with `wait_for_completion()` watching for done / cancelled / timeout. Polling `communicate(timeout=…)` in a loop is *not* an option: after input has been sent, a second call raises `ValueError("Cannot send input after starting communication")`. On cancel or timeout the process is killed and the worker joined — the same cleanup `subprocess.run` did on timeout, and it matters here because an abandoned `claude` keeps running web searches. `TimeoutExpired` is still raised on timeout, so the existing handler is unchanged.
- **`hermes_api`** — the OpenAI SDK call has no cancel handle, so it runs in a **daemon worker whose result is discarded** on barge-in; the request itself keeps going until hermes finishes it. Switching to `stream=True` would allow closing the connection for a real cancel, but that changes the request shape and would need verifying against the gateway; a local (127.0.0.1) request capped at `max_tokens=256` finishes on its own in a few seconds, so abandoning it was the cheaper trade. The skill's own timeout still comes from the SDK client (`HERMES_TIMEOUT`), which is why `wait_for_completion()` is called without a `deadline` there.

**Still not covered: the timer alarm.** It plays from a `timer.py` subprocess, which nothing here holds a handle to. It rings while the main loop is idle, though, so the wake word already works normally during it.

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

`agent/skills/hermes_api.py` sends any utterance no other skill claimed to a **hermes gateway** OpenAI-compatible server (`hermes gateway`, port 8642, model `qwen3:8b`) and speaks the reply. It calls the `openai` SDK with only `base_url` swapped; `max_retries=0` so a dead gateway fails fast instead of stalling the voice turn. qwen3 can emit `<think>…</think>` blocks even with thinking disabled, so `strip_think()` removes them before TTS, and the system prompt demands one or two short plain-text Korean sentences (the answer is read aloud) **written with English words transliterated into Hangul** (`Python` → 파이썬, `GPU` → 지피유) — see "Text normalization" for why the TTS model can't take the alphabet.

Config comes from a **git-ignored `.env`** in the project root — copy `.env.example` and fill it in. `load_env_file()` in `agent/config.py` parses it with the stdlib (no `python-dotenv` dependency): `KEY=VALUE` per line, `#` comments and blank lines skipped, surrounding quotes stripped, and **real environment variables always win** over `.env` values. Keys: `HERMES_ENABLED`, `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`.

`HERMES_ENABLED` is the explicit **on/off switch**, evaluated in `is_enabled()`: a truthy value (`1`/`true`/`yes`/`on`) turns the skill on (it still needs `HERMES_API_KEY`, since the OpenAI SDK requires the field even though hermes itself needs no auth), and a falsy value (`0`/`false`/`no`/`off`) turns it off. When off, `is_enabled()` returns `False` so `handle()` exits immediately — the hermes-calling code (the `BackgroundSound` waiting sound and `ask()`) never runs — and the echo fallback handles the turn. For backward compatibility, if `HERMES_ENABLED` is **unset**, the switch falls back to `HERMES_API_KEY` presence (the original behavior). An unrecognized `HERMES_ENABLED` value is treated as off (with a warning). With no `.env` the skill returns `False` and the echo fallback runs — so **dev works unchanged without a hermes server**, and prod enables the LLM by dropping in a `.env`. If the call fails or returns an empty body, the skill speaks a short apology and returns `True` (an echo would be confusing for what was clearly a question).

**Waiting sound (응답 지연 안내):** the hermes call is a blocking HTTP request that can take several seconds. To signal "still working, not stuck", `handle()` wraps `ask()` with a `BackgroundSound` (`agent/background_sound.py`) that loops `WAITING_SOUND_FILE` (`soundfile/waiting.wav`) on a background thread. Two behaviors matter: (1) it only starts after a `WAITING_SOUND_DELAY_SECONDS` (0.8 s) threshold, so replies faster than that get **no** sound and are not interrupted; (2) `stop()` is called **before** `tts.speak()` on every path (success, empty, exception) and joins the playback thread, so the waiting loop's output stream is fully closed before TTS opens the same device — no two-streams-on-one-device conflict. If `waiting.wav` is missing or playback fails, the thread swallows the error and the LLM turn proceeds normally.

The mic is **open** during this window (that is what makes the wait interruptible — see "Barge-in"), so the waiting sound is heard by the wake-word listener. It is a short chime played once every `WAITING_SOUND_INTERVAL_SECONDS` rather than a continuous loop, so it is far less likely to false-trigger than the TTS answer, and the same raised `BARGE_IN_THRESHOLD` covers it. The pairing to keep intact is that the skill's `stop_waiting()` stops **both** the sound and the listener before speaking.

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

  Separately, `--append-system-prompt`'s brevity instruction ("최대 3문장") measurably *reduces* tool use (one `WebSearch` instead of a `WebSearch` + three `WebFetch` chain on the same question). That is intended for a spoken reply — fewer tool calls than a bare `claude -p` in the terminal is not by itself a bug.
- **`--append-system-prompt`** carries the same spoken-reply instructions as hermes — Korean only, plain text, summarized to at most three sentences. `strip_markdown()` then defensively removes links, list markers, code fences, and emphasis characters that web-search answers tend to include anyway.

  It also asks for **번역 and 음차 separately**: "translate English" alone leaves `Python` and `GPU` untouched, because proper nouns and acronyms are not things to translate — they need transliterating (파이썬, 지피유). That distinction is what the TTS model requires; see "Text normalization".
- **`cwd` is a temp dir** (`WORK_DIR = tempfile.gettempdir()`), not the project root. Running `claude` inside this repo would load this very `CLAUDE.md` as project context, contaminating answers to ordinary questions ("서울 수도 어디야") with repo instructions.
- **Failure paths all return `True`** (never fall through to the echo, which would be confusing for a question): a timeout speaks `TIMEOUT_MESSAGE`, any other failure or an empty answer speaks `ERROR_MESSAGE`. The one exception is a **missing `claude` binary** (`shutil.which`), which returns `False` so the turn degrades to hermes/echo; the warning prints once (`_warned_missing`) instead of every turn.
- **Waiting sound + barge-in** — identical contract to the hermes section above (delay threshold, `stop_waiting()` before `tts.speak()` on every path). Both matter more here: a web-search turn can run well past hermes' latency, so it is the window most worth being able to interrupt — and unlike hermes, the cancel actually **kills** the `claude` process rather than abandoning it (see "Barge-in").

Config keys in `.env`: `CLAUDE_CLI_ENABLED`, `CLAUDE_CLI_MODEL`, `CLAUDE_CLI_EFFORT`, `CLAUDE_CLI_TIMEOUT` (default 60 s), `CLAUDE_CLI_ALLOWED_TOOLS`.

`CLAUDE_CLI_ENABLED` is **explicit opt-in — unset means off**, unlike `HERMES_ENABLED`'s backward-compatible key-presence fallback. The claude CLI needs no API key, so "installed ⇒ enabled" would silently route every dev utterance to the cloud. Only `1`/`true`/`yes`/`on` turns it on; anything else (including an unrecognized value, which warns) is off.

### Batch jobs (`batch/`)

Work that is too slow for a voice turn, or that should run without anyone asking, lives in `batch/` and runs from cron: `python -m batch.<잡이름>` from the repo root. There are two jobs, both writing one markdown file per day (overwritten on re-run) and both notifying Discord after the save. Details and cron setup: `batch/README.md`.

- **정기 LLM 요약** (`batch/daily_briefing.py`) — walks a topic list, asks the Claude Code CLI to summarize each with web search → `batch/output/YYYY-MM-DD.md`.
- **URL 브리핑** (`batch/url_briefing.py`) — opens one community site with `WebFetch`, skims what is posted there right now, and writes an overview plus six items → `batch/output/url-YYYY-MM-DD.md`.

**The output directory is shared; the filename prefix is what keeps the two apart.** Both read `BRIEFING_OUTPUT_DIR`, and only `FILENAME_PREFIX` (`url-`) stops the same day's results from overwriting each other — cheaper than a per-job directory key, but it means a new job must pick a prefix.

**`python batch/daily_briefing.py` does not work, and that is why `-m` is the documented form.** Running a file inside the package puts `sys.path[0]` at `batch/`, so `import agent` fails; `-m` puts the cwd (repo root) on the path instead. The same cwd matters for a second reason — `agent/config.py`'s file paths are relative (`soundfile/…`) and the default output dir is repo-relative — so `batch/run.sh` `cd`s to the root before doing anything.

**Config isolation is one function, and it works by pre-emption.** `agent.config.load_env_file()` parses only on its **first** call (module-global guard), and the skills call it with no argument (root `.env`) from inside `is_enabled()`. `batch/config.py`'s `load_batch_env()` calls it first with `batch/.env`, so every later call is a no-op and the root `.env` is never read. Two consequences:

- The guard is set **before** the file-existence check, so a *missing* `batch/.env` also blocks the root one. A batch job with no config exits with code 1 rather than quietly running on the voice agent's settings — which is the failure mode worth having, since the two want different models and timeouts.
- Because the isolation is an ordering contract, `load_batch_env()` is called at the top of **every** batch entry function, not just `main()`. Whichever one you call first wins, so there is no order to remember (the same reason the skills re-call `load_env_file()` in each `is_enabled()`).

`batch/.env` is already covered by `.gitignore`'s `.env` pattern (no slash ⇒ matches at any depth); `batch/.env.example` is not, so it is committed as the template. Real environment variables still beat both, which is what makes `CLAUDE_CLI_EFFORT=low ./batch/run.sh` work for a one-off.

**Why `batch/claude_query.py` re-assembles the CLI command instead of calling `claude_p.ask()`.** Three things differ, all from "heard vs. read": the system prompt asks for a markdown list rather than three plain sentences with Hangul transliteration (a file has none of the TTS vocab limits, but it has a different budget — see below); nothing strips markdown, since that *is* the deliverable; and there is no barge-in to watch, so a plain `subprocess.run` with a much longer default timeout (300 s vs 60 s) replaces the `Popen`+thread dance. What it does **not** duplicate is `resolve_model()` / `resolve_effort()` / `WORK_DIR` — those are imported, because a second copy of the alias table drifts and then the same `.env` value runs a different model in voice than in batch (the reason `test-claude-cli.py` imports them too).

**The same reasoning is why a second job did not get a second `claude_query`.** What differs between jobs is only the *shape of the deliverable* — a topic summary vs. a skim of a community page — so `build_command()` / `ask()` take an optional `system_prompt` and everything else (command assembly, model/effort resolution, JSON handling, `fix_bullets`) is shared. `claude_query.SYSTEM_PROMPT` is the default (정기 요약); `url_briefing` passes its own. Copying `build_command()` per job would mean one copy gets fixed and the rest silently drift — the same failure the imported alias table avoids.

**The summary length is derived from the Discord budget, not from taste — and the derivation assumes one topic per run.** `SYSTEM_PROMPT` demands exactly 6 bullets, ≤200 characters each, source as an outlet name and never a URL. The arithmetic: 2000 (message body) − 90 (header) − ~16 (`## 제목`) − 9 (truncation notice) = 1885 for the bullets, and 6 × 203 ≈ 1218 → a 1324-character document. That leaves **44% of headroom** before anything is cut. It isn't tightened further because with a single topic there is nobody to share the budget with, and it isn't loosened because that headroom is the only insurance against the model ignoring the instruction. The URL ban is the same budget in another form — one link often exceeds 100 characters, and six of them eat two or three bullets' worth.

**Raising the topic count breaks this arithmetic.** Six 200-character bullets per topic runs out of budget partway through the second topic, and later topics lose even their heading (the file still has everything). Scaling up means re-dividing the prompt's allowance by the topic count — roughly 3 bullets × 100 characters at five topics.

**`url_briefing.SYSTEM_PROMPT` is the same derivation, but it pays for links.** Each bullet wraps the post title in a Discord masked link — `- [__"제목"__](<주소>) 설명` — so the arithmetic is 2000 − ~118 (title, timestamp, **and the target link**) − 9 = 1873, and an overview of 152 plus 6 × 191 ≈ 1298 → a ~1415-character document, **41% of headroom** (measured, not estimated). A bullet's 191 breaks down as `- ` 2 + link syntax 13 + title 25 + URL 70 + description 80 + newline 1.

**`render_document()` carries neither the model nor the effort, and the target is a link rather than a bare URL.** The header reads `> 대상: [__"이름"__](<주소>)` — the same shape as a bullet, so a reader learns one rule instead of two, and a long community URL stops dominating the header. The label comes from `URL_BRIEFING_NAME` / `--name`, **not** from the model: a site's name does not change between runs, so asking the model for it would only add a chance to invent one and a blank when the fetch fails. `site_label()` falls back to the hostname (an empty label renders as a link with nothing to click) and strips `[` `]`, which would otherwise break the link syntax where it is hardest to notice — in a message already delivered to the channel. Dropping the model/effort line frees more than the link syntax costs, so headroom is unchanged versus the pre-link 45%. That diagnostic is not lost, only moved: `build_command()` still logs the resolved model once per process into `batch/logs/YYYY-MM-DD.log`. Keeping a `.md` file in isolation, though, no longer tells you which model wrote it — `daily_briefing` still records it in its own header.

**The character limit counts the raw content, not what the reader sees, so masking a URL saves nothing** — it costs 13 characters *more* than a bare URL. A masked link buys legibility with budget; it does not conserve it. That is what pushed the description from 180 characters down to 80, and it is an acceptable trade because a description no longer has to stand alone: the link is right there. Title and URL length are set by the page and cannot be instructed away, so they are budgeted at averages (25 / 70) and verified against a worst case (40 / 90 → 1636, still inside the limit).

Three pieces of that link syntax each earn their place: `[…](…)` renders only because **webhook and bot messages** support masked links in `content` (user-typed messages do not); `<…>` around the URL suppresses the embed preview, without which six links attach six cards and bury the channel; `__…__` is Discord's underline, kept after a live test showed it reads better than link colour alone — at the cost of rendering as **bold** in the saved `.md`, since standard markdown gives `__` a different meaning. Banning bare URLs *inside* the description is the same budget argument as before, now sharper: a community post URL exceeds 100 characters, so repeating one outside its link costs a whole bullet.

**The model is told not to invent URLs** — only addresses actually seen on the page, otherwise title with no link. A dead link is worse than no link, and nothing downstream can detect one.

**This is also why the job takes exactly one URL rather than a comma-separated list like `BRIEFING_TOPICS`.** A second site would be cut from the message, and the fix is a second cron line (which also gets its own notification), not a longer value. `read_url()` therefore returns a single string.

The limit is a **best-effort instruction, not a guarantee**, which is why each job's `warn_if_too_long()` logs the overflow (only when a webhook is configured — warning daily about a limit that is never applied is how a warning gets ignored). Truncation stays as the backstop and cuts from the tail, so with one topic the loss is the last bullets — and since the prompt asks for most-important-first, what gets dropped is the least important.

The two jobs keep **separate copies** of that function. What they share is three lines ("over the limit ⇒ print"); what differs is the only useful part — which prompt to tighten. Parameterizing the advice would save no lines and couple the jobs.

`fix_bullets()` is the one piece of post-processing: the model intermittently drops the space in `-항목`, which then renders as literal text instead of a list item. It repairs `-` only — not `*` (line-initial `*` may be emphasis) and not `-` followed by a digit (`-5도` is a negative number, and "fixing" it would change 영하 5도 into 5도). Missing a real bullet is cosmetic; corrupting a number is not.

**Failure posture: one topic, not the whole day.** Each topic is a separate CLI call; a failure writes the reason into that section and the rest continue. Exit code tells cron what happened — `0` all succeeded, `1` did nothing (switch off / no `claude` / no target), `2` the document was written but a topic failed **or the Discord send failed**. There is no hermes fallback here, unlike the voice chain: web search is the point of the job, so a local model can't stand in.

`url_briefing` uses the same exit codes but **has no partial success** — one target means one CLI call. It still writes the failure document and still notifies, because "today's briefing broke" is exactly what should reach the channel when nobody is watching the job. Two extra `1` cases guard against a wasted 300-second call: an empty `URL_BRIEFING_URL`, and one that fails `validate_url()`. The scheme check matters more than it looks — handed `www.example.com`, the model does not fail, it web-searches for something similar and returns a plausible report, and a **wrong success** is far harder to notice than an error.

**`warn_if_tool_missing()` warns but does not block.** `WebFetch` missing from `CLAUDE_CLI_ALLOWED_TOOLS` makes the report pure model memory, so it is worth saying loudly — but the check is a substring match on a tool name, so a CLI-side rename would break the check before it breaks the job. Failing hard there would make a correctly configured job unrunnable; the warning leaves the judgement to whoever reads the log, and `ask()`'s existing `턴 수: 1 (검색 미사용)` line catches the same condition from the other side.

#### Discord 알림 (`batch/discord_notify.py`)

Batch results are delivered to a Discord channel through a webhook. This is **not a job** — it is the one shared part the jobs call, so it takes text and knows nothing about briefings. `python -m batch.discord_notify` runs it standalone (`--file` to send a file's contents, `--dry-run` to see the exact body without sending); exit codes are `0` sent, `1` config/argument problem, `2` send failed.

- **Message body, not an attachment** — so Discord's 2000-character `content` limit is the binding constraint, and `truncate()` cuts to fit and appends `(...생략)`. It cuts at a **line boundary** (a markdown bullet split mid-line reads as garbage; losing one whole line does not), falling back to a character cut when the last newline would throw away more than half the budget — otherwise a single-paragraph document would lose almost everything.
- **Length is measured in UTF-16 code units** (`content_length()`), not `len()`. Python counts code points but a non-BMP character (emoji) costs 2 in Discord's counter, so a body that measures 2000 by `len()` can be rejected with a 400. Counting the larger way can only make the message shorter.
- **Truncating, not splitting into several messages.** The full text stays in the source file (`batch/output/YYYY-MM-DD.md`); the message is an alert. Several messages per run from the same job is the failure mode that makes a channel unreadable, which defeats the alert.
- **The webhook URL is the switch** (`is_enabled()` = URL present), with no separate `DISCORD_ENABLED`. Unlike `CLAUDE_CLI_ENABLED` — where "installed ⇒ on" would silently route utterances to the cloud — there is no state here where sending is on but there is nowhere to send. Blank the URL to turn it off. The URL is also **never printed**: it is itself the credential, so anyone who reads it out of a log can post to that channel.
- **`send()` raises, `notify()` doesn't.** Jobs call `notify()`: a failed notification must not undo a briefing that is already written to disk, and a line in the cron log is enough. `send()` is for a caller that wants to decide for itself. A 429 is retried **once** after `retry_after` (capped by `MAX_RETRY_WAIT`, 5 s) — a cron job should not hang for minutes on one alert.

Keys in `batch/.env`: `DISCORD_WEBHOOK_URL`, `DISCORD_USERNAME` (optional display name — useful once several jobs post to one channel), `DISCORD_TIMEOUT` (default 15 s).

**How `daily_briefing` wires it in** (`notify_document()`, called right after the file is written):

- **It re-reads the saved file instead of sending the in-memory document.** If what landed in the channel and what sits on disk could differ, the alert stops being trustworthy — the reader opens the file expecting the same text.
- **No webhook is not a failure; a failed send is.** An unconfigured webhook returns quietly and the job still exits `0`, because notification is optional and a job that exits `2` every day trains cron alerts to be ignored. A configured webhook that fails exits `2` (with the document still on disk), since nobody watches this job — the exit code is the only way to learn the alert never went out.
- **`--no-notify` skips the send** for a same-day re-run that shouldn't post the briefing to the channel twice; `--stdout` writes no file, so it skips it too (and says so when a webhook is configured).
- A run where every topic failed **still notifies** — the document says `_요약 실패: …_`, and "the briefing broke" is exactly the thing worth pushing to a channel. The topic failure wins the exit code (`2` either way).

### TTS single source

`agent/text_to_speech.py` (`TextToSpeech`) is the only TTS implementation; `text_to_wav.py` imports it, and `timer.py` shares its playback path (`agent/audio_play.py`).

### Text normalization (why English and digits need rewriting)

The `mms-tts-kor` tokenizer's vocab is **26 tokens** — `abcdeghijklmnoprstuwy` plus quote characters. Korean reaches it through uroman romanization (`안녕하세요` → `annyeonghaseyo`), but nothing else has a path in. Three things break, all silently:

- **Digits** are absent from the vocab → dropped entirely (`65도` → `do`).
- **English passes through as spelling**, so the model reads it with *Korean* romanization rules (`python` → roughly "프이톤"), and **f/q/v/x/z aren't in the vocab at all** → they vanish. `Fox quiz vex` reaches the model as `o ui e`; `Netflix` as `netli`.
- **Symbols** (`%`, `℃`, `°`) are dropped like digits.

`agent/text_norm.py` therefore rewrites all three **into Hangul** before tokenization. Hangul is the point: the rewritten text then takes the same uroman path as the rest of the sentence, so the model only ever sees the Korean-romanization distribution it was trained on. `synthesize_to_file()` calls `normalize_for_tts()` as its first step.

Order is **symbols → English → numbers**, and it matters: `°C` must be consumed before the English pass, or that pass claims the `C` and yields "도씨"; English must run before numbers, or `MP3` splits into `MP` + `삼` and camel-case splitting misfires.

**English → Hangul** (`english_to_hangul()`) tries four things in order:

1. `_OVERRIDES` — words whose conventional Korean spelling differs from what the rules produce (`python` → 파이썬 not 파이산, `docker` → 도커 not 다커), plus units that would otherwise be spelled out letter by letter (`km` → 킬로미터, not 케이엠).
2. All-caps tokens → letter names (`AI` → 에이아이, `USB` → 유에스비), except `_ACRONYM_AS_WORD` entries like `NASA`.
3. CMU pronouncing dictionary → ARPAbet → Hangul via `_arpabet_to_hangul()`. Camel case is split first, so `ChatGPT` → `Chat` + `GPT` → 챗지피티. Overrides are checked on the **whole token before splitting**, or `YouTube` would never match its `youtube` entry and would come out 유투브.
4. OOV → `_spell_to_arpabet()`, crude spelling rules feeding the same composer. This is what handles proper nouns newer than the dictionary (`anthropic` → 앤스로픽).

`_arpabet_to_hangul()` follows 외래어 표기법 rather than transcribing phone-by-phone, because Korean coda rules are what make the result sound right:

- Only 짧은 모음 + 어말/자음앞 무성 파열음 becomes a 받침 (`book` → 북, `cap` → 캡) — otherwise `으` is appended (`take` → 테이크, `desk` → 데스크). The stop must be **immediately** after the vowel; without that check the `K` in `desk` attaches to `스` and yields 데슥.
- `[l]` before a vowel doubles to ㄹㄹ (`claude` → 클로드, `hello` → 헬로, `class` → 클래스). Without this, initial clusters come out 크로드/크래스.
- Coda `[r]` is not written (`car` → 카, `server` → 서버), since cmudict is rhotic and Korean loanwords are not.
- `AO` is deliberately **not** in `_SHORT_VOWELS` (it's long) — including it turns `walk`/`talk`/`blog` into 웍/톡/블록.
- Only `[b]` is in `_FINAL_VOICED_STOPS` (`club` → 클럽, `web` → 웹). `[g]` and `[d]` are excluded because `그`/`드` dominate actual usage (`tag` 태그, `bug` 버그, `bed` 베드).

`cmudict` is a required dependency: a single ~940 KB wheel that **bundles** its data, so there is no runtime download and the offline property holds. It is imported lazily on the first English word (~0.17 s once, then negligible), and if it is missing the module warns once and falls back to the spelling rules rather than failing a voice turn.

Both LLM skills' system prompts also ask for Hangul transliteration (see the two LLM sections above), so most replies arrive already converted. `text_norm` is the safety net for what slips through — and the only thing covering the echo fallback and timer messages, which no prompt touches.

## Environment assumptions

- **`main_agent.py` environment is selectable** via `--environment dev|prod` (default `dev`); the preset drives the compute device (STT/TTS) and the audio devices (`dev`=cpu/mic index 0, `prod`=cpu/USB devices matched by name — see "Device selection" above). Both presets run STT/TTS on cpu; the GPU belongs to hermes (the LLM), which runs as a separate server process rather than in-process. `timer.py` auto-detects (`cuda` if available, else `cpu`).
- **The agent is no longer 100% offline when hermes is enabled** — but hermes runs locally (127.0.0.1), so no cloud APIs are involved and the offline property holds at the network boundary.
- **`CLAUDE_CLI_ENABLED=1` does break the offline property**, unlike hermes: `agent/skills/claude_p.py` shells out to Claude Code CLI, which calls the Anthropic API (and, with `--allowedTools "WebSearch,WebFetch"`, the open web). That is the point of the skill — answering questions a local model can't — but it means utterances leave the machine. Off by default.
- Both LLM stages are gated by `.env` (`CLAUDE_CLI_ENABLED`; `HERMES_ENABLED`, falling back to `HERMES_API_KEY` presence when unset), not by `--environment`. In practice that means prod-only, since dev has no `.env`.
- **`batch/` reads `batch/.env`, never the root `.env`** — so enabling the claude CLI for batch summaries does *not* enable it for voice turns, and vice versa. The same key names are reused with different values on purpose (voice keeps the timeout short because latency is felt; batch sets 300 s because nobody is waiting). A batch job with no `batch/.env` exits with code 1 instead of borrowing the voice settings. Batch summaries are cloud + web calls by definition, so the offline property does not apply to them at all — and `DISCORD_WEBHOOK_URL`, when set, adds one more outbound call (the result text leaves the machine for Discord). `url_briefing` goes one step further: it fetches a named third-party site on a schedule, so that site sees a request from this machine every time cron fires.
- Audio config is fixed at 16 kHz mono, 16-bit (`CHUNK=1280`, `RATE=16000` in `agent/config.py`).
- `res0.wav` (wake acknowledgment sound) must exist in the working directory; if missing, `play_wav_file()` logs an error and the turn continues without it.
- A mic and a speaker are required for the normal loop, but **not** for `--off-speaker`, which opens no audio device at all. Batch jobs need neither — they open no audio device and load no model.
- Both entry paths must run **from the repo root** (relative file paths); `batch/run.sh` enforces this for cron, which otherwise starts in `$HOME`.
- Code comments, prompts, and print output are in Korean.

## Docs

- `README.md` — 한국어 사용자 문서: 파이프라인/스킬 디스패처 다이어그램, 모듈 표, venv + 설치, 실행 방법(`--environment`, `--off-speaker`, 장치 선택, 끼어들기, 진단), 배치 잡, 상시 실행(nohup). 이 파일(`CLAUDE.md`)과 다루는 범위가 겹치는데, 나누는 기준은 **무엇을 하는가(README) vs. 왜 그렇게 되어 있는가(CLAUDE.md)** 다. 동작을 바꾸는 변경은 양쪽 모두 손봐야 한다.
- `requirements.txt` — 의존성 **정본**. 직접 import 하는 패키지만 핀과 함께 적고, 설치 방법·CUDA 런타임 주석·핀 갱신 명령도 파일 상단에 있다. 목록을 문서로 복사하지 말 것 (그래서 어긋났었다).
- `GEMINI.md` — original project design doc (Korean). Describes an LLM-in-the-loop pipeline built on Ollama; the LLM stage now runs on hermes gateway instead (see above).
- `.env.example` — template for the git-ignored `.env` (hermes settings, Claude Code CLI settings, audio device name patterns, barge-in switch/threshold). Covers the **voice agent only** — batch has its own.
- `batch/README.md` — batch job list, 잡별 실행법, 공용 모듈(Discord 알림), cron 등록(래퍼가 처리하는 cwd/PATH/로그, 잡별 `DISCORD_USERNAME` 구분), 종료 코드, 설정 격리 방식, 새 잡 추가 절차.
- `batch/.env.example` — template for the git-ignored `batch/.env` (batch-side Claude CLI settings, `BRIEFING_TOPICS`, `BRIEFING_OUTPUT_DIR`, `URL_BRIEFING_URL`, `URL_BRIEFING_NAME`, Discord 웹훅 설정 `DISCORD_WEBHOOK_URL` / `DISCORD_USERNAME` / `DISCORD_TIMEOUT`).
- `test_hermes_api.py` — standalone hermes connectivity check (`python test_hermes_api.py "질문"`).
- `test-claude-cli.py` — standalone Claude Code CLI check, same "질문 → 응답 → 통계" shape as the hermes one so the two backends can be compared (`python test-claude-cli.py "질문"`; `--with-tools` to allow tools, `--model` to pick a model, `--effort` to set thinking depth).

  It **imports `resolve_model()` and `EFFORT_LEVELS` from `agent/skills/claude_p.py`** rather than restating them (the same reason `test_hermes_api.py` imports `hermes_api`'s switch constants): a second copy of the alias table drifts from the skill's, and then the script built to compare the two backends is quietly measuring a different model than the agent runs. `--model sonnet` therefore expands to `claude-sonnet-5` here too, and the startup line logs the **resolved** name so a saved measurement can be traced back to a model.

  One deliberate difference: an unrecognized `--effort` is rejected by `argparse` instead of the skill's warn-and-ignore. The skill reads a config file and must degrade rather than kill a voice turn; this is a flag a human typed, and silently ignoring `--effort xhgih` would report a timing for the CLI default while labelling it otherwise.
