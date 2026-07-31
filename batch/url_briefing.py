"""URL 요약 배치 잡 — 커뮤니티 사이트 한 곳을 훑어 날짜별 마크다운 보고서로 남긴다.

    ./bin/python -m batch.url_briefing                              # batch/.env 의 URL
    ./bin/python -m batch.url_briefing --url https://example.com    # URL 직접 지정
    ./bin/python -m batch.url_briefing --name "클리앙 모두의공원"      # 링크 라벨 지정
    ./bin/python -m batch.url_briefing --stdout                     # 파일 대신 표준출력
    ./bin/python -m batch.url_briefing --output out.md              # 저장 경로 직접 지정
    ./bin/python -m batch.url_briefing --no-notify                  # 저장만, Discord 전송 생략

대상 URL 은 `batch/.env` 의 `URL_BRIEFING_URL`, 보고서에 표시할 사이트 이름은
`URL_BRIEFING_NAME`(비우면 호스트명), 저장 위치는 `BRIEFING_OUTPUT_DIR`
(기본 `batch/output/`)에서 읽는다. 결과 파일명은 `url-YYYY-MM-DD.md` 이고, 같은 날 다시
돌리면 **덮어쓴다** (`daily_briefing` 의 `YYYY-MM-DD.md` 와 접두어로만 갈린다 — 두 잡이
같은 디렉터리를 쓰면서 겹치지 않는 이유가 이 접두어다).

**URL 은 하나만 받는다.** 여러 개를 받지 않는 것은 게을러서가 아니라 분량 때문이다.
요약 분량이 Discord 본문 2000자를 이 보고서 하나가 다 쓴다고 보고 역산돼 있어
(`SYSTEM_PROMPT` 주석), 두 번째 URL 부터는 메시지에서 잘려 나간다. 여러 곳을 훑고
싶으면 URL 마다 cron 줄을 따로 두는 편이 낫다 — 그러면 알림도 따로 온다.

`daily_briefing` 과 달리 **부분 성공이 없다.** 대상이 하나뿐이라 요약이 실패하면 남길
내용도 없다. 그래도 실패 사유를 적은 문서를 쓰고 알림까지 보내는데, "브리핑이 깨졌다"
야말로 채널에 떠야 할 소식이기 때문이다 (아무도 이 잡을 지켜보고 있지 않다).

종료 코드 (cron 이 실패를 알아볼 수 있도록):
    0 — 요약 성공 (알림을 보냈거나, 웹훅을 설정하지 않았거나)
    1 — 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / claude 없음 / URL 없음·형식 오류)
    2 — 문서는 만들었지만 요약이 실패했거나 Discord 전송에 실패함
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from agent.skills.claude_p import DEFAULT_ALLOWED_TOOLS, is_enabled
from batch import claude_query, discord_notify
from batch.config import load_batch_env, output_dir, read_site_name, read_url

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2

# 결과 파일명 접두어. `daily_briefing` 의 `YYYY-MM-DD.md` 와 같은 디렉터리에 나란히
# 저장되므로, 이 접두어가 두 잡의 그날 결과가 서로를 덮어쓰지 않게 하는 유일한 장치다.
FILENAME_PREFIX = "url-"

# 대상 페이지를 실제로 여는 것이 이 잡의 존재 이유라, 이 도구가 허용 목록에 없으면
# 보고서가 통째로 모델의 기억이 된다.
REQUIRED_TOOL = "WebFetch"

# 커뮤니티 페이지 훑기용 시스템 프롬프트. `claude_query.SYSTEM_PROMPT`(주제 요약용)
# 대신 `ask(system_prompt=...)` 로 넘긴다.
#
# 항목의 글 제목은 **Discord 마스크 링크**(`[__"제목"__](<주소>)`)로 감싼다. 형식이 셋 다
# 이유가 있다:
#
# - `[제목](<주소>)` — Discord 는 웹훅/봇이 보낸 메시지의 content 안에서 마스크 링크를
#   렌더링한다(사용자가 직접 친 메시지에서는 안 되는 것과 다르다). 긴 주소를 그대로
#   노출하지 않고 제목만 눌러 원문으로 갈 수 있다.
# - `<` `>` — 주소를 부등호로 감싸면 **임베드 미리보기가 억제**된다. 감싸지 않으면 링크
#   여섯 개마다 미리보기 카드가 붙어 메시지가 채널을 덮는다.
# - `__…__` — Discord 에서 밑줄. 링크는 이미 파란색으로 렌더되지만 밑줄이 있는 편이
#   눈에 띈다는 실사용 확인에 따른 것이다. 표준 마크다운에서 `__` 는 볼드라, 저장된
#   `.md` 파일에서는 볼드로 보인다 — 파일과 알림의 겉모습이 갈리는 것은 감수한 값이다.
#
# **분량(개요 150자 + 항목 6개 × 설명 80자)은 취향이 아니라 Discord 예산에서 역산한
# 값이고, 그 역산은 "실행 한 번에 URL 하나"를 전제로 한다.** 결과 문서는 저장 직후
# Discord 메시지 본문으로 전송되는데(batch/discord_notify.py), 본문 상한이 2000자다:
#     2000 − 머리말(제목·생성 시각·대상 링크) ~118 − 잘림 표시 9 = 본문에 쓸 수 있는 1873자
#     개요 152자 + 항목 6개 × 191자 ≈ 1298자 → 문서 전체 ~1415자 (여유 585자 = 41%)
#     항목 191자 = "- " 2 + 링크 문법 13 + 제목 25 + 주소 70 + 설명 80 + 줄바꿈 1
#
# **길이 상한은 화면에 보이는 글자가 아니라 전송되는 원문 기준이라, 주소를 마스킹해도
# 예산은 줄지 않는다.** 오히려 문법 13자가 더 붙어 생짜 주소보다 길다 — 링크는 보기 편해
# 지는 대가로 예산을 쓰는 기능이지, 아끼는 기능이 아니다. 링크를 넣으면서 설명이 180자
# → 80자로 짧아진 것이 그 대가다. 대신 설명이 길 이유도 줄었다: 궁금하면 눌러서 원문을
# 보면 된다.
#
# 제목과 주소는 길이를 지시할 수 없는(페이지가 정하는) 값이라 평균치로 잡았다. 최악
# 조건(제목 40자 · 주소 90자 · 긴 사이트 이름)에서도 문서는 1634자로 상한 안에 들어온다.
#
# 설명 안에 생짜 주소를 금지하는 이유도 같은 예산이다. 커뮤니티 게시글 주소는 쿼리
# 문자열이 붙어 100자를 넘기 일쑤라, 링크 밖에 또 적으면 항목 하나가 통째로 날아간다.
#
# 이 지시는 모델이 지켜 줄 때만 유효한 '최선 노력'이다. 실제로 지켜지는지는
# `warn_if_too_long()` 이 매 실행마다 로그로 알려준다.
SYSTEM_PROMPT = (
    "당신은 한국어 브리핑 작성자입니다. "
    "주어진 URL 은 특정 주제를 다루는 커뮤니티 웹사이트입니다. "
    "반드시 그 페이지를 직접 열어 지금 올라와 있는 글들을 훑어보고, 필요하면 눈에 띄는 "
    "글을 몇 개 더 열어 확인한 뒤 요약하세요. "
    "페이지를 열지 못했다면 열지 못했다고 그대로 쓰세요. 기억이나 추측으로 채우지 마세요. "
    "먼저 이 커뮤니티에서 지금 무엇이 주로 오가고 분위기가 어떤지를 공백 포함 150자 이내로 "
    "한두 문장 쓰세요. "
    "그 뒤에 빈 줄을 하나 넣고, '-' 로 시작하는 항목을 정확히 6개 쓰세요. "
    "화제가 큰 것부터 적으세요.\n"
    "각 항목은 아래 형식을 그대로 지키세요.\n"
    '- [__"글 제목"__](<글 주소>) 설명\n'
    "글 주소는 반드시 부등호 < > 로 감싸세요. 감싸지 않으면 링크 미리보기가 붙습니다. "
    "글 주소는 페이지에서 실제로 확인한 것만 쓰고, 지어내지 마세요. 주소가 확실하지 "
    '않으면 링크 없이 __"글 제목"__ 만 쓰세요. '
    "설명은 공백 포함 80자 이내로, 무슨 이야기인지와 사람들의 반응(호응·반박·논쟁 등)을 "
    "적으세요. 설명 안에는 주소를 쓰지 마세요. "
    "제목(#)은 넣지 마세요. 상위 문서가 제목과 대상 URL 을 붙입니다. "
    "인사말·서론·맺음말 없이 개요와 항목만 출력하세요."
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="커뮤니티 사이트 한 곳을 훑어 요약 보고서를 마크다운으로 저장한다.")
    parser.add_argument(
        "--url", metavar="주소",
        help="요약할 사이트 주소. 주면 batch/.env 의 URL_BRIEFING_URL 대신 이 값을 쓴다.")
    parser.add_argument(
        "--name", metavar="이름",
        help="보고서에서 대상 링크에 표시할 사이트 이름 (기본: batch/.env 의 "
             "URL_BRIEFING_NAME, 그것도 없으면 호스트명)")
    parser.add_argument(
        "--output", metavar="경로",
        help="결과를 저장할 파일 경로 (기본: <BRIEFING_OUTPUT_DIR>/url-YYYY-MM-DD.md)")
    parser.add_argument(
        "--stdout", action="store_true",
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (cron 메일·디버깅용). "
             "보낼 파일이 없으므로 Discord 전송도 하지 않는다.")
    parser.add_argument(
        "--no-notify", action="store_true",
        help="저장만 하고 Discord 전송은 건너뛴다 (같은 날 다시 돌릴 때 채널에 "
             "같은 보고서를 또 올리지 않으려는 용도)")
    return parser.parse_args()


def validate_url(url):
    """URL 이 쓸 만한 형태인지 본다. 문제가 있으면 사유 문자열, 없으면 None.

    설정 오타를 **1초 안에** 잡기 위한 것이다. 검사를 건너뛰면 `www.example.com` 같은
    값도 그대로 claude 로 넘어가는데, 모델은 페이지를 여는 대신 웹 검색으로 비슷한 것을
    찾아 그럴듯한 보고서를 써 온다 — 실패가 아니라 **엉뚱한 성공**으로 보이는 것이 최악이고,
    그 사실을 알아채기까지 제한 시간(기본 300초)을 다 쓴다.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "http:// 또는 https:// 로 시작해야 합니다"
    if not parsed.netloc:
        return "호스트 부분이 없습니다"
    return None


def warn_if_tool_missing():
    """허용 도구 목록에 `WebFetch` 가 없으면 경고한다 (잡을 막지는 않는다).

    막지 않고 경고만 하는 이유: 허용 목록은 문자열이라 CLI 쪽에서 도구 이름이 바뀌면
    이 대조가 먼저 틀린다. 그때 잡을 죽이면 설정은 멀쩡한데 실행이 안 되고, 경고만
    남기면 사람이 로그를 보고 판단할 수 있다. 실제로 도구를 못 쓴 실행은
    `claude_query.ask()` 가 찍는 '턴 수 1 (검색 미사용)' 로도 한 번 더 드러난다.
    """
    allowed = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS)
    if REQUIRED_TOOL.lower() in allowed.lower():
        return

    print(f"[경고] CLAUDE_CLI_ALLOWED_TOOLS 에 {REQUIRED_TOOL} 이 없습니다: {allowed!r}")
    print(f"       대상 페이지를 열지 못해 모델의 기억으로 쓴 보고서가 나옵니다 — "
          f"batch/.env 에 {REQUIRED_TOOL} 을 넣으세요.")


def build_prompt(url, today):
    """대상 URL 하나를 질의 문장으로 만든다.

    날짜를 명시하는 이유는 `daily_briefing.build_prompt()` 와 같다: 학습 시점 지식과
    '오늘'을 구분해 줘야 페이지를 실제로 확인하려 하고, "어제 올라온" 같은 시점 표현도
    맞는다. 커뮤니티 글은 특히 상대 시간 표기가 많아 이 기준이 없으면 어긋난다.
    """
    return (f"대상 URL: {url}\n\n"
            f"오늘은 {today} 입니다. 이 페이지를 직접 열어 지금 올라와 있는 글들을 "
            f"훑어보고, 어떤 이야기가 오가고 있는지 요약해 주세요.")


def summarize_url(url, today):
    """대상 URL 을 요약한다. 반환값은 (본문, 실패했는지) 쌍.

    실패를 예외로 올리지 않고 본문에 담아 돌려주는 이유는 위 모듈 주석 참고 — 실패한
    실행도 문서와 알림을 남겨야 '오늘 브리핑이 깨졌다'는 사실이 채널에 뜬다.
    """
    start = time.monotonic()
    try:
        body = claude_query.ask(build_prompt(url, today), system_prompt=SYSTEM_PROMPT)
    except subprocess.TimeoutExpired:
        print("[오류] 요약이 제한 시간을 초과했습니다.")
        print("       batch/.env 의 CLAUDE_CLI_TIMEOUT 을 늘려보세요. 커뮤니티 페이지는")
        print("       글을 몇 개 더 열어 보느라 주제 검색보다 오래 걸릴 수 있습니다.")
        return "_요약 실패: 제한 시간 초과_", True
    except Exception as e:
        print(f"[오류] 요약 실패: {e}")
        return f"_요약 실패: {e}_", True

    elapsed = time.monotonic() - start
    if not body:
        print(f"[오류] 응답이 비어 있습니다. ({elapsed:.1f}초)")
        return "_요약 실패: 응답이 비어 있음_", True

    print(f"[완료] 요약 ({elapsed:.1f}초)")
    return body, False


def site_label(name, url):
    """대상 링크에 쓸 라벨. 설정한 이름이 없으면 호스트명으로 대신한다.

    **빈 라벨을 절대 만들지 않는 것이 이 함수의 존재 이유다.** `[__""__](<주소>)` 는
    Discord 에서 누를 글자가 없는 링크가 되어 사실상 사라진다. 호스트명은 URL 에서 바로
    나오므로 실패할 수 없고, `URL_BRIEFING_NAME` 을 채우지 않은 설정에서도 보고서가
    멀쩡히 나온다.

    대괄호는 지운다 — 라벨 안의 `[` `]` 는 마스크 링크 문법을 깨서 Discord 에 문법이
    글자 그대로 노출된다. 사람이 `.env` 에 적는 값이라 흔하지는 않지만, 깨진 것을
    알아채는 곳이 이미 채널에 나간 메시지라 되돌릴 수가 없다.
    """
    label = re.sub(r"[\[\]]", "", name).strip()
    return label or urlparse(url).netloc or url


def render_document(url, name, body, today):
    """요약 본문을 하나의 마크다운 문서로 조립한다.

    대상은 생짜 URL 이 아니라 **이름을 라벨로 단 마스크 링크**로 남긴다. 보고서만 떼어
    봐도 어디를 훑은 것인지 알 수 있어야 하는데(Discord 로 간 메시지에는 파일 경로조차
    없다), 커뮤니티 주소는 길어서 그대로 두면 머리말이 주소로 뒤덮인다. 항목의 글 제목과
    같은 형식이라 읽는 쪽에서 규칙이 하나로 통일된다.

    **모델/effort 는 일부러 넣지 않는다.** 알림으로 읽는 글에는 군더더기이고, 정작
    필요한 진단 상황에서는 `claude_query.build_command()` 가 프로세스마다 한 번 찍는
    `[System] claude CLI 모델: …` 이 cron 로그(`batch/logs/YYYY-MM-DD.log`)에 남아 있다.
    즉 정보가 사라진 게 아니라 문서 밖으로 옮겨진 것이다 — 다만 문서 파일만 따로 보관하면
    어떤 모델이 쓴 글인지는 알 수 없게 된다.
    """
    lines = [
        f"# {today} URL 브리핑",
        "",
        f"> 생성: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f'> 대상: [__"{site_label(name, url)}"__](<{url}>)',
        "",
        body,
        "",
    ]
    return "\n".join(lines)


def warn_if_too_long(document):
    """문서가 Discord 본문 예산을 넘으면 경고한다 (잡을 막지는 않는다).

    분량 상한은 `SYSTEM_PROMPT` 의 지시로만 걸려 있고, 그것은 모델이 지켜 줄 때만
    유효한 최선 노력이다. 지켜졌는지 확인할 방법이 이 로그뿐이라 매 실행마다 남긴다.

    웹훅을 설정하지 않았으면 아무 말도 하지 않는다. 전송하지도 않을 상한을 두고 매일
    경고하면, 정작 전송을 켰을 때 그 경고를 이미 무시하고 있게 된다.

    `daily_briefing` 에 같은 이름의 함수가 있지만 합치지 않았다. 공유되는 것은 "넘었으면
    출력한다" 세 줄뿐이고, 정작 쓸모 있는 부분(어느 프롬프트를 조여야 하는지)이 잡마다
    다르다 — 그 안내를 인자로 뽑으면 줄 수는 그대로인데 잡끼리 엮이기만 한다.
    """
    if not discord_notify.is_enabled():
        return

    total = discord_notify.content_length(document)
    if total <= discord_notify.CONTENT_LIMIT:
        return

    print(f"[경고] 문서가 Discord 본문 상한을 넘었습니다: {total}자 "
          f"(상한 {discord_notify.CONTENT_LIMIT}자)")
    print("       전송 시 뒤쪽부터 잘립니다 — 마지막 항목들이 빠집니다.")
    print("       batch/url_briefing.py 의 SYSTEM_PROMPT 에서 설명 80자 지시를 줄이거나,")
    print("       항목 수를 줄이세요. 제목·주소는 페이지가 정하는 길이라 조일 수 없으므로,")
    print("       주소가 유난히 긴 사이트라면 항목 수 쪽을 먼저 줄이는 편이 확실합니다.")


def notify_document(path, skip=False):
    """저장한 보고서를 Discord 로 보낸다. **전송에 실패했으면 True** 를 반환한다.

    메모리에 있는 문서가 아니라 **저장된 파일을 다시 읽어서** 보낸다. 채널에 올라간
    내용과 디스크에 남은 내용이 어긋날 여지를 없애기 위함이다 — 알림을 보고 원본을
    열었을 때 다른 글이 있으면 알림 쪽을 믿을 수 없게 된다.

    웹훅을 설정하지 않은 것은 **실패가 아니다.** 알림은 선택 기능이고, 설정하지 않은
    환경에서 매일 종료 코드 2 로 끝나면 cron 경보가 의미를 잃는다. 반대로 웹훅이 있는데
    전송이 실패한 것은 알려야 한다 — 아무도 안 보는 잡이라 알림이 안 나갔다는 사실
    자체를 알아챌 방법이 그 종료 코드뿐이다.
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
        print("[System] CLAUDE_CLI_ENABLED 가 꺼져 있어 URL 브리핑을 건너뜁니다.")
        print("         batch/.env 를 만들고(cp batch/.env.example batch/.env) 값을 채우세요.")
        return EXIT_CONFIG

    if shutil.which("claude") is None:
        print("[오류] `claude` 명령을 찾을 수 없습니다.")
        print("       cron 은 PATH 가 최소한이라 자주 겪는 문제입니다 — batch/run.sh 의")
        print("       PATH 설정을 확인하거나 crontab 에 PATH 를 직접 지정하세요.")
        return EXIT_CONFIG

    url = (args.url or read_url()).strip()
    if not url:
        print("[오류] 요약할 URL 이 없습니다.")
        print("       batch/.env 의 URL_BRIEFING_URL 을 채우거나 --url 로 지정하세요.")
        return EXIT_CONFIG

    problem = validate_url(url)
    if problem:
        print(f"[오류] URL 형식이 올바르지 않습니다 ({problem}): {url}")
        return EXIT_CONFIG

    warn_if_tool_missing()

    name = (args.name or read_site_name()).strip()
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"[System] {today} URL 브리핑 시작 — 대상: {site_label(name, url)} ({url})")

    started = time.monotonic()
    body, did_fail = summarize_url(url, today)

    document = render_document(url, name, body, today)
    warn_if_too_long(document)

    notify_failed = False
    if args.stdout:
        print()
        print(document)
        if discord_notify.is_enabled():
            print("[System] --stdout 이므로 Discord 전송을 건너뜁니다.")
    else:
        path = (output_dir() / f"{FILENAME_PREFIX}{today}.md" if args.output is None
                else Path(args.output))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document, encoding="utf-8")
        print(f"[System] 저장: {path}")
        notify_failed = notify_document(path, skip=args.no_notify)

    elapsed = time.monotonic() - started
    if did_fail:
        print(f"[System] 완료 ({elapsed:.1f}초) — 요약에 실패했습니다 "
              f"(사유는 문서에 적혀 있습니다).")
        return EXIT_PARTIAL

    if notify_failed:
        print(f"[System] 완료 ({elapsed:.1f}초) — 요약은 성공했지만 Discord 전송에 "
              f"실패했습니다 (문서는 저장되어 있습니다).")
        return EXIT_PARTIAL

    print(f"[System] 완료 ({elapsed:.1f}초) — 요약 성공")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
