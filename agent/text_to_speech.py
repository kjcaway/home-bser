"""MMS-VITS 한국어 TTS 엔진 (실제 음성 합성·재생).

마이크/스피커가 없는 테스트용 대역은 agent/silent_text_to_speech.py 를 쓴다.
"""

import wave

import numpy as np
import torch
from transformers import VitsModel, AutoTokenizer

from agent.audio_play import play_wav_file
from agent.config import TTS_OUTPUT_FILE
from agent.text_norm import normalize_for_tts


class TextToSpeech:
    """MMS-VITS 한국어 TTS 엔진. 모델을 1회 로드한 뒤 재사용합니다."""

    def __init__(self, device, model_name="facebook/mms-tts-kor", output_device_index=None,
                 barge_in=None):
        self.device = device
        self.output_device_index = output_device_index
        # 답변 재생 중 호출어를 감시해 재생을 끊는 리스너
        # (agent/barge_in_listener.BargeInListener).
        # None 이면 기존처럼 재생이 끝날 때까지 끼어들 수 없다. 마이크 스트림이 필요해
        # TTS 보다 늦게 만들어지므로, 호출부에서 나중에 붙여도 되도록 속성으로 둔다.
        self.barge_in = barge_in
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
        """텍스트를 음성으로 변환한 뒤 스피커로 재생합니다.

        barge_in 리스너가 붙어 있으면 합성·재생 구간 내내 마이크를 열어 호출어를
        감시하고, 감지되면 재생을 청크 경계에서 즉시 끊는다. 합성(수 초)이 끝나기
        전에 감지될 수도 있으므로 재생 직전에 한 번 더 확인한다.
        """
        listener = self.barge_in

        # 이미 이 턴에서 끼어들기가 있었다면(스킬이 speak 를 두 번 호출하는 경우 등)
        # 사용자는 다음 명령을 말하려는 중이다. 뒤늦은 답변을 덮어 재생하지 않는다.
        if listener is not None and listener.triggered:
            print(f"✋ 끼어들기 이후이므로 답변 재생을 건너뜁니다: {text}")
            return

        print("🗣️ 답변을 음성으로 변환 중...")

        if listener is None:
            play_wav_file(self.synthesize_to_file(text), self.output_device_index)
            return

        listener.start()
        try:
            output_path = self.synthesize_to_file(text)
            if not listener.triggered:
                play_wav_file(output_path, self.output_device_index,
                              stop_event=listener.stop_event)
        finally:
            listener.stop()
