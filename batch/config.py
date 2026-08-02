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

# 결과 파일명의 시각 표기. 날짜에 **시(hour)까지** 붙이는 이유는 같은 잡을 하루에 여러 번
# 돌리는 쓰임(시간대별 cron 줄) 때문이다 — 날짜뿐이면 그날의 마지막 실행만 남는다.
# 분·초까지 내려가지 않는 것은 의도적이다: 같은 시간대의 재실행은 덮어쓰게 두어야
# 실패 후 다시 돌린 결과가 옆에 쌓이지 않는다.
FILENAME_TIME_FORMAT = "%Y-%m-%d-%H"


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


def read_url():
    """`URL_BRIEFING_URL`(대상 사이트 주소 하나)를 읽는다. 없으면 빈 문자열.

    주제 목록(`read_topics`)과 달리 **하나만** 받는다. 커뮤니티 한 곳을 훑어 하나의
    보고서를 쓰는 잡이고, 결과 분량이 Discord 본문 예산 하나를 다 쓰도록 맞춰져 있어
    여러 곳을 받으면 뒤쪽이 잘리기 때문이다 (`batch/url_briefing.py` 주석 참고).

    키 이름에 `URL_` 을 붙인 이유는 `BRIEFING_*` 가 정기 요약 잡(`daily_briefing`)의
    설정 묶음이기 때문이다. `BRIEFING_URL` 이라고 쓰면 그 묶음의 일부로 읽힌다.
    """
    load_batch_env()
    return os.environ.get("URL_BRIEFING_URL", "").strip()


def read_site_name():
    """`URL_BRIEFING_NAME`(대상 사이트의 표시 이름)을 읽는다. 없으면 빈 문자열.

    보고서에서 대상 링크의 라벨로 쓴다. 모델에게 물어보지 않고 설정에서 받는 이유는,
    사이트 이름이 **실행마다 달라질 값이 아니기 때문**이다. 모델이 페이지에서 읽게 하면
    매 실행 지어낼 여지가 생기고 페이지를 못 열었을 때 빈 값이 되는데, 설정값은 그 두 가지
    실패가 아예 없다. 비워 두면 호스트명으로 대신한다 (`url_briefing.site_label()`).
    """
    load_batch_env()
    return os.environ.get("URL_BRIEFING_NAME", "").strip()


def output_dir():
    """요약 결과를 저장할 **뿌리** 디렉터리. 잡별 하위 디렉터리는 `output_path()` 가 만든다.

    설정 키는 잡마다 늘리지 않고 `BRIEFING_OUTPUT_DIR` 하나를 모든 잡이 함께 쓴다.
    잡끼리 겹치지 않게 하는 것은 그 아래 잡 이름 디렉터리 쪽이다.

    `BRIEFING_OUTPUT_DIR` 에 상대경로를 적으면 **cwd 기준**이다. cron 은 임의의 cwd 로
    돌기 때문에 `batch/run.sh` 가 저장소 루트로 이동한 뒤 실행한다.
    """
    load_batch_env()
    raw = os.environ.get("BRIEFING_OUTPUT_DIR", "").strip()
    return Path(raw).expanduser() if raw else DEFAULT_OUTPUT_DIR


def output_path(job_name, when):
    """잡 이름과 실행 시각으로 결과 파일 경로를 만든다 — `<뿌리>/<잡 이름>/YYYY-MM-DD-HH.md`.

    **잡을 가르는 것이 파일명 접두어에서 디렉터리로 바뀌었다.** 접두어(`url-`)는 한
    디렉터리에 두 잡의 결과가 섞여 있어야 성립하는 장치였는데, 실행마다 파일이 쌓이기
    시작하면 그 섞임 자체가 문제가 된다 — 잡 하나의 결과만 보려 해도 남의 잡 파일을
    눈으로 걸러야 한다. 디렉터리로 가르면 새 잡은 이름만 정하면 되고, 접두어를 고르다
    실수로 겹칠 여지도 없다.

    **`when` 을 인자로 받고 여기서 `datetime.now()` 를 부르지 않는 이유**는 잡이 실행
    시각을 이미 갖고 있기 때문이다. 문서 머리말의 날짜와 프롬프트의 '오늘'이 그 값에서
    나오는데, 저장 시점에 시각을 다시 읽으면 자정이나 정시를 걸친 실행에서 파일명과
    문서 내용의 시각이 어긋난다 (`# 2026-08-02 브리핑` 이 `2026-08-03-00.md` 에 담기는 식).
    """
    return output_dir() / job_name / f"{when.strftime(FILENAME_TIME_FORMAT)}.md"
