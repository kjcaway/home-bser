"""URL 브리핑 배치 잡 — 커뮤니티 사이트 한 곳을 훑어 마크다운 보고서로 남기고 Discord 로 보낸다.

    ./bin/python -m batch.url_briefing [--url 주소] [--name 이름] [--output 경로] [--stdout] [--no-notify]

설정은 `batch/.env`(`URL_BRIEFING_URL`, `URL_BRIEFING_NAME`, `BRIEFING_OUTPUT_DIR`),
결과는 `batch/output/url_briefing/YYYY-MM-DD-HH.md`. **URL 은 하나만** 받는다 (분량이
Discord 본문 하나를 다 쓰도록 역산돼 있어, 여러 곳은 cron 줄을 나누는 편이 낫다).

종료 코드: 0 성공 / 1 설정 문제로 아무것도 안 함 / 2 문서는 썼지만 요약·전송이 실패함.
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
from batch.config import load_batch_env, output_path, read_site_name, read_url

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL = 2

# 결과를 담을 하위 디렉터리 이름 (모듈 이름과 같게 둔다).
JOB_NAME = "url_briefing"

# 대상 페이지를 실제로 여는 도구. 없으면 보고서가 통째로 모델의 기억이 된다.
REQUIRED_TOOL = "WebFetch"

# 커뮤니티 페이지 훑기용 시스템 프롬프트. 결과물의 모양이 곧 잡의 정체라 잡 파일이 갖는다.
#
# 항목 제목은 Discord 마스크 링크(`[__"제목"__](<주소>)`)로 감싼다. 세 조각의 이유:
#   `[…](…)` 웹훅 메시지에서만 렌더 / `<…>` 임베드 미리보기 억제 / `__…__` 밑줄(가독성).
#
# 분량(개요 150자 + 항목 6개 × 설명 80자)은 Discord 본문 2000자에서 역산한 값이고,
# **실행 한 번에 URL 하나**를 전제로 한다. 마스킹은 예산을 아끼지 않고 오히려 13자를
# 더 쓰므로(상한은 보이는 글자가 아니라 원문 기준), 설명이 180자에서 80자로 짧아졌다.
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
        help="결과를 저장할 파일 경로 "
             "(기본: <BRIEFING_OUTPUT_DIR>/url_briefing/YYYY-MM-DD-HH.md)")
    parser.add_argument(
        "--stdout", action="store_true",
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (Discord 전송도 하지 않는다)")
    parser.add_argument(
        "--no-notify", action="store_true",
        help="저장만 하고 Discord 전송은 건너뛴다")
    return parser.parse_args()


def validate_url(url):
    """URL 형태를 본다. 문제가 있으면 사유 문자열, 없으면 None.

    스킴이 없으면 모델이 페이지를 여는 대신 웹 검색으로 그럴듯한 보고서를 써 온다 —
    실패보다 '엉뚱한 성공'이 알아채기 어려워서 미리 막는다.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "http:// 또는 https:// 로 시작해야 합니다"
    if not parsed.netloc:
        return "호스트 부분이 없습니다"
    return None


def warn_if_tool_missing():
    """허용 도구에 `WebFetch` 가 없으면 경고한다 (문자열 대조라 막지는 않는다)."""
    allowed = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS)
    if REQUIRED_TOOL.lower() in allowed.lower():
        return

    print(f"[경고] CLAUDE_CLI_ALLOWED_TOOLS 에 {REQUIRED_TOOL} 이 없습니다: {allowed!r}")
    print(f"       대상 페이지를 열지 못해 모델의 기억으로 쓴 보고서가 나옵니다 — "
          f"batch/.env 에 {REQUIRED_TOOL} 을 넣으세요.")


def build_prompt(url, today):
    """대상 URL 을 질의 문장으로 만든다 (날짜를 박아 커뮤니티 글의 상대 시간 표기를 맞춘다)."""
    return (f"대상 URL: {url}\n\n"
            f"오늘은 {today} 입니다. 이 페이지를 직접 열어 지금 올라와 있는 글들을 "
            f"훑어보고, 어떤 이야기가 오가고 있는지 요약해 주세요.")


def summarize_url(url, today):
    """대상 URL 을 요약한다. 반환값은 (본문, 실패했는지) 쌍 — 실패도 예외 대신 본문에 담는다."""
    start = time.monotonic()
    try:
        body = claude_query.ask(build_prompt(url, today), system_prompt=SYSTEM_PROMPT)
    except subprocess.TimeoutExpired:
        print("[오류] 요약이 제한 시간을 초과했습니다.")
        print("       batch/.env 의 CLAUDE_CLI_TIMEOUT 을 늘려보세요.")
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
    """대상 링크의 라벨. 이름이 없으면 호스트명으로 대신한다.

    빈 라벨은 누를 글자가 없는 링크가 되고, 대괄호는 마스크 링크 문법을 깨므로 지운다.
    """
    label = re.sub(r"[\[\]]", "", name).strip()
    return label or urlparse(url).netloc or url


def render_document(url, name, body, today):
    """요약 본문을 마크다운 문서로 조립한다.

    대상은 생짜 URL 이 아니라 이름을 라벨로 단 마스크 링크로 남긴다(항목과 같은 형식).
    모델/effort 는 넣지 않는다 — 필요하면 cron 로그에 남아 있다.
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
    print("       전송 시 뒤쪽부터 잘립니다 — 마지막 항목들이 빠집니다.")
    print("       batch/url_briefing.py 의 SYSTEM_PROMPT 에서 설명 80자 지시를 줄이거나,")
    print("       항목 수를 줄이세요.")


def notify_document(path, skip=False):
    """저장한 보고서를 Discord 로 보낸다. **전송에 실패했으면 True**.

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
    # 실행 시각은 한 번만 읽는다 — 문서의 날짜와 파일명이 같은 값에서 나오도록.
    run_at = datetime.now()
    today = run_at.strftime("%Y-%m-%d")
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
        path = (output_path(JOB_NAME, run_at) if args.output is None
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
