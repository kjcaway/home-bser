import os
import wave
from math import gcd

import numpy as np
import pyaudio
from scipy.signal import resample_poly

from agent.config import CHUNK, FORMAT, CHANNELS, RATE


class MicStream:
    """장치 네이티브 샘플레이트로 캡처해 16kHz 모노 int16 로 변환해주는 래퍼.

    다수의 하드웨어(hw:*) 마이크는 16000Hz/모노를 직접 지원하지 않아 PyAudio가
    -9997(Invalid sample rate)로 실패한다. 이 클래스는 우선 16kHz/모노로 직접
    열기를 시도하고(pulse/default 등 변환 지원 장치는 성공), 실패하면 장치의
    네이티브 레이트/채널로 열어 read() 할 때마다 16kHz 모노로 소프트웨어 변환한다.

    stream.read/start_stream/stop_stream/close/get_read_available 인터페이스를
    그대로 노출하므로 기존 호출부(main_agent, record_frames, flush)는 수정 불필요.
    """

    def __init__(self, audio, device_index=None):
        self.audio = audio
        self._device_index = device_index

        # 1) 16kHz/모노 직접 시도 (사운드서버 계열 장치는 성공)
        try:
            self._stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                                      input=True, frames_per_buffer=CHUNK,
                                      input_device_index=device_index)
            self.capture_rate = RATE
            self.capture_channels = CHANNELS
            self._needs_convert = False
            return
        except OSError:
            pass  # 하드웨어 장치 → 네이티브 캡처 + 소프트웨어 변환으로 폴백

        # 2) 장치 네이티브 설정으로 열고 변환
        if device_index is not None:
            info = audio.get_device_info_by_index(device_index)
        else:
            info = audio.get_default_input_device_info()
        self.capture_rate = int(info["defaultSampleRate"])
        self.capture_channels = 2 if int(info["maxInputChannels"]) >= 2 else 1

        self._stream = audio.open(format=FORMAT, channels=self.capture_channels,
                                  rate=self.capture_rate, input=True,
                                  frames_per_buffer=self._native_frames(CHUNK),
                                  input_device_index=device_index)
        self._needs_convert = True
        g = gcd(RATE, self.capture_rate)
        self._up = RATE // g
        self._down = self.capture_rate // g
        print(f"[System] 마이크 네이티브 {self.capture_rate}Hz/{self.capture_channels}ch "
              f"→ {RATE}Hz/모노 소프트웨어 변환 사용")

    def _native_frames(self, target_frames):
        """16kHz 기준 target_frames 에 대응하는 네이티브 프레임 수."""
        return int(round(target_frames * self.capture_rate / RATE))

    def read(self, num_frames, exception_on_overflow=False):
        """16kHz 모노 int16 PCM 바이트를 num_frames 개 반환한다."""
        if not self._needs_convert:
            return self._stream.read(num_frames, exception_on_overflow=exception_on_overflow)

        native = self._native_frames(num_frames)
        raw = self._stream.read(native, exception_on_overflow=exception_on_overflow)
        samples = np.frombuffer(raw, dtype=np.int16)

        if self.capture_channels > 1:
            usable = (len(samples) // self.capture_channels) * self.capture_channels
            samples = samples[:usable].reshape(-1, self.capture_channels).mean(axis=1)
        else:
            samples = samples.astype(np.float64)

        converted = resample_poly(samples, self._up, self._down)

        # 정확히 num_frames 길이로 맞춤 (경계 오차 보정)
        if len(converted) >= num_frames:
            converted = converted[:num_frames]
        else:
            converted = np.pad(converted, (0, num_frames - len(converted)))

        return np.clip(np.round(converted), -32768, 32767).astype(np.int16).tobytes()

    def get_read_available(self):
        avail = self._stream.get_read_available()
        if not self._needs_convert:
            return avail
        return int(avail * RATE / self.capture_rate)

    def start_stream(self):
        self._stream.start_stream()

    def stop_stream(self):
        self._stream.stop_stream()

    def close(self):
        self._stream.close()


def list_input_devices(audio):
    """사용 가능한 입력(마이크) 장치 목록을 출력합니다."""
    print("[System] 사용 가능한 입력 장치 목록:")
    found = False
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get("maxInputChannels", 0)) > 0:
            found = True
            print(f"    [{i}] {info['name']} "
                  f"(채널 {int(info['maxInputChannels'])}, "
                  f"기본 {int(info['defaultSampleRate'])}Hz)")
    if not found:
        print("    (입력 가능한 장치를 찾지 못했습니다. 마이크 연결/권한을 확인하세요.)")


def list_output_devices(audio):
    """사용 가능한 출력(스피커) 장치 목록을 출력합니다."""
    print("[System] 사용 가능한 출력 장치 목록:")
    try:
        default_index = audio.get_default_output_device_info().get("index")
    except OSError:
        default_index = None
    found = False
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get("maxOutputChannels", 0)) > 0:
            found = True
            mark = " (기본)" if i == default_index else ""
            print(f"    [{i}] {info['name']} "
                  f"(채널 {int(info['maxOutputChannels'])}, "
                  f"기본 {int(info['defaultSampleRate'])}Hz){mark}")
    if not found:
        print("    (출력 가능한 장치를 찾지 못했습니다. 스피커 연결/권한을 확인하세요.)")


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
        print("   → 실행 환경 프리셋(agent/config.py ENVIRONMENTS)의 "
              "input_device_index 를 위 목록의 장치로 지정해 다시 실행하세요.")
        audio.terminate()
        raise
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


def _convert_pcm16(raw, src_channels, src_rate, dst_channels, dst_rate):
    """16-bit PCM 바이트를 대상 채널/레이트로 변환한다 (다운믹스→리샘플→업믹스)."""
    samples = np.frombuffer(raw, dtype=np.int16)

    # 다중 채널 → 모노 다운믹스
    if src_channels > 1:
        usable = (len(samples) // src_channels) * src_channels
        samples = samples[:usable].reshape(-1, src_channels).mean(axis=1)
    else:
        samples = samples.astype(np.float64)

    # 레이트 변환
    if src_rate != dst_rate:
        g = gcd(dst_rate, src_rate)
        samples = resample_poly(samples, dst_rate // g, src_rate // g)

    samples = np.clip(np.round(samples), -32768, 32767).astype(np.int16)

    # 모노 → 대상 채널 업믹스(복제)
    if dst_channels > 1:
        samples = np.repeat(samples[:, None], dst_channels, axis=1).flatten()

    return samples.tobytes()


def play_wav_file(file_path, output_device_index=None):
    """wav 파일을 스피커로 재생합니다.

    출력 장치가 wav 원본 레이트를 직접 지원하지 않으면(raw hw 장치 등, -9999),
    장치의 네이티브 레이트/채널로 열고 16-bit PCM 을 소프트웨어 변환하여 재생합니다.
    """
    if not os.path.exists(file_path):
        print(f"❌ 재생할 파일을 찾을 수 없습니다: {file_path}")
        return

    print(f"🔊 응답음 재생 중: {file_path}")
    wf = wave.open(file_path, 'rb')
    width = wf.getsampwidth()
    src_channels = wf.getnchannels()
    src_rate = wf.getframerate()
    frames = wf.readframes(wf.getnframes())
    wf.close()

    p = pyaudio.PyAudio()
    fmt = p.get_format_from_width(width)

    # 1) wav 원본 설정으로 직접 열기 시도
    try:
        stream = p.open(format=fmt, channels=src_channels, rate=src_rate, output=True,
                        output_device_index=output_device_index)
        out_channels, out_frames = src_channels, frames
    except OSError:
        # 2) 폴백: 장치 네이티브 레이트/채널로 열고 소프트웨어 변환 (16-bit 만 지원)
        if width != 2:
            print(f"❌ 출력 장치가 {src_rate}Hz 를 지원하지 않고 16-bit 가 아니라 변환 불가")
            p.terminate()
            return
        if output_device_index is not None:
            info = p.get_device_info_by_index(output_device_index)
        else:
            info = p.get_default_output_device_info()
        dst_rate = int(info["defaultSampleRate"])
        dst_channels = 2 if int(info["maxOutputChannels"]) >= 2 else 1
        print(f"[System] 재생 네이티브 변환: {src_rate}Hz/{src_channels}ch "
              f"→ {dst_rate}Hz/{dst_channels}ch")
        out_frames = _convert_pcm16(frames, src_channels, src_rate, dst_channels, dst_rate)
        out_channels = dst_channels
        stream = p.open(format=fmt, channels=dst_channels, rate=dst_rate, output=True,
                        output_device_index=output_device_index)

    # 청크 단위로 기록
    step = 1024 * 2 * out_channels  # frames * bytes_per_sample(2) * channels
    for i in range(0, len(out_frames), step):
        stream.write(out_frames[i:i + step])

    stream.stop_stream()
    stream.close()
    p.terminate()
