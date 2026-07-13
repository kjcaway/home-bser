import openwakeword
from openwakeword.model import Model


def load_wakeword_model(keyword="alexa"):
    """openwakeword 내장 모델 중 지정된 호출어 모델을 로드합니다."""
    pretrained_models = openwakeword.get_pretrained_model_paths()
    keyword_path = [path for path in pretrained_models if keyword in path.lower()][0]
    return Model(wakeword_model_paths=[keyword_path])


def get_score(oww_model, audio_data):
    """오디오 청크에 대한 호출어 인식 점수를 반환합니다."""
    prediction = oww_model.predict(audio_data)
    return list(prediction.values())[0]
