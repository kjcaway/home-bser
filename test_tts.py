import os
import time
import wave
import pyaudio
import numpy as np
import torch
from transformers import VitsModel, AutoTokenizer

# ==========================================
# 1. 설정 및 로컬 모델 로드
# ==========================================
print("[System] 로컬 TTS 모델(Meta MMS-VITS)을 로드하는 중입니다...")
MODEL_NAME = "facebook/mms-tts-kor"

# GPU(CUDA) 가용 여부 확인 후 디바이스 할당
device = "cuda" if torch.cuda.is_available() else "cpu"
print(f"[System] 사용할 하드웨어 디바이스: {device}")

# 토크나이저 및 모델 로드 (최초 실행 시 모델 다운로드로 인해 시간이 다소 걸릴 수 있습니다)
tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
model = VitsModel.from_pretrained(MODEL_NAME).to(device)

OUTPUT_FILENAME = "tts_output.wav"

# ==========================================
# 2. 핵심 기능 함수 구현
# ==========================================
def text_to_speech(text, output_path):
    """텍스트 문자열을 받아 로컬에서 .wav 오디오 파일로 생성하는 함수"""
    # 텍스트 토큰화 및 텐서 변환
    inputs = tokenizer(text, return_tensors="pt").to(device)
    
    # 그래디언트 계산 비활성화 (추론 모드)
    with torch.no_grad():
        output = model(**inputs).waveform
        
    # 오디오 데이터를 CPU 메모리로 이동 후 Numpy 배열로 변환
    audio_data = output.cpu().numpy().squeeze()
    
    # 모델 출력값(float32, -1.0 ~ 1.0)을 오디오 포맷(int16)에 맞게 변환
    audio_data = (audio_data * 32767).astype(np.int16)
    
    # WAV 파일 생성 및 저장
    sample_rate = model.config.sampling_rate
    with wave.open(output_path, "wb") as wav_file:
        wav_file.setnchannels(1)      # 단일 채널 (모노)
        wav_file.setsampwidth(2)      # 16-bit (2 bytes)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(audio_data.tobytes())
        
    print(f"💾 로컬 음성 파일 저장 완료: {output_path}")

def play_audio(file_path):
    """저장된 WAV 오디오 파일을 읽어 스피커로 재생하는 함수"""
    if not os.path.exists(file_path):
        print(f"❌ 재생할 파일을 찾을 수 없습니다: {file_path}")
        return

    print("🔊 스피커로 답변을 출력합니다...")
    wf = wave.open(file_path, 'rb')
    p = pyaudio.PyAudio()
    
    # 오디오 파일의 사양에 맞춰 스트림 개방
    stream = p.open(format=p.get_format_from_width(wf.getsampwidth()),
                    channels=wf.getnchannels(),
                    rate=wf.getframerate(),
                    output=True)
    
    # 데이터를 청크 단위로 읽어 프레임 출력
    chunk_size = 1024
    data = wf.readframes(chunk_size)
    
    while len(data) > 0:
        stream.write(data)
        data = wf.readframes(chunk_size)
        
    # 리소스 해제
    stream.stop_stream()
    stream.close()
    p.terminate()
    wf.close()

# ==========================================
# 3. 테스트 실행부
# ==========================================
if __name__ == "__main__":
    test_text = "안녕하세요. 헤르메스 보이스 에이전트 시스템입니다. 로컬 오프라인 음성 합성 테스트를 시작합니다."
    print(f"\n📝 테스트 문장: \"{test_text}\"")
    
    # 합성 속도 측정을 위한 시간 기록
    start_time = time.time()
    text_to_speech(test_text, OUTPUT_FILENAME)
    end_time = time.time()
    
    print(f"⏱️ 로컬 음성 생성 소요 시간: {end_time - start_time:.2f}초")
    
    # 오디오 출력 장치로 재생
    play_audio(OUTPUT_FILENAME)
    
    print("\n[System] TTS 유닛 테스트가 정상적으로 종료되었습니다.")