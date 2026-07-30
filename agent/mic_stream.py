"""장치 네이티브 레이트로 캡처해 16kHz 모노로 변환해주는 마이크 스트림 래퍼.

스트림을 여는 진입점은 audio_record.open_input_stream() 이며, 이 모듈은 그 안에서
쓰이는 MicStream 클래스만 담는다.
"""

from math import gcd

import numpy as np
from scipy.signal import resample_poly

from agent.audio_format import downmix_to_mono, supports_format
from agent.config import CHUNK, FORMAT, CHANNELS, RATE


class MicStream:
    """장치 네이티브 샘플레이트로 캡처해 16kHz 모노 int16 로 변환해주는 래퍼.

    다수의 하드웨어(hw:*) 마이크는 16000Hz/모노를 직접 지원하지 않아 PyAudio가
    -9997(Invalid sample rate)로 실패한다. 이 클래스는 우선 16kHz/모노로 직접
    열기를 시도하고(pulse/default 등 변환 지원 장치는 성공), 실패하면 장치의
    네이티브 레이트/채널로 열어 read() 할 때마다 16kHz 모노로 소프트웨어 변환한다.

    stream.read/start_stream/stop_stream/close/get_read_available 인터페이스를
    그대로 노출하므로 기존 호출부(main_agent, record_until_silence, flush)는 수정 불필요.
    """

    def __init__(self, audio, device_index=None):
        self.audio = audio

        # is_format_supported 는 구체 장치 인덱스가 필요하므로 기본 입력 장치를 해석
        if device_index is not None:
            info = audio.get_device_info_by_index(device_index)
        else:
            info = audio.get_default_input_device_info()
        resolved_index = int(info["index"])

        # 1) 장치가 16kHz/모노를 직접 지원하면 그대로 오픈 (오픈 시도 없이 미리 조회)
        if supports_format(audio, resolved_index, CHANNELS, RATE, FORMAT, "input"):
            self._stream = audio.open(format=FORMAT, channels=CHANNELS, rate=RATE,
                                      input=True, frames_per_buffer=CHUNK,
                                      input_device_index=device_index)
            self.capture_rate = RATE
            self.capture_channels = CHANNELS
            self._needs_convert = False
            return

        # 2) 미지원: 장치 네이티브 설정으로 열고 변환
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

        samples = downmix_to_mono(samples, self.capture_channels)
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
