"""URL 브리핑의 gemini 판 — 같은 프롬프트를 gemini CLI 로 돌려 결과를 견주는 잡.

    ./bin/python -m batch.url_briefing_gemini [--url 주소] [--name 이름] [--stdout] [--no-notify]

결과물의 모양(프롬프트·질의문·문서 조립·전송)은 전부 `batch/url_briefing.py` 에서
import 한다 — 두 잡이 다른 것이 모델뿐이어야 비교가 성립하기 때문이다.
결과는 `batch/output/url_briefing_gemini/YYYY-MM-DD-HH.md`, 종료 코드는 claude 판과 같다.
문서에 백엔드 표시가 없으므로 한 채널에 둘 다 보낸다면 `DISCORD_USERNAME` 으로 구분한다.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from batch import discord_notify, gemini_query
from batch.config import load_batch_env, output_path, read_site_name, read_url
from batch.url_briefing import (
    EXIT_CONFIG,
    EXIT_OK,
    EXIT_PARTIAL,
    SYSTEM_PROMPT,
    build_prompt,
    notify_document,
    render_document,
    site_label,
    validate_url,
    warn_if_too_long,
)

# 결과를 담을 하위 디렉터리 이름 (claude 판과 섞이지 않는 것이 이 잡의 쓸모다).
JOB_NAME = "url_briefing_gemini"

# 대상 페이지를 여는 도구. claude 의 `WebFetch` 에 해당한다.
REQUIRED_TOOL = "web_fetch"


def parse_args():
    parser = argparse.ArgumentParser(
        description="커뮤니티 사이트 한 곳을 gemini CLI 로 훑어 요약 보고서를 저장한다.")
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
             "(기본: <BRIEFING_OUTPUT_DIR>/url_briefing_gemini/YYYY-MM-DD-HH.md)")
    parser.add_argument(
        "--stdout", action="store_true",
        help="파일로 저장하지 않고 표준출력으로 내보낸다 (Discord 전송도 하지 않는다)")
    parser.add_argument(
        "--no-notify", action="store_true",
        help="저장만 하고 Discord 전송은 건너뛴다")
    return parser.parse_args()


def warn_if_tool_missing():
    """허용 도구에 `web_fetch` 가 없으면 경고한다 (문자열 대조라 막지는 않는다).

    gemini 는 비대화형 실행에서 이 도구를 목록에서 아예 빼므로, 빠뜨리면 "권한이 없다"는
    말조차 없이 모델 기억으로 쓴 보고서가 나온다.
    """
    allowed = os.environ.get("GEMINI_CLI_ALLOWED_TOOLS",
                             gemini_query.DEFAULT_ALLOWED_TOOLS)
    if REQUIRED_TOOL.lower() in allowed.lower():
        return

    print(f"[경고] GEMINI_CLI_ALLOWED_TOOLS 에 {REQUIRED_TOOL} 이 없습니다: {allowed!r}")
    print(f"       비대화형 실행에서는 이 도구가 목록에서 빠져 대상 페이지를 열지 못하고,")
    print(f"       모델의 기억으로 쓴 보고서가 나옵니다 — batch/.env 에 {REQUIRED_TOOL} 을 "
          f"넣으세요.")


def summarize_url(url, today):
    """대상 URL 을 gemini 로 요약한다. 반환값은 (본문, 실패했는지) 쌍.

    질의 문장과 지시는 claude 판과 같고, 바뀌는 것은 `gemini_query` 를 부르는 것뿐이다.
    """
    start = time.monotonic()
    try:
        body = gemini_query.ask(build_prompt(url, today), system_prompt=SYSTEM_PROMPT)
    except subprocess.TimeoutExpired:
        print("[오류] 요약이 제한 시간을 초과했습니다.")
        print("       batch/.env 의 GEMINI_CLI_TIMEOUT 을 늘려보세요.")
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


def main():
    # 무엇보다 먼저 배치 .env 를 적재한다 (batch/config.load_batch_env 참고).
    load_batch_env()
    args = parse_args()

    if not gemini_query.is_enabled():
        print("[System] GEMINI_CLI_ENABLED 가 꺼져 있어 URL 브리핑(gemini)을 건너뜁니다.")
        print("         batch/.env 를 만들고(cp batch/.env.example batch/.env) 값을 채우세요.")
        return EXIT_CONFIG

    if shutil.which(gemini_query.CLI_NAME) is None:
        print(f"[오류] `{gemini_query.CLI_NAME}` 명령을 찾을 수 없습니다.")
        print("       gemini 는 보통 nvm 아래(~/.nvm/versions/node/<버전>/bin)에 깔리는데")
        print("       batch/run.sh 의 기본 PATH 에는 없으니 crontab 에 PATH 를 지정하세요.")
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
    print(f"[System] {today} URL 브리핑(gemini) 시작 — "
          f"대상: {site_label(name, url)} ({url})")

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
