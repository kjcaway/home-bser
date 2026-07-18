import numpy as np
from faster_whisper import WhisperModel


# 인식 정확도를 높이기 위해 호출어/스킬 어휘를 힌트로 준다.
# faster-whisper 는 initial_prompt 가 None 일 때만 hotwords 를 사용하므로 둘을 같이 쓰지 않는다.
HOTWORDS = "알렉사 타이머 알람 스톱워치 1분 3분 5분 10초 30초"


def load_stt_model(device, compute_type, model_size="small"):
    """Faster-Whisper STT 모델을 로드합니다."""
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_pcm(whisper_model, pcm_bytes, language="ko"):
    """int16 PCM 바이트를 텍스트로 변환합니다.

    녹음은 VAD 동적 종료(record_until_silence)라 뒤쪽 무음은 짧지만, 발화 끝
    판정 여유분(STT_SILENCE_MS)만큼의 무음과 배경 소음은 남는다. Whisper 는 무음
    구간에서 학습 데이터(유튜브 자막)의 상투구를 환각 생성하므로("시청해주셔서
    감사합니다" 등) 아래 옵션으로 이를 이중으로 억제한다:
      - vad_filter: 무음 구간을 아예 모델에 넣지 않는다 (환각 억제의 핵심)
      - condition_on_previous_text=False: 환각이 다음 세그먼트로 번지는 루프를 차단
      - no_speech_threshold / log_prob_threshold: 저신뢰 세그먼트를 버린다
      - hotwords: 타이머 스킬이 쓰는 어휘 쪽으로 디코딩을 유도
    """
    audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = whisper_model.transcribe(
        audio_np,
        language=language,
        beam_size=5,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        condition_on_previous_text=False,
        no_speech_threshold=0.6,
        log_prob_threshold=-1.0,
        hotwords=HOTWORDS,
    )
    return "".join([segment.text for segment in segments]).strip()
