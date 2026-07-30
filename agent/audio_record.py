"""마이크 입력: 스트림 열기 → VAD 동적 녹음 → 버퍼 비우기 → wav 저장.

입력 경로의 함수만 모았다. 장치 이름 해석은 audio_device.py, 재생은 audio_play.py.
"""

import wave

import numpy as np
import pyaudio

from agent.audio_device import list_input_devices
from agent.config import CHUNK, CHANNELS, RATE
from agent.mic_stream import MicStream


def open_input_stream(device_index=None):
    """마이크 입력 스트림을 열고 (PyAudio 인스턴스, 스트림) 을 반환합니다.

    device_index 를 지정하면 해당 PyAudio 입력 장치를 사용합니다. 장치가 16kHz를
    직접 지원하지 않으면 네이티브 레이트로 캡처 후 16kHz로 소프트웨어 변환합니다
    (MicStream). 열기에 실패하면 사용 가능한 입력 장치 목록을 출력하여 진단을 돕습니다.
    """
    audio = pyaudio.PyAudio()
    try:
        stream = MicStream(audio, device_index)
    except OSError as e:
        print(f"❌ 마이크 입력 스트림 열기에 실패했습니다: {e}")
        print(f"   (요청 설정: {RATE}Hz / {CHANNELS}채널 / 16-bit, "
              f"장치 인덱스: {device_index if device_index is not None else '기본'})")
        list_input_devices(audio)
        print("   → 위 목록에서 쓸 장치의 이름을 골라 .env 의 AUDIO_INPUT_NAME "
              "(또는 agent/config.py ENVIRONMENTS 의 input_device_name)에 지정해 다시 실행하세요.")
        audio.terminate()
        raise
    return audio, stream


def save_pcm_wav(path, pcm_bytes, rate=RATE, channels=CHANNELS):
    """16-bit PCM 바이트를 wav 파일로 저장한다 (녹음 디버그용)."""
    wf = wave.open(path, "wb")
    wf.setnchannels(channels)
    wf.setsampwidth(2)   # 16-bit
    wf.setframerate(rate)
    wf.writeframes(pcm_bytes)
    wf.close()


def record_until_silence(stream, vad, min_seconds, max_seconds,
                         silence_ms, start_timeout_seconds):
    """발화가 끝날 때까지 동적으로 녹음하여 16kHz 모노 PCM 바이트를 반환합니다.

    고정 길이 녹음 대신 Silero VAD(agent/silero_vad.SileroVAD)로 매 프레임의 음성/무음을
    판정해 '말이 끝나는 시점'을 감지한다:
      - 발화가 시작되기 전(무음)에 start_timeout_seconds 가 지나면 b'' 반환
        (호출만 하고 말이 없는 경우 → 호출부가 조용히 취소)
      - 발화 시작 후 silence_ms 만큼 연속 무음이면 발화 끝으로 보고 종료
        (단, 총 녹음이 min_seconds 미만이면 순간 잡음으로 간주해 종료하지 않음)
      - 어떤 경우든 max_seconds 를 넘으면 강제 종료(소음 환경 무한 녹음 방지)

    Silero 는 512 샘플(32ms) 고정 창으로 판정하므로, CHUNK(1280) 로 읽은 오디오를
    512 샘플 창 단위로 잘라 넘긴다. 남는 샘플은 다음 read 와 이어 붙인다.
    반환 오디오는 녹음 시작~종료까지의 원본 프레임 전체다(짧은 뒤쪽 무음은
    STT 의 vad_filter 가 추가로 걸러낸다).
    """
    from agent.silero_vad import WINDOW_SAMPLES

    vad.reset()
    frames = []
    leftover = np.empty(0, dtype=np.int16)
    speech_started = False
    silence_run_ms = 0.0
    elapsed_ms = 0.0
    window_ms = WINDOW_SAMPLES / RATE * 1000.0  # 32ms
    min_ms = min_seconds * 1000.0
    max_ms = max_seconds * 1000.0
    start_timeout_ms = start_timeout_seconds * 1000.0

    while True:
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
        samples = np.frombuffer(data, dtype=np.int16)
        leftover = np.concatenate([leftover, samples]) if leftover.size else samples

        # 512 샘플 창 단위로 VAD 판정
        while len(leftover) >= WINDOW_SAMPLES:
            window = leftover[:WINDOW_SAMPLES]
            leftover = leftover[WINDOW_SAMPLES:]
            elapsed_ms += window_ms
            is_speech = vad.is_speech(window)

            if not speech_started:
                if is_speech:
                    speech_started = True
                    silence_run_ms = 0.0
                elif elapsed_ms >= start_timeout_ms:
                    # 호출만 하고 발화 없음 → 취소 신호(빈 바이트)
                    return b''
            else:
                if is_speech:
                    silence_run_ms = 0.0
                else:
                    silence_run_ms += window_ms
                    if silence_run_ms >= silence_ms and elapsed_ms >= min_ms:
                        return b''.join(frames)

        # 하드 상한(창 경계와 무관하게 매 read 마다 확인)
        if elapsed_ms >= max_ms:
            return b''.join(frames)


def flush_input_stream(stream):
    """입력 스트림 버퍼에 쌓인 오래된 오디오를 비웁니다.

    TTS 재생이나 타이머 실행 등으로 루프가 블로킹된 동안 마이크에 녹음된
    소리(스피커 출력 포함)가 버퍼에 남아 웨이크워드를 오인식시키는 것을 방지합니다.
    """
    while stream.get_read_available() >= CHUNK:
        stream.read(CHUNK, exception_on_overflow=False)
