#!/usr/bin/env bash
#
# 배치 잡 cron 래퍼. 기본값은 정기 LLM 요약(daily_briefing)이고, 첫 인자로 다른
# 잡 모듈 이름을 줄 수 있다.
#
#   ./batch/run.sh                        # batch.daily_briefing
#   ./batch/run.sh daily_briefing --stdout
#
# crontab 등록 예 (매일 07:00):
#   0 7 * * * /path/to/home-bser/batch/run.sh
#
# 이 래퍼가 하는 일은 셋뿐이며, 세 가지 모두 cron 에서만 문제가 되는 것들이다.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ① cwd 를 저장소 루트로. 설정의 파일 경로들이 상대경로이고 배치 출력 기본 경로도
#    저장소 기준인데, cron 은 임의의 cwd(보통 $HOME)로 실행한다.
cd "$ROOT"

# ② PATH 보강. cron 의 PATH 는 최소한(`/usr/bin:/bin`)이라 claude/node 를 못 찾는
#    일이 흔하다. 대표 설치 경로를 앞에 붙인다. 다른 곳에 설치했다면 여기나
#    crontab 의 PATH= 줄에 추가할 것.
export PATH="$HOME/.local/bin:$HOME/.claude/local:$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

# ③ 로그 남기기. cron 은 출력을 메일로 보내려 하는데 서버에 메일이 없으면 그냥
#    사라진다. 날짜별 파일로 남겨 실패를 나중에 확인할 수 있게 한다.
LOG_DIR="$ROOT/batch/logs"
mkdir -p "$LOG_DIR"

JOB="${1:-daily_briefing}"
if [ "$#" -gt 0 ]; then shift; fi

# venv 의 python 을 직접 부르면 `source bin/activate` 없이 동작한다 (저장소 루트가
# venv 루트). 종료 코드는 그대로 cron 에 전달된다.
exec "$ROOT/bin/python3" -m "batch.$JOB" "$@" >>"$LOG_DIR/$(date +%F).log" 2>&1
