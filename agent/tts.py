import wave
import numpy as np
import torch
from transformers import VitsModel, AutoTokenizer

from agent.config import TTS_OUTPUT_FILE
from agent.audio_io import play_wav_file


class TextToSpeech:
    """MMS-VITS 한국어 TTS 엔진. 모델을 1회 로드한 뒤 재사용합니다."""

    def __init__(self, device, model_name="facebook/mms-tts-kor"):
        self.device = device
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = VitsModel.from_pretrained(model_name).to(device)

    def synthesize_to_file(self, text, output_path=TTS_OUTPUT_FILE):
        """텍스트를 wav 파일로 변환하여 저장합니다."""
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
        play_wav_file(output_path)
