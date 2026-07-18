"""Silero VAD 로더 및 프레임별 음성/무음 판정.

발화 종료 감지(endpointing)에 사용한다. 고정 길이 녹음을 대체하는
audio_io.record_until_silence() 가 이 모듈의 SileroVAD 로 매 프레임을 판정한다.

오프라인 제약: torch.hub 방식은 모델을 GitHub 에서 내려받아야 하므로 부적합하다.
pip 패키지 `silero-vad` 는 jit 모델 파일을 패키지에 번들하므로 네트워크 없이
로드된다 → 이 모듈은 load_silero_vad() 만 사용한다.

Silero(16kHz) 는 프레임 크기가 512 샘플(32ms)로 고정이다. 파이프라인의 CHUNK
(1280 샘플)와 배수가 맞지 않으므로, 호출부(record_until_silence)에서 샘플을 모아
정확히 512 샘플 창 단위로 is_speech() 를 호출한다.
"""
import numpy as np
import torch

from agent.config import VAD_THRESHOLD

# Silero 16kHz 고정 입력 창 크기(샘플). 32ms 에 해당.
WINDOW_SAMPLES = 512
_SAMPLE_RATE = 16000


def load_vad_model():
    """Silero VAD jit 모델을 로드한다(네트워크 없이 번들 파일 사용)."""
    from silero_vad import load_silero_vad
    return load_silero_vad()


class SileroVAD:
    """프레임별 음성 확률을 내는 상태 유지형 VAD 래퍼.

    Silero 모델은 내부 순환 상태를 가지므로 한 발화를 처리하기 전에 reset() 해
    이전 발화의 상태가 새 발화 판정에 새는 것을 막는다.
    """

    def __init__(self, model, threshold=VAD_THRESHOLD):
        self.model = model
        self.threshold = threshold
        self.model.reset_states()

    def reset(self):
        """새 발화 녹음을 시작하기 전에 모델 순환 상태를 초기화한다."""
        self.model.reset_states()

    def speech_prob(self, window_int16):
        """길이 512 int16 창의 음성 확률(0~1)을 반환한다."""
        audio = window_int16.astype(np.float32) / 32768.0
        return self.model(torch.from_numpy(audio), _SAMPLE_RATE).item()

    def is_speech(self, window_int16):
        """창이 음성이면 True (확률이 threshold 이상)."""
        return self.speech_prob(window_int16) >= self.threshold


def load_vad():
    """SileroVAD 인스턴스를 만들어 반환하는 편의 함수."""
    return SileroVAD(load_vad_model())
