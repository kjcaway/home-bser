"""정기 LLM 요약 배치 잡 — 관심 주제를 웹 검색으로 훑어 날짜별 마크다운으로 남긴다.

    ./bin/python -m batch.daily_briefing                    # batch/.env 의 주제 전체
    ./bin/python -m batch.daily_briefing --topic "AI 동향"    # 주제 하나만 (반복 지정 가능)
    ./bin/python -m batch.daily_briefing --stdout            # 파일 대신 표준출력
    ./bin/python -m batch.daily_briefing --output out.md     # 저장 경로 직접 지정
    ./bin/python -m batch.daily_briefing --no-notify         # 저장만 하고 Discord 전송 생략

주제 목록은 `batch/.env` 의 `BRIEFING_TOPICS`(쉼표 구분), 저장 위치는
`BRIEFING_OUTPUT_DIR`(기본 `batch/output/`)에서 읽는다. 결과 파일명은
`YYYY-MM-DD.md` 이고, 같은 날 다시 돌리면 **덮어쓴다** (하루에 한 번 도는 잡이므로
누적보다 재실행 가능한 편이 낫다).

저장이 끝나면 `DISCORD_WEBHOOK_URL` 이 설정된 경우에 한해 그 파일을 Discord 로 보낸다
(`batch/discord_notify.py`). 웹훅이 없으면 조용히 넘어가므로 알림을 쓰지 않는 환경에서도
잡은 그대로 돈다.

**주제 하나가 실패해도 잡 전체를 멈추지 않는다.** 실패한 주제는 그 자리에 실패 사유를
적고 나머지를 계속 요약한 뒤, 종료 코드로 알린다. 웹 검색 한 건이 타임아웃 났다고
그날 브리핑이 통째로 없어지는 것이 더 나쁘기 때문이다.

종료 코드 (cron 이 실패를 알아볼 수 있도록):
    0 — 모든 주제 성공 (알림을 보냈거나, 웹훅을 설정하지 않았거나)
    1 — 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / claude 없음 / 주제 없음)
    2 — 문서는 만들었지만 실패한 주제가 있거나 Discord 전송에 실패함
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
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (cron 메일·디버깅용). "
             "보낼 파일이 없으므로 Discord 전송도 하지 않는다.")
    parser.add_argument(
        "--no-notify", action="store_true",
        help="저장만 하고 Discord 전송은 건너뛴다 (같은 날 다시 돌릴 때 채널에 "
             "같은 브리핑을 또 올리지 않으려는 용도)")
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


def warn_if_too_long(document):
    """문서가 Discord 본문 예산을 넘으면 경고한다 (잡을 막지는 않는다).

    분량 상한은 `claude_query.SYSTEM_PROMPT` 의 지시로만 걸려 있고, 그것은 모델이
    지켜 줄 때만 유효한 최선 노력이다. 지켜졌는지 확인할 방법이 이 로그뿐이라
    매 실행마다 남긴다 — 경고가 반복되면 주제 수를 줄이거나 프롬프트를 더 조인다.

    웹훅을 설정하지 않았으면 아무 말도 하지 않는다. 전송하지도 않을 상한을 두고
    매일 경고하면, 정작 전송을 켰을 때 그 경고를 이미 무시하고 있게 된다.
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
    print("       batch/claude_query.py 의 SYSTEM_PROMPT 분량 지시를 조이거나 "
          "주제 수를 줄이세요.")


def notify_document(path, skip=False):
    """저장한 브리핑을 Discord 로 보낸다. **전송에 실패했으면 True** 를 반환한다.

    메모리에 있는 문서가 아니라 **저장된 파일을 다시 읽어서** 보낸다. 채널에 올라간
    내용과 디스크에 남은 내용이 어긋날 여지를 없애기 위함이다 — 알림을 보고 원본을
    열었을 때 다른 글이 있으면 알림 쪽을 믿을 수 없게 된다.

    웹훅을 설정하지 않은 것은 **실패가 아니다.** 알림은 선택 기능이고, 설정하지 않은
    환경에서 브리핑이 매일 종료 코드 2 로 끝나면 cron 경보가 의미를 잃는다. 반대로
    웹훅이 있는데 전송이 실패한 것은 알려야 한다 — 아무도 안 보는 잡이라 알림이 안
    나갔다는 사실 자체를 알아챌 방법이 그 종료 코드뿐이다.
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
    warn_if_too_long(document)

    notify_failed = False
    if args.stdout:
        print()
        print(document)
        if discord_notify.is_enabled():
            print("[System] --stdout 이므로 Discord 전송을 건너뜁니다.")
    else:
        path = output_dir() / f"{today}.md" if args.output is None else Path(args.output)
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
