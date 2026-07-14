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


# 실행 환경(--environment) 프리셋: (device, 마이크 입력 인덱스, 스피커 출력 인덱스)
#   prod: 운영환경  (cuda GPU, USB 마이크 인덱스 2)
#   dev : 개발환경  (cpu,      기본 마이크 인덱스 0)
#
# output_device_index:
#   None  = 시스템 기본 출력장치(사운드서버)를 사용.
#   정수  = 특정 출력장치를 콕 집어 사용. nohup/SSH 백그라운드 실행 시 기본 출력이
#          실제 스피커로 라우팅되지 않아 소리가 안 나는 경우, `--list-devices` 로
#          스피커 인덱스를 확인해 여기에 지정하세요.
ENVIRONMENTS = {
    "prod": {"device": "cuda", "input_device_index": 2, "output_device_index": None},
    "dev": {"device": "cpu", "input_device_index": 0, "output_device_index": None},
}


def parse_device_args():
    """실행 인자를 파싱하여 (device, stt_compute_type, input_device_index,
    output_device_index, list_devices) 튜플을 반환합니다.

    --environment 로 실행 환경 프리셋을 선택합니다 (기본값: dev)
      prod = 운영환경 (device=cuda, mic=2)
      dev  = 개발환경 (device=cpu,  mic=0)
    --list-devices 를 주면 입출력 장치 목록만 출력하고 종료하도록 신호합니다.
    STT(Faster-Whisper) compute_type: GPU는 float16, CPU는 int8 이 적합합니다.
    """
    parser = argparse.ArgumentParser(description="로컬 오프라인 보이스 에이전트")
    parser.add_argument(
        "--environment",
        choices=list(ENVIRONMENTS.keys()),
        default="dev",
        help="실행 환경 프리셋 (prod=cuda/mic2, dev=cpu/mic0, 기본값: dev)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="사용 가능한 입력(마이크)/출력(스피커) 장치 목록을 출력하고 종료",
    )
    args = parser.parse_args()

    preset = ENVIRONMENTS[args.environment]
    device = preset["device"]
    input_device_index = preset["input_device_index"]
    output_device_index = preset["output_device_index"]
    stt_compute_type = "float16" if device == "cuda" else "int8"
    print(f"[System] 실행 환경: {args.environment} "
          f"(device: {device}, 마이크 입력 인덱스: {input_device_index}, "
          f"스피커 출력 인덱스: {output_device_index if output_device_index is not None else '기본'}, "
          f"STT compute_type: {stt_compute_type})")
    return device, stt_compute_type, input_device_index, output_device_index, args.list_devices
