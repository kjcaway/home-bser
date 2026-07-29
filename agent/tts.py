import re
import wave
import numpy as np
import torch
from transformers import VitsModel, AutoTokenizer

from agent.config import TTS_OUTPUT_FILE
from agent.audio_io import play_wav_file

# mms-tts-kor 토크나이저 vocab에는 숫자가 없어 아라비아 숫자는 무음 처리됩니다.
# TTS 입력 전에 숫자를 한자어 한글 읽기로 변환하기 위한 테이블입니다.
_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ["", "십", "백", "천"]       # 4자리 그룹 내부 단위
_GROUP_UNITS = ["", "만", "억", "조"]       # 4자리 그룹 단위


def _read_number(num_str):
    """숫자 문자열을 한자어 한글 읽기로 변환합니다. (예: '30' -> '삼십', '10000' -> '만')"""
    num = int(num_str)
    if num == 0:
        return "영"

    # 4자리씩 그룹으로 나눔 (일의 자리 그룹부터)
    groups = []
    while num > 0:
        groups.append(num % 10000)
        num //= 10000

    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        group = groups[gi]
        if group == 0:
            continue
        piece = ""
        for pos in range(3, -1, -1):
            d = (group // 10 ** pos) % 10
            if d == 0:
                continue
            # '일십', '일백', '일천'은 '십', '백', '천'으로 읽음
            if not (d == 1 and pos > 0):
                piece += _DIGITS[d]
            piece += _SMALL_UNITS[pos]
        # '일만', '일억' 등도 '만', '억'으로 읽음
        if piece == "일" and gi > 0:
            piece = ""
        parts.append(piece + _GROUP_UNITS[gi])
    return " ".join(parts)


def normalize_numbers(text):
    """텍스트 안의 아라비아 숫자를 한글 읽기로 치환합니다. (예: '1분 30초' -> '일분 삼십초')"""
    return re.sub(r"\d+", lambda m: _read_number(m.group()), text)


class TextToSpeech:
    """MMS-VITS 한국어 TTS 엔진. 모델을 1회 로드한 뒤 재사용합니다."""

    def __init__(self, device, model_name="facebook/mms-tts-kor", output_device_index=None):
        self.device = device
        self.output_device_index = output_device_index
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = VitsModel.from_pretrained(model_name).to(device)

    def synthesize_to_file(self, text, output_path=TTS_OUTPUT_FILE):
        """텍스트를 wav 파일로 변환하여 저장합니다."""
        text = normalize_numbers(text)
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
        """실제 재생 대신 답변을 표준출력에 찍습니다."""
        print(f"🔇 [무음 응답] {text}")
