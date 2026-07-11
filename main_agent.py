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
MODEL_NAME = "qwen3:14b"     # 사용 중인 Hermes 모델명
TTS_OUTPUT_FILE = "response.wav"

messages_history = [
    {"role": "system", "content": "당신은 음성으로 대화하는 친절하고 간결한 AI 비서 '헤르메스'입니다. 답변을 나중에 음성으로 읽어주어야 하므로, 특수문자나 복잡한 기호는 빼고 자연스러운 구어체로 2~3문장 이내로 짧게 대답해 주세요."}
]

# ==========================================
# 2. 모든 로컬 모델 로드 (Wake Word, STT, TTS)
# ==========================================
print("[System] 모든 로컬 AI 모델을 불러오는 중입니다. 잠시만 기다려주세요...")

# 2-1. Wake Word (알렉사)
pretrained_models = openwakeword.get_pretrained_model_paths()
alexa_path = [path for path in pretrained_models if "alexa" in path.lower()][0]
oww_model = Model(wakeword_model_paths=[alexa_path])

# 2-2. STT (Faster-Whisper) - GPU 모드 기준
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
    
    # 1. 텍스트를 오디오 파일로 변환
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
        
    # 2. 생성된 파일 스피커로 재생
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
print(f"🎙️ [최종 보이스 에이전트 가동] '알렉사'라고 부르고 대화해보세요!")
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
            
            # # LLM 처리
            # messages_history.append({"role": "user", "content": user_text})
            
            # # TTS가 문장을 통째로 읽어야 자연스러우므로, 스트리밍 대신 한 번에 답변을 받습니다.
            # response = ollama.chat(model=MODEL_NAME, messages=messages_history)
            # agent_text = response['message']['content']
            
            # print(f"🤖 헤르메스: {agent_text}")
            # messages_history.append({"role": "assistant", "content": agent_text})
            
            # TTS 출력
            text_to_speech_and_play(f"인지된 음성은 {user_text} 입니다")
            
            print("====================================================")
            print("🎙️ 대기 중...")
            
            oww_model.reset()

except KeyboardInterrupt:
    print("\n[System] 시스템을 종료합니다.")
finally:
    stream.stop_stream()
    stream.close()
    audio.terminate()