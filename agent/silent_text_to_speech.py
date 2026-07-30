"""음성 합성 없이 답변을 출력만 하는 TTS 대역 (--off-speaker 전용).

실제 엔진은 agent/text_to_speech.py 의 TextToSpeech.
"""

from agent.text_norm import normalize_for_tts


class SilentTextToSpeech:
    """음성 합성 없이 답변을 텍스트로만 출력하는 TTS 대역 (--off-speaker 테스트용).

    TextToSpeech 와 같은 인터페이스(`speak()`, `output_device_index`)를 노출하므로
    스킬들은 실제 TTS 인지 구분하지 않고 그대로 받아 씁니다. VITS 모델을 아예 로드하지
    않아 기동이 빠르고, 오디오 장치가 없는 환경(CI·SSH 세션 등)에서도 동작합니다.
    """

    # 무음 모드임을 알리는 표식. 오디오를 '직접' 내는 대신 외부 프로세스를 띄우는
    # 스킬(timer)이 이를 보고 재생을 건너뛴다. speak() 만으로는 그런 경로를 막을 수 없다.
    silent = True

    def __init__(self, output_device_index=None):
        self.output_device_index = output_device_index

    def speak(self, text):
        """실제 재생 대신 답변을 표준출력에 찍습니다.

        정규화 결과가 원문과 다르면 그것도 같이 찍는다. 영문·숫자가 실제로 어떻게
        읽힐지는 소리를 들어야만 알 수 있는데, --off-speaker 에는 그 소리가 없다.
        """
        print(f"🔇 [무음 응답] {text}")
        reading = normalize_for_tts(text)
        if reading != text:
            print(f"🔡 [읽기] {reading}")
