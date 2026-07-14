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
pip3 install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.20.*"
pip3 install requests
pip3 install torch transformers scipy
pip3 install uroman
```

> **cuDNN 버전 핀 주의**
> `nvidia-cudnn-cu12` 는 반드시 **torch 가 빌드된 cuDNN 버전과 일치**해야 합니다.
> 버전을 고정하지 않고 최신을 받으면 torch(예: cuDNN 9.20)와 pip 런타임(예: 9.24)이
> 어긋나 TTS(conv1d) 실행 시 `CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH` 로 죽습니다.
> torch 가 요구하는 버전은 아래로 확인하고 핀을 맞추세요.
> ```bash
> # torch 가 빌드된 cuDNN 버전 (예: 92000 = 9.20.0)
> python -c "import torch; print(torch.backends.cudnn.version())"
> # torch 가 의존성으로 요구하는 정확한 핀
> python -c "import importlib.metadata as m; print([r for r in m.requires('torch') if 'cudnn' in r.lower()])"
> ```

## How to run
```bash
# it must be executed in venv
# --environment 로 실행 환경 프리셋을 선택합니다 (dev | prod, 기본값: dev)
python main_agent.py                    # 기본 dev (cpu, mic 0)
python main_agent.py --environment dev  # 개발환경: cpu, mic index 0
python main_agent.py --environment prod # 운영환경: cuda GPU, mic index 1
```
- `--environment` 프리셋은 STT/TTS 실행 디바이스와 마이크 입력 인덱스를 함께 결정합니다.
  - `dev` — `device=cpu`, `input-device=0`
  - `prod` — `device=cuda`, `input-device=1`
- `prod`(cuda) 는 NVIDIA GPU + CUDA 환경에서만 동작합니다. CUDA 미가용 상태에서 `prod` 를 주면 에러가 발생합니다. (자동 CPU 폴백 없음)
- 프로그램 로드 시 선택된 환경이 로그로 출력됩니다. (예: `[System] 실행 환경: ...`)
- STT(Faster-Whisper) compute_type 은 디바이스에 따라 자동 설정됩니다. (cuda: float16, cpu: int8)

## How to run in production (상시 실행)
SSH 연결이 닫혀도 프로세스가 종료되지 않도록 `nohup` 으로 백그라운드 실행합니다.
`SIGHUP` 을 무시하고 실행되며, 출력은 `agent.log` 로 남습니다.

```bash
# it must be executed in venv
cd /Users/jckang/workspace_vscode/home-bser
nohup ./bin/python main_agent.py --environment prod > agent.log 2>&1 &
```
- `res0.wav`(호출어 응답음) 접근을 위해 반드시 프로젝트 루트에서 실행하세요.
- `./bin/python` 을 직접 지정하면 `source bin/activate` 없이 venv 로 동작합니다.

```bash
tail -f agent.log       # 실시간 로그 확인
pgrep -af main_agent.py # 실행 중인 프로세스 확인
pkill -f main_agent.py  # 프로세스 종료
```
- 자동 재시작·부팅 시 자동 시작이 필요하면 `systemd user service` 사용을 권장합니다.

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
# nvidia-cudnn-cu12==9.20.*   # ★ torch 빌드 cuDNN 버전과 반드시 일치시킬 것
#                            #   (불일치 시 TTS 에서 CUDNN_STATUS_SUBLIBRARY_VERSION_MISMATCH)
#
# 참고: torch 를 CUDA 빌드로 설치하려면 버전 태그(예: torch==2.x.x+cu121)를
#       실제 운영 머신의 pip3 freeze 결과에서 그대로 복사해 위 torch 라인에 반영하세요.
```