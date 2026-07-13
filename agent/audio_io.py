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
