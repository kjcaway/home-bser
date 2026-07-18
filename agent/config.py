import argparse
import os
from pathlib import Path
from typing import NamedTuple

import pyaudio

# 프로젝트 루트(= venv 루트). agent/config.py 기준 한 단계 위.
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ==========================================
# 오디오 및 파일 설정 상수
# ==========================================
CHUNK = 1280
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 5   # (레거시) 고정 길이 녹음용. 현재 파이프라인은 VAD 동적 녹음을 사용.
TTS_OUTPUT_FILE = "response.wav"
WAKE_RESPONSE_FILE = "soundfile/res0.wav"   # 호출어("알렉사") 감지 성공 시 사용자에게 들려줄 응답음
TIMER_ALARM_FILE = "soundfile/timer_wakeup.wav"   # 타이머 종료 시 재생할 알람음
WAITING_SOUND_FILE = "soundfile/waiting.wav"   # LLM 응답 대기 중 '처리 중'을 알리는 대기음

# ==========================================
# 대기음(LLM 응답 지연 안내) 파라미터
# ==========================================
# hermes LLM 호출은 수 초 걸릴 수 있어, 그 동안 대기음(WAITING_SOUND_FILE)을 반복
# 재생해 '멈춘 게 아님'을 사용자에게 알린다. 단, 아래 임계값 안에 응답이 오면 재생을
# 아예 시작하지 않아 빠른 응답에 불필요한 효과음이 끼어들지 않는다.
WAITING_SOUND_DELAY_SECONDS = 0.8   # 이 시간 안에 응답이 오면 대기음을 재생하지 않음
WAITING_SOUND_INTERVAL_SECONDS = 5.0   # 대기음을 연속 반복하지 않고 이 간격마다 한 번씩만 재생


# ==========================================
# STT 동적 녹음(VAD endpointing) 파라미터
# ==========================================
# 고정 5초 녹음 대신, 말이 시작되면 녹음하고 일정 시간 무음이면 종료한다.
#   - 짧은 명령은 1~2초에 즉시 반응, 긴 명령(5초 초과)도 상한까지 안 잘림
#   - 무음이 녹음에 거의 안 들어가므로 Whisper 환각(무음 구간 상투구)도 완화
# 종료 판정은 Silero VAD(agent/vad.py)의 프레임별 음성 확률로 한다.
STT_MIN_RECORD_SECONDS = 0.5     # 최소 녹음. 순간 잡음 한 프레임으로 즉시 끊기는 것 방지
STT_MAX_RECORD_SECONDS = 15.0    # 최대 녹음. 소음 환경에서 무한 녹음되는 것 방지(하드 상한)
STT_SILENCE_MS = 800             # 발화 시작 후 이만큼 연속 무음이면 발화 끝으로 판정
STT_START_TIMEOUT_SECONDS = 6.0  # 호출만 하고 이 시간 내 아무 말 없으면 조용히 취소
VAD_THRESHOLD = 0.5              # Silero 음성 확률이 이 값 이상이면 '음성' 프레임으로 간주


# 실행 환경(--environment) 프리셋
#   prod: 운영환경  (cpu STT/TTS, USB 마이크/스피커를 이름으로 탐색)
#   dev : 개발환경  (cpu,         기본 마이크 인덱스 0)
#
# device: STT(faster-whisper)/TTS(VITS) 를 구동할 연산 장치. STT/TTS 는 CPU 가 더
#   적합하다는 판단에 따라 prod 도 cpu 를 사용한다. GPU(cuda) 는 추후 로컬 LLM 전용으로
#   남겨둔다 — LLM 스테이지가 추가되면 그때 별도 device 설정을 둔다.
#
# input_device_name / output_device_name:
#   장치 이름의 부분일치 패턴(대소문자 무시). PyAudio 인덱스는 USB 재연결/부팅 순서에
#   따라 바뀌므로 운영환경은 인덱스 대신 이름으로 장치를 찾는다. 실제 이름은
#   `python main_agent.py --list-devices` 로 확인하고, 코드 수정 없이 바꾸려면
#   .env 의 AUDIO_INPUT_NAME / AUDIO_OUTPUT_NAME 으로 덮어쓴다.
# input_device_index / output_device_index:
#   이름 패턴이 없거나 일치하는 장치를 찾지 못했을 때 쓰는 폴백.
#   None = 시스템 기본 장치(사운드서버)를 사용.
ENVIRONMENTS = {
    "prod": {
        "device": "cpu",
        "input_device_name": "USB",
        "output_device_name": "USB",
        "input_device_index": None,
        "output_device_index": None,
    },
    "dev": {
        "device": "cpu",
        "input_device_name": None,
        "output_device_name": None,
        "input_device_index": 0,
        "output_device_index": None,
    },
}


# ==========================================
# .env 로더
# ==========================================
# API 키 같은 비밀값은 git 에 올리지 않고 프로젝트 루트의 .env 파일로 관리한다.
# python-dotenv 의존성을 추가하지 않기 위해 필요한 최소 문법만 직접 파싱한다.
#   - KEY=VALUE 한 줄에 하나
#   - '#' 로 시작하는 줄과 빈 줄은 무시
#   - 값을 감싼 홑/겹따옴표는 제거
#   - 이미 os.environ 에 있는 값은 덮어쓰지 않는다 (실제 환경변수가 항상 우선)
_env_loaded = False


def load_env_file(path=None):
    """프로젝트 루트의 .env 파일을 읽어 os.environ 에 채운다.

    여러 번 호출해도 실제 파싱은 한 번만 수행한다. 파일이 없으면 조용히 넘어간다
    (개발환경처럼 .env 를 두지 않는 경우가 정상 동작이기 때문).
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True

    env_path = Path(path) if path else PROJECT_ROOT / ".env"
    if not env_path.is_file():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        # 값을 감싼 따옴표 제거 (예: KEY="my value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


class RunConfig(NamedTuple):
    """실행 인자 + 환경 프리셋을 해석한 결과."""
    device: str
    stt_compute_type: str
    input_device_name: str | None
    input_device_index: int | None
    output_device_name: str | None
    output_device_index: int | None
    list_devices: bool


def parse_device_args():
    """실행 인자를 파싱하여 RunConfig 를 반환합니다.

    --environment 로 실행 환경 프리셋을 선택합니다 (기본값: dev)
      prod = 운영환경 (device=cpu, USB 마이크/스피커를 이름으로 탐색)
      dev  = 개발환경 (device=cpu, mic=0)
    --list-devices 를 주면 입출력 장치 목록만 출력하고 종료하도록 신호합니다.
    STT(Faster-Whisper) compute_type: GPU는 float16, CPU는 int8 이 적합합니다.

    장치 이름 패턴은 .env 의 AUDIO_INPUT_NAME / AUDIO_OUTPUT_NAME 으로 덮어쓸 수
    있습니다 (코드 수정 없이 운영환경 장치를 바꾸기 위함).
    """
    parser = argparse.ArgumentParser(description="로컬 오프라인 보이스 에이전트")
    parser.add_argument(
        "--environment",
        choices=list(ENVIRONMENTS.keys()),
        default="dev",
        help="실행 환경 프리셋 (prod=cpu/USB 장치 이름 탐색, dev=cpu/mic0, 기본값: dev)",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="사용 가능한 입력(마이크)/출력(스피커) 장치 목록을 출력하고 종료",
    )
    args = parser.parse_args()

    load_env_file()
    preset = ENVIRONMENTS[args.environment]
    device = preset["device"]
    stt_compute_type = "float16" if device == "cuda" else "int8"
    input_device_name = os.environ.get("AUDIO_INPUT_NAME") or preset["input_device_name"]
    output_device_name = os.environ.get("AUDIO_OUTPUT_NAME") or preset["output_device_name"]

    def describe(name, index):
        if name:
            return f"이름 '{name}'"
        if index is not None:
            return f"인덱스 {index}"
        return "시스템 기본"

    print(f"[System] 실행 환경: {args.environment} "
          f"(device: {device}, "
          f"마이크: {describe(input_device_name, preset['input_device_index'])}, "
          f"스피커: {describe(output_device_name, preset['output_device_index'])}, "
          f"STT compute_type: {stt_compute_type})")

    return RunConfig(
        device=device,
        stt_compute_type=stt_compute_type,
        input_device_name=input_device_name,
        input_device_index=preset["input_device_index"],
        output_device_name=output_device_name,
        output_device_index=preset["output_device_index"],
        list_devices=args.list_devices,
    )
