# batch — 정기 실행(배치) 잡

정해진 시각에 cron 으로 도는 작업들. 모듈 하나가 잡 하나이고, 설정은 루트 `.env` 가
아니라 **`batch/.env`** 에서 읽는다.

## 잡 목록

세 잡 모두 LLM CLI 로 요약을 만들어 **잡 이름 디렉터리 아래 실행 시각(시 단위)으로**
저장하고, 웹훅이 설정돼 있으면 그 파일을 Discord 로 보낸다. 같은 시간대 재실행은
덮어쓰고, 다른 시각의 실행은 파일이 따로 쌓인다.

| 잡 | 하는 일 | 결과 파일 |
| --- | --- | --- |
| `daily_briefing` | 주제 목록(`BRIEFING_TOPICS`)을 웹 검색으로 훑어 주제별 요약 | `batch/output/daily_briefing/YYYY-MM-DD-HH.md` |
| `url_briefing` | 커뮤니티 사이트 한 곳(`URL_BRIEFING_URL`)을 열어 지금 올라온 글 요약 | `batch/output/url_briefing/YYYY-MM-DD-HH.md` |
| `url_briefing_gemini` | 위와 같은 프롬프트를 gemini CLI 로 (모델 비교용) | `batch/output/url_briefing_gemini/YYYY-MM-DD-HH.md` |

결과 모양은 셋 다 같다 — 개요 한두 문장 + 항목 6개, 항목 제목은 Discord 마스크 링크
(`[__"제목"__](<주소>)`).

```markdown
# 2026-08-01 URL 브리핑

> 생성: 2026-08-01 07:00:12
> 대상: [__"클리앙 모두의공원"__](<https://www.clien.net/service/board/park>)

최근 며칠은 ... (개요 150자 이내)

- [__"체감 전기요금 정리해봄"__](<https://example.com/18928374>) 누진구간 계산이 틀렸다는 반박이 이어졌습니다.
- ... (항목 6개)
```

**주제와 URL 은 실행 한 번에 하나씩이 전제다.** 분량이 Discord 본문 2000자를 보고서
하나가 다 쓴다고 보고 역산돼 있어, 늘리면 메시지 뒤쪽이 잘린다(파일은 온전하고 로그에
경고가 뜬다). 여러 개를 돌리려면 **cron 줄을 나눈다** — 알림도 따로 온다.

### 종료 코드

| 코드 | 뜻 |
| --- | --- |
| `0` | 성공 (알림을 보냈거나, 웹훅을 설정하지 않았거나) |
| `1` | 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / `claude`·`gemini` 없음 / 주제·URL 없음·형식 오류) |
| `2` | 문서는 만들었지만 요약 또는 Discord 전송이 실패함 |

## 1. 로컬 실행

### 최초 1회 — 설정 파일

이 파일이 없으면 잡은 종료 코드 `1` 로 끝난다 (루트 `.env` 를 대신 읽지 않는다).

```bash
cd /path/to/home-bser        # 반드시 저장소 루트에서
cp batch/.env.example batch/.env
$EDITOR batch/.env
```

전체 설명은 `batch/.env.example` 주석에 있고, **실제 환경변수가 이 파일보다 우선**이다.

| 키 | 설명 |
| --- | --- |
| `CLAUDE_CLI_ENABLED` | claude 백엔드 스위치. 꺼져 있으면 종료 코드 1 |
| `CLAUDE_CLI_MODEL` / `CLAUDE_CLI_EFFORT` | 모델과 생각 깊이 |
| `CLAUDE_CLI_TIMEOUT` | 호출당 제한 시간(초). 기본 300 |
| `CLAUDE_CLI_ALLOWED_TOOLS` | 허용 도구. `WebSearch,WebFetch` |
| `GEMINI_CLI_ENABLED` | gemini 판 스위치 (claude 와 별개라 둘 다 켜도 됨) |
| `GEMINI_CLI_MODEL` / `GEMINI_CLI_TIMEOUT` | 모델과 제한 시간. gemini 에는 effort 가 없음 |
| `GEMINI_CLI_ALLOWED_TOOLS` | 허용 도구. `google_web_search,web_fetch` |
| `GEMINI_API_KEY` | gemini 인증 키 (아래 주의 참고) |
| `BRIEFING_TOPICS` | 정기 요약 주제(쉼표 구분) |
| `BRIEFING_OUTPUT_DIR` | 결과가 쌓이는 뿌리 디렉터리(기본 `batch/output`) |
| `URL_BRIEFING_URL` / `URL_BRIEFING_NAME` | URL 브리핑 대상과 링크 라벨(비우면 호스트명) |
| `DISCORD_WEBHOOK_URL` | 비우면 전송을 건너뛴다(별도 on/off 키 없음). 로그에 출력하지 않음 |
| `DISCORD_USERNAME` / `DISCORD_TIMEOUT` | 표시 이름 / 전송 제한 시간(기본 15초) |

### 실행

```bash
./bin/python -m batch.daily_briefing                      # batch/.env 의 주제 전체
./bin/python -m batch.daily_briefing --topic "AI 동향"      # 주제 하나만 (반복 지정 가능)

./bin/python -m batch.url_briefing
./bin/python -m batch.url_briefing --url https://example.com/board   # .env 대신 이 주소
./bin/python -m batch.url_briefing --name "클리앙 모두의공원"           # 링크 라벨 지정

./bin/python -m batch.url_briefing_gemini                 # 인자는 claude 판과 동일
```

공통 인자: `--stdout`(저장·전송 없이 화면 출력), `--no-notify`(저장만),
`--output 경로`(저장 경로 지정).

```bash
# Discord 웹훅만 따로 확인
./bin/python -m batch.discord_notify "테스트 메시지"
./bin/python -m batch.discord_notify --file batch/output/url_briefing/2026-08-01-07.md --dry-run

# 같은 시각의 두 백엔드 결과 비교
diff batch/output/url_briefing/$(date +%F-%H).md \
     batch/output/url_briefing_gemini/$(date +%F-%H).md
```

> **`python batch/daily_briefing.py` 는 쓰지 않는다** — `sys.path[0]` 이 `batch/` 가 되어
> `import agent` 가 깨진다. `-m` 이 cwd(저장소 루트)를 경로에 올려 준다.
> 새 사이트를 걸기 전에 `--stdout` 으로 한 번 돌려 보자 (JS 렌더링·robots.txt 로
> `WebFetch` 가 막히는 곳이 있다).

### 새 잡을 추가할 때

1. `batch/<잡이름>.py` 를 만들고 `main()` **첫 줄에 `load_batch_env()`** 호출.
2. `SYSTEM_PROMPT` 상수를 그 잡 파일에 두고 `claude_query.ask(prompt, system_prompt=…)`
   로 넘긴다 (필수 인자 — 빠뜨리면 `TypeError`). gemini 를 쓰려면 `gemini_query` 로
   바꿔 부르기만 하면 된다 (시그니처가 같다).
3. `JOB_NAME` 상수를 두고 저장 경로를 `config.output_path(JOB_NAME, run_at)` 로 만든다.
   `run_at` 은 `main()` 에서 `datetime.now()` 를 **한 번만** 읽은 값이어야 한다.
4. 알림이 필요하면 `discord_notify.notify(text)` 한 줄.

## 2. `batch/run.sh` 로 실행

cron 용 래퍼. 첫 인자가 잡 이름(생략 시 `daily_briefing`), 그 뒤 인자는 잡으로 전달된다.

```bash
chmod +x batch/run.sh                    # 최초 1회

./batch/run.sh                           # = batch.daily_briefing
./batch/run.sh url_briefing
./batch/run.sh url_briefing --stdout
tail -f batch/logs/$(date +%F).log
```

래퍼가 해 주는 것은 셋이고 모두 cron 에서만 문제가 된다: **cwd**(저장소 루트로 이동),
**PATH**(claude/node 설치 경로 보강), **로그**(`batch/logs/YYYY-MM-DD.log`).
venv 의 `bin/python3` 을 직접 부르므로 `source bin/activate` 도 필요 없다.

`gemini` 는 PATH 목록에 없다 — 보통 nvm 아래(`~/.nvm/versions/node/<버전>/bin`)에
깔리는데 그 경로에 노드 버전이 박혀 있어서다. crontab 의 `PATH=` 줄에 적는다.

## 3. crontab 등록

**절대 경로**로, 그리고 반드시 **래퍼(`run.sh`)** 를 적는다.

```cron
# gemini 를 쓸 때만 필요
PATH=/home/me/.nvm/versions/node/v22.11.0/bin:/usr/local/bin:/usr/bin:/bin

0 7 * * * /home/me/home-bser/batch/run.sh daily_briefing
30 7 * * * /home/me/home-bser/batch/run.sh url_briefing

# 같은 대상을 gemini 로도 (알림 이름을 갈라 두 결과를 구분)
35 7 * * * DISCORD_USERNAME="URL 브리핑 (gemini)" /home/me/home-bser/batch/run.sh url_briefing_gemini
```

### 잡별로 설정 덮어쓰기

명령 필드는 `/bin/sh -c` 로 실행되므로, 명령 **앞에 공백으로 나열**하면 환경 변수를
넘길 수 있다 (crontab 맨 위의 `NAME=value` 줄은 그 아래 **모든** 잡에 적용되므로
`PATH` 처럼 전체 공통인 것에만 쓴다).

```cron
30 7 * * * URL_BRIEFING_URL=https://www.clien.net/service/board/park URL_BRIEFING_NAME="클리앙 모두의공원" DISCORD_USERNAME="클리앙 브리핑" /home/me/home-bser/batch/run.sh url_briefing
0 7 * * * CLAUDE_CLI_EFFORT=low /home/me/home-bser/batch/run.sh daily_briefing
```

변수가 서너 개를 넘으면 사이트별 래퍼가 읽기 편하고 `%` 이스케이프도 신경 쓸 필요가 없다.

```bash
#!/usr/bin/env bash
# batch/run-clien.sh
export URL_BRIEFING_URL="https://www.clien.net/service/board/park"
export URL_BRIEFING_NAME="클리앙 모두의공원"
export DISCORD_USERNAME="클리앙 브리핑"
exec "$(dirname "$0")/run.sh" url_briefing "$@"
```

등록 전에는 스케줄만 뗀 줄을 `sh -c` 로 감싸 그대로 돌려 본다 (cron 과 같은 셸이다).

```bash
sh -c 'URL_BRIEFING_URL=https://example.com/board /home/me/home-bser/batch/run.sh url_briefing'
```

## 주의할 점

- **`%` 는 crontab 특수문자다.** 명령 안의 `%` 는 개행으로 해석된다 — 퍼센트 인코딩
  URL 이 대표적인 함정으로, 명령이 끊겼는데 종료 코드는 0 이라 cron 은 정상으로 본다.
  `\%` 로 이스케이프하거나 사이트별 래퍼를 쓴다.
- **절대 경로 + `chmod +x`, 출력 리디렉션 금지** (`>> log 2>&1` 을 붙이면 `run.sh` 의
  로그와 둘로 갈린다).
- **등록 직후 한 번은 직접 돌려 보자.** claude CLI 는 로그인 인증을 쓰기 때문에
  대화형 셸에서 되던 것이 cron 사용자 환경에서는 실패할 수 있다.
- **허용 도구를 비우지 말 것.** `WebFetch` 가 빠지면 페이지 대신 모델 기억으로 쓴
  보고서가 나온다(로그의 `턴 수: 1 (검색 미사용)` 이 신호). gemini 판은 더 조용하다 —
  `web_fetch` 가 빠지면 "권한이 없다"는 말조차 없다(신호는 `도구 호출: 0회`).
- **`gemini` 는 인증을 따로 확인해야 한다.** 개인 계정 로그인(`oauth-personal`)이
  `IneligibleTierError` 로 거부되면 `GEMINI_API_KEY` 만으로는 부족하고,
  `~/.gemini/settings.json` 의 `security.auth.selectedType` 도 `"gemini-api-key"` 로
  바꿔야 한다(설정값이 환경변수보다 우선).
- **같은 대상을 두 백엔드로 돌리면 그 사이트는 요청을 두 번 받는다.** 비교가 끝나면
  gemini 판 cron 줄은 빼거나 간격을 둔다.
- **시각은 시스템 로컬 시간대 기준.** macOS 는 cron 이 TCC 제한을 받으므로 홈 디렉터리
  접근이 막히면 `launchd` 로 등록한다(Ubuntu 는 해당 없음).
