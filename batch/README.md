# batch — 정기 실행(배치) 잡

음성 턴 안에서 하기엔 너무 느리거나, 사람이 부르지 않아도 돌아야 하는 작업들을 모아둔
디렉터리입니다. 모듈 하나가 잡 하나이고, 설정은 **루트 `.env` 가 아니라 `batch/.env`**
에서 읽습니다.

| 잡 | 모듈 | 하는 일 |
| --- | --- | --- |
| 정기 LLM 요약 | `batch/daily_briefing.py` | 관심 주제를 claude CLI 의 웹 검색으로 훑어 날짜별 마크다운으로 저장 |

## 실행

```bash
# 저장소 루트에서
cp batch/.env.example batch/.env      # 최초 1회: 주제·모델 설정
./bin/python -m batch.daily_briefing
```

```bash
./bin/python -m batch.daily_briefing --topic "AI 업계 주요 소식"   # 주제 하나만 (반복 가능)
./bin/python -m batch.daily_briefing --stdout                    # 파일 저장 없이 출력만
./bin/python -m batch.daily_briefing --output /tmp/brief.md       # 저장 경로 지정
```

> **`python batch/daily_briefing.py` 는 쓰지 않습니다.** 그렇게 부르면 `sys.path[0]` 이
> `batch/` 가 되어 `import agent` 가 깨집니다. `-m` 은 cwd(저장소 루트)를 경로에 올려
> 주므로 `agent` 를 그대로 쓸 수 있고, 저장소가 이미 쓰는 방식(`python -m agent.text_norm`)
> 과 같습니다.

결과는 `batch/output/YYYY-MM-DD.md` 로 저장됩니다(`BRIEFING_OUTPUT_DIR` 로 변경 가능).
같은 날 다시 돌리면 덮어씁니다.

```markdown
# 2026-07-30 브리핑

> 생성: 2026-07-30 07:00:12 · 모델: claude-sonnet-5 · effort: high · 주제 3개

## AI 업계 주요 소식

- ...
```

## cron 등록

```bash
chmod +x batch/run.sh
crontab -e
```
```cron
# 매일 07:00 브리핑
0 7 * * * /path/to/home-bser/batch/run.sh
```

`batch/run.sh` 가 cron 특유의 문제 셋을 대신 처리합니다.

1. **cwd** — 저장소 루트로 이동합니다. 설정의 파일 경로가 상대경로이고 출력 기본
   경로도 저장소 기준인데, cron 은 임의의 cwd(보통 `$HOME`)로 실행합니다.
2. **PATH** — cron 의 PATH 는 `/usr/bin:/bin` 수준이라 `claude`(와 그 node 런타임)를
   못 찾는 일이 흔합니다. 대표 설치 경로를 앞에 붙입니다. 다른 곳에 설치했다면
   `run.sh` 나 crontab 의 `PATH=` 줄에 추가하세요.
3. **로그** — cron 은 출력을 메일로 보내려 하는데 서버에 메일이 없으면 그냥 사라집니다.
   `batch/logs/YYYY-MM-DD.log` 로 남깁니다.

> **처음 등록했으면 cron 을 기다리지 말고 `./batch/run.sh` 를 직접 한 번 돌려보세요.**
> claude CLI 는 로그인 인증을 사용하므로, 대화형 셸에서는 되던 것이 cron 사용자
> 환경에서는 인증을 못 찾아 실패할 수 있습니다. 이건 PATH 와는 다른 문제이고,
> 로그를 봐야 구분됩니다.

## 종료 코드

cron 이 실패를 알아볼 수 있도록 구분합니다.

| 코드 | 뜻 |
| --- | --- |
| `0` | 모든 주제 성공 |
| `1` | 설정 문제로 아무것도 하지 않음 (스위치 꺼짐 / `claude` 없음 / 주제 없음) |
| `2` | 문서는 만들었지만 실패한 주제가 하나 이상 있음 |

주제 하나가 실패해도 **잡 전체를 멈추지 않습니다.** 실패한 주제 자리에는 사유를 적고
나머지를 계속 요약합니다 — 검색 한 건이 타임아웃 났다고 그날 브리핑이 통째로 없어지는
편이 더 나쁘기 때문입니다.

## 설정이 루트 `.env` 와 분리되는 방식

`batch/config.py` 의 `load_batch_env()` 하나가 전부입니다.

`agent.config.load_env_file()` 은 여러 번 불려도 **첫 호출만 실제로 파싱**하는 전역
가드를 갖고 있고, 스킬들(`claude_p.is_enabled()` 등)은 내부에서 인자 없이 그것을 호출해
루트 `.env` 를 읽습니다. 배치가 **먼저** 자기 파일을 지정해 부르면 이후 스킬 쪽 호출이
no-op 이 되어, 루트 `.env` 는 아예 읽히지 않습니다.

순서가 곧 계약이므로 잊기 쉽습니다. 그래서 `load_batch_env()` 를 **배치 쪽 모든 진입
함수의 첫 줄**에서 호출합니다(스킬들이 `is_enabled()` 마다 `load_env_file()` 을 부르는
것과 같은 이유). 새 배치 모듈을 추가할 때도 같은 규칙을 지키세요.

`batch/.env` 는 루트 `.gitignore` 의 `.env` 패턴(슬래시가 없어 모든 하위 경로에 적용)에
이미 걸려 커밋되지 않습니다. `batch/.env.example` 은 무시되지 않으므로 템플릿으로
커밋됩니다.

## claude CLI 호출을 스킬과 따로 두는 이유

`batch/claude_query.py` 는 `agent/skills/claude_p.py` 의 `ask()` 를 쓰지 않고 명령을
따로 조립합니다. 차이는 셋이고 모두 "결과를 귀로 듣는가 / 눈으로 읽는가" 에서 나옵니다.

| | 음성 스킬 | 배치 |
| --- | --- | --- |
| 시스템 프롬프트 | 최대 3문장 평문, 영문은 한글 음차 (TTS vocab 제약) | 마크다운 목록 3~5개, 음차 불필요 |
| 후처리 | `strip_markdown()` 으로 목록·링크 제거 | 마크다운이 결과물이므로 그대로 |
| 취소·제한 시간 | 끼어들기 감시 필요(`Popen`+스레드), 기본 60초 | 끊을 사용자가 없음(`subprocess.run`), 기본 300초 |

단, **모델/effort 해석 규칙과 실행 디렉터리는 스킬에서 import 해서 씁니다.** 별칭 표를
복사해두면 스킬 쪽과 조용히 어긋나, 같은 `.env` 값을 적었는데 음성과 배치가 다른
모델로 도는 상태가 됩니다 (`test-claude-cli.py` 가 같은 이유로 `resolve_model()` 을
가져다 쓰는 것과 같은 판단입니다).

## 새 배치 잡 추가

1. `batch/<잡이름>.py` 를 만들고 `main()` 에서 **첫 줄에 `load_batch_env()`** 호출.
2. 저장소의 코드 규칙(한 파일에 클래스 하나 또는 클래스 없이 함수 여럿)을 그대로 따름.
3. `./bin/python -m batch.<잡이름>` 으로 실행되는지 확인.
4. cron 은 `./batch/run.sh <잡이름>` 으로 등록 (래퍼가 cwd/PATH/로그를 처리).
