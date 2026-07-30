"""정기 LLM 요약 배치 잡 — 관심 주제를 웹 검색으로 훑어 날짜별 마크다운으로 남긴다.

    ./bin/python -m batch.daily_briefing                    # batch/.env 의 주제 전체
    ./bin/python -m batch.daily_briefing --topic "AI 동향"    # 주제 하나만 (반복 지정 가능)
    ./bin/python -m batch.daily_briefing --stdout            # 파일 대신 표준출력
    ./bin/python -m batch.daily_briefing --output out.md     # 저장 경로 직접 지정

주제 목록은 `batch/.env` 의 `BRIEFING_TOPICS`(쉼표 구분), 저장 위치는
`BRIEFING_OUTPUT_DIR`(기본 `batch/output/`)에서 읽는다. 결과 파일명은
`YYYY-MM-DD.md` 이고, 같은 날 다시 돌리면 **덮어쓴다** (하루에 한 번 도는 잡이므로
누적보다 재실행 가능한 편이 낫다).

**주제 하나가 실패해도 잡 전체를 멈추지 않는다.** 실패한 주제는 그 자리에 실패 사유를
적고 나머지를 계속 요약한 뒤, 종료 코드로 알린다. 웹 검색 한 건이 타임아웃 났다고
그날 브리핑이 통째로 없어지는 것이 더 나쁘기 때문이다.

종료 코드 (cron 이 실패를 알아볼 수 있도록):
    0 — 모든 주제 성공
    1 — 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / claude 없음 / 주제 없음)
    2 — 문서는 만들었지만 실패한 주제가 하나 이상 있음
"""

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from agent.skills.claude_p import is_enabled
from batch import claude_query
from batch.config import load_batch_env, output_dir, read_topics

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2


def parse_args():
    parser = argparse.ArgumentParser(
        description="관심 주제를 웹 검색으로 요약해 날짜별 마크다운으로 저장한다.")
    parser.add_argument(
        "--topic", action="append", metavar="주제",
        help="요약할 주제. 여러 번 지정할 수 있다. 주면 batch/.env 의 "
             "BRIEFING_TOPICS 대신 이 값들만 사용한다.")
    parser.add_argument(
        "--output", metavar="경로",
        help="결과를 저장할 파일 경로 (기본: <BRIEFING_OUTPUT_DIR>/YYYY-MM-DD.md)")
    parser.add_argument(
        "--stdout", action="store_true",
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (cron 메일·디버깅용)")
    return parser.parse_args()


def build_prompt(topic, today):
    """주제 하나를 질의 문장으로 만든다.

    날짜를 명시하는 이유: 모델의 학습 시점 지식과 '오늘'을 구분해 줘야 검색으로
    확인하려 하고, 요약문에 들어가는 시점 표현도 맞는다.
    """
    return (f"주제: {topic}\n\n"
            f"오늘은 {today} 입니다. 이 주제의 최신 소식을 웹 검색으로 확인해 요약해 주세요.")


def summarize_topic(topic, today):
    """주제 하나를 요약한다. 반환값은 (본문, 실패했는지) 쌍.

    실패를 예외로 올리지 않고 본문에 담아 돌려주는 이유는 위 모듈 주석 참고 —
    한 주제의 실패가 그날 문서 전체를 없애지 않게 하기 위함이다.
    """
    start = time.monotonic()
    try:
        body = claude_query.ask(build_prompt(topic, today))
    except subprocess.TimeoutExpired:
        print(f"[오류] '{topic}' 요약이 제한 시간을 초과했습니다.")
        print("       batch/.env 의 CLAUDE_CLI_TIMEOUT 을 늘리거나 주제를 좁혀보세요.")
        return "_요약 실패: 제한 시간 초과_", True
    except Exception as e:
        print(f"[오류] '{topic}' 요약 실패: {e}")
        return f"_요약 실패: {e}_", True

    elapsed = time.monotonic() - start
    if not body:
        print(f"[오류] '{topic}' 응답이 비어 있습니다. ({elapsed:.1f}초)")
        return "_요약 실패: 응답이 비어 있음_", True

    print(f"[완료] '{topic}' ({elapsed:.1f}초)")
    return body, False


def render_document(sections, today, failed):
    """(주제, 본문) 목록을 하나의 마크다운 문서로 조립한다.

    머리말에 모델/effort 를 남기는 이유는 음성 스킬이 같은 값을 로그로 남기는 것과
    같다 — 나중에 요약 품질이 달라졌을 때 어떤 모델이 쓴 글인지 문서 자체로 확인된다.
    """
    model, effort = claude_query.describe_options()
    lines = [
        f"# {today} 브리핑",
        "",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} · "
        f"모델: {model} · effort: {effort} · 주제 {len(sections)}개"
        + (f" (실패 {len(failed)}개)" if failed else ""),
        "",
    ]
    for topic, body in sections:
        lines += [f"## {topic}", "", body, ""]
    return "\n".join(lines)


def main():
    # 무엇보다 먼저 배치 .env 를 적재한다 (batch/config.load_batch_env 주석 참고).
    load_batch_env()
    args = parse_args()

    if not is_enabled():
        print("[System] CLAUDE_CLI_ENABLED 가 꺼져 있어 브리핑을 건너뜁니다.")
        print("         batch/.env 를 만들고(cp batch/.env.example batch/.env) 값을 채우세요.")
        return EXIT_CONFIG

    if shutil.which("claude") is None:
        print("[오류] `claude` 명령을 찾을 수 없습니다.")
        print("       cron 은 PATH 가 최소한이라 자주 겪는 문제입니다 — batch/run.sh 의")
        print("       PATH 설정을 확인하거나 crontab 에 PATH 를 직접 지정하세요.")
        return EXIT_CONFIG

    topics = args.topic or read_topics()
    if not topics:
        print("[오류] 요약할 주제가 없습니다.")
        print("       batch/.env 의 BRIEFING_TOPICS 를 채우거나 --topic 으로 지정하세요.")
        return EXIT_CONFIG

    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[System] {today} 브리핑 시작 — 주제 {len(topics)}개")

    sections = []
    failed = []
    started = time.monotonic()
    for index, topic in enumerate(topics, 1):
        print(f"[{index}/{len(topics)}] '{topic}' 요약 중...")
        body, did_fail = summarize_topic(topic, today)
        sections.append((topic, body))
        if did_fail:
            failed.append(topic)

    document = render_document(sections, today, failed)

    if args.stdout:
        print()
        print(document)
    else:
        path = output_dir() / f"{today}.md" if args.output is None else Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
        print(f"[System] 저장: {path}")

    elapsed = time.monotonic() - started
    if failed:
        print(f"[System] 완료 ({elapsed:.1f}초) — 실패한 주제 {len(failed)}개: "
              f"{', '.join(failed)}")
        return EXIT_PARTIAL

    print(f"[System] 완료 ({elapsed:.1f}초) — 주제 {len(topics)}개 모두 성공")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
