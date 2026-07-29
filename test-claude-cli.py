"""Claude Code CLI(`claude -p`) 질의응답 테스트 스크립트.

로컬에 설치된 `claude` 명령을 비대화형 모드(`-p/--print`)로 실행해 질문을 보내고
응답을 출력합니다. hermes gateway 테스트(`test_hermes_api.py`)와 같은 형태로
"질문 → 응답 → 통계" 를 찍어, 두 백엔드의 응답 품질/속도를 비교할 수 있게 했습니다.

질문은 **표준입력으로** 전달합니다(`-` 로 시작하는 문장이 CLI 옵션으로 오해받거나
아주 긴 프롬프트가 인자 길이 제한에 걸리는 것을 피하기 위함).

기본값은 순수 질의응답에 맞춰져 있습니다:
- `--tools ""` 로 모든 도구를 끕니다 → 파일 접근/권한 프롬프트 없이 바로 답만 받습니다.
  (도구를 쓰는 에이전트 동작까지 테스트하려면 `--with-tools`)
- `--append-system-prompt` 로 음성 비서용 짧은 한국어 응답 지침을 덧붙입니다.
  (끄려면 `--system-prompt ""`)

사용 예:
    python test-claude-cli.py                              # 기본 질문으로 테스트
    python test-claude-cli.py "서울의 수도는 어디야?"          # 질문 직접 지정
    python test-claude-cli.py --model sonnet                # 모델 지정 (opus/sonnet/haiku/전체 이름)
    python test-claude-cli.py --output-format text          # 원문 텍스트만 출력
    python test-claude-cli.py --with-tools "이 저장소 구조 알려줘"  # 도구 사용 허용
    echo "질문" | python test-claude-cli.py -               # 질문을 파이프로 전달
"""

import argparse
import json
import shutil
import subprocess
import sys
import time

DEFAULT_QUESTION = "안녕하세요. 자기소개를 한 문장으로 해주세요."
DEFAULT_TIMEOUT = 120.0

# 음성 에이전트와 동일한 조건으로 비교하기 위한 시스템 프롬프트.
# 응답이 TTS 로 읽힌다고 가정하고 짧은 한국어 평문을 요구한다.
SYSTEM_PROMPT = (
    "당신은 한국어 음성 비서입니다. "
    "질문에 한국어로 한두 문장으로 짧게 답하세요. "
    "마크다운, 이모지, 특수기호는 사용하지 마세요."
)


def build_command(args) -> list:
    """`claude -p ...` 명령줄을 조립합니다."""
    cmd = ["claude", "--print", "--output-format", args.output_format]

    if args.model:
        cmd += ["--model", args.model]

    # 기본 시스템 프롬프트는 살려두고(출력 형식 등 CLI 자체 규칙이 담겨 있음)
    # 응답 스타일 지침만 덧붙인다. 빈 문자열이면 생략.
    if args.system_prompt:
        cmd += ["--append-system-prompt", args.system_prompt]

    # 순수 질의응답 테스트에서는 도구를 모두 끈다. 도구가 켜져 있으면
    # 파일 탐색 등으로 응답이 느려지거나 권한 확인에서 멈출 수 있다.
    if not args.with_tools:
        cmd += ["--tools", ""]

    return cmd


def print_json_result(payload: dict, elapsed: float) -> int:
    """`--output-format json` 결과를 사람이 읽기 좋게 출력하고 종료 코드를 돌려줍니다."""
    answer = (payload.get("result") or "").strip()

    if payload.get("is_error"):
        print(f"[오류] Claude 가 오류 결과를 반환했습니다: {payload.get('subtype', 'unknown')}")
        if answer:
            print(f"       {answer}")
        return 1

    print(f"\n[응답] {answer}" if answer else "\n[응답] (빈 응답)")

    # 통계: CLI 가 알려주는 값(duration_ms 등)은 API 왕복 기준, elapsed 는
    # 프로세스 기동까지 포함한 체감 시간이라 둘 다 찍는다.
    stats = [f"소요 시간: {elapsed:.2f}초(프로세스 전체)"]
    duration_ms = payload.get("duration_ms")
    if duration_ms is not None:
        stats.append(f"CLI 측정: {duration_ms / 1000:.2f}초")
    if payload.get("num_turns") is not None:
        stats.append(f"턴 수: {payload['num_turns']}")
    if payload.get("total_cost_usd") is not None:
        stats.append(f"비용: ${payload['total_cost_usd']:.4f}")

    usage = payload.get("usage") or {}
    if usage:
        stats.append(f"입력 토큰: {usage.get('input_tokens', '?')}")
        stats.append(f"출력 토큰: {usage.get('output_tokens', '?')}")

    print("\n[통계] " + " | ".join(stats))
    if payload.get("session_id"):
        print(f"[세션] {payload['session_id']}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Code CLI(claude -p) 질의응답 테스트")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION,
                        help=f"질문 문장. '-' 이면 표준입력에서 읽습니다 (기본값: {DEFAULT_QUESTION!r})")
    parser.add_argument("--model", default=None,
                        help="모델 별칭(opus/sonnet/haiku) 또는 전체 이름 (기본값: CLI 설정값)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                        help=f"응답 대기 제한 시간(초) (기본값: {DEFAULT_TIMEOUT})")
    parser.add_argument("--output-format", choices=["json", "text"], default="json",
                        help="claude 출력 형식. json 이면 통계까지 출력 (기본값: json)")
    parser.add_argument("--system-prompt", default=SYSTEM_PROMPT,
                        help="기본 시스템 프롬프트에 덧붙일 지침. 빈 문자열이면 덧붙이지 않음")
    parser.add_argument("--with-tools", action="store_true",
                        help="도구 사용을 허용 (기본값: 모든 도구 끔)")
    parser.add_argument("--show-command", action="store_true",
                        help="실행할 claude 명령줄을 함께 출력")
    args = parser.parse_args()

    if shutil.which("claude") is None:
        print("[오류] `claude` 명령을 찾을 수 없습니다.")
        print("       Claude Code CLI 를 설치하고 PATH 에 등록되어 있는지 확인하세요.")
        sys.exit(1)

    question = args.question
    if question == "-":
        question = sys.stdin.read().strip()
    if not question:
        print("[오류] 질문이 비어 있습니다.")
        sys.exit(1)

    cmd = build_command(args)
    print(f"[System] claude CLI: {shutil.which('claude')} "
          f"(model: {args.model or '기본값'}, tools: {'on' if args.with_tools else 'off'})")
    if args.show_command:
        print(f"[System] 실행 명령: {' '.join(cmd)} < (stdin)")
    print(f"[질문] {question}")

    start = time.monotonic()
    try:
        # 질문은 stdin 으로 넘긴다(위 docstring 참고). claude 는 -p 모드에서
        # 프롬프트 인자가 없으면 표준입력을 프롬프트로 읽는다.
        proc = subprocess.run(cmd, input=question, capture_output=True,
                              text=True, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        print(f"[오류] {args.timeout}초 안에 응답이 오지 않아 중단했습니다.")
        print("       --timeout 을 늘리거나 --model 을 더 빠른 모델로 바꿔보세요.")
        sys.exit(1)
    except OSError as e:
        print(f"[오류] claude 실행 실패: {e}")
        sys.exit(1)
    elapsed = time.monotonic() - start

    if proc.returncode != 0:
        print(f"[오류] claude 가 비정상 종료했습니다 (exit code {proc.returncode})")
        if proc.stderr.strip():
            print(proc.stderr.strip())
        sys.exit(proc.returncode)

    if proc.stderr.strip():
        # 경고 등은 응답과 섞이지 않게 따로 보여준다.
        print(f"[stderr] {proc.stderr.strip()}")

    stdout = proc.stdout.strip()
    if args.output_format == "text":
        print(f"\n[응답] {stdout}" if stdout else "\n[응답] (빈 응답)")
        print(f"\n[통계] 소요 시간: {elapsed:.2f}초")
        return

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # json 을 기대했는데 파싱이 안 되면 원문을 그대로 보여준다.
        print("[경고] json 응답을 파싱하지 못해 원문을 그대로 출력합니다.")
        print(stdout)
        sys.exit(1)

    sys.exit(print_json_result(payload, elapsed))


if __name__ == "__main__":
    main()
