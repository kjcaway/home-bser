import wave
import numpy as np
import torch
from transformers import VitsModel, AutoTokenizer

from agent.config import TTS_OUTPUT_FILE
from agent.audio_io import play_wav_file
from agent.text_norm import normalize_for_tts, normalize_numbers  # noqa: F401  (재노출)


class TextToSpeech:
    """MMS-VITS 한국어 TTS 엔진. 모델을 1회 로드한 뒤 재사용합니다."""

    def __init__(self, device, model_name="facebook/mms-tts-kor", output_device_index=None):
        self.device = device
        self.output_device_index = output_device_index
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = VitsModel.from_pretrained(model_name).to(device)

    def synthesize_to_file(self, text, output_path=TTS_OUTPUT_FILE):
        """텍스트를 wav 파일로 변환하여 저장합니다."""
        text = normalize_for_tts(text)
        inputs = self.tokenizer(text, return_tensors="pt").to(self.device)
        with torch.no_grad():
            output = self.model(**inputs).waveform

        audio_data = output.cpu().numpy().squeeze()
        audio_data = (audio_data * 32767).astype(np.int16)

        with wave.open(output_path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self.model.config.sampling_rate)
            wav_file.writeframes(audio_data.tobytes())

        return output_path

    def speak(self, text):
        """텍스트를 음성으로 변환한 뒤 스피커로 재생합니다."""
        print("🗣️ 답변을 음성으로 변환 중...")
        output_path = self.synthesize_to_file(text)
        play_wav_file(output_path, self.output_device_index)


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
