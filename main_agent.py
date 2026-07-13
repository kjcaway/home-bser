import numpy as np

from agent.config import (
    CHUNK,
    RECORD_SECONDS,
    WAKE_RESPONSE_FILE,
    parse_device_args,
)
from agent.audio_io import (
    open_input_stream,
    record_frames,
    play_wav_file,
    flush_input_stream,
)
from agent.wakeword import load_wakeword_model, get_score, reset_wakeword_state
from agent.stt import load_stt_model, transcribe_pcm
from agent.tts import TextToSpeech
from agent.skills import timer


# ==========================================
# 스킬 레지스트리
# ==========================================
# 각 스킬은 handle(user_text, tts) -> bool 규약을 따른다.
# 자신이 처리할 명령이면 수행 후 True, 아니면 False 를 반환한다.
# 새 기능 추가 = handle 함수를 작성해 이 리스트에 등록하면 끝. (아래 루프는 그대로)
SKILLS = [
    timer.handle,
]


# ==========================================
# 코어: 사용자 명령 수행 (스킬 디스패처)
# ==========================================
def execute_command(user_text, tts):
    """등록된 스킬을 순회하며 명령을 처리한다.

    처리한 스킬이 있으면 종료하고, 없으면 인식 결과를 그대로 안내한다.
    """
    print(f"\n[사용자 입력]: \"{user_text}\"")

    for skill in SKILLS:
        if skill(user_text, tts):
            return

    # 처리 가능한 스킬이 없을 때의 폴백
    print("-> 처리 가능한 스킬이 없습니다.")
    tts.speak(f"인지된 음성은 {user_text} 입니다")


def main():
    # ==========================================
    # 1. 환경 설정 (--device 인자 파싱)
    # ==========================================
    device, stt_compute_type = parse_device_args()

    # ==========================================
    # 2. 모든 로컬 모델 로드 (Wake Word, STT, TTS)
    # ==========================================
    print("[System] 모든 로컬 AI 모델을 불러오는 중입니다. 잠시만 기다려주세요...")

    oww_model = load_wakeword_model("alexa")
    whisper_model = load_stt_model(device, stt_compute_type)
    tts = TextToSpeech(device)

    print("[System] 모델 로드 완료! 에이전트가 준비되었습니다.")

    # ==========================================
    # 3. 메인 루프 (마이크 스트림 및 파이프라인)
    # ==========================================
    audio, stream = open_input_stream()

    print("\n====================================================")
    print("🎙️ [최종 보이스 에이전트 가동] '알렉사'라고 부르고 대화해보세요!")
    print("====================================================\n")

    try:
        while True:
            pcm_data = stream.read(CHUNK, exception_on_overflow=False)
            audio_data = np.frombuffer(pcm_data, dtype=np.int16)

            score = get_score(oww_model, audio_data)

            if score > 0.5:
                print("\n🔔 [Wake Word 감지!] 👂 듣고 있습니다...")

                # 호출 성공을 사용자에게 알리는 응답음 재생 (녹음 시작 전)
                play_wav_file(WAKE_RESPONSE_FILE)

                # STT 녹음 및 변환
                pcm_bytes = record_frames(stream, RECORD_SECONDS)

                print("🛑 녹음 완료! 생각 중...")

                # STT/명령 처리(TTS·알람 재생 포함) 동안 마이크 입력을 정지하여
                # 스피커 출력이 녹음되어 웨이크워드를 재호출하는 것을 방지
                stream.stop_stream()

                user_text = transcribe_pcm(whisper_model, pcm_bytes)

                if user_text:
                    print(f"👤 사용자: {user_text}")

                    # 코어: 사용자 명령 수행
                    execute_command(user_text, tts)

                print("====================================================")
                print("🎙️ 대기 중...")

                # 마이크 입력 재개 후, 정지 전후로 버퍼에 남아 있던 오디오를 비우고
                # 호출어 모델의 특징 버퍼도 무음으로 초기화 (직전 호출어 재감지 방지)
                stream.start_stream()
                flush_input_stream(stream)
                reset_wakeword_state(oww_model)

    except KeyboardInterrupt:
        print("\n[System] 시스템을 종료합니다.")
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()


if __name__ == "__main__":
    main()
