#!/usr/bin/env bash
#
# 배치 잡 cron 래퍼. 첫 인자가 잡 이름(기본 daily_briefing), 그 뒤 인자는 잡으로 전달된다.
#
#   ./batch/run.sh url_briefing --stdout
#   0 7 * * * /path/to/home-bser/batch/run.sh daily_briefing
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ① cwd — 설정의 파일 경로가 상대경로인데 cron 은 임의의 cwd 로 실행한다.
cd "$ROOT"

# ② PATH — cron 의 PATH 는 최소한이라 claude/node 를 못 찾는다. 다른 곳에 설치했거나
#    gemini(보통 nvm 아래)를 쓴다면 여기나 crontab 의 PATH= 줄에 추가할 것.
export PATH="$HOME/.local/bin:$HOME/.claude/local:$HOME/.npm-global/bin:/usr/local/bin:/opt/homebrew/bin:$PATH"

# ③ 로그 — cron 의 출력은 메일이 없는 서버에서 그냥 사라진다.
LOG_DIR="$ROOT/batch/logs"
mkdir -p "$LOG_DIR"

JOB="${1:-daily_briefing}"
if [ "$#" -gt 0 ]; then shift; fi

# venv 의 python 을 직접 부르므로 `source bin/activate` 가 필요 없다.
exec "$ROOT/bin/python3" -m "batch.$JOB" "$@" >>"$LOG_DIR/$(date +%F).log" 2>&1
