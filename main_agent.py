import os
import time
import wave
import pyaudio
import numpy as np
import openwakeword
from openwakeword.model import Model
from faster_whisper import WhisperModel
import torch
from transformers import VitsModel, AutoTokenizer
import subprocess
import sys
import re
import argparse

# ==========================================
# 1. 환경 설정
# ==========================================
CHUNK = 1280
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE = 16000
RECORD_SECONDS = 5
TTS_OUTPUT_FILE = "response.wav"

# 실행 인자 파싱: --device 로 cpu / cuda 선택 (기본값: cpu)
parser = argparse.ArgumentParser(description="로컬 오프라인 보이스 에이전트")
parser.add_argument(
    "--device",
    choices=["cpu", "cuda"],
    default="cpu",
    help="STT/TTS 모델을 실행할 디바이스 (기본값: cpu)",
)
args = parser.parse_args()
DEVICE = args.device
# STT(Faster-Whisper) compute_type: GPU는 float16, CPU는 int8 이 적합합니다.
STT_COMPUTE_TYPE = "float16" if DEVICE == "cuda" else "int8"
print(f"[System] 실행 디바이스: {DEVICE} (STT compute_type: {STT_COMPUTE_TYPE})")

# ==========================================
# 2. 모든 로컬 모델 로드 (Wake Word, STT, TTS)
# ==========================================
print("[System] 모든 로컬 AI 모델을 불러오는 중입니다. 잠시만 기다려주세요...")

# 2-1. Wake Word (알렉사)
pretrained_models = openwakeword.get_pretrained_model_paths()
alexa_path = [path for path in pretrained_models if "alexa" in path.lower()][0]
oww_model = Model(wakeword_model_paths=[alexa_path])

# 2-2. STT (Faster-Whisper)
whisper_model = WhisperModel("small", device=DEVICE, compute_type=STT_COMPUTE_TYPE)

# 2-3. TTS (MMS-VITS)
tts_device = DEVICE
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
# 타이머 재생 함수
# ==========================================
def run_timer_script(time_arg):
    print(f"타이머 스크립트를 호출합니다. 설정 시간: {time_arg}")
    
    try:
        # sys.executable은 현재 실행 중인 파이썬 인터프리터 경로를 자동으로 가져옵니다. (예: python, python3)
        # subprocess.run을 통해 외부 스크립트를 실행하고 종료될 때까지 기다립니다.
        result = subprocess.run(
            [sys.executable, "timer.py", time_arg],
            check=True
        )
        print("타이머 스크립트가 성공적으로 종료되었습니다.")
        
    except subprocess.CalledProcessError as e:
        print(f"스크립트 실행 중 오류가 발생했습니다. 종료 코드: {e.returncode}")
    except FileNotFoundError:
        print("timer.py 파일을 찾을 수 없습니다. 같은 폴더에 있는지 확인해주세요.")

# ==========================================
# 타이머 문맥 패턴 매칭 함수
# ==========================================
def check_timer_intent(sentence: str) -> bool:
    # 1. 공백 제거 및 소문자 변환 (비교를 단순화하기 위함)
    clean_text = sentence.replace(" ", "").lower()
    
    # 2. 명시적 핵심 키워드 검사
    direct_keywords = ["타이머", "스탑워치", "스톱워치", "초시계", "시계바늘", "카운트다운", "타임업", "타이마", "타이먼지"]
    if any(kw in clean_text for kw in direct_keywords):
        return True
        
    # 3. 문맥적 추론 패턴: [시간 숫자 + 단위(분/초/시간)] + [동사/행동]
    # 예: "3분뒤에", "10초만" 등의 패턴 감지
    time_unit_pattern = r"(\d+)(분|초|시간)"
    has_time_unit = bool(re.search(time_unit_pattern, clean_text))
    
    # 타이머/스탑워치와 자주 쓰이는 행동 동사 및 명사
    action_verbs = [
        "재줘", "마춰", "맞춰", "세팅", "셋팅", "알려", "깨워", "울려", 
        "측정", "카운트", "스타트", "시작", "돌려", "체크", "남았"
    ]
    
    # 시간 단위가 존재하고, 관련 행동 동사가 문장에 포함되어 있다면 True로 추론
    if has_time_unit and any(verb in clean_text for verb in action_verbs):
        return True
        
    # 4. 간접적 표현 추론 (시간 단위가 없더라도 행동 자체가 명확한 경우)
    indirect_expressions = ["시간재", "시간측정", "시간측정", "초읽기"]
    if any(exp in clean_text for exp in indirect_expressions):
        return True

    return False

# ==========================================
# 시간추출 함수
# ==========================================
def extract_time_unit(sentence: str) -> str | None:
    """
    한글 문장에서 분/초 단위를 찾아 '1m' 또는 '30s' 형태로 반환합니다.
    분과 초가 함께 있다면 초 단위로 합산하여 반환합니다.
    """
    # 공백을 제거하고 소문자로 통일하여 분석을 단순화합니다.
    clean_text = sentence.replace(" ", "").lower()
    
    # 1. [숫자+분 + 숫자+초] 복합 패턴 처리 (예: 1분 30초 -> 90s)
    complex_pattern = r"(\d+)분(\d+)초"
    complex_match = re.search(complex_pattern, clean_text)
    if complex_match:
        minutes = int(complex_match.group(1))
        seconds = int(complex_match.group(2))
        total_seconds = (minutes * 60) + seconds
        return f"{total_seconds}s"
        
    # 2. [숫자+분] 단독 패턴 처리 (예: 5분 뒤에 -> 5m)
    minute_pattern = r"(\d+)분"
    minute_match = re.search(minute_pattern, clean_text)
    if minute_match:
        return f"{minute_match.group(1)}m"
        
    # 3. [숫자+초] 단독 패턴 처리 (예: 10초만 -> 10s)
    second_pattern = r"(\d+)초"
    second_match = re.search(second_pattern, clean_text)
    if second_match:
        return f"{second_match.group(1)}s"
        
    # 4. 영문 혼용 패턴 처리 (예: 5m 세팅해줘 -> 5m)
    eng_pattern = r"(\d+)(m|s)"
    eng_match = re.search(eng_pattern, clean_text)
    if eng_match:
        return f"{eng_match.group(1)}{eng_match.group(2)}"
        
    # 매칭되는 시간 단위가 없을 경우 None 반환
    return None

# ==========================================
# 문장으로부터 타이머 요청 여부를 파악한 뒤, 시간을 추출하고 timer.py를 실행하는 함수
# ==========================================
def process_user_command(user_sentence: str):
    print(f"\n[사용자 입력]: \"{user_sentence}\"")
    
    # Step 1: 타이머를 원하는 문장인지 의도 추론
    if not check_timer_intent(user_sentence):
        print("-> 타이머/스탑워치 관련 명령이 아닙니다.")
        text_to_speech_and_play(f"인지된 음성은 {user_sentence} 입니다")
        return

    # Step 2: 문장에서 시간 매칭 및 단위 변환
    time_argument = extract_time_unit(user_sentence)
    if not time_argument:
        print("-> 타이머 명령인 것 같지만, 정확한 시간을 인식하지 못했습니다. (예: 3분, 10초)")
        text_to_speech_and_play(f"인지된 음성은 {user_sentence} 입니다. 타이머 명령인 것 같지만, 정확한 시간을 인식하지 못했습니다.")
        return
        
    print(f"-> 의도 확인 완료! 추출된 시간 파라미터: {time_argument}")
    
    # Step 3: 외부 타이머 스크립트 실행
    try:
        text_to_speech_and_play(f"{time_argument} 뒤에 알람을 실행합니다.")
        subprocess.run([sys.executable, "timer.py", time_argument], check=True)
    except Exception as e:
        print(f"스크립트 실행 실패: {e}")

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

            # 타이머 실행 체크 및 tts
            process_user_command(user_text)

            print("====================================================")
            print("🎙️ 대기 중...")
            
            oww_model.reset()

except KeyboardInterrupt:
    print("\n[System] 시스템을 종료합니다.")
finally:
    stream.stop_stream()
    stream.close()
    audio.terminate()