"""끼어들기 취소 예외. 감지 쪽은 agent/barge_in_listener.py."""


class BargeInCancelled(Exception):
    """기다리던 작업을 사용자의 호출어 끼어들기로 취소했음을 알리는 예외.

    LLM 스킬(`claude_p` / `hermes_api`)의 `ask()` 가 올리고, `handle()` 이 받아
    답변을 말하지 않고 턴을 끝낸다.
    """
