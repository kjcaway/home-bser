"""hermes gateway API 연결 테스트 스크립트.

운영환경(우분투)에서 `hermes gateway` 로 띄운 OpenAI 호환 API 서버(8642 포트)에
질문을 보내고 응답을 확인합니다. OpenAI SDK 를 사용하되 base_url 만 hermes 로
바꿔 호출합니다.

사용 예:
    python test_hermes_api.py                          # 기본 질문으로 테스트
    python test_hermes_api.py "서울의 수도는 어디야?"      # 질문 직접 지정
    python test_hermes_api.py --base-url http://127.0.0.1:8642/v1 --model qwen3:8b
"""

import argparse
import re
import sys
import time

from openai import OpenAI

DEFAULT_BASE_URL = "http://127.0.0.1:8642/v1"
DEFAULT_MODEL = "qwen3:8b"
DEFAULT_QUESTION = "안녕하세요. 자기소개를 한 문장으로 해주세요."

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
    parser = argparse.ArgumentParser(description="hermes gateway OpenAI 호환 API 테스트")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION,
                        help=f"질문 문장 (기본값: {DEFAULT_QUESTION!r})")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL,
                        help=f"hermes gateway 주소 (기본값: {DEFAULT_BASE_URL})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"모델 이름 (기본값: {DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=float, default=30.0,
                        help="응답 대기 제한 시간(초) (기본값: 30)")
    args = parser.parse_args()

    # hermes 는 인증을 요구하지 않지만 SDK 가 api_key 를 필수로 요구하므로 더미 값을 넣는다.
    # 연결 실패를 바로 알 수 있도록 SDK 기본 재시도(2회)는 끈다.
    client = OpenAI(base_url=args.base_url, api_key="qqqqqqqqqqqqqqqq1",
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
