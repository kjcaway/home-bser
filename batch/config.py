"""배치 잡 공통 설정 읽기 (`batch/.env`).

배치 설정을 음성 에이전트의 루트 `.env` 와 분리하는 장치가 `load_batch_env()` 하나다.
아래 주석의 "왜 진입점마다 부르는가" 를 읽지 않으면 조용히 루트 `.env` 가 쓰이는
상태가 될 수 있으니, 새 배치 잡을 추가할 때 함께 볼 것.
"""

import os
from pathlib import Path

from agent.config import load_env_file

BATCH_DIR = Path(__file__).resolve().parent

# 요약 결과 기본 저장 위치. BRIEFING_OUTPUT_DIR 로 덮어쓸 수 있다.
DEFAULT_OUTPUT_DIR = BATCH_DIR / "output"


def load_batch_env():
    """`batch/.env` 를 `os.environ` 에 적재한다. 배치 모듈의 **모든 진입점에서** 호출한다.

    `agent.config.load_env_file()` 은 여러 번 불려도 **첫 호출만 실제로 파싱**하는
    전역 가드를 갖고 있다. 그리고 스킬들(`claude_p.is_enabled()` 등)은 내부에서 인자
    없이 그것을 호출해 루트 `.env` 를 읽는다. 배치가 **먼저** 자기 파일을 지정해 부르면
    이후 스킬 쪽 호출이 no-op 이 되므로, 루트 `.env` 는 아예 읽히지 않고 배치 설정만
    쓰인다 — 이것이 설정 분리의 전부이고, 그래서 순서가 곧 계약이다.

    순서에 의존하는 계약은 잊기 쉬우므로, 이 함수를 배치 쪽 모든 진입 함수의 첫 줄에서
    부른다. 어느 함수를 먼저 부르든 배치 `.env` 가 이기게 되어 함정이 사라진다
    (스킬들이 `is_enabled()` 마다 `load_env_file()` 을 부르는 것과 같은 이유).

    실제 환경변수는 `.env` 보다 항상 우선이므로, cron 에서 잡별로
    `CLAUDE_CLI_EFFORT=low ./batch/run.sh` 처럼 덮어쓰는 것도 그대로 동작한다.
    """
    load_env_file(BATCH_DIR / ".env")


def read_topics():
    """`BRIEFING_TOPICS`(쉼표 구분)를 주제 리스트로 읽는다. 없으면 빈 리스트."""
    load_batch_env()
    raw = os.environ.get("BRIEFING_TOPICS", "")
    return [topic.strip() for topic in raw.split(",") if topic.strip()]


def output_dir():
    """요약 결과를 저장할 디렉터리.

    `BRIEFING_OUTPUT_DIR` 에 상대경로를 적으면 **cwd 기준**이다. cron 은 임의의 cwd 로
    돌기 때문에 `batch/run.sh` 가 저장소 루트로 이동한 뒤 실행한다.
    """
    load_batch_env()
    raw = os.environ.get("BRIEFING_OUTPUT_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_OUTPUT_DIR
