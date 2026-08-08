"""Discord 웹훅 전송 — 잡이 아니라 잡들이 가져다 쓰는 공용 부품.

    ./bin/python -m batch.discord_notify "메시지" | --file 경로 [--dry-run]

첨부파일이 아니라 **메시지 본문**으로 보내므로 2000자 상한이 그대로 제약이고, 넘치면
`truncate()` 가 자른다. 잡에서는 실패를 삼키는 `notify()`, 직접 다룰 때는 `send()`.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests

from batch.config import load_batch_env

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_SEND_FAILED = 2

# Discord 메시지 본문(content) 최대 길이. 프로토콜 상수라 설정으로 빼지 않는다.
CONTENT_LIMIT = 2000

# 앞의 빈 줄은 마크다운에서 직전 목록/문단과 붙지 않게 한다.
TRUNCATION_NOTICE = "\n\n(...생략)"

DEFAULT_TIMEOUT = 15.0

# 429 재시도를 기다려 줄 최대 시간(초). 더 길게 요구하면 기다리지 않고 실패로 본다.
MAX_RETRY_WAIT = 5.0


def webhook_url():
    """`DISCORD_WEBHOOK_URL` 값. 없으면 빈 문자열.

    **로그에 찍지 말 것** — 이 URL 자체가 인증 수단이다.
    """
    load_batch_env()
    return os.environ.get("DISCORD_WEBHOOK_URL", "").strip()


def is_enabled():
    """웹훅 URL 이 있으면 켜진 것으로 본다 (별도 on/off 키 없음 — 비우면 꺼진다)."""
    return bool(webhook_url())


def content_length(text):
    """Discord 기준 길이(UTF-16 코드 단위) — 이모지는 `len()` 과 달리 2를 차지한다."""
    return len(text.encode("utf-16-le")) // 2


def truncate(text, limit=CONTENT_LIMIT, notice=TRUNCATION_NOTICE):
    """`limit` 을 넘으면 잘라내고 `notice` 를 붙인다.

    자를 위치는 줄 경계를 우선한다(목록 항목이 반 토막 나지 않게). 다만 줄바꿈이 거의
    없는 문서에서 내용을 통째로 날리지 않도록, 예산의 절반 이상이 남을 때만 그렇게 한다.
    """
    if content_length(text) <= limit:
        return text

    budget = limit - content_length(notice)
    if budget <= 0:
        # notice 만으로 상한을 넘는 비정상 설정. 본문을 버리더라도 상한은 지킨다.
        return notice[:limit]

    body = text[:budget]
    # 코드포인트로 자른 뒤 UTF-16 기준으로 다시 재서 초과분을 덜어낸다.
    while body and content_length(body) > budget:
        body = body[:-1]

    cut = body.rfind("\n")
    if cut >= len(body) // 2:
        body = body[:cut]

    return body.rstrip() + notice


def send(text, webhook=None, username=None, timeout=None):
    """메시지 하나를 보내고 실제로 보낸 본문을 반환한다. 실패는 예외.

    길이 초과는 오류가 아니라 `truncate()` 로 처리한다.
    `webhook` / `username` / `timeout` 을 주면 `.env` 값보다 우선한다.
    """
    load_batch_env()

    url = (webhook or webhook_url()).strip()
    if not url:
        raise RuntimeError(
            "DISCORD_WEBHOOK_URL 이 비어 있습니다. batch/.env 에 웹훅 URL 을 넣으세요.")

    content = truncate(text or "")
    if not content.strip():
        # 빈 본문은 Discord 가 400 으로 거절한다. 원인을 바로 알 수 있게 여기서 막는다.
        raise ValueError("보낼 내용이 비어 있습니다.")

    payload = {"content": content}
    name = username if username is not None else os.environ.get("DISCORD_USERNAME", "").strip()
    if name:
        payload["username"] = name

    if timeout is None:
        timeout = _timeout()

    response = requests.post(url, json=payload, timeout=timeout)

    # 레이트 리밋은 잠깐 기다리면 풀리는 일시적 실패다. 짧게 한 번만 재시도한다.
    if response.status_code == 429:
        wait = _retry_after(response)
        if wait is not None and wait <= MAX_RETRY_WAIT:
            print(f"[System] Discord 레이트 리밋 — {wait:.1f}초 후 한 번 재시도합니다.")
            time.sleep(wait)
            response = requests.post(url, json=payload, timeout=timeout)

    if response.status_code >= 400:
        # 상태 코드만으로는 구분되지 않는 사유가 많아 본문 앞부분을 함께 남긴다
        # (본문에 웹훅 URL 은 들어가지 않는다).
        detail = (response.text or "").strip().replace("\n", " ")
        raise RuntimeError(f"Discord 가 HTTP {response.status_code} 로 거절했습니다: {detail[:300]}")

    return content


def notify(text, webhook=None, username=None, timeout=None):
    """`send()` 의 예외를 삼키는 래퍼. 보냈으면 True, 아니면 False.

    잡에서는 이쪽을 쓴다 — 알림 실패로 이미 저장된 결과물까지 실패로 만들지 않기 위해.
    """
    if not is_enabled() and webhook is None:
        print("[System] DISCORD_WEBHOOK_URL 이 없어 Discord 전송을 건너뜁니다.")
        return False

    try:
        content = send(text, webhook=webhook, username=username, timeout=timeout)
    except requests.Timeout:
        print(f"[오류] Discord 전송이 제한 시간({_timeout():.0f}초)을 초과했습니다.")
        return False
    except Exception as e:
        print(f"[오류] Discord 전송 실패: {e}")
        return False

    sent = content_length(content)
    total = content_length(text or "")
    if sent < total:
        print(f"[System] Discord 전송 완료 — 길이 제한으로 {total}자 중 {sent}자만 보냈습니다.")
    else:
        print(f"[System] Discord 전송 완료 ({sent}자)")
    return True


def _timeout():
    """`DISCORD_TIMEOUT`(초). 비어 있거나 숫자가 아니면 기본값 (경고만 남기고 진행)."""
    raw = os.environ.get("DISCORD_TIMEOUT", "").strip()
    if not raw:
        return DEFAULT_TIMEOUT
    try:
        return float(raw)
    except ValueError:
        print(f"[경고] DISCORD_TIMEOUT 을 숫자로 읽을 수 없어 "
              f"기본값({DEFAULT_TIMEOUT})을 씁니다: {raw!r}")
        return DEFAULT_TIMEOUT


def _retry_after(response):
    """429 응답에서 기다릴 초. 본문 JSON 의 `retry_after` 를 먼저 보고, 없으면 헤더."""
    try:
        value = response.json().get("retry_after")
    except (json.JSONDecodeError, ValueError):
        value = None
    if value is None:
        value = response.headers.get("Retry-After")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_args():
    parser = argparse.ArgumentParser(
        description="batch/.env 의 DISCORD_WEBHOOK_URL 로 텍스트 메시지를 보낸다.")
    parser.add_argument(
        "text", nargs="?", metavar="메시지",
        help="보낼 텍스트. --file 과 함께 쓸 수 없다.")
    parser.add_argument(
        "--file", metavar="경로",
        help="이 파일의 내용을 메시지 본문으로 보낸다 "
             "(예: batch/output/daily_briefing/2026-07-30-07.md)")
    parser.add_argument(
        "--username", metavar="이름",
        help="이 메시지에만 쓸 표시 이름 (기본: .env 의 DISCORD_USERNAME)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="보내지 않고, 실제로 전송될 본문만 출력한다 (길이 잘림 확인용)")
    return parser.parse_args()


def main():
    # 무엇보다 먼저 배치 .env 를 적재한다 (batch/config.load_batch_env 참고).
    load_batch_env()
    args = parse_args()

    if bool(args.text) == bool(args.file):
        print("[오류] 메시지 텍스트나 --file 중 하나만 지정하세요.")
        return EXIT_CONFIG

    if args.file:
        path = Path(args.file)
        if not path.is_file():
            print(f"[오류] 파일을 찾을 수 없습니다: {path}")
            return EXIT_CONFIG
        text = path.read_text(encoding="utf-8")
    else:
        text = args.text

    if args.dry_run:
        content = truncate(text)
        print(f"[System] 전송하지 않음(--dry-run) — {content_length(content)}자 "
              f"/ 원문 {content_length(text)}자")
        print()
        print(content)
        return EXIT_OK

    if not is_enabled():
        print("[오류] DISCORD_WEBHOOK_URL 이 설정되지 않았습니다.")
        print("       batch/.env 를 만들고(cp batch/.env.example batch/.env) 웹훅 URL 을 넣으세요.")
        return EXIT_CONFIG

    return EXIT_OK if notify(text, username=args.username) else EXIT_SEND_FAILED


if __name__ == "__main__":
    sys.exit(main())
