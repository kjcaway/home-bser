# batch — 정기 실행(배치) 잡

사람이 부르지 않아도 정해진 시각에 돌아야 하는 작업들입니다. 모듈 하나가 잡 하나이고,
설정은 **프로젝트 루트의 `.env` 가 아니라 `batch/.env`** 에서 읽습니다.

## 개요

두 잡 모두 claude CLI 로 요약을 만들어 **날짜별 마크다운 파일**로 저장하고, 웹훅이
설정돼 있으면 그 파일을 **Discord 로 전송**합니다. 같은 날 다시 돌리면 파일을 덮어씁니다.

| 잡 | 모듈 | 하는 일 | 결과 파일 |
| --- | --- | --- | --- |
| 정기 LLM 요약 | `batch/daily_briefing.py` | 관심 **주제**(`BRIEFING_TOPICS`)를 웹 검색으로 훑어 주제별로 요약 | `batch/output/YYYY-MM-DD.md` |
| URL 브리핑 | `batch/url_briefing.py` | 커뮤니티 **사이트 한 곳**(`URL_BRIEFING_URL`)을 직접 열어 지금 올라온 글들을 요약 | `batch/output/url-YYYY-MM-DD.md` |

**두 잡의 결과 모양은 같습니다** — 개요 한두 문장 + 항목 6개, 항목의 제목은 **Discord
마스크 링크**(`[__"제목"__](<주소>)`)로 감쌉니다. 긴 주소를 감추고, `< >` 로 링크
미리보기(임베드) 카드를 억제합니다. 알림이 같은 채널에 나란히 올라오므로 읽는 쪽이
규칙을 하나만 익히면 되게 맞춘 것입니다.

### 정기 LLM 요약 (`daily_briefing`)

주제 목록을 순회하며 주제마다 claude 호출을 한 번씩 보냅니다. **주제 하나가 실패해도
나머지는 계속** 진행하고, 실패한 자리에는 사유를 적습니다.

```markdown
# 2026-08-01 브리핑

> 생성: 2026-08-01 07:00:12
> 주제 1개

## AI 업계 주요 소식

이번 주는 오픈소스 모델 공개와 추론 비용 인하가 겹치면서 ... (개요 150자 이내)

- [__"오픈소스 모델 가격 경쟁 본격화"__](<https://example.com/news/12345>) 추론 단가 인하 발표가 이어지며 도입 문턱이 낮아졌다는 평가입니다.
- ... (항목 6개)
```

**주제는 여러 개를 받지만, 분량은 "한 번에 주제 하나"로 역산돼 있습니다.** 주제를 늘리면
파일은 온전해도 Discord 메시지에서는 뒤 주제가 통째로 잘립니다 — 주제별로 알림을 받고
싶으면 `--topic` 을 단 **cron 줄을 나누세요**.

### URL 브리핑 (`url_briefing`)

대상 페이지를 `WebFetch` 로 직접 열어 개요 한두 문장 + 항목 6개를 씁니다. 머리말의
둘째 줄에 대상 사이트를 링크로 답니다 (`daily_briefing` 은 그 자리에 주제 수를 적습니다 —
주제 이름은 `## 주제` 로 이미 나오기 때문입니다).

```markdown
# 2026-08-01 URL 브리핑

> 생성: 2026-08-01 07:00:12
> 대상: [__"클리앙 모두의공원"__](<https://www.clien.net/service/board/park>)

최근 며칠은 ... (개요 150자 이내)

- [__"체감 전기요금 정리해봄"__](<https://example.com/18928374>) 누진구간 계산이 틀렸다는 반박이 이어졌습니다.
- ... (항목 6개)
```

**URL 은 하나만 받습니다.** 요약 분량이 Discord 본문 2000자를 이 보고서 하나가 다 쓴다고
보고 역산돼 있어, 두 번째 URL 부터는 메시지에서 잘려 나갑니다. 여러 곳을 훑고 싶으면
**cron 줄을 나누세요** — 알림도 사이트별로 따로 옵니다.

### 종료 코드

두 잡이 같은 규약을 씁니다. cron 이 실패를 알아보는 유일한 수단입니다.

| 코드 | 뜻 |
| --- | --- |
| `0` | 성공 (알림을 보냈거나, 웹훅을 설정하지 않았거나) |
| `1` | 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / `claude` 없음 / 주제·URL 없음·형식 오류) |
| `2` | 문서는 만들었지만 **요약이 실패했거나 Discord 전송에 실패**함 |

웹훅을 설정하지 않은 것은 실패가 아닙니다. 매일 `2` 로 끝나면 cron 경보가 의미를 잃기
때문입니다. 반대로 웹훅이 있는데 전송이 실패하면 `2` 입니다 — 아무도 지켜보지 않는 잡이라
알림이 안 나갔다는 사실을 알 방법이 종료 코드뿐입니다.

## 1. 로컬 실행 (개발·테스트)

### 최초 1회 — 설정 파일

이 파일이 없으면 잡은 아무것도 하지 않고 종료 코드 `1` 로 끝납니다 (루트 `.env` 를 대신
읽지 않습니다).

```bash
cd /path/to/home-bser        # 반드시 저장소 루트에서
cp batch/.env.example batch/.env
$EDITOR batch/.env
```

자주 건드리는 키만 추리면 아래와 같습니다. 전체 설명은 `batch/.env.example` 주석에 있고,
**실제 환경변수가 이 파일보다 우선**합니다(그래서 cron 줄에서 잡별로 덮어쓸 수 있습니다).

| 키 | 설명 |
| --- | --- |
| `CLAUDE_CLI_ENABLED` | 요약 백엔드 스위치. 꺼져 있으면 잡이 종료 코드 1 로 끝남 |
| `CLAUDE_CLI_MODEL` / `CLAUDE_CLI_EFFORT` | 모델과 생각 깊이 |
| `CLAUDE_CLI_TIMEOUT` | 호출당 제한 시간(초). 기본 300 |
| `CLAUDE_CLI_ALLOWED_TOOLS` | 허용 도구. `WebSearch,WebFetch` |
| `BRIEFING_TOPICS` | 정기 요약 주제(쉼표 구분) |
| `BRIEFING_OUTPUT_DIR` | 결과 디렉터리(기본 `batch/output`). 모든 잡이 공유 |
| `URL_BRIEFING_URL` / `URL_BRIEFING_NAME` | URL 브리핑 대상과 링크 라벨(비우면 호스트명) |
| `DISCORD_WEBHOOK_URL` | **비우면 전송을 건너뜁니다**(별도 on/off 키 없음). URL 자체가 인증 수단이라 로그에 절대 출력하지 않습니다 |
| `DISCORD_USERNAME` / `DISCORD_TIMEOUT` | 표시 이름 / 전송 제한 시간(기본 15초) |

### 정기 LLM 요약

```bash
./bin/python -m batch.daily_briefing                     # batch/.env 의 주제 전체
./bin/python -m batch.daily_briefing --topic "AI 동향"     # 주제 하나만 (반복 지정 가능)
./bin/python -m batch.daily_briefing --stdout            # 파일 저장·전송 없이 화면 출력만
./bin/python -m batch.daily_briefing --no-notify         # 저장만, Discord 전송 생략
./bin/python -m batch.daily_briefing --output /tmp/a.md  # 저장 경로 지정
```

### URL 브리핑

```bash
./bin/python -m batch.url_briefing
./bin/python -m batch.url_briefing --url https://example.com/board   # .env 대신 이 주소
./bin/python -m batch.url_briefing --name "클리앙 모두의공원"          # 링크 라벨 지정
./bin/python -m batch.url_briefing --stdout
./bin/python -m batch.url_briefing --no-notify
```

### Discord 웹훅만 따로 확인

```bash
./bin/python -m batch.discord_notify "테스트 메시지"
./bin/python -m batch.discord_notify --file batch/output/2026-08-01.md
./bin/python -m batch.discord_notify --file batch/output/2026-08-01.md --dry-run   # 보내지 않고 본문만
```

> **`python batch/daily_briefing.py` 는 쓰지 않습니다.** 그렇게 부르면 `sys.path[0]` 이
> `batch/` 가 되어 `import agent` 가 깨집니다. `-m` 이 cwd(저장소 루트)를 경로에 올려 줍니다.
> 새 사이트를 걸 때는 cron 에 넣기 전에 `--stdout` 으로 한 번 돌려 내용이 실제로
> 나오는지 확인하세요 (JS 렌더링·robots.txt 로 `WebFetch` 가 막히는 곳이 있습니다).

### 새 잡을 추가할 때

1. `batch/<잡이름>.py` 를 만들고 `main()` **첫 줄에 `load_batch_env()`** 호출
   (설정 격리가 호출 순서 계약이라 진입 함수마다 부릅니다).
2. claude CLI 를 쓴다면 **그 잡 파일에 `SYSTEM_PROMPT` 상수를 두고**
   `claude_query.ask(prompt, system_prompt=SYSTEM_PROMPT)` 로 넘길 것 — 명령 조립·모델
   해석·JSON 처리는 공유합니다. `system_prompt` 는 **필수 인자**입니다(기본값 없음).
   빠뜨리면 남의 잡 모양으로 조용히 도는 대신 `TypeError` 로 바로 멈춥니다.
3. 결과 파일명에 **잡을 알아볼 접두어**를 붙일 것 (출력 디렉터리를 모든 잡이 공유하므로,
   같은 날 도는 잡끼리 겹치지 않게 하는 것이 접두어뿐입니다).
4. 알림이 필요하면 `discord_notify.notify(text)` 한 줄. 웹훅이 없으면 조용히 건너뜁니다.

## 2. `batch/run.sh` 로 실행

cron 용 래퍼입니다. 첫 인자가 잡 이름이고(생략 시 `daily_briefing`), 그 뒤 인자는 잡으로
그대로 전달됩니다.

```bash
chmod +x batch/run.sh                    # 최초 1회

./batch/run.sh                           # = batch.daily_briefing
./batch/run.sh daily_briefing
./batch/run.sh url_briefing
./batch/run.sh url_briefing --stdout     # 뒤 인자는 잡으로 그대로 전달

tail -f batch/logs/$(date +%F).log       # 실행 결과 확인
```

래퍼가 대신 해 주는 것은 셋이고, **모두 cron 에서만 문제가 되는 것들**입니다.

1. **cwd** — 저장소 루트로 이동합니다. 설정의 파일 경로가 상대경로인데 cron 은 임의의
   cwd(보통 `$HOME`)로 실행합니다.
2. **PATH** — cron 의 PATH 는 `/usr/bin:/bin` 수준이라 `claude`(와 node 런타임)를 못 찾는
   일이 흔합니다. 대표 설치 경로를 앞에 붙입니다. 다른 곳에 설치했다면 `run.sh` 의
   `export PATH=...` 줄에 추가하세요.
3. **로그** — cron 은 출력을 메일로 보내려 하는데 서버에 메일이 없으면 사라집니다.
   `batch/logs/YYYY-MM-DD.log` 로 남깁니다.

또한 venv 의 `./bin/python3` 을 직접 부르므로 `source bin/activate` 가 필요 없고, 잡의
종료 코드는 그대로 cron 에 전달됩니다.

## 3. crontab 등록

crontab 에는 **절대 경로**로, 그리고 반드시 **래퍼(`run.sh`)** 를 적습니다.

```bash
cd /path/to/home-bser && pwd     # → /home/me/home-bser (이 값을 아래에 씁니다)
crontab -e
```

```cron
# 매일 07:00 정기 LLM 요약
0 7 * * * /home/me/home-bser/batch/run.sh daily_briefing

# 매일 07:30 URL 브리핑
30 7 * * * /home/me/home-bser/batch/run.sh url_briefing
```

### 잡별로 설정 덮어쓰기

crontab 의 명령 필드는 `/bin/sh -c` 로 실행되므로, 명령 **앞에 공백으로 나열**하면 환경
변수를 몇 개든 넘길 수 있습니다.

```cron
# 사이트별로 줄을 나누고, 알림 이름까지 구분
30 7 * * * URL_BRIEFING_URL=https://www.clien.net/service/board/park URL_BRIEFING_NAME="클리앙 모두의공원" DISCORD_USERNAME="클리앙 브리핑" /home/me/home-bser/batch/run.sh url_briefing

# 이 실행에만 effort 낮추기
0 7 * * * CLAUDE_CLI_EFFORT=low /home/me/home-bser/batch/run.sh daily_briefing
```

**crontab 맨 위의 `NAME=value` 줄과는 다른 물건입니다.** 상단 줄은 그 아래 **모든** 잡에
적용되고 셸 파싱도 되지 않으므로, `PATH` 처럼 전체 공통인 것에만 쓰세요.

변수가 서너 개를 넘으면 사이트별 래퍼가 읽기 편하고, 셸 파일 안에서는 `%` 이스케이프를
신경 쓸 필요가 없어 더 확실합니다.

```bash
#!/usr/bin/env bash
# batch/run-clien.sh
export URL_BRIEFING_URL="https://www.clien.net/service/board/park"
export URL_BRIEFING_NAME="클리앙 모두의공원"
export DISCORD_USERNAME="클리앙 브리핑"
exec "$(dirname "$0")/run.sh" url_briefing "$@"
```

### 등록 전에 그 줄을 그대로 돌려보세요

적을 줄에서 **스케줄만 떼고** `sh -c` 로 감싸 실행합니다. cron 과 같은 셸이라 zsh 에서만
되는 문법에 속지 않습니다.

```bash
sh -c 'URL_BRIEFING_URL=https://example.com/board URL_BRIEFING_NAME="예시" /home/me/home-bser/batch/run.sh url_briefing'
tail -f batch/logs/$(date +%F).log
```

## 주의할 점

- **`%` 는 crontab 특수문자입니다.** 명령 안의 `%` 는 개행으로 해석되고 그 뒤는 stdin 으로
  넘어갑니다. **퍼센트 인코딩 URL** 이 대표적인 함정 — `topics/%EC%A3%BC%EC%8B%9D` 는 첫
  `%` 에서 명령이 끊겨 `run.sh` 가 아예 실행되지 않는데 종료 코드는 0 이라 cron 은 정상으로
  봅니다. `\%` 로 이스케이프하거나 사이트별 래퍼를 쓰세요.
- **절대 경로 + `chmod +x`, 그리고 출력 리디렉션 금지.** crontab 에 상대 경로는 쓸 수 없고,
  `>> log 2>&1` 을 덧붙이면 `run.sh` 의 로그와 둘로 갈립니다.
- **등록 직후 한 번은 직접 돌려보세요.** claude CLI 는 로그인 인증을 쓰기 때문에, 대화형
  셸에서는 되던 것이 cron 사용자 환경에서는 인증을 못 찾아 실패할 수 있습니다.
- **결과 파일은 하루에 하나씩 덮어씁니다.** 같은 잡을 하루에 여러 번 돌리면 파일은 마지막
  것만 남습니다(Discord 알림은 실행마다 나갑니다). 둘 다 남기려면 `--output` 으로 경로를
  가르세요.
- **Discord 본문 2000자를 넘으면 뒤쪽이 잘립니다.** 로그에 `[경고] 문서가 Discord 본문
  상한을 넘었습니다` 로 남고, 전문은 원본 파일에 그대로 있습니다.
- **`URL_BRIEFING_URL` 에는 스킴(`https://`)을 꼭 붙이고, 허용 도구를 비우지 마세요.**
  스킴이 없으면 실행 즉시 오류이고, `WebFetch` 가 빠지면 페이지 대신 모델의 기억으로 쓴
  보고서가 나옵니다(로그의 `턴 수: 1 (검색 미사용)` 이 그 신호).
- **시각은 시스템 로컬 시간대 기준**입니다. macOS 는 cron 이 TCC 제한을 받으므로 홈
  디렉터리 접근이 막히면 `launchd` 로 등록하세요(Ubuntu 는 해당 없음).
