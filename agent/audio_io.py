import os
import wave
import pyaudio

from agent.config import CHUNK, FORMAT, CHANNELS, RATE


def open_input_stream():
    """마이크 입력 스트림을 열고 (PyAudio 인스턴스, 스트림) 을 반환합니다."""
    audio = pyaudio.PyAudio()
    stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                        input=True, frames_per_buffer=CHUNK)
    return audio, stream


def record_frames(stream, seconds):
    """입력 스트림에서 지정된 초만큼 녹음하여 PCM 바이트를 반환합니다."""
    frames = []
    for _ in range(0, int(RATE / CHUNK * seconds)):
        data = stream.read(CHUNK, exception_on_overflow=False)
        frames.append(data)
    return b''.join(frames)


def flush_input_stream(stream):
    """입력 스트림 버퍼에 쌓인 오래된 오디오를 비웁니다.

    TTS 재생이나 타이머 실행 등으로 루프가 블로킹된 동안 마이크에 녹음된
    소리(스피커 출력 포함)가 버퍼에 남아 웨이크워드를 오인식시키는 것을 방지합니다.
    """
    while stream.get_read_available() >= CHUNK:
        stream.read(CHUNK, exception_on_overflow=False)


def play_wav_file(file_path):
    """wav 파일을 스피커로 재생합니다."""
    if not os.path.exists(file_path):
        print(f"❌ 재생할 파일을 찾을 수 없습니다: {file_path}")
        return

    print(f"🔊 응답음 재생 중: {file_path}")
    wf = wave.open(file_path, 'rb')
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
