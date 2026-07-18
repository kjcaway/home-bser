import threading

from agent.audio_io import play_wav_file


class BackgroundSound:
    """지연 임계값 후 wav 를 백그라운드 스레드에서 주기적으로 재생하고 stop() 으로 멈춘다.

    hermes LLM 응답처럼 수 초 걸리는 블로킹 작업 동안 '처리 중' 대기음을 들려주기
    위한 헬퍼다. start() 후 delay_seconds 안에 stop() 이 호출되면(= 작업이 빨리
    끝나면) 재생을 아예 시작하지 않아, 빠른 응답에 불필요한 효과음이 끼어들지 않는다.

    대기음은 끊김 없이 연속 반복하면 사용자에게 거슬리므로, 한 번 재생한 뒤
    interval_seconds 만큼 쉬고 다시 재생한다(= interval_seconds 마다 한 번씩).
    쉬는 동안 stop() 이 호출되면 즉시 종료한다.

    stop() 은 재생 스레드가 스트림을 닫고 종료할 때까지 기다린다(join). 따라서
    stop() 이 반환된 뒤에는 출력 장치가 비어 있어, 이어서 TTS 를 재생해도 같은
    장치를 두 스트림이 동시에 여는 충돌이 없다.
    """

    def __init__(self, file_path, output_device_index=None, delay_seconds=0.8,
                 interval_seconds=5.0):
        self.file_path = file_path
        self.output_device_index = output_device_index
        self.delay_seconds = delay_seconds
        self.interval_seconds = interval_seconds
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        # 지연 임계값 동안 대기 — 이 사이 stop() 되면(빠른 응답) 재생하지 않는다.
        if self._stop.wait(self.delay_seconds):
            return
        try:
            # 한 번 재생 → interval_seconds 대기 → 반복. loop=False 로 한 번만
            # 재생하고, 재생 사이 간격은 여기서 준다(대기 중 stop() 되면 즉시 종료).
            while not self._stop.is_set():
                play_wav_file(self.file_path, self.output_device_index,
                              stop_event=self._stop, loop=False)
                if self._stop.wait(self.interval_seconds):
                    break
        except Exception as e:
            # 대기음은 부가 기능이므로 실패해도 본 흐름(LLM 응답)을 막지 않는다.
            print(f"[System] 대기음 재생 실패(무시): {e}")

    def stop(self):
        """재생을 멈추고 스레드가 완전히 끝날 때까지 기다린다(반복 호출 안전)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
