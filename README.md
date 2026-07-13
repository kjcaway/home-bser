# Home Bser 
home agent mini-project


# Python venv
```bash
mkdir home-bser
cd home-bser
python3 -m venv .
```

## Python package
```bash
# it must be executed in venv
pip3 install pyaudio numpy openwakeword faster-whisper
pip3 install nvidia-cublas-cu12 nvidia-cudnn-cu12
pip3 install requests
pip3 install torch transformers scipy
pip3 install uroman
```

## How to run
```bash
# it must be executed in venv
# --device 로 STT/TTS 실행 디바이스를 선택합니다 (cpu | cuda, 기본값: cpu)
python main_agent.py                 # 기본 cpu
python main_agent.py --device cuda   # GPU (CUDA) 사용
python main_agent.py --device cpu    # 명시적 cpu 사용
```
- `cuda` 는 NVIDIA GPU + CUDA 환경에서만 동작합니다. CUDA 미가용 상태에서 `--device cuda` 를 주면 에러가 발생합니다.
- 프로그램 로드 시 선택된 디바이스가 로그로 출력됩니다. (예: `[System] 실행 디바이스: cpu (STT compute_type: int8)`)
- STT(Faster-Whisper) compute_type 은 디바이스에 따라 자동 설정됩니다. (cuda: float16, cpu: int8)

### How to make requirements
```
# 로컬 오프라인 보이스 에이전트 의존성 (B안: 핵심 패키지만 정리)
#
# 버전 핀 방법:
#   실제 운영 중인 Ubuntu + CUDA GPU 머신의 venv에서 아래 명령으로 버전을 확인 후,
#   각 패키지 뒤에 ==<버전> 을 채워 넣으세요.
#     source bin/activate
#     pip3 freeze | grep -iE 'pyaudio|numpy|openwakeword|faster-whisper|requests|torch|transformers|scipy|uroman'
#
# 설치:
#   pip3 install -r requirements.txt
#   # CUDA 런타임(nvidia-*)은 GPU 환경에서만 아래 별도 섹션 주석을 해제해 설치하세요.

# --- 오디오 I/O ---
pyaudio
numpy
scipy

# --- Wake Word (호출어 감지) ---
openwakeword

# --- STT (음성 인식, CUDA 가속) ---
faster-whisper

# --- TTS (음성 합성) ---
torch
transformers
uroman

# --- 기타 ---
requests

# =========================================================
# CUDA 런타임 (GPU 전용) — Ubuntu + NVIDIA 환경에서만 필요.
# CPU/macOS 머신에서는 설치하지 마세요. 필요 시 주석 해제.
# =========================================================
# nvidia-cublas-cu12
# nvidia-cudnn-cu12
#
# 참고: torch 를 CUDA 빌드로 설치하려면 버전 태그(예: torch==2.x.x+cu121)를
#       실제 운영 머신의 pip3 freeze 결과에서 그대로 복사해 위 torch 라인에 반영하세요.
```