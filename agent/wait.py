"""밖에서 끊을 수 없는 블로킹 작업을 워커 스레드에 맡기고 감시하는 헬퍼."""

import time


def wait_for_completion(done_event, cancel_event=None, deadline=None, poll_seconds=0.05):
    """작업 완료 / 끼어들기 / 제한 시간 중 먼저 오는 것을 기다려 무엇이었는지 반환한다.

    LLM 응답처럼 **밖에서 끊을 수단이 없는 블로킹 호출**을 워커 스레드에 맡겨두고,
    이 함수로 감시하기 위한 헬퍼다. 반환값은 `"done"` / `"cancelled"` / `"timeout"`
    이며, 뒤처리(프로세스 kill, 예외 변환 등)는 호출자가 각자의 사정에 맞게 한다 —
    claude 는 자식 프로세스를 죽일 수 있지만 hermes 의 SDK 호출은 그럴 수 없어,
    두 스킬의 취소 뒤처리가 서로 다르기 때문이다.

    `deadline` 은 `time.monotonic()` 기준 절대 시각이며, None 이면 제한 시간을 보지
    않는다(호출 쪽이 이미 자체 타임아웃을 갖고 있는 경우).
    """
    while True:
        if done_event.wait(poll_seconds):
            return "done"
        if cancel_event is not None and cancel_event.is_set():
            return "cancelled"
        if deadline is not None and time.monotonic() >= deadline:
            return "timeout"
