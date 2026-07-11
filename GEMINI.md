# 로컬 오프라인 보이스 에이전트 구축 프로젝트 (Local Voice Agent Project)

## 1. 프로젝트 목적 (Project Purpose)
본 프로젝트의 목적은 외부 클라우드나 API 의존 없이 **우분투(Ubuntu) 로컬 환경에서 100% 오프라인으로 동작하는 한국어 음성 대화형 AI 비서를 구축**하는 것입니다. 
마이크를 통한 음성 입력부터 스피커를 통한 음성 출력까지의 모든 파이프라인이 로컬 GPU/CPU 자원만으로 처리되며, 빠르고 자연스러운 연속 대화가 가능한 시스템을 목표로 합니다.

## 2. 기술 스택 및 환경 (Tech Stack & Environment)
* **OS:** Ubuntu (Python `venv` 가상환경 기반, PEP 668 준수)
* **Audio I/O:** `pyaudio`, `wave`, `numpy`
* **1. Wake Word (호출어 감지):** `openwakeword` 
* **2. STT (음성 인식):** `faster-whisper` (CUDA GPU 가속 활용)
* **3. LLM (대화/추론):** `ollama` (로컬 대화형 인공지능)
* **4. TTS (음성 합성):** `transformers`, `torch` (`facebook/mms-tts-kor` VitsModel 적용, `uroman` 전처리 포함)

## 3. 시스템 파이프라인 및 동작 단계 (System Pipeline)
이 시스템은 무한 루프 속에서 다음과 같은 4단계 파이프라인으로 순환 동작합니다.

1. **상시 대기 (Wake Word Detection):** 마이크 스트림을 지속적으로 모니터링하며, 특정 호출어(예: "알렉사")의 음향 특징이 임계치(0.5) 이상 감지될 때까지 대기합니다.
2. **청취 및 텍스트 변환 (STT):** 호출어가 감지되면 5초간 사용자 음성을 버퍼에 녹음하고, Faster-Whisper 모델을 통해 텍스트로 변환합니다.
3. **문맥 이해 및 답변 생성 (LLM):** 변환된 사용자 텍스트를 대화 기록(History)에 누적한 뒤 Ollama API에 전달합니다. 시스템 프롬프트에 따라 특수기호가 배제된 짧은 구어체의 텍스트 답변을 생성합니다.
4. **음성 합성 및 재생 (TTS):** 텍스트 답변을 MMS-VITS 한국어 모델을 통해 즉시 `.wav` 오디오 데이터로 렌더링하고, 스피커로 출력합니다. 재생이 완료되면 대화 기록을 유지한 채 다시 1단계(상시 대기)로 돌아갑니다.

## 4. 메인 프로젝트 코드 요약 (`main_agent.py`)
이 파일은 단일 엔트리 포인트(Single Entry Point)로서 모든 로컬 모델을 메모리에 올리고 파이프라인을 관장합니다.

### [코드 구조 요약]
* **초기화부:** 오디오 청크 설정 및 LLM 시스템 프롬프트(대화 문맥 배열) 초기화.
* **모델 로드부:** Wake Word, STT(GPU), TTS(GPU) 모델을 최초 1회 메모리에 적재.
* **TTS 처리부 (`text_to_speech_and_play`):** 텐서(Tensor) 연산으로 텍스트를 파형으로 변환하고 즉시 PyAudio 스트림으로 출력하는 독립 함수.
* **메인 루프부:** `pyaudio` 입력 스트림을 열고 `while True` 구문을 통해 [호출어 감지 -> 녹음 -> STT -> LLM -> TTS]의 과정을 절차적으로 실행.

### [소스 코드]
```python
import os
import time
import wave
import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model
from faster_whisper import WhisperModel
import ollama
import torch
from transformers import VitsModel, AutoTokenizer

# ==========================================
# 1. 환경 설정
# ==========================================
CHUNK = 1280                 
FORMAT = pyaudio.paInt16     
CHANNELS = 1                 
RATE = 16000                 
RECORD_SECONDS = 5           
MODEL_NAME = "qwen3:14b"     # 사용 중인 로컬 LLM 모델명
TTS_OUTPUT_FILE = "response.wav"

messages_history = [
    {"role": "system", "content": "당신은 음성으로 대화하는 친절하고 간결한 AI 비서입니다. 답변을 나중에 음성으로 읽어주어야 하므로, 특수문자나 복잡한 기호는 빼고 자연스러운 구어체로 2~3문장 이내로 짧게 대답해 주세요."}
]

# ==========================================
# 2. 모든 로컬 모델 로드 (Wake Word, STT, TTS)
# ==========================================
print("[System] 모든 로컬 AI 모델을 불러오는 중입니다...")

# 2-1. Wake Word
pretrained_models = openwakeword.get_pretrained_model_paths()
alexa_path = [path for path in pretrained_models if "alexa" in path.lower()][0]
oww_model = Model(wakeword_model_paths=[alexa_path])

# 2-2. STT (GPU)
whisper_model = WhisperModel("small", device="cuda", compute_type="float16")

# 2-3. TTS (MMS-VITS)
tts_device = "cuda" if torch.cuda.is_available() else "cpu"
tts_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-kor")
tts_model = VitsModel.from_pretrained("facebook/mms-tts-kor").to(tts_device)

print("[System] 모델 로드 완료! 에이전트가 준비되었습니다.")

# ==========================================
# 3. TTS 및 재생 함수
# ==========================================
def text_to_speech_and_play(text):
    print("🗣️ 답변을 음성으로 변환 중...")
    
    inputs = tts_tokenizer(text, return_tensors="pt").to(tts_device)
    with torch.no_grad():
        output = tts_model(**inputs).waveform
        
    audio_data = output.cpu().numpy().squeeze()
    audio_data = (audio_data * 32767).astype(np.int16)
    
    with wave.open(TTS_OUTPUT_FILE, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(tts_model.config.sampling_rate)
        wav_file.writeframes(audio_data.tobytes())
        
    print("🔊 스피커 출력 중...")
    wf = wave.open(TTS_OUTPUT_FILE, 'rb')
    p = pyaudio.PyAudio()
    stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True)
    
    data = wf.readframes(1024)
    while len(data) > 0:
        stream.write(data)
        data = wf.readframes(1024)
        
    stream.stop_stream()
    stream.close()
    p.terminate()
    wf.close()

# ==========================================
# 4. 메인 루프 (마이크 스트림 및 파이프라인)
# ==========================================
audio = pyaudio.PyAudio()
stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE, input=True, frames_per_buffer=CHUNK)

print("\n====================================================")
print(f"🎙️ [최종 보이스 에이전트 가동] 호출어를 부르고 대화해보세요!")
print("====================================================\n")

try:
    while True:
        pcm_data = stream.read(CHUNK, exception_on_overflow=False)
        audio_data = np.frombuffer(pcm_data, dtype=np.int16)

        prediction = oww_model.predict(audio_data)
        score = list(prediction.values())[0]
        
        if score > 0.5:
            print(f"\n🔔 [Wake Word 감지!] 👂 듣고 있습니다...")
            
            # STT 녹음 및 변환
            frames = []
            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)
                
            print("🛑 녹음 완료! 생각 중...")
            stt_audio_data = b''.join(frames)
            stt_audio_np = np.frombuffer(stt_audio_data, dtype=np.int16).astype(np.float32) / 32768.0
            segments, _ = whisper_model.transcribe(stt_audio_np, language="ko", beam_size=5)
            user_text = "".join([segment.text for segment in segments]).strip()
            
            if not user_text:
                continue
                
            print(f"👤 사용자: {user_text}")
            
            # LLM 처리
            messages_history.append({"role": "user", "content": user_text})
            
            response = ollama.chat(model=MODEL_NAME, messages=messages_history)
            agent_text = response['message']['content']
            
            print(f"🤖 AI 비서: {agent_text}")
            messages_history.append({"role": "assistant", "content": agent_text})
            
            # TTS 출력
            text_to_speech_and_play(agent_text)
            
            print("====================================================")
            print("🎙️ 대기 중...")
            
            oww_model.reset()

except KeyboardInterrupt:
    print("\n[System] 시스템을 종료합니다.")
finally:
    stream.stop_stream()
    stream.close()
    audio.terminate()