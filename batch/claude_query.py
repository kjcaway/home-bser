"""배치용 Claude Code CLI 질의 (마크다운 결과를 그대로 돌려준다).

음성 스킬(`agent/skills/claude_p.py`)과 목적이 달라 명령 조립과 후처리를 따로 둔다.
차이는 셋이고, 모두 "결과를 귀로 듣는가 / 눈으로 읽는가" 에서 나온다:

1. **시스템 프롬프트** — 스킬은 "최대 3문장 평문, 영문은 한글 음차"를 요구한다.
   TTS 모델(`mms-tts-kor`) vocab 이 26자뿐이라 알파벳이 그대로 오면 잘못 읽히기
   때문이다(`agent/text_norm.py` 참고). 배치 결과물은 파일이라 그 제약이 없고,
   오히려 항목 수가 넉넉한 마크다운이 읽기 좋다.
2. **후처리** — 스킬은 `strip_markdown()` 으로 목록 기호와 링크를 지운다. 여기서는
   그것이 결과물 자체이므로 손대지 않는다.
3. **취소·제한 시간** — 배치에는 끼어들 사용자가 없으니 `Popen` + 워커 스레드로
   취소를 감시할 이유가 없고, `subprocess.run` 으로 충분하다. 대신 제한 시간을 훨씬
   길게 잡는다 (웹 검색을 여러 번 도는 것이 정상이고, 지연이 체감되지 않는다).

모델/effort 해석 규칙과 실행 디렉터리는 스킬에서 **가져다 쓴다**(복사하지 않는다).
별칭 표를 여기에 복사해두면 스킬 쪽과 조용히 어긋나, 같은 `.env` 값을 적었는데
음성과 배치가 다른 모델로 도는 상태가 된다 — `test-claude-cli.py` 가 같은 이유로
`resolve_model()` 을 import 하는 것과 같은 판단이다.
"""

import json
import os
import re
import subprocess

from agent.skills.claude_p import (
    DEFAULT_ALLOWED_TOOLS,
    WORK_DIR,
    resolve_effort,
    resolve_model,
)
from batch.config import load_batch_env

# 음성 스킬의 기본 60초보다 훨씬 길다. 웹 검색을 여러 주제로 도는 잡이고, 사람이
# 기다리고 있지 않으므로 지연보다 완주가 중요하다.
DEFAULT_TIMEOUT = 300.0

# 결과가 마크다운 문서에 그대로 들어가므로, 스킬과 달리 목록 표기를 **요구**한다.
# 제목(#)을 금지하는 이유는 상위 문서(daily_briefing.render_document)가 주제별
# 제목을 붙이기 때문이다 — 모델이 제목을 또 달면 계층이 두 겹으로 어긋난다.
SYSTEM_PROMPT = (
    "당신은 한국어 브리핑 작성자입니다. "
    "주어진 주제에 대해 웹 검색으로 최신 정보를 확인한 뒤 한국어로 요약하세요. "
    "'-' 로 시작하는 항목 3~5개로 정리하고, 각 항목은 한두 문장으로 쓰세요. "
    "제목(#)은 넣지 마세요. 상위 문서가 주제별 제목을 붙입니다. "
    "각 항목 끝에 출처를 괄호로 덧붙이세요. "
    "검색으로 확인되지 않은 내용은 쓰지 마세요."
)

# 어떤 모델/effort 로 돌았는지는 결과 품질을 해석할 때 필요하지만 매 주제마다 찍을
# 필요는 없다. 프로세스당 한 번만 출력한다 (스킬의 _logged_options 와 같은 취지).
_logged_options = False


def fix_bullets(text):
    """`-항목` 처럼 불릿 뒤 공백이 빠진 줄을 `- 항목` 으로 고친다.

    스킬의 `strip_markdown()` 과 방향이 반대인 **복구**다. 모델이 간헐적으로 첫 항목의
    공백을 빠뜨리는데, 그러면 마크다운에서 목록으로 렌더되지 않고 `-모샨AI…` 가 글자
    그대로 보인다. 결과물이 사람이 읽는 문서라 눈에 바로 띄는 흠이면서, 고치는 규칙은
    한 줄로 끝나 프롬프트를 더 조이는 것보다 확실하다.

    `-` 만 보정하고 `*` 는 손대지 않는다. 줄 머리의 `*` 는 목록일 수도 있고 강조일 수도
    있어서(`*이탤릭* 시작`), 공백을 넣으면 오히려 뜻이 바뀐다. 반면 `-` 뒤에 바로 글자가
    오는 형태는 마크다운에서 목록이 되려다 실패한 경우뿐이다. `---`(구분선)은 기호가
    이어지므로 제외된다.

    **숫자도 제외한다** — `-5도까지 떨어짐` 은 음수이고, 공백을 넣으면 영하 5도가 5도로
    뜻이 바뀐다. 대신 `-2026년…` 처럼 숫자로 시작하는 진짜 불릿은 못 고치고 넘어가는데,
    그 대가가 더 싸기 때문이다: 놓치면 문서에 `-` 한 줄이 글자로 보이는 미용상 흠이고,
    잘못 고치면 값의 의미가 바뀐다.
    """
    return re.sub(r"^-(?=[^\s\-\d])", "- ", text, flags=re.MULTILINE)


def describe_options():
    """현재 설정에서 해석된 (모델, effort) 문자열 쌍. 결과 문서 머리말에 적는다."""
    load_batch_env()
    model = resolve_model(os.environ.get("CLAUDE_CLI_MODEL", ""))
    effort = resolve_effort(os.environ.get("CLAUDE_CLI_EFFORT", ""))
    return model or "CLI 기본값", effort or "CLI 기본값"


def build_command():
    """`claude --print …` 명령줄을 조립한다."""
    global _logged_options

    load_batch_env()

    # json 형식은 오류 여부(is_error)와 턴 수까지 함께 주므로 배치 로그 진단에 유리하다.
    cmd = ["claude", "--print", "--output-format", "json"]

    # 별칭이 들어와도 전체 모델 이름으로 펴서 넘긴다 (claude_p.resolve_model 주석 참고).
    model = resolve_model(os.environ.get("CLAUDE_CLI_MODEL", ""))
    if model:
        cmd += ["--model", model]

    effort = resolve_effort(os.environ.get("CLAUDE_CLI_EFFORT", ""))
    if effort:
        cmd += ["--effort", effort]

    if not _logged_options:
        _logged_options = True
        print(f"[System] claude CLI 모델: {model or 'CLI 기본값'} / "
              f"effort: {effort or 'CLI 기본값'}")

    cmd += ["--append-system-prompt", SYSTEM_PROMPT]

    # 도구 허용 목록. 빈 값이면 도구 목록 자체를 비운다 — 플래그를 생략하면 도구는
    # 살아 있고 권한만 없어서, 모델이 호출했다가 런타임에 거부당한다
    # (claude_p.build_command 의 같은 주석 참고).
    #
    # 다만 이 잡은 웹 검색이 존재 이유라, 도구를 끈 브리핑은 모델이 기억으로 쓴 글이
    # 된다. 설정을 막지는 않되 무엇이 벌어지는지는 로그로 남긴다.
    allowed_tools = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS).strip()
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    else:
        print("[경고] CLAUDE_CLI_ALLOWED_TOOLS 가 비어 있어 웹 검색 없이 요약합니다.")
        print("       최신 정보가 필요하면 batch/.env 에 WebSearch,WebFetch 를 넣으세요.")
        cmd += ["--tools", ""]

    return cmd


def ask(question, timeout=None):
    """claude CLI 에 질문을 보내고 마크다운 그대로의 답변 문자열을 반환한다.

    실패는 예외로 올린다 (`subprocess.TimeoutExpired` 포함) — 호출자가 주제 단위로
    받아 실패한 주제만 표시하고 나머지는 계속 진행할 수 있게 하기 위함이다.

    질문은 인자가 아니라 **표준입력**으로 넘긴다. '-' 로 시작하는 문장이 CLI 옵션으로
    오해받거나 긴 프롬프트가 인자 길이 제한에 걸리는 것을 피하려는 것으로,
    스킬과 `test-claude-cli.py` 가 쓰는 방식과 같다.
    """
    load_batch_env()

    if timeout is None:
        timeout = float(os.environ.get("CLAUDE_CLI_TIMEOUT", DEFAULT_TIMEOUT))
    cmd = build_command()

    # cwd 를 임시 디렉터리로 두는 이유는 스킬과 같다: 저장소 안에서 claude 를 돌리면
    # 이 저장소의 CLAUDE.md 가 프로젝트 지침으로 딸려 들어가 일반 주제 요약에까지
    # 저장소 문맥이 섞인다.
    proc = subprocess.run(
        cmd,
        input=question,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=WORK_DIR,
    )

    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        raise RuntimeError(f"claude 비정상 종료 (exit {proc.returncode}): {stderr[:300]}")

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return ""

    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        # 출력 형식이 바뀌어도 잡을 죽이지 않고 원문을 그대로 문서에 담는다.
        return fix_bullets(stdout)

    if payload.get("is_error"):
        raise RuntimeError(f"claude 오류 결과: {payload.get('subtype', 'unknown')}")

    # num_turns 는 도구를 썼는지 판별하는 가장 싼 지표다. 1 이면 검색 없이 모델
    # 기억으로 답한 것 — 최신 정보를 기대한 브리핑에서 계속 1 이 찍히면
    # CLAUDE_CLI_ALLOWED_TOOLS 를 확인한다.
    num_turns = payload.get("num_turns")
    if num_turns is not None:
        print(f"[System] claude 턴 수: {num_turns} "
              f"({'검색 사용' if num_turns > 1 else '검색 미사용'})")

    return fix_bullets((payload.get("result") or "").strip())
