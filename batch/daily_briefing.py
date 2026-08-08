"""정기 LLM 요약 배치 잡 — 관심 주제를 웹 검색으로 훑어 마크다운으로 남기고 Discord 로 보낸다.

    ./bin/python -m batch.daily_briefing [--topic 주제] [--output 경로] [--stdout] [--no-notify]

설정은 `batch/.env`(`BRIEFING_TOPICS`, `BRIEFING_OUTPUT_DIR`), 결과는
`batch/output/daily_briefing/YYYY-MM-DD-HH.md`. 주제 하나가 실패해도 나머지는 계속한다.

종료 코드: 0 성공 / 1 설정 문제로 아무것도 안 함 / 2 문서는 썼지만 요약·전송이 실패함.
"""

import argparse
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from agent.skills.claude_p import is_enabled
from batch import claude_query, discord_notify
from batch.config import load_batch_env, output_path, read_topics

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2

# 결과를 담을 하위 디렉터리 이름 (모듈 이름과 같게 둔다).
JOB_NAME = "daily_briefing"

# 주제 요약용 시스템 프롬프트. 결과물의 모양이 곧 잡의 정체라 잡 파일이 갖는다.
# 모양은 `url_briefing.SYSTEM_PROMPT` 와 같고(개요 + 마스크 링크 항목 6개), 마스크 링크
# 문법 세 조각의 이유는 그쪽 주석에 한 번만 적혀 있다.
#
# 분량(개요 150자 + 항목 6개 × 설명 80자)은 Discord 본문 2000자에서 역산한 값이고,
# **실행 한 번에 주제 하나**를 전제로 한다 — 주제를 늘리면 메시지 뒤쪽이 잘린다
# (파일은 온전하고, `warn_if_too_long()` 이 로그로 알린다).
SYSTEM_PROMPT = (
    "당신은 한국어 브리핑 작성자입니다. "
    "주어진 주제에 대해 웹 검색으로 최신 정보를 확인한 뒤 한국어로 요약하세요. "
    "검색으로 확인되지 않은 내용은 쓰지 마세요. "
    "먼저 이 주제에서 지금 무엇이 가장 크게 오가고 있는지를 공백 포함 150자 이내로 "
    "한두 문장 쓰세요. "
    "그 뒤에 빈 줄을 하나 넣고, '-' 로 시작하는 항목을 정확히 6개 쓰세요. "
    "중요한 것부터 적으세요.\n"
    "각 항목은 아래 형식을 그대로 지키세요.\n"
    '- [__"기사 제목"__](<기사 주소>) 설명\n'
    "기사 주소는 반드시 부등호 < > 로 감싸세요. 감싸지 않으면 링크 미리보기가 붙습니다. "
    "기사 주소는 검색으로 실제로 확인한 것만 쓰고, 지어내지 마세요. 주소가 확실하지 "
    '않으면 링크 없이 __"기사 제목"__ 만 쓰세요. '
    "설명은 공백 포함 80자 이내로, 무슨 내용이고 왜 중요한지를 적으세요. "
    "설명 안에는 주소를 쓰지 마세요. "
    "제목(#)은 넣지 마세요. 상위 문서가 주제별 제목을 붙입니다. "
    "인사말·서론·맺음말 없이 개요와 항목만 출력하세요."
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="관심 주제를 웹 검색으로 요약해 날짜별 마크다운으로 저장한다.")
    parser.add_argument(
        "--topic", action="append", metavar="주제",
        help="요약할 주제. 여러 번 지정할 수 있다. 주면 batch/.env 의 "
             "BRIEFING_TOPICS 대신 이 값들만 사용한다.")
    parser.add_argument(
        "--output", metavar="경로",
        help="결과를 저장할 파일 경로 "
             "(기본: <BRIEFING_OUTPUT_DIR>/daily_briefing/YYYY-MM-DD-HH.md)")
    parser.add_argument(
        "--stdout", action="store_true",
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (Discord 전송도 하지 않는다)")
    parser.add_argument(
        "--no-notify", action="store_true",
        help="저장만 하고 Discord 전송은 건너뛴다")
    return parser.parse_args()


def build_prompt(topic, today):
    """주제 하나를 질의 문장으로 만든다 (날짜를 박아 학습 시점 지식과 '오늘'을 구분시킨다)."""
    return (f"주제: {topic}\n\n"
            f"오늘은 {today} 입니다. 이 주제의 최신 소식을 웹 검색으로 확인해 요약해 주세요.")


def summarize_topic(topic, today):
    """주제 하나를 요약한다. 반환값은 (본문, 실패했는지) 쌍 — 실패도 예외 대신 본문에 담는다."""
    start = time.monotonic()
    try:
        body = claude_query.ask(build_prompt(topic, today), system_prompt=SYSTEM_PROMPT)
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
    """(주제, 본문) 목록을 마크다운 문서로 조립한다.

    머리말 모양은 `url_briefing.render_document()` 와 같다. 모델/effort 는 넣지 않는다 —
    필요하면 cron 로그의 `[System] claude CLI 모델: …` 에 남아 있다.
    """
    lines = [
        f"# {today} 브리핑",
        "",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> 주제 {len(sections)}개" + (f" (실패 {len(failed)}개)" if failed else ""),
        "",
    ]
    for topic, body in sections:
        lines += [f"## {topic}", "", body, ""]
    return "\n".join(lines)


def warn_if_too_long(document):
    """문서가 Discord 본문 예산을 넘으면 경고한다 (잡을 막지는 않는다).

    분량 상한은 프롬프트 지시로만 걸려 있어 지켜졌는지 확인할 방법이 이 로그뿐이다.
    웹훅이 없으면 아무 말도 하지 않는다 — 쓰지도 않을 상한으로 매일 경고하지 않기 위해.
    """
    if not discord_notify.is_enabled():
        return

    total = discord_notify.content_length(document)
    if total <= discord_notify.CONTENT_LIMIT:
        return

    print(f"[경고] 문서가 Discord 본문 상한을 넘었습니다: {total}자 "
          f"(상한 {discord_notify.CONTENT_LIMIT}자)")
    print("       전송 시 뒤쪽부터 잘립니다 — 주제가 하나면 마지막 항목들이, "
          "여러 개면 뒤 주제가 통째로 빠집니다.")
    print("       batch/daily_briefing.py 의 SYSTEM_PROMPT 에서 설명 80자 지시를 줄이거나,")
    print("       항목 수 또는 주제 수를 줄이세요.")


def notify_document(path, skip=False):
    """저장한 브리핑을 Discord 로 보낸다. **전송에 실패했으면 True**.

    메모리의 문서가 아니라 저장된 파일을 다시 읽어 보낸다(채널과 디스크가 어긋나지 않게).
    웹훅 미설정은 실패가 아니고, 설정돼 있는데 실패한 것은 알려야 한다.
    """
    if skip:
        print("[System] --no-notify 이므로 Discord 전송을 건너뜁니다.")
        return False

    if not discord_notify.is_enabled():
        return False

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[오류] 저장한 문서를 다시 읽지 못해 Discord 전송을 건너뜁니다: {e}")
        return True

    return not discord_notify.notify(text)


def main():
    # 무엇보다 먼저 배치 .env 를 적재한다 (batch/config.load_batch_env 참고).
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

    # 실행 시각은 한 번만 읽는다 — 문서의 날짜와 파일명이 같은 값에서 나오도록.
    run_at = datetime.now()
    today = run_at.strftime("%Y-%m-%d")
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
    warn_if_too_long(document)

    notify_failed = False
    if args.stdout:
        print()
        print(document)
        if discord_notify.is_enabled():
            print("[System] --stdout 이므로 Discord 전송을 건너뜁니다.")
    else:
        path = (output_path(JOB_NAME, run_at) if args.output is None
                else Path(args.output))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
        print(f"[System] 저장: {path}")
        notify_failed = notify_document(path, skip=args.no_notify)

    elapsed = time.monotonic() - started
    if failed:
        print(f"[System] 완료 ({elapsed:.1f}초) — 실패한 주제 {len(failed)}개: "
              f"{', '.join(failed)}")
        return EXIT_PARTIAL

    if notify_failed:
        print(f"[System] 완료 ({elapsed:.1f}초) — 주제 {len(topics)}개 모두 성공했지만 "
              f"Discord 전송에 실패했습니다 (문서는 저장되어 있습니다).")
        return EXIT_PARTIAL

    print(f"[System] 완료 ({elapsed:.1f}초) — 주제 {len(topics)}개 모두 성공")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
