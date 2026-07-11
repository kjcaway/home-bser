import pyaudio
import numpy as np
import openwakeword  # 추가된 부분
from openwakeword.model import Model
from faster_whisper import WhisperModel
import time
import requests

# ==========================================
# 1. 설정 값 초기화
# ==========================================
CHUNK = 1280
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 5

# ==========================================
# 2. 모델 로드 (Wake Word & STT) - 🚨 수정된 부분
# ==========================================
print("[System] 모델을 불러오는 중입니다. 잠시만 기다려주세요...")

# OpenWakeWord 내장 모델 경로 찾기
pretrained_models = openwakeword.get_pretrained_model_paths()
# 여러 내장 모델 중 'alexa'가 포함된 경로를 가져옵니다.
alexa_path = [path for path in pretrained_models if "alexa" in path.lower()][0]

# 변경된 파라미터(wakeword_model_paths)로 모델 로드
oww_model = Model(wakeword_model_paths=[alexa_path])

# Faster-Whisper 로드 (GPU 사용 시 "cuda", CPU 사용 시 "cpu")
whisper_model = WhisperModel("small", device="cuda", compute_type="float16")

print("[System] 모델 로드 완료!")

# ==========================================
# 3. 마이크 스트림 설정
# ==========================================
audio = pyaudio.PyAudio()
stream = audio.open(format=FORMAT,
                    channels=CHANNELS,
                    rate=RATE,
                    input=True,
                    frames_per_buffer=CHUNK)

print("\n====================================================")
print("🎙️  테스트 시작: '알렉사'라고 말해보세요! (종료: Ctrl+C)")
print("====================================================\n")

def send_to_hermes(text: str) -> str:
    resp = requests.post(
        "http://localhost:8642/v1/chat/completions",  # 실제 엔드포인트로 교체
        headers={"Authorization": "Bearer qqqqqqqqqqqqqqqq1"},
        json={"message": text},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json().get("response", "")

try:
    while True:
        pcm_data = stream.read(CHUNK, exception_on_overflow=False)
        audio_data = np.frombuffer(pcm_data, dtype=np.int16)

        # 호출어 감지 수행
        prediction = oww_model.predict(audio_data)

        # 🚨 수정된 부분: 모델 이름(Key)이 경로에 따라 다를 수 있으므로, 딕셔너리의 첫 번째 값(인식 점수)을 바로 가져옵니다.
        score = list(prediction.values())[0]

        if score > 0.5:
            print(f"\n🔔 [Wake Word 감지!] '알렉사'가 호출되었습니다. (인식 점수: {score:.2f})")
            print(f"👂 {RECORD_SECONDS}초 동안 음성을 듣습니다. 말씀해 주세요...")

            frames = []

            for _ in range(0, int(RATE / CHUNK * RECORD_SECONDS)):
                data = stream.read(CHUNK, exception_on_overflow=False)
                frames.append(data)

            print("🛑 녹음 완료! 텍스트로 변환 중...")

            stt_audio_data = b''.join(frames)
            stt_audio_np = np.frombuffer(stt_audio_data, dtype=np.int16).astype(np.float32) / 32768.0

            segments, info = whisper_model.transcribe(stt_audio_np, language="ko", beam_size=5)

            print("\n📝 [인식된 텍스트]:")
            for segment in segments:
                print(f" -> {segment.text}")
                recognized_text = segment.text
                if recognized_text.strip():
                    print("🤖 Hermes 에이전트에게 전달 중...")
                    agent_response = send_to_hermes(recognized_text)
                    print(f"💬 [Hermes 응답]: {agent_response}")

            print("\n====================================================")
            print("🎙️  다시 대기 중... '알렉사'라고 말해보세요.")
            print("====================================================")

            oww_model.reset()

except KeyboardInterrupt:
    print("\n[System] 사용자에 의해 프로그램이 종료되었습니다.")