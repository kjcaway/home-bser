"""배치(정기 실행) 잡 모음.

모듈 하나가 잡 하나이며, **저장소 루트에서** `python -m batch.<잡이름>` 으로 실행한다.

    ./bin/python -m batch.daily_briefing

`python batch/daily_briefing.py` 로는 실행하지 않는다 — 그렇게 부르면 `sys.path[0]`
이 `batch/` 가 되어 `import agent` 가 깨진다. `-m` 은 cwd(저장소 루트)를 경로에 올려
주므로 `agent` 를 그대로 import 할 수 있고, 이미 저장소가 쓰는 방식
(`python -m agent.text_norm`)과 같다.

cwd 가 저장소 루트여야 하는 이유가 하나 더 있다: `agent/config.py` 의 파일 경로들이
상대경로(`soundfile/…`)이고, 배치 출력 기본 경로도 저장소 기준이다. cron 은 임의의
cwd 로 돌기 때문에 `batch/run.sh` 가 먼저 루트로 이동해 준다.

설정은 루트 `.env` 가 아니라 **`batch/.env`** 를 읽는다 (`batch/config.py` 참고).
음성 에이전트와 배치는 모델·제한 시간·도구 허용 범위가 서로 다르기 때문이다.
"""
