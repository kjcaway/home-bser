"""배치용 Gemini CLI 질의 — `batch/claude_query.py` 와 시그니처·반환값·실패 방식이 같다.

잡 모듈이 import 대상만 바꿔 백엔드를 갈아끼울 수 있게 하려는 것이라 이 대칭이 목적이다.
CLI 차이 넷: 시스템 프롬프트 전용 플래그 없음 / `--effort` 없음 /
`--allowed-tools` 는 승인 필요 도구를 아예 제외 / stderr 잡음(`clean_stderr`).
"""

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from batch.config import load_batch_env
from batch.markdown_fix import fix_bullets

CLI_NAME = "gemini"

DEFAULT_TIMEOUT = 300.0

# gemini CLI 의 내장 도구 이름 (claude 의 WebSearch/WebFetch 에 해당).
DEFAULT_ALLOWED_TOOLS = "google_web_search,web_fetch"

# 모델 별칭 → 전체 모델 ID. 별칭은 "그 계열의 최신 모델"이라 CLI 버전이 오르면 조용히
# 바뀌므로 아는 것은 여기서 편다. 표에 없는 값은 그대로 흘려보낸다.
MODEL_ALIASES = {
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
    "flash-lite": "gemini-2.5-flash-lite",
}

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# gemini 를 실행할 작업 디렉터리. 저장소 루트에서 돌리면 `GEMINI.md` 가 문맥으로
# 딸려 들어가고, gemini 는 cwd 를 작업 공간으로 삼으므로 전용 디렉터리를 쓴다.
WORK_DIR = Path(tempfile.gettempdir()) / "home-bser-gemini"

_logged_options = False


def is_enabled():
    """`GEMINI_CLI_ENABLED` 로 판단한다 — truthy 만 켜짐, 미설정·해석 불가는 꺼짐."""
    load_batch_env()

    flag = os.environ.get("GEMINI_CLI_ENABLED")
    if flag is None or flag.strip() == "":
        return False

    normalized = flag.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False

    print(f"[경고] GEMINI_CLI_ENABLED 값을 해석할 수 없어 gemini CLI 를 끕니다: {flag!r}")
    return False


def resolve_model(raw):
    """모델 값을 전체 이름으로 편다. 빈 값이면 빈 문자열(호출자가 `--model` 생략)."""
    name = (raw or "").strip()
    if not name:
        return ""
    return MODEL_ALIASES.get(name.lower(), name)


def resolve_allowed_tools(raw):
    """쉼표로 적은 도구 목록을 CLI 인자용 리스트로 나눈다. 빈 값이면 빈 리스트.

    gemini 는 비대화형 실행에서 승인이 필요한 도구(`web_fetch` 등)를 목록에서 **아예
    제외**하므로, 빠뜨리면 오류 없이 모델 기억으로 쓴 결과가 나온다.
    """
    return [tool.strip() for tool in (raw or "").split(",") if tool.strip()]


def clean_stderr(text):
    """gemini stderr 에서 node 잡음(DeprecationWarning·인증 로그·스택 프레임)을 걷어낸다.

    호출자가 메시지를 300자로 잘라 문서에 싣기 때문에, 남겨두면 정작 사유가 잘려 나간다.
    정체가 확실한 줄만 지운다.
    """
    noise = ("(node:", "(Use `node", "Loaded cached credentials")
    lines = [line for line in (text or "").splitlines()
             if not line.strip().startswith(noise)
             # 들여쓰기까지 함께 봐야 "at least …" 같은 평범한 문장을 지우지 않는다.
             and not re.match(r"\s+at\s", line)]
    return "\n".join(lines).strip()


def log_usage(stats):
    """실제로 답을 쓴 모델과 도구 호출 수를 남긴다 (gemini 는 한도에 따라 flash 로 내려간다)."""
    if not isinstance(stats, dict):
        return

    models = stats.get("models")
    if isinstance(models, dict) and models:
        print(f"[System] gemini 응답 모델: {', '.join(models)}")

    tools = stats.get("tools")
    if isinstance(tools, dict):
        calls = tools.get("totalCalls", 0)
        print(f"[System] gemini 도구 호출: {calls}회 "
              f"({'검색 사용' if calls else '검색 미사용'})")


def build_command(system_prompt):
    """`gemini --output-format json …` 명령줄을 조립한다. `system_prompt` 는 필수 인자.

    CLI 가 최종 입력을 `표준입력 + "\\n\\n" + --prompt` 로 합치므로 질문은 표준입력,
    시스템 프롬프트는 `--prompt` 로 넘긴다 (`--prompt` 는 비대화형 모드도 명시적으로 켠다).
    """
    global _logged_options

    load_batch_env()

    cmd = [CLI_NAME, "--output-format", "json"]

    model = resolve_model(os.environ.get("GEMINI_CLI_MODEL", ""))
    if model:
        cmd += ["--model", model]

    if not _logged_options:
        _logged_options = True
        print(f"[System] gemini CLI 모델: {model or 'CLI 기본값'}")

    allowed_tools = resolve_allowed_tools(
        os.environ.get("GEMINI_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS))
    if allowed_tools:
        # 배열 인자라 값을 하나씩 넘긴다 (`--allowed-tools a --allowed-tools b`).
        for tool in allowed_tools:
            cmd += ["--allowed-tools", tool]
    else:
        print("[경고] GEMINI_CLI_ALLOWED_TOOLS 가 비어 있습니다.")
        print("       비대화형 실행에서는 web_fetch 가 도구 목록에서 빠져 페이지를 열지")
        print("       못합니다 — batch/.env 에 google_web_search,web_fetch 를 넣으세요.")

    cmd += ["--prompt", system_prompt]

    return cmd


def ask(question, system_prompt, timeout=None):
    """gemini CLI 에 질문을 보내고 마크다운 답변을 반환한다. 실패는 예외."""
    load_batch_env()

    if timeout is None:
        timeout = float(os.environ.get("GEMINI_CLI_TIMEOUT", DEFAULT_TIMEOUT))
    cmd = build_command(system_prompt)

    # 없으면 subprocess 가 FileNotFoundError 로 죽는데, `gemini` 를 못 찾은 것과 헷갈린다.
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    proc = subprocess.run(
        cmd,
        input=question,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=WORK_DIR,
    )

    stdout = (proc.stdout or "").strip()

    # 종료 코드보다 본문의 error 객체를 먼저 본다 — 실패해도 JSON 은 정상적으로 나오고,
    # 그쪽 메시지가 훨씬 구체적이다(예: 인증 실패 사유).
    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            raise RuntimeError(
                f"gemini 오류 결과 ({error.get('type', 'unknown')}): "
                f"{str(error.get('message', ''))[:300]}")

    if proc.returncode != 0:
        stderr = clean_stderr(proc.stderr)
        raise RuntimeError(f"gemini 비정상 종료 (exit {proc.returncode}): {stderr[:300]}")

    if not stdout:
        return ""

    if payload is None:
        return fix_bullets(stdout)

    log_usage(payload.get("stats"))

    return fix_bullets(str(payload.get("response") or "").strip())
