"""답변 재생 중 호출어 감지(barge-in).

기본 파이프라인은 명령 처리(STT·LLM·TTS 재생) 동안 마이크를 정지시킨다. 스피커
출력이 그대로 되녹음되어 호출어를 오인식하는 것을 막기 위해서인데, 그 부작용으로
**답변이 끝날 때까지 사용자가 끼어들 수 없다** — 엉뚱한 답변도 끝까지 들어야 한다.

이 모듈은 그 틈을 메운다. 답변을 만들고 들려주는 동안 마이크를 다시 열어 별도
스레드에서 호출어를 감시하고, 감지되면 `stop_event` 를 세운다. 감시가 걸리는 구간은
두 곳이다:

- **답변 재생** (`TextToSpeech.speak()`) — `play_wav_file` 이 `stop_event` 를 청크
  경계에서 확인하므로 재생이 즉시 끊긴다.
- **LLM 응답 대기** (`claude_p` / `hermes_api` 의 `ask()`) — 같은 이벤트를
  `cancel_event` 로 넘겨받아, 서면 claude 프로세스를 죽이거나 응답을 버리고
  `BargeInCancelled`(agent/barge_in_cancelled.py) 를 올린다. 대기가 수십 초까지 갈
  수 있는 구간이라 여기서 끊을 수 있는 것이 재생보다 오히려 더 중요하다. 그 대기
  자체는 agent/wait.py 의 `wait_for_completion()` 이 감시한다.

어느 쪽이든 호출부는 `triggered` 를 보고 곧바로 다음 턴을 열면 된다 — 사용자는 이미
호출어를 말한 상태이므로 다시 부르게 해서는 안 된다.

에코 대책: 재생 중에는 스피커 소리가 마이크로 되돌아온다(AEC 가 없다). 그래서
대기 상태(WAKE_THRESHOLD)보다 높은 임계값을 따로 쓰고, 감시를 시작할 때
reset_wakeword_state() 로 특징 버퍼를 씻어낸다. 버퍼에는 이 턴을 연 "알렉사" 가
아직 남아 있어, 씻지 않으면 첫 청크에서 곧바로 자기 자신을 재감지한다.
"""

import threading

import numpy as np

from agent.audio_record import flush_input_stream
from agent.config import CHUNK
from agent.wakeword import get_score, reset_wakeword_state


class BargeInListener:
    """재생 구간에만 마이크를 열어 호출어를 감시하고 재생을 끊는 리스너.

    한 턴의 사용 흐름은 `reset()` → (`start()` → `stop()`)* → `triggered` 확인이다.
    `start()`/`stop()` 은 한 턴에 여러 번 불릴 수 있으므로(스킬이 speak() 를 두 번
    호출하는 경우) 반복 호출에 안전하게 만들었다. 한 번 감지되면 그 턴이 끝날 때까지
    `triggered` 가 유지되어 뒤따르는 재생은 아예 시작하지 않는다.
    """

    def __init__(self, stream, oww_model, threshold, enabled=True):
        self.stream = stream
        self.oww_model = oww_model
        self.threshold = threshold
        self.enabled = enabled
        # 재생 중단 신호. play_wav_file(stop_event=...) 에 그대로 넘긴다.
        self.stop_event = threading.Event()
        self._triggered = False
        self._thread = None

    @property
    def triggered(self):
        """이번 턴에서 사용자가 호출어로 끼어들었는지."""
        return self._triggered

    def reset(self):
        """감지 상태를 지운다 (새 턴 시작 시 호출)."""
        self._triggered = False
        self.stop_event.clear()

    def start(self):
        """감시 스레드를 띄운다. 꺼져 있거나 이미 감지된 뒤면 아무 것도 하지 않는다.

        마이크는 이 구간에만 켠다(`stop()` 에서 다시 끈다). 명령 처리 중 내내 켜두면
        STT·LLM 대기 동안 쌓인 오래된 오디오를 감시 스레드가 먼저 읽게 되어 판정이
        실시간에서 밀린다.
        """
        if not self.enabled or self._triggered or self._thread is not None:
            return

        self.stop_event.clear()
        self.stream.start_stream()
        flush_input_stream(self.stream)      # 정지 전후로 버퍼에 남은 오디오 폐기
        reset_wakeword_state(self.oww_model)  # 이 턴을 연 호출어의 잔상 제거
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        try:
            while not self.stop_event.is_set():
                # 블로킹 read. stop() 이 호출돼도 한 청크(80ms) 안에 루프로 돌아온다.
                data = self.stream.read(CHUNK, exception_on_overflow=False)
                score = get_score(self.oww_model, np.frombuffer(data, dtype=np.int16))
                if score > self.threshold:
                    self._triggered = True
                    self.stop_event.set()   # 재생 중단
                    print(f"\n✋ [끼어들기] 호출어 감지 (score {score:.2f}) — 재생을 멈춥니다.")
                    return
        except Exception as e:
            # 끼어들기는 부가 기능이므로 실패해도 본 흐름(답변 재생)을 막지 않는다.
            print(f"[System] 끼어들기 감지 실패(무시): {e}")

    def stop(self):
        """감시를 멈추고 스레드가 끝날 때까지 기다린 뒤 마이크를 다시 정지한다.

        반환값은 이번 턴의 감지 여부. 반복 호출해도 안전하다.
        """
        self.stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
            self.stream.stop_stream()
        return self._triggered
