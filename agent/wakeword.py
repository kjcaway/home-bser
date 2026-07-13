import numpy as np
import openwakeword
from openwakeword.model import Model

from agent.config import CHUNK, RATE


def load_wakeword_model(keyword="alexa"):
    """openwakeword 내장 모델 중 지정된 호출어 모델을 로드합니다."""
    pretrained_models = openwakeword.get_pretrained_model_paths()
    keyword_path = [path for path in pretrained_models if keyword in path.lower()][0]
    return Model(wakeword_model_paths=[keyword_path])


def get_score(oww_model, audio_data):
    """오디오 청크에 대한 호출어 인식 점수를 반환합니다."""
    prediction = oww_model.predict(audio_data)
    return list(prediction.values())[0]


def reset_wakeword_state(oww_model, seconds=2):
    """호출어 모델의 내부 오디오 특징 버퍼를 무음으로 씻어낸 뒤 예측 버퍼를 초기화합니다.

    Model.reset() 은 예측 점수 버퍼만 비우고 오디오 특징 버퍼는 남겨두기 때문에,
    직전 턴에서 인식된 호출어("알렉사") 소리가 특징 윈도우에 남아 다음 턴의
    첫 청크에서 곧바로 재감지될 수 있습니다. 무음을 충분히 흘려 넣어 윈도우를
    새 상태로 만든 뒤 reset() 을 호출합니다.
    """
    silence = np.zeros(CHUNK, dtype=np.int16)
    for _ in range(int(RATE / CHUNK * seconds)):
        oww_model.predict(silence)
    oww_model.reset()
