import time

import numpy as np

from agent.config import (
    CHUNK,
    RATE,
    WAKE_RESPONSE_FILE,
    STT_MIN_RECORD_SECONDS,
    STT_MAX_RECORD_SECONDS,
    STT_SILENCE_MS,
    STT_START_TIMEOUT_SECONDS,
    parse_device_args,
)
from agent.audio_io import (
    open_input_stream,
    record_until_silence,
    play_wav_file,
    flush_input_stream,
    list_input_devices,
    list_output_devices,
    resolve_devices,
)
import pyaudio
from agent.wakeword import load_wakeword_model, get_score, reset_wakeword_state
from agent.stt import load_stt_model, transcribe_pcm
from agent.vad import load_vad
from agent.tts import TextToSpeech
from agent.skills import timer, hermes_api


# ==========================================
# 스킬 레지스트리
# ==========================================
# 각 스킬은 handle(user_text, tts) -> bool 규약을 따른다.
# 자신이 처리할 명령이면 수행 후 True, 아니면 False 를 반환한다.
# 새 기능 추가 = handle 함수를 작성해 이 리스트에 등록하면 끝. (아래 루프는 그대로)
#
# 순서가 중요하다: hermes_api 는 문장을 가리지 않고 받는 catch-all 스킬이므로
# 반드시 마지막에 둔다. (.env 에 hermes 설정이 없으면 스스로 False 를 반환해
# 아래 에코 폴백으로 넘어간다)
SKILLS = [
    timer.handle,
    hermes_api.handle,
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
    # 1. 환경 설정 (--environment 인자 파싱)
    # ==========================================
    cfg = parse_device_args()

    # --list-devices: 입출력 장치 목록만 출력하고 종료 (마이크/스피커 이름 확인용)
    if cfg.list_devices:
        audio = pyaudio.PyAudio()
        list_input_devices(audio)
        list_output_devices(audio)
        audio.terminate()
        return

    # 장치 이름 → 인덱스 해석. USB 장치의 PyAudio 인덱스는 연결/부팅마다 바뀌므로
    # 프리셋의 이름 패턴으로 매번 새로 찾는다 (이름이 없으면 프리셋 인덱스 폴백).
    input_device_index, output_device_index = resolve_devices(
        cfg.input_device_name, cfg.input_device_index,
        cfg.output_device_name, cfg.output_device_index,
    )

    # ==========================================
    # 2. 모든 로컬 모델 로드 (Wake Word, STT, TTS)
    # ==========================================
    print("[System] 모든 로컬 AI 모델을 불러오는 중입니다. 잠시만 기다려주세요...")

    oww_model = load_wakeword_model("alexa")
    whisper_model = load_stt_model(cfg.device, cfg.stt_compute_type)
    vad = load_vad()   # 발화 종료 감지(endpointing)용 Silero VAD
    tts = TextToSpeech(cfg.device, output_device_index=output_device_index)

    print("[System] 모델 로드 완료! 에이전트가 준비되었습니다.")

    # ==========================================
    # 3. 메인 루프 (마이크 스트림 및 파이프라인)
    # ==========================================
    audio, stream = open_input_stream(input_device_index)

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
                play_wav_file(WAKE_RESPONSE_FILE, output_device_index)

                # 응답음 재생(블로킹) 동안에도 마이크 스트림은 계속 돌아 링버퍼에 쌓인다.
                # 비우지 않으면 record_frames 가 그 오래된 오디오부터 읽어 녹음 창이
                # 앞으로 밀리고(= 응답음이 녹음되고) 사용자 말끝이 잘린다.
                flush_input_stream(stream)

                # STT 녹음: 고정 길이 대신 VAD 로 발화가 끝날 때까지 동적 녹음.
                # 짧은 명령은 즉시 종료, 긴 명령은 상한(STT_MAX_RECORD_SECONDS)까지 안 잘림.
                _t_rec = time.monotonic()
                pcm_bytes = record_until_silence(
                    stream, vad,
                    min_seconds=STT_MIN_RECORD_SECONDS,
                    max_seconds=STT_MAX_RECORD_SECONDS,
                    silence_ms=STT_SILENCE_MS,
                    start_timeout_seconds=STT_START_TIMEOUT_SECONDS,
                )
                _rec_elapsed = time.monotonic() - _t_rec

                # STT/명령 처리(TTS·알람 재생 포함) 동안 마이크 입력을 정지하여
                # 스피커 출력이 녹음되어 웨이크워드를 재호출하는 것을 방지
                stream.stop_stream()

                if not pcm_bytes:
                    # 호출만 하고 발화가 없었음 → 조용히 대기 상태로 복귀
                    print("🤫 발화가 감지되지 않았습니다. 대기 상태로 돌아갑니다.")
                else:
                    _audio_sec = len(pcm_bytes) / 2 / RATE
                    print(f"🛑 녹음 완료! (오디오 {_audio_sec:.1f}초 / 녹음대기 {_rec_elapsed:.1f}초) 생각 중...")
                    _t_stt = time.monotonic()
                    user_text = transcribe_pcm(whisper_model, pcm_bytes)
                    print(f"[System] STT 전사 소요: {time.monotonic() - _t_stt:.1f}초")

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
