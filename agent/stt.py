import numpy as np
from faster_whisper import WhisperModel


def load_stt_model(device, compute_type, model_size="small"):
    """Faster-Whisper STT 모델을 로드합니다."""
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def transcribe_pcm(whisper_model, pcm_bytes, language="ko"):
    """int16 PCM 바이트를 텍스트로 변환합니다."""
    audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    segments, _ = whisper_model.transcribe(audio_np, language=language, beam_size=5)
    return "".join([segment.text for segment in segments]).strip()
