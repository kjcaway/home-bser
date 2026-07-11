import sys
import time
import wave
import re
import torch
from transformers import VitsModel, AutoTokenizer
import pyaudio
import numpy as np 

CHUNK = 1280                 
FORMAT = pyaudio.paInt16     
CHANNELS = 1                 
RATE = 16000                 
RECORD_SECONDS = 5           
MODEL_NAME = "qwen3:14b"     # 사용 중인 Hermes 모델명
TTS_OUTPUT_FILE = "poop.wav"

# 2-3. TTS (MMS-VITS)
tts_device = "cuda" if torch.cuda.is_available() else "cpu"
tts_tokenizer = AutoTokenizer.from_pretrained("facebook/mms-tts-kor")
tts_model = VitsModel.from_pretrained("facebook/mms-tts-kor").to(tts_device)


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

def main():
    # 인자 개수 확인
    if len(sys.argv) != 2:
        print("사용법: python timer.py [시간][단위] (예: 1m, 30s)")
        sys.exit(1)

    time_input = sys.argv[1].lower()
    
    # 정규표현식으로 숫자와 단위(m 또는 s) 분리
    match = re.match(r'^(\d+)(m|s)$', time_input)
    if not match:
        print("잘못된 입력 형식입니다. '1m', '30s' 와 같이 입력해주세요.")
        sys.exit(1)
        
    value = int(match.group(1))
    unit = match.group(2)
    
    # 초(second) 단위로 변환
    if unit == 'm':
        seconds = value * 60
    else:
        seconds = value
        
    print(f"[{time_input}] {seconds}초 후에 알람이 울립니다...")
    
    # 지정된 시간만큼 대기
    time.sleep(seconds)
    
    print("시간이 되었습니다!")
    
    # 비프음 5번 출력
    for _ in range(2):
        text_to_speech_and_play("잇츠 타임투 풒")
        time.sleep(0.5) # 비프음 사이의 간격

if __name__ == "__main__":
    main()