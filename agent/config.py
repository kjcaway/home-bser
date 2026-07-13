import argparse
import pyaudio

# ==========================================
# 오디오 및 파일 설정 상수
# ==========================================
CHUNK = 1280
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 5
TTS_OUTPUT_FILE = "response.wav"
WAKE_RESPONSE_FILE = "res0.wav"   # 호출어("알렉사") 감지 성공 시 사용자에게 들려줄 응답음


def parse_device_args():
    """실행 인자를 파싱하여 (device, stt_compute_type) 튜플을 반환합니다.

    --device 로 cpu / cuda 선택 (기본값: cpu)
    STT(Faster-Whisper) compute_type: GPU는 float16, CPU는 int8 이 적합합니다.
    """
    parser = argparse.ArgumentParser(description="로컬 오프라인 보이스 에이전트")
    parser.add_argument(
        "--device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="STT/TTS 모델을 실행할 디바이스 (기본값: cpu)",
    )
    args = parser.parse_args()
    device = args.device
    stt_compute_type = "float16" if device == "cuda" else "int8"
    print(f"[System] 실행 디바이스: {device} (STT compute_type: {stt_compute_type})")
    return device, stt_compute_type
