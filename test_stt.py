"""STT(음성→텍스트) 정확도 테스트 스크립트.

main_agent.py 의 파이프라인 중 **STT 단계만** 떼어내 반복 측정한다.
웨이크워드/TTS/스킬은 모두 제외하고, "마이크 녹음 → faster-whisper 변환" 경로만
돌린다. STT 로직은 전적으로 agent 패키지의 것(agent.stt.transcribe_pcm)을 그대로
사용하므로, 여기서 나오는 결과 = 실제 에이전트가 인식하는 결과다.

동작:
  Enter 를 누르면 RECORD_SECONDS 동안 녹음 후 텍스트로 변환해 출력한다.
  정답 문장(참조 텍스트)을 먼저 입력해두면 문자 단위 정확도(CER 기반)를 함께 계산한다.

사용 예:
    python test_stt.py                      # 개발환경(dev) 마이크로 반복 테스트
    python test_stt.py --environment prod   # 운영환경 USB 마이크로 테스트
    python test_stt.py --list-devices       # 입출력 장치 목록만 출력하고 종료

메인 루프에서 Enter=녹음, 'q'+Enter=종료. 녹음 전에 정답 문장을 물어보며,
비워두면 정확도 계산 없이 인식 결과만 출력한다.
"""

import pyaudio

from agent.config import RECORD_SECONDS, parse_device_args
from agent.audio_io import (
    open_input_stream,
    record_frames,
    flush_input_stream,
    list_input_devices,
    list_output_devices,
    resolve_devices,
)
from agent.stt import load_stt_model, transcribe_pcm


def char_error_rate(reference, hypothesis):
    """참조 문장 대비 인식 문장의 문자 단위 편집 거리(CER)를 계산한다.

    Levenshtein 거리를 참조 길이로 나눈 값(0=완벽, 1=전부 틀림). 외부 의존성 없이
    표준 DP 로 구현한다. 공백은 인식 편차가 커 정확도를 왜곡하므로 제거하고 비교한다.
    """
    ref = reference.replace(" ", "")
    hyp = hypothesis.replace(" ", "")
    if not ref:
        return 0.0 if not hyp else 1.0

    prev = list(range(len(hyp) + 1))
    for i, rc in enumerate(ref, start=1):
        cur = [i]
        for j, hc in enumerate(hyp, start=1):
            cost = 0 if rc == hc else 1
            cur.append(min(
                prev[j] + 1,        # 삭제
                cur[j - 1] + 1,     # 삽입
                prev[j - 1] + cost, # 치환
            ))
        prev = cur
    return prev[-1] / len(ref)


def main():
    # ==========================================
    # 1. 환경 설정 (main_agent 과 동일한 인자 파싱 재사용)
    # ==========================================
    cfg = parse_device_args()

    # --list-devices: 장치 목록만 출력하고 종료
    if cfg.list_devices:
        audio = pyaudio.PyAudio()
        list_input_devices(audio)
        list_output_devices(audio)
        audio.terminate()
        return

    # 장치 이름 → 인덱스 해석 (출력 장치는 STT 테스트에 불필요하나 동일 API 로 함께 해석)
    input_device_index, _ = resolve_devices(
        cfg.input_device_name, cfg.input_device_index,
        cfg.output_device_name, cfg.output_device_index,
    )

    # ==========================================
    # 2. STT 모델만 로드 (웨이크워드/TTS 는 로드하지 않음)
    # ==========================================
    print("[System] STT 모델을 불러오는 중입니다...")
    whisper_model = load_stt_model(cfg.device, cfg.stt_compute_type)
    print("[System] STT 모델 로드 완료!")

    # ==========================================
    # 3. 반복 측정 루프
    # ==========================================
    audio, stream = open_input_stream(input_device_index)

    print("\n====================================================")
    print("🎙️  [STT 정확도 테스트]")
    print(f"    Enter=녹음 시작({RECORD_SECONDS}초) / 'q'+Enter=종료")
    print("====================================================\n")

    total = 0
    cer_sum = 0.0
    exact = 0

    try:
        while True:
            reference = input("정답 문장(없으면 Enter, 종료는 q): ").strip()
            if reference.lower() == "q":
                break

            input(f"👉 Enter 를 누르면 {RECORD_SECONDS}초간 녹음합니다...")

            # 녹음 직전 버퍼에 남은 오래된 오디오를 비워 녹음 창을 정렬한다.
            flush_input_stream(stream)

            print("🔴 녹음 중...")
            pcm_bytes = record_frames(stream, RECORD_SECONDS)
            print("🛑 녹음 완료! 변환 중...")

            # 실제 에이전트가 쓰는 STT 함수를 그대로 호출
            user_text = transcribe_pcm(whisper_model, pcm_bytes)

            print(f"\n🗣️  인식 결과: \"{user_text}\"")

            if reference:
                cer = char_error_rate(reference, user_text)
                accuracy = (1.0 - cer) * 100
                is_exact = reference.replace(" ", "") == user_text.replace(" ", "")
                total += 1
                cer_sum += cer
                exact += 1 if is_exact else 0
                print(f"    정답 문장: \"{reference}\"")
                print(f"    정확도: {accuracy:.1f}% (CER {cer:.3f})"
                      f"{'  ✅ 완전일치' if is_exact else ''}")

            print("----------------------------------------------------\n")

    except KeyboardInterrupt:
        print("\n[System] 중단합니다.")
    finally:
        stream.stop_stream()
        stream.close()
        audio.terminate()

    # ==========================================
    # 4. 요약 통계 (정답 문장을 준 케이스만 집계)
    # ==========================================
    if total:
        print("\n==================== 요약 ====================")
        print(f"측정 횟수: {total}")
        print(f"평균 정확도: {(1.0 - cer_sum / total) * 100:.1f}% "
              f"(평균 CER {cer_sum / total:.3f})")
        print(f"완전일치: {exact}/{total} ({exact / total * 100:.1f}%)")
        print("=============================================")


if __name__ == "__main__":
    main()
