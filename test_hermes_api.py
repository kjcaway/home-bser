"""hermes gateway API 연결 테스트 스크립트.

운영환경(우분투)에서 `hermes gateway` 로 띄운 OpenAI 호환 API 서버(8642 포트)에
질문을 보내고 응답을 확인합니다. OpenAI SDK 를 사용하되 base_url 만 hermes 로
바꿔 호출합니다.

설정은 hermes_api 스킬과 동일하게 프로젝트 루트의 `.env` 에서 읽습니다
(`HERMES_ENABLED`, `HERMES_BASE_URL`, `HERMES_API_KEY`, `HERMES_MODEL`, `HERMES_TIMEOUT`).
우선순위는 CLI 인자 > .env > 기본값 순입니다.

`HERMES_ENABLED` 가 꺼짐(`0`/`false`/`no`/`off`)이면 스킬과 동일하게 hermes 를
호출하지 않고 종료합니다. 스위치와 무관하게 연결만 확인하려면 `--force` 를 씁니다.

사용 예:
    python test_hermes_api.py                          # .env 설정으로 테스트
    python test_hermes_api.py "서울의 수도는 어디야?"      # 질문 직접 지정
    python test_hermes_api.py --force                  # HERMES_ENABLED 무시하고 강제 테스트
    python test_hermes_api.py --base-url http://127.0.0.1:8642/v1 --model qwen3:8b
"""

import argparse
import os
import re
import sys
import time

from openai import OpenAI

from agent.config import load_env_file
from agent.skills.hermes_api import _FALSE_VALUES, _TRUE_VALUES

DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_TIMEOUT = 30.0
DEFAULT_QUESTION = "안녕하세요. 자기소개를 한 문장으로 해주세요."

# hermes 는 인증을 요구하지 않지만 OpenAI SDK 가 api_key 를 필수로 요구하므로,
# .env 에 HERMES_API_KEY 가 없을 때 쓸 더미 값.
DUMMY_API_KEY = "qqqqqqqqqqqqqqqq1"

# 음성 에이전트와 동일한 조건으로 테스트하기 위한 시스템 프롬프트.
# 응답은 TTS 로 읽히므로 짧은 한국어 평문을 요구한다.
SYSTEM_PROMPT = (
    "당신은 한국어 음성 비서입니다. "
    "질문에 한국어로 한두 문장으로 짧게 답하세요. "
    "마크다운, 이모지, 특수기호는 사용하지 마세요."
)


def strip_think(text: str) -> str:
    """qwen3 가 생성하는 <think>...</think> 사고 블록을 제거합니다.

    thinking 을 꺼도 빈 <think></think> 태그가 남는 경우가 있어 방어적으로 제거한다.
    """
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def main():
    # main_agent.py 와 동일하게 .env 를 읽어 os.environ 에 채운다.
    # (이미 있는 실제 환경변수가 항상 우선한다)
    load_env_file()

    # 기본값은 .env(HERMES_*) → 하드코딩 순으로 정한다. CLI 인자를 명시하면
    # argparse 가 이 기본값을 덮어쓰므로 최종 우선순위는 CLI > .env > 기본값.
    env_base_url = os.environ.get("HERMES_BASE_URL", DEFAULT_BASE_URL)
    env_model = os.environ.get("HERMES_MODEL", DEFAULT_MODEL)
    env_timeout = float(os.environ.get("HERMES_TIMEOUT", DEFAULT_TIMEOUT))

    parser = argparse.ArgumentParser(description="hermes gateway OpenAI 호환 API 테스트")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION,
                        help=f"질문 문장 (기본값: {DEFAULT_QUESTION!r})")
    parser.add_argument("--base-url", default=env_base_url,
                        help=f"hermes gateway 주소 (기본값: {env_base_url})")
    parser.add_argument("--model", default=env_model,
                        help=f"모델 이름 (기본값: {env_model})")
    parser.add_argument("--timeout", type=float, default=env_timeout,
                        help=f"응답 대기 제한 시간(초) (기본값: {env_timeout})")
    parser.add_argument("--force", action="store_true",
                        help="HERMES_ENABLED 가 꺼져 있어도 무시하고 강제로 테스트")
    args = parser.parse_args()

    # HERMES_ENABLED 로 on/off 를 판단(스킬과 동일). 명시적으로 꺼져 있으면
    # 호출하지 않고 종료한다. --force 로 스위치를 무시할 수 있다.
    if not args.force:
        flag = os.environ.get("HERMES_ENABLED")
        if flag is not None and flag.strip() != "":
            normalized = flag.strip().lower()
            if normalized in _FALSE_VALUES:
                print("[System] HERMES_ENABLED 가 꺼져 있어 테스트를 건너뜁니다.")
                print("         연결만 확인하려면 --force 를, 활성화하려면 .env 에서 HERMES_ENABLED=1 로 두세요.")
                return
            if normalized not in _TRUE_VALUES:
                print(f"[경고] HERMES_ENABLED 값을 해석할 수 없어 테스트를 건너뜁니다: {flag!r}")
                print("       연결만 확인하려면 --force 를 쓰세요.")
                return

    # hermes 는 인증을 요구하지 않지만 SDK 가 api_key 를 필수로 요구한다.
    # .env 의 HERMES_API_KEY 가 있으면 쓰고, 없으면 더미 값을 넣는다.
    # 연결 실패를 바로 알 수 있도록 SDK 기본 재시도(2회)는 끈다.
    api_key = os.environ.get("HERMES_API_KEY") or DUMMY_API_KEY
    client = OpenAI(base_url=args.base_url, api_key=api_key,
                    timeout=args.timeout, max_retries=0)

    print(f"[System] hermes gateway: {args.base_url} (model: {args.model})")
    print(f"[질문] {args.question}")

    start = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=args.model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": args.question},
            ],
            max_tokens=256,
            temperature=0.7,
        )
    except Exception as e:
        print(f"[오류] hermes API 호출 실패: {e}")
        print("       hermes gateway 가 실행 중인지, 포트(8642)가 맞는지 확인하세요.")
        sys.exit(1)
    elapsed = time.monotonic() - start

    raw = response.choices[0].message.content or ""
    answer = strip_think(raw)

    print(f"\n[응답] {answer}")
    if answer != raw:
        print("       (<think> 블록이 제거되었습니다)")

    usage = response.usage
    if usage:
        print(f"\n[통계] 소요 시간: {elapsed:.2f}초 | "
              f"입력 토큰: {usage.prompt_tokens} | 출력 토큰: {usage.completion_tokens}")
    else:
        print(f"\n[통계] 소요 시간: {elapsed:.2f}초")


if __name__ == "__main__":
    main()
