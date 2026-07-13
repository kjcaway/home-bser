import numpy as np

from agent.config import (
    CHUNK,
    RECORD_SECONDS,
    WAKE_RESPONSE_FILE,
    parse_device_args,
)
from agent.audio_io import open_input_stream, record_frames, play_wav_file
from agent.wakeword import load_wakeword_model, get_score
from agent.stt import load_stt_model, transcribe_pcm
from agent.tts import TextToSpeech
from agent.intent import process_user_command


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
                user_text = transcribe_pcm(whisper_model, pcm_bytes)

                if not user_text:
                    continue

                print(f"👤 사용자: {user_text}")

                # 타이머 실행 체크 및 tts
                process_user_command(user_text, tts)

                print("====================================================")
                print("🎙️ 대기 중...")

                oww_model.reset()

    except KeyboardInterrupt:
        print("\n[System] 시스템을 종료합니다.")
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()


if __name__ == "__main__":
    main()
