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
WAKE_RESPONSE_FILE = "soundfile/res0.wav"   # 호출어("알렉사") 감지 성공 시 사용자에게 들려줄 응답음
TIMER_ALARM_FILE = "soundfile/timer_wakeup.wav"   # 타이머 종료 시 재생할 알람음


# 실행 환경(--environment) 프리셋: (device, 마이크 입력 장치 인덱스)
#   prod: 운영환경  (cuda GPU, USB 마이크 인덱스 1)
#   dev : 개발환경  (cpu,      기본 마이크 인덱스 0)
ENVIRONMENTS = {
    "prod": {"device": "cuda", "input_device_index": 1},
    "dev": {"device": "cpu", "input_device_index": 0},
}


def parse_device_args():
    """실행 인자를 파싱하여 (device, stt_compute_type, input_device_index) 튜플을 반환합니다.

    --environment 로 실행 환경 프리셋을 선택합니다 (기본값: dev)
      prod = 운영환경 (device=cuda, input-device=1)
      dev  = 개발환경 (device=cpu,  input-device=0)
    STT(Faster-Whisper) compute_type: GPU는 float16, CPU는 int8 이 적합합니다.
    """
    parser = argparse.ArgumentParser(description="로컬 오프라인 보이스 에이전트")
    parser.add_argument(
        "--environment",
        choices=list(ENVIRONMENTS.keys()),
        default="dev",
        help="실행 환경 프리셋 (prod=cuda/mic1, dev=cpu/mic0, 기본값: dev)",
    )
    args = parser.parse_args()

    preset = ENVIRONMENTS[args.environment]
    device = preset["device"]
    input_device_index = preset["input_device_index"]
    stt_compute_type = "float16" if device == "cuda" else "int8"
    print(f"[System] 실행 환경: {args.environment} "
          f"(device: {device}, 마이크 입력 장치 인덱스: {input_device_index}, "
          f"STT compute_type: {stt_compute_type})")
    return device, stt_compute_type, input_device_index
