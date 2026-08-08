"""배치 잡 공통 설정 (`batch/.env`).

`load_batch_env()` 가 루트 `.env` 보다 **먼저** 불려야 배치 설정이 쓰인다.
그래서 모든 배치 진입 함수의 첫 줄에서 호출한다.
"""

import os
from pathlib import Path

from agent.config import load_env_file

BATCH_DIR = Path(__file__).resolve().parent

DEFAULT_OUTPUT_DIR = BATCH_DIR / "output"

# 결과 파일명의 시각 표기. 시(hour)까지만 붙여 같은 시간대의 재실행은 덮어쓰게 한다.
FILENAME_TIME_FORMAT = "%Y-%m-%d-%H"


def load_batch_env():
    """`batch/.env` 를 `os.environ` 에 적재한다.

    `load_env_file()` 은 첫 호출만 실제로 파싱하므로, 배치가 먼저 부르면 이후 스킬의
    루트 `.env` 호출이 no-op 이 된다 — 설정 격리가 이 순서 하나에 걸려 있다.
    """
    load_env_file(BATCH_DIR / ".env")


def read_topics():
    """`BRIEFING_TOPICS`(쉼표 구분)를 주제 리스트로 읽는다. 없으면 빈 리스트."""
    load_batch_env()
    raw = os.environ.get("BRIEFING_TOPICS", "")
    return [topic.strip() for topic in raw.split(",") if topic.strip()]


def read_url():
    """`URL_BRIEFING_URL`(대상 주소 **하나**)를 읽는다. 없으면 빈 문자열."""
    load_batch_env()
    return os.environ.get("URL_BRIEFING_URL", "").strip()


def read_site_name():
    """`URL_BRIEFING_NAME`(대상 링크 라벨)을 읽는다. 없으면 빈 문자열."""
    load_batch_env()
    return os.environ.get("URL_BRIEFING_NAME", "").strip()


def output_dir():
    """결과가 쌓이는 **뿌리** 디렉터리 (`BRIEFING_OUTPUT_DIR`, 상대경로는 cwd 기준)."""
    load_batch_env()
    raw = os.environ.get("BRIEFING_OUTPUT_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_OUTPUT_DIR


def output_path(job_name, when):
    """결과 파일 경로 — `<뿌리>/<잡 이름>/YYYY-MM-DD-HH.md`.

    `when` 을 인자로 받는 이유는 문서의 날짜와 파일명이 같은 시각에서 나와야
    자정·정시를 걸친 실행에서 어긋나지 않기 때문이다.
    """
    return output_dir() / job_name / f"{when.strftime(FILENAME_TIME_FORMAT)}.md"
