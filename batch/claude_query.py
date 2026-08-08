"""배치용 Claude Code CLI 질의 — 마크다운 결과를 그대로 반환하고, 실패는 예외로 올린다.

음성 스킬(`agent/skills/claude_p.py`)과 달리 마크다운을 지우지 않고 제한 시간이 길다.
시스템 프롬프트는 잡 모듈이 갖고 인자로 넘긴다 (기본값 없음 — 빠뜨리면 `TypeError`).
"""

import json
import os
import subprocess

from agent.skills.claude_p import (
    DEFAULT_ALLOWED_TOOLS,
    WORK_DIR,
    resolve_effort,
    resolve_model,
)
from batch.config import load_batch_env
from batch.markdown_fix import fix_bullets  # noqa: F401  (claude_query.fix_bullets 재수출)

DEFAULT_TIMEOUT = 300.0

# 모델/effort 로그는 프로세스당 한 번만 출력한다.
_logged_options = False


def build_command(system_prompt):
    """`claude --print …` 명령줄을 조립한다. `system_prompt` 는 필수 인자."""
    global _logged_options

    load_batch_env()

    # json 형식은 오류 여부(is_error)와 턴 수를 함께 주므로 로그 진단에 유리하다.
    cmd = ["claude", "--print", "--output-format", "json"]

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

    cmd += ["--append-system-prompt", system_prompt]

    # 빈 값이면 도구 목록 자체를 비운다 — 플래그만 생략하면 도구는 살아 있고 권한만
    # 없어서 모델이 호출했다가 런타임에 거부당한다.
    allowed_tools = os.environ.get("CLAUDE_CLI_ALLOWED_TOOLS", DEFAULT_ALLOWED_TOOLS).strip()
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    else:
        print("[경고] CLAUDE_CLI_ALLOWED_TOOLS 가 비어 있어 웹 검색 없이 요약합니다.")
        print("       최신 정보가 필요하면 batch/.env 에 WebSearch,WebFetch 를 넣으세요.")
        cmd += ["--tools", ""]

    return cmd


def ask(question, system_prompt, timeout=None):
    """claude CLI 에 질문을 보내고 마크다운 답변을 반환한다. 실패는 예외.

    질문은 표준입력으로 넘긴다 ('-' 로 시작하는 문장의 옵션 오해·인자 길이 제한 회피).
    """
    load_batch_env()

    if timeout is None:
        timeout = float(os.environ.get("CLAUDE_CLI_TIMEOUT", DEFAULT_TIMEOUT))
    cmd = build_command(system_prompt)

    # cwd 를 저장소 밖에 두는 이유: 저장소 안에서 돌리면 이 저장소의 CLAUDE.md 가
    # 프로젝트 지침으로 딸려 들어간다.
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
        # 출력 형식이 바뀌어도 잡을 죽이지 않고 원문을 그대로 담는다.
        return fix_bullets(stdout)

    if payload.get("is_error"):
        raise RuntimeError(f"claude 오류 결과: {payload.get('subtype', 'unknown')}")

    # 턴 수 1 = 도구 없이 모델 기억으로 답한 것. 계속 1 이면 허용 도구 설정을 확인한다.
    num_turns = payload.get("num_turns")
    if num_turns is not None:
        print(f"[System] claude 턴 수: {num_turns} "
              f"({'검색 사용' if num_turns > 1 else '검색 미사용'})")

    return fix_bullets((payload.get("result") or "").strip())
