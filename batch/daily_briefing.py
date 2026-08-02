"""정기 LLM 요약 배치 잡 — 관심 주제를 웹 검색으로 훑어 실행 시각별 마크다운으로 남긴다.

    ./bin/python -m batch.daily_briefing                    # batch/.env 의 주제 전체
    ./bin/python -m batch.daily_briefing --topic "AI 동향"    # 주제 하나만 (반복 지정 가능)
    ./bin/python -m batch.daily_briefing --stdout            # 파일 대신 표준출력
    ./bin/python -m batch.daily_briefing --output out.md     # 저장 경로 직접 지정
    ./bin/python -m batch.daily_briefing --no-notify         # 저장만 하고 Discord 전송 생략

주제 목록은 `batch/.env` 의 `BRIEFING_TOPICS`(쉼표 구분), 저장 위치는
`BRIEFING_OUTPUT_DIR`(기본 `batch/output/`)에서 읽는다. 결과는 **잡 이름 디렉터리 아래
실행 시각으로** 남는다 — `batch/output/daily_briefing/YYYY-MM-DD-HH.md`. 같은 시간대에
다시 돌리면 덮어쓰고(실패 후 재실행이 옆에 쌓이지 않는다), 다른 시각에 돌리면 파일이
따로 남는다 (`batch/config.output_path()` 참고).

저장이 끝나면 `DISCORD_WEBHOOK_URL` 이 설정된 경우에 한해 그 파일을 Discord 로 보낸다
(`batch/discord_notify.py`). 웹훅이 없으면 조용히 넘어가므로 알림을 쓰지 않는 환경에서도
잡은 그대로 돈다.

결과의 모양(머리말 두 줄 + 주제마다 개요 한두 문장 + 마스크 링크 항목 6개)은
`batch/url_briefing.py` 와 같다. 두 잡의 알림이 같은 채널에 나란히 올라오므로 읽는 쪽이
규칙을 하나만 익히면 되게 한 것이다.

**주제는 여러 개를 받지만, 분량은 "한 번에 주제 하나"로 역산돼 있다**
(아래 `SYSTEM_PROMPT` 주석). 주제를 늘리면 파일은 온전해도 Discord 메시지에서는
뒤 주제가 통째로 잘린다 — 주제마다 알림을 따로 받고 싶으면 `--topic` 을 단 cron 줄을
나누는 편이 낫다.

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
from batch.config import load_batch_env, output_path, read_topics

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2

# 결과를 담을 하위 디렉터리 이름. 모듈 이름과 같게 둔다 — 로그에 찍힌 잡 이름
# (`-m batch.daily_briefing`)과 결과가 쌓이는 자리를 따로 외울 필요가 없다.
JOB_NAME = "daily_briefing"

# 주제 요약용 시스템 프롬프트. `claude_query.ask(system_prompt=...)` 로 넘긴다 — 공용
# 모듈에 기본값을 두지 않는 이유는 그쪽 모듈 주석 참고 (프롬프트는 결과물의 모양이고,
# 그 모양이 곧 잡의 정체라 잡 파일이 갖는다).
#
# 결과가 마크다운 문서에 그대로 들어가므로, 음성 스킬과 달리 목록 표기를 **요구**한다.
# 제목(#)을 금지하는 이유는 아래 `render_document()` 가 주제별 제목을 붙이기 때문이다 —
# 모델이 제목을 또 달면 계층이 두 겹으로 어긋난다.
#
# **결과물의 모양은 `url_briefing.SYSTEM_PROMPT` 와 같다** — 개요 한두 문장 + '-' 항목
# 6개, 항목의 제목은 Discord 마스크 링크(`[__"제목"__](<주소>)`). 두 잡의 알림이 같은
# 채널에 나란히 올라오므로 읽는 쪽이 규칙을 하나만 익히면 되고, 마스크 링크 문법 세 조각
# (`[…](…)` 렌더링 · `<…>` 임베드 억제 · `__…__` 밑줄)이 각각 왜 필요한지는
# `batch/url_briefing.py` 의 같은 상수 주석에 한 번만 적어 두었다.
#
# **출처를 괄호 안 매체명(`(연합뉴스)`)으로 받던 자리를 이 링크가 대신한다.** 매체명은
# 주소를 쓸 수 없어 대신 두었던 값이라, 제목이 원문으로 이어지는 지금은 예산만 먹는
# 중복이다 — 눌러 보면 어느 매체인지 나온다.
#
# **분량(개요 150자 + 항목 6개 × 설명 80자)은 취향이 아니라 Discord 예산에서 역산한
# 값이고, 그 역산은 "실행 한 번에 주제 하나"를 전제로 한다.** 결과 문서는 저장 직후
# Discord 메시지 본문으로 전송되는데(batch/discord_notify.py), 본문 상한이 2000자다:
#     2000 − 머리말(제목·생성 시각·주제 수·"## 주제") ~70 − 잘림 표시 9 = 본문 1921자
#     개요 152자 + 항목 6개 × 191자 ≈ 1290자 → 문서 전체 ~1360자 (여유 640자 = 47%)
#     항목 191자 = "- " 2 + 링크 문법 13 + 제목 25 + 주소 70 + 설명 80 + 줄바꿈 1
#
# 머리말이 `url_briefing`(~118자)보다 짧은 만큼 여유도 크다. 그쪽은 대상 사이트 주소를
# 머리말에 링크로 달지만, 이 잡의 대상은 주소가 아니라 주제어라서다.
#
# **길이 상한은 화면에 보이는 글자가 아니라 전송되는 원문 기준이라, 주소를 마스킹해도
# 예산은 줄지 않는다** (`url_briefing` 주석의 같은 이야기). 설명이 200자에서 80자로
# 짧아진 것이 링크를 들인 대가다. 제목과 주소는 길이를 지시할 수 없는(기사가 정하는)
# 값이라 평균치로 잡았고, 최악 조건(제목 40자 · 주소 90자)에서도 문서는 1569자로 상한
# 안에 들어온다.
#
# **주제를 2개 이상으로 늘리면 이 계산이 깨진다.** 항목 하나가 191자라 두 번째 주제는
# 개요부터 잘려 나간다. 그때는 항목 수와 설명 길이를 주제 수로 나눠 다시 잡아야 한다
# (주제 3개면 항목 3개 × 설명 40자 수준). 파일은 온전히 남고 메시지만 잘리므로 조용히
# 넘어가기 쉬운데, 아래 `warn_if_too_long()` 이 그날 로그로 알려준다.
#
# 잘림은 문서 뒤쪽에서 일어난다. 주제가 하나면 사라지는 것은 마지막 항목들이고,
# 프롬프트가 "중요한 것부터"를 요구하므로 잃는 쪽은 항상 덜 중요한 항목이다.
#
# 이 지시는 모델이 지켜 줄 때만 유효한 '최선 노력'이다. 실제로 지켜지는지는
# `warn_if_too_long()` 이 매 실행마다 로그로 알려준다.
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
    """(주제, 본문) 목록을 하나의 마크다운 문서로 조립한다.

    머리말은 `url_briefing.render_document()` 와 같은 모양이다 — 제목 한 줄, `>` 두 줄,
    그 다음 본문. 두 잡의 알림이 같은 채널에 나란히 올라오므로 겉모습이 갈릴 이유가 없다.
    다른 것은 둘째 줄뿐인데, 그쪽 대상은 주소(마스크 링크)이고 이쪽 대상은 주제어라
    이미 `## 주제` 로 붙는다. 그래서 여기서는 개수만 적는다.

    **모델/effort 는 일부러 넣지 않는다** (`url_briefing` 과 같은 판단). 알림으로 읽는
    글에는 군더더기이고, 정작 필요한 진단 상황에서는 `claude_query.build_command()` 가
    프로세스마다 한 번 찍는 `[System] claude CLI 모델: …` 이 cron 로그
    (`batch/logs/YYYY-MM-DD.log`)에 남아 있다. 즉 정보가 사라진 게 아니라 문서 밖으로
    옮겨진 것이다 — 다만 문서 파일만 따로 보관하면 어떤 모델이 쓴 글인지는 알 수 없다.
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

    분량 상한은 이 파일의 `SYSTEM_PROMPT` 지시로만 걸려 있고, 그것은 모델이 지켜 줄
    때만 유효한 최선 노력이다. 지켜졌는지 확인할 방법이 이 로그뿐이라 매 실행마다
    남긴다 — 경고가 반복되면 주제 수를 줄이거나 프롬프트를 더 조인다.

    웹훅을 설정하지 않았으면 아무 말도 하지 않는다. 전송하지도 않을 상한을 두고
    매일 경고하면, 정작 전송을 켰을 때 그 경고를 이미 무시하고 있게 된다.

    `url_briefing` 에 같은 이름의 함수가 있지만 합치지 않았다. 공유되는 것은 "넘었으면
    출력한다" 세 줄뿐이고, 정작 쓸모 있는 부분(어느 프롬프트를 어떻게 조여야 하는지)이
    잡마다 다르다 — 그 안내를 인자로 뽑으면 줄 수는 그대로인데 잡끼리 엮이기만 한다.
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
    print("       항목 수 또는 주제 수를 줄이세요. 기사 제목·주소는 기사가 정하는 길이라")
    print("       조일 수 없으므로, 주제가 여럿이면 주제 수 쪽을 먼저 줄이는 편이 확실합니다.")


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

    # 실행 시각을 한 번만 읽어 문서의 날짜와 결과 파일명이 같은 값에서 나오게 한다
    # (자정·정시를 걸친 실행에서 둘이 어긋나지 않도록 — `output_path()` 주석 참고).
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
