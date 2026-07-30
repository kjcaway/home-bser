"""hermes gateway LLM 질의 스킬.

운영환경(우분투)에서 `hermes gateway` 로 띄운 OpenAI 호환 API 서버에 사용자의
질문을 그대로 넘기고, 돌아온 답변을 TTS 로 읽어줍니다.

이 스킬은 **catch-all(폴백)** 성격이라 다른 스킬이 처리하지 못한 모든 문장을
받습니다. 따라서 `main_agent.py` 의 SKILLS 리스트에서 **가장 마지막**에 등록해야
합니다.

설정은 프로젝트 루트의 `.env` 파일에서 읽습니다 (`.env.example` 참고):

    HERMES_ENABLED=1
    HERMES_BASE_URL=http://127.0.0.1:8642/v1
    HERMES_API_KEY=...
    HERMES_MODEL=qwen3:8b
    HERMES_TIMEOUT=30

on/off 는 `HERMES_ENABLED` 로 명시적으로 제어합니다:

- 켜짐(`1`/`true`/`yes`/`on`) → 기존처럼 hermes 에 질의합니다. (`HERMES_API_KEY` 도
  있어야 OpenAI SDK 를 만들 수 있습니다.)
- 꺼짐(`0`/`false`/`no`/`off`) → 스킬이 즉시 False 를 반환하여 hermes API 를 호출하는
  코드(대기음 재생·`ask()`)가 **아예 실행되지 않고** 기존 에코 폴백이 동작합니다.
- `HERMES_ENABLED` 미설정 → 하위호환을 위해 `HERMES_API_KEY` 존재 여부로 판단합니다.

hermes 가 없는 개발환경(dev)에서는 .env 를 두지 않으면 되고, 그러면 기존
에코 폴백이 그대로 동작합니다.
"""

import os
import re
import threading
import time

from agent.background_sound import BackgroundSound
from agent.barge_in_cancelled import BargeInCancelled
from agent.config import (
    WAITING_SOUND_FILE,
    WAITING_SOUND_DELAY_SECONDS,
    WAITING_SOUND_INTERVAL_SECONDS,
    load_env_file,
)
from agent.wait import wait_for_completion

DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 30.0

# 응답은 TTS 로 읽히므로 짧은 한국어 평문을 요구한다.
#
# 영문을 한글로 적으라고 요구하는 이유는 TTS 모델 제약이다. mms-tts-kor 의 vocab 은
# 26자뿐이라 영문이 그대로 오면 한국어 로마자로 잘못 읽히고 f/q/v/x/z 는 아예 사라진다
# (agent/text_norm.py 참고). 여기서 원천 차단하는 편이 표기가 자연스럽고, text_norm 의
# 자동 변환은 그래도 새어 나오는 것을 받는 안전망으로 남는다.
SYSTEM_PROMPT = (
    "당신은 한국어 음성 비서입니다. "
    "질문에 한국어로 한두 문장으로 짧게 답하세요. "
    "마크다운, 이모지, 특수기호는 사용하지 마세요. "
    "영문 단어와 약어는 알파벳 그대로 쓰지 말고 한글 음차로 적으세요. "
    "(예: Python 은 파이썬, GPU 는 지피유, 8GB 는 팔 기가바이트)"
)

# 호출 실패 시 사용자에게 들려줄 안내 문구
ERROR_MESSAGE = "죄송합니다. 지금은 답변을 가져오지 못했습니다."

# HERMES_ENABLED 로 인정하는 truthy/falsy 문자열
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# OpenAI 클라이언트는 최초 사용 시 한 번만 만들어 재사용한다.
_client = None


def strip_think(text: str) -> str:
    """qwen3 가 생성하는 <think>...</think> 사고 블록을 제거합니다.

    thinking 을 꺼도 빈 <think></think> 태그가 남는 경우가 있어 방어적으로 제거한다.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def is_enabled() -> bool:
    """.env 의 HERMES_ENABLED 로 이 스킬의 on/off 를 판단한다.

    - HERMES_ENABLED 가 truthy → 켜짐 (단, OpenAI SDK 생성에 HERMES_API_KEY 도 필요)
    - HERMES_ENABLED 가 falsy → 꺼짐 (hermes API 호출 코드가 실행되지 않음)
    - HERMES_ENABLED 미설정 → 하위호환: HERMES_API_KEY 존재 여부로 판단
    """
    load_env_file()

    flag = os.environ.get("HERMES_ENABLED")
    if flag is not None and flag.strip() != "":
        normalized = flag.strip().lower()
        if normalized in _FALSE_VALUES:
            return False
        if normalized in _TRUE_VALUES:
            return bool(os.environ.get("HERMES_API_KEY"))
        # 인식할 수 없는 값은 안전하게 꺼짐으로 처리
        print(f"[경고] HERMES_ENABLED 값을 해석할 수 없어 hermes 를 끕니다: {flag!r}")
        return False

    # HERMES_ENABLED 미설정 → 기존 동작(키 존재 여부)
    return bool(os.environ.get("HERMES_API_KEY"))


def _get_client():
    """OpenAI SDK 클라이언트를 생성(최초 1회)하여 반환합니다.

    base_url 만 hermes 로 바꾼 OpenAI 호환 호출이다. 음성 응답은 지연에 민감하므로
    SDK 기본 재시도(2회)는 끄고 실패를 곧바로 알린다.
    """
    global _client
    if _client is None:
        # openai 패키지는 이 스킬을 쓸 때만 필요하므로 지연 임포트한다.
        from openai import OpenAI

        _client = OpenAI(
            base_url=os.environ.get("HERMES_BASE_URL", DEFAULT_BASE_URL),
            api_key=os.environ["HERMES_API_KEY"],
            timeout=float(os.environ.get("HERMES_TIMEOUT", DEFAULT_TIMEOUT)),
            max_retries=0,
        )
    return _client


def ask(question: str, cancel_event=None) -> str:
    """hermes 에 질문을 보내고 <think> 블록이 제거된 답변 문자열을 반환합니다.

    호출 실패 시 예외를 그대로 올리므로, 호출자가 처리해야 합니다.

    cancel_event(threading.Event)를 주면 응답을 기다리는 동안 set 되는 즉시
    BargeInCancelled 를 올립니다(사용자가 호출어로 끼어든 경우).

    claude 스킬과 달리 **요청 자체는 취소되지 않는다.** OpenAI SDK 의 블로킹 호출은
    밖에서 끊을 수단이 없어, 호출을 데몬 워커 스레드에 남겨두고 결과만 버린다.
    스트리밍(`stream=True`)으로 바꾸면 연결을 닫아 진짜로 끊을 수 있지만, 그러면
    요청 형태가 달라져 hermes 게이트웨이 쪽 동작까지 같이 검증해야 한다. 여기서는
    요청을 그대로 두는 쪽을 골랐다 — hermes 는 로컬(127.0.0.1)이고 max_tokens 가
    256 이라 버려진 요청도 몇 초 안에 스스로 끝나기 때문이다.
    """
    client = _get_client()
    model = os.environ.get("HERMES_MODEL", DEFAULT_MODEL)
    done = threading.Event()
    result = {}

    def call():
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                max_tokens=256,
                temperature=0.7,
            )
            result["text"] = strip_think(response.choices[0].message.content or "")
        except Exception as e:
            result["error"] = e
        finally:
            done.set()

    worker = threading.Thread(target=call, daemon=True)
    worker.start()

    # 제한 시간은 SDK 클라이언트가 이미 갖고 있으므로(HERMES_TIMEOUT) 여기서는
    # 끼어들기만 감시한다 — 응답이 없으면 워커가 그 타임아웃으로 예외를 물고 끝난다.
    if wait_for_completion(done, cancel_event) == "cancelled":
        raise BargeInCancelled()

    if "error" in result:
        raise result["error"]
    return result.get("text", "")


# ==========================================
# hermes 질의 스킬 진입점
# ==========================================
def handle(user_text: str, tts) -> bool:
    """hermes LLM 질의 스킬 진입점 (catch-all).

    .env 에 hermes 설정이 없으면 False 를 반환하여 디스패처의 기존 에코 폴백이
    동작하게 합니다. 설정이 있으면 질문을 보내고 답변을 읽어준 뒤 True 를 반환합니다.

    tts 는 speak(text) 메서드를 가진 TTS 엔진입니다.
    """
    if not is_enabled():
        return False

    print(f"-> hermes 에 질문합니다: {user_text}")

    # 응답이 지연되면(임계값 초과) 대기음을 반복 재생해 '처리 중'임을 알린다.
    # 임계값 안에 응답이 오면 재생을 시작하지 않는다. TTS 재생과 겹치지 않도록
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

    # 응답을 기다리는 동안에도 호출어를 감시해 사용자가 질문을 바꿀 수 있게 한다.
    # 재생 중 끼어들기와 같은 리스너이며, 감지되면 stop_event 가 서고 그것을
    # cancel_event 로 받은 ask() 가 BargeInCancelled 를 올린다.
    listener = getattr(tts, "barge_in", None)
    if listener is not None:
        listener.start()

    def stop_waiting():
        """대기음과 호출어 감시를 함께 멈춘다 (TTS 재생 전 반드시 호출).

        대기음은 출력 장치를, 감시는 입력 장치를 잡고 있어 둘 다 정리해야 이어지는
        tts.speak() 가 깨끗한 상태에서 시작한다.
        """
        waiting.stop()
        if listener is not None:
            listener.stop()

    start = time.monotonic()
    try:
        answer = ask(user_text, cancel_event=listener.stop_event if listener else None)
    except BargeInCancelled:
        stop_waiting()
        print(f"[System] 사용자 끼어들기 — hermes 응답을 버립니다 "
              f"({time.monotonic() - start:.1f}초 대기 후).")
        # 답변은 말하지 않는다. 턴은 처리한 것으로 보고(True) 끝내면, 호출부가
        # 끼어들기를 감지해 곧바로 다음 명령을 받는다.
        return True
    except Exception as e:
        stop_waiting()
        print(f"[오류] hermes API 호출 실패: {e}")
        print("       hermes gateway 가 실행 중인지, .env 의 HERMES_BASE_URL 이 맞는지 확인하세요.")
        tts.speak(ERROR_MESSAGE)
        return True
    elapsed = time.monotonic() - start

    stop_waiting()   # answer 를 말하기 전에 대기음·감시를 멈추고 스트림 정리까지 대기

    # 모델이 <think> 블록만 뱉고 본문이 비는 경우가 있어 방어한다.
    if not answer:
        print("[오류] hermes 응답이 비어 있습니다.")
        tts.speak(ERROR_MESSAGE)
        return True

    print(f"[응답] {answer} ({elapsed:.2f}초)")
    tts.speak(answer)
    return True
