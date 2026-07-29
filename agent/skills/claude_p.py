"""Claude Code CLI(`claude -p`) 질의응답 스킬.

로컬에 설치된 `claude` 명령을 비대화형 모드(`-p/--print`)로 실행해 사용자의 질문을
넘기고, 돌아온 답변을 TTS 로 읽어줍니다. 웹 검색 도구(WebSearch/WebFetch)를 허용해
hermes(로컬 LLM)가 알 수 없는 최신 정보도 답할 수 있는 것이 차이입니다.

실행하는 명령은 다음과 같습니다 (test-claude-cli.py 와 동일한 형태):

    claude --print --output-format json --allowedTools "WebSearch,WebFetch" < (질문)

질문은 인자가 아니라 **표준입력**으로 넘깁니다. '-' 로 시작하는 문장이 CLI 옵션으로
오해받거나 긴 질문이 인자 길이 제한에 걸리는 것을 피하기 위함이며,
`test-claude-cli.py` 와 같은 방식입니다.

이 스킬도 hermes 와 마찬가지로 **catch-all(폴백)** 성격이라 다른 스킬이 처리하지
못한 모든 문장을 받습니다. `main_agent.py` 의 SKILLS 리스트에서 timer 같은
구체 스킬들보다 **뒤에** 두어야 합니다.

설정은 프로젝트 루트의 `.env` 파일에서 읽습니다 (`.env.example` 참고):

    CLAUDE_CLI_ENABLED=1
    CLAUDE_CLI_MODEL=sonnet
    CLAUDE_CLI_TIMEOUT=60
    CLAUDE_CLI_ALLOWED_TOOLS=WebSearch,WebFetch

on/off 는 `CLAUDE_CLI_ENABLED` 로만 제어합니다. hermes 와 달리 **미설정이면 꺼짐**
입니다 — claude CLI 는 별도 키가 필요 없어 설치만 되어 있으면 켜지는 구조가 되면
개발환경에서 의도치 않게 모든 발화가 claude 로 나가기 때문입니다(명시적 opt-in).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time

from agent.backgroundsound import BackgroundSound
from agent.config import (
    WAITING_SOUND_FILE,
    WAITING_SOUND_DELAY_SECONDS,
    WAITING_SOUND_INTERVAL_SECONDS,
    load_env_file,
)

DEFAULT_ALLOWED_TOOLS = "WebSearch,WebFetch"
DEFAULT_TIMEOUT = 60.0

# 응답은 TTS 로 읽히므로 짧은 한국어 평문을 요구한다. (hermes 스킬과 동일한 지침)
# CLI 자체 규칙이 담긴 기본 시스템 프롬프트는 살려두고 --append-system-prompt 로 덧붙인다.
SYSTEM_PROMPT = (
    "당신은 한국어 음성 비서입니다. "
    "질문에 한국어로 한두 문장으로 짧게 답하세요. "
    "마크다운, 이모지, 특수기호, URL 은 사용하지 마세요."
)

# 호출 실패 시 사용자에게 들려줄 안내 문구
ERROR_MESSAGE = "죄송합니다. 지금은 답변을 가져오지 못했습니다."
TIMEOUT_MESSAGE = "죄송합니다. 답변이 너무 오래 걸려서 중단했습니다."

# CLAUDE_CLI_ENABLED 로 인정하는 truthy/falsy 문자열
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# claude 를 실행할 작업 디렉터리.
# 프로젝트 디렉터리에서 실행하면 저장소의 CLAUDE.md 가 프로젝트 지침으로 딸려 들어가
# 일반 질문("서울 수도 어디야")에까지 이 저장소 문맥이 섞인다. 중립적인 임시
# 디렉터리에서 실행해 그 영향을 없앤다.
WORK_DIR = tempfile.gettempdir()

# claude 명령을 못 찾았다는 경고를 매 턴 반복 출력하지 않기 위한 플래그
_warned_missing = False


def is_enabled() -> bool:
    """.env 의 CLAUDE_CLI_ENABLED 로 이 스킬의 on/off 를 판단한다.

    - truthy(`1`/`true`/`yes`/`on`) → 켜짐
    - 그 외(falsy·미설정·해석 불가) → 꺼짐

    hermes 와 달리 미설정을 '켜짐'으로 볼 근거(API 키)가 없으므로 명시적 opt-in 이다.
    """
    load_env_file()

    flag = os.environ.get("CLAUDE_CLI_ENABLED")
    if flag is None or flag.strip() == "":
        return False

    normalized = flag.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    # 인식할 수 없는 값은 안전하게 꺼짐으로 처리
    print(f"[경고] CLAUDE_CLI_ENABLED 값을 해석할 수 없어 claude CLI 를 끕니다: {flag!r}")
    return False


def strip_markdown(text: str) -> str:
    """TTS 로 읽으면 곤란한 마크다운/URL 표기를 제거한다.

    시스템 프롬프트로 평문을 요구하지만, 웹 검색 결과를 인용하면 링크나 목록 기호가
    섞여 나오는 경우가 있어 방어적으로 정리한다.
    """
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)      # 코드 블록
    text = re.sub(r"`([^`]*)`", r"\1", text)                     # 인라인 코드
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)         # [텍스트](링크) → 텍스트
    text = re.sub(r"https?://\S+", " ", text)                    # 남은 URL
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.MULTILINE)  # 목록 기호
    text = re.sub(r"[*_#>|]+", " ", text)                        # 강조/헤딩/인용/표 기호
    return re.sub(r"\s+", " ", text).strip()


def build_command() -> list:
    """`claude --print ...` 명령줄을 조립합니다."""
    # json 형식은 오류 여부(is_error)와 소요 시간까지 함께 주므로 진단에 유리하다.
    cmd = ["claude", "--print", "--output-format", "json"]

    model = os.environ.get("CLAUDE_CLI_MODEL")
    if model:
        cmd += ["--model", model]

    if SYSTEM_PROMPT:
        cmd += ["--append-system-prompt", SYSTEM_PROMPT]

    # 웹 검색 도구만 허용. 빈 값으로 두면 도구 없이 모델 지식만으로 답한다.
    #
    # 주의: 빈 값일 때 --allowedTools 를 그냥 생략하면 '도구 없음'이 되지 않는다.
    # 도구 목록은 그대로 살아 있어서 모델이 WebSearch/WebFetch 를 호출하고,
    # 허용 목록에 없으니 런타임에 거부당한다("Claude requested permissions to use
    # WebFetch, but you haven't granted it yet."). 턴만 낭비하고 답변은
    # "권한이 없어서 확인하지 못했습니다"가 되는데, --output-format json 은 최종
    # 텍스트만 주므로 로그에서 원인이 보이지 않는다. 도구를 진짜 끄려면
    # --tools "" 로 도구 목록 자체를 비워야 한다 (test-claude-cli.py 와 같은 방식).
    allowed_tools = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS).strip()
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    else:
        cmd += ["--tools", ""]

    return cmd


def ask(question: str) -> str:
    """claude CLI 에 질문을 보내고 TTS 로 읽을 수 있게 정리된 답변을 반환합니다.

    실행 실패/오류 응답은 예외를 올리므로, 호출자가 처리해야 합니다.
    (subprocess.TimeoutExpired 는 그대로 전달되어 호출자가 따로 안내할 수 있다.)
    """
    timeout = float(os.environ.get("CLAUDE_CLI_TIMEOUT", DEFAULT_TIMEOUT))

    proc = subprocess.run(
        build_command(),
        input=question,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=WORK_DIR,
    )

    if proc.returncode != 0:
        stderr = proc.stderr.strip()
        raise RuntimeError(f"claude 비정상 종료 (exit {proc.returncode}): {stderr[:300]}")

    stdout = proc.stdout.strip()
    if not stdout:
        return ""

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # 출력 형식이 예상과 달라도 말은 하도록 원문을 그대로 정리해 쓴다.
        return strip_markdown(stdout)

    if payload.get("is_error"):
        raise RuntimeError(f"claude 오류 결과: {payload.get('subtype', 'unknown')}")

    # num_turns 는 도구를 썼는지 판별하는 가장 싼 지표다. 1 이면 모델이 곧바로
    # 답한 것(도구 미사용), 2 이상이면 도구를 호출하고 그 결과로 다시 답한 것.
    # 웹 검색이 필요한 질문인데 계속 1 이 찍히면 도구가 꺼졌거나 거부당하는
    # 상태이므로 CLAUDE_CLI_ALLOWED_TOOLS 설정을 확인한다.
    num_turns = payload.get("num_turns")
    if num_turns is not None:
        print(f"[System] claude 턴 수: {num_turns} ({'도구 사용' if num_turns > 1 else '도구 미사용'})")

    return strip_markdown(payload.get("result") or "")


# ==========================================
# claude CLI 질의 스킬 진입점
# ==========================================
def handle(user_text: str, tts) -> bool:
    """claude CLI 질의 스킬 진입점 (catch-all).

    .env 에서 꺼져 있거나 `claude` 명령이 없으면 False 를 반환하여 디스패처가 다음
    스킬(hermes / 에코 폴백)로 넘어가게 합니다. 켜져 있으면 질문을 보내고 답변을
    읽어준 뒤 True 를 반환합니다.

    tts 는 speak(text) 메서드를 가진 TTS 엔진입니다.
    """
    global _warned_missing

    if not is_enabled():
        return False

    if shutil.which("claude") is None:
        # 설정은 켜져 있는데 CLI 가 없는 상태. 매 턴 같은 경고를 반복하지 않는다.
        if not _warned_missing:
            _warned_missing = True
            print("[경고] `claude` 명령을 찾을 수 없어 claude CLI 스킬을 건너뜁니다.")
            print("       Claude Code CLI 설치 여부와 PATH 등록을 확인하세요.")
        return False

    print(f"-> claude CLI 에 질문합니다: {user_text}")

    # 웹 검색까지 도는 질의는 수 초 이상 걸릴 수 있으므로, 임계값을 넘기면 대기음을
    # 재생해 '멈춘 게 아님'을 알린다. TTS 와 같은 장치를 동시에 열지 않도록
    # answer 를 말하기 전에 반드시 stop()(스트림 정리까지 대기)을 호출한다.
    waiting = BackgroundSound(
        WAITING_SOUND_FILE,
        output_device_index=tts.output_device_index,
        delay_seconds=WAITING_SOUND_DELAY_SECONDS,
        interval_seconds=WAITING_SOUND_INTERVAL_SECONDS,
        # 대기음은 tts 를 거치지 않고 직접 재생되므로, 무음 모드(--off-speaker)에서는
        # 여기서 꺼야 한다.
        enabled=not getattr(tts, "silent", False),
    )
    waiting.start()

    start = time.monotonic()
    try:
        answer = ask(user_text)
    except subprocess.TimeoutExpired:
        waiting.stop()
        print("[오류] claude CLI 응답이 제한 시간을 초과했습니다.")
        print("       .env 의 CLAUDE_CLI_TIMEOUT 을 늘리거나 CLAUDE_CLI_MODEL 을 더 빠른 모델로 바꿔보세요.")
        tts.speak(TIMEOUT_MESSAGE)
        return True
    except Exception as e:
        waiting.stop()
        print(f"[오류] claude CLI 호출 실패: {e}")
        tts.speak(ERROR_MESSAGE)
        return True
    elapsed = time.monotonic() - start

    waiting.stop()   # answer 를 말하기 전에 대기음을 멈추고 스트림 정리까지 대기

    if not answer:
        print("[오류] claude CLI 응답이 비어 있습니다.")
        tts.speak(ERROR_MESSAGE)
        return True

    print(f"[응답] {answer} ({elapsed:.2f}초)")
    tts.speak(answer)
    return True
