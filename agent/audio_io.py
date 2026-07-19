import os
import wave
from math import gcd

import numpy as np
import pyaudio
from scipy.signal import resample_poly

from agent.config import CHUNK, FORMAT, CHANNELS, RATE


def _supports_format(audio, device_index, channels, rate, fmt, kind="input"):
    """장치가 해당 레이트/채널을 직접 지원하는지 조회한다 (입력/출력 공통).

    Pa_OpenStream 대신 Pa_IsFormatSupported(가벼운 hw params 프로브)만 사용하므로,
    raw hw 장치의 레이트 거부 시 PortAudio 가 stderr 로 쏟아내는 C 레벨 ALSA 경고
    (paInvalidSampleRate / PaAlsaStream_Configure ... failed)가 발생하지 않는다.
    """
    try:
        if kind == "input":
            return audio.is_format_supported(rate, input_device=device_index,
                                             input_channels=channels, input_format=fmt)
        return audio.is_format_supported(rate, output_device=device_index,
                                         output_channels=channels, output_format=fmt)
    except ValueError:
        return False


def _downmix_to_mono(samples, channels):
    """다채널 int16 샘플 배열을 float64 모노로 다운믹스한다 (모노면 dtype 만 변환).

    채널 수의 배수로 딱 떨어지지 않는 꼬리 샘플은 버려 reshape 오류를 막는다.
    """
    if channels > 1:
        usable = (len(samples) // channels) * channels
        return samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float64)


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
        if _supports_format(audio, resolved_index, CHANNELS, RATE, FORMAT, "input"):
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

        samples = _downmix_to_mono(samples, self.capture_channels)
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


def _list_devices(audio, kind):
    """사용 가능한 입력(마이크)/출력(스피커) 장치 목록을 출력한다.

    출력 장치일 때만 시스템 기본 장치에 '(기본)' 표시를 붙인다.
    """
    is_input = kind == "input"
    channel_key = "maxInputChannels" if is_input else "maxOutputChannels"
    label = "입력" if is_input else "출력"
    hint = "마이크" if is_input else "스피커"

    default_index = None
    if not is_input:
        try:
            default_index = audio.get_default_output_device_info().get("index")
        except OSError:
            default_index = None

    print(f"[System] 사용 가능한 {label} 장치 목록:")
    found = False
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get(channel_key, 0)) <= 0:
            continue
        found = True
        mark = " (기본)" if (not is_input and i == default_index) else ""
        print(f"    [{i}] {info['name']} "
              f"(채널 {int(info[channel_key])}, "
              f"기본 {int(info['defaultSampleRate'])}Hz){mark}")
    if not found:
        print(f"    ({label} 가능한 장치를 찾지 못했습니다. {hint} 연결/권한을 확인하세요.)")


def list_input_devices(audio):
    """사용 가능한 입력(마이크) 장치 목록을 출력합니다."""
    _list_devices(audio, "input")


def list_output_devices(audio):
    """사용 가능한 출력(스피커) 장치 목록을 출력합니다."""
    _list_devices(audio, "output")


def find_device_by_name(audio, name_pattern, kind="input"):
    """이름에 name_pattern 이 포함된 첫 번째 입/출력 장치의 인덱스를 반환한다.

    USB 장치의 PyAudio 인덱스는 연결 순서/부팅마다 바뀌므로 인덱스를 고정값으로
    쓸 수 없다. 반면 장치 이름은 하드웨어에 따라오므로 이름으로 찾는다.
    대소문자를 무시한 부분일치이며, 못 찾으면 None 을 반환한다.
    """
    channel_key = "maxInputChannels" if kind == "input" else "maxOutputChannels"
    needle = name_pattern.lower()

    matches = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get(channel_key, 0)) <= 0:
            continue
        if needle in str(info["name"]).lower():
            matches.append((i, info["name"]))

    if not matches:
        return None

    index, name = matches[0]
    label = "입력" if kind == "input" else "출력"
    if len(matches) > 1:
        others = ", ".join(f"[{i}] {n}" for i, n in matches[1:])
        print(f"[System] {label} 장치 이름 '{name_pattern}' 에 여러 장치가 일치합니다 "
              f"(선택: [{index}] {name} / 나머지: {others})")
    else:
        print(f"[System] {label} 장치 이름 '{name_pattern}' → [{index}] {name}")
    return index


def resolve_device_index(audio, name_pattern, fallback_index, kind="input"):
    """이름 패턴으로 장치 인덱스를 해석하고, 실패하면 fallback_index 를 쓴다.

    name_pattern 이 없으면 곧바로 fallback_index 를 반환한다. 패턴이 있는데
    일치하는 장치가 없으면 장치 목록을 출력해 진단을 돕고 fallback 으로 넘어간다.
    """
    if not name_pattern:
        return fallback_index

    index = find_device_by_name(audio, name_pattern, kind)
    if index is not None:
        return index

    label = "입력(마이크)" if kind == "input" else "출력(스피커)"
    print(f"⚠️  {label} 장치 이름 '{name_pattern}' 과 일치하는 장치를 찾지 못했습니다. "
          f"→ {'인덱스 ' + str(fallback_index) if fallback_index is not None else '시스템 기본 장치'} 사용")
    if kind == "input":
        list_input_devices(audio)
    else:
        list_output_devices(audio)
    return fallback_index


def resolve_devices(input_name, input_fallback, output_name, output_fallback):
    """마이크/스피커 인덱스를 이름 기준으로 해석해 (input_index, output_index) 반환.

    스트림을 열기 전에 한 번만 호출하며, 해석용 PyAudio 인스턴스는 즉시 정리한다.
    """
    audio = pyaudio.PyAudio()
    try:
        input_index = resolve_device_index(audio, input_name, input_fallback, "input")
        output_index = resolve_device_index(audio, output_name, output_fallback, "output")
    finally:
        audio.terminate()
    return input_index, output_index


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

    고정 길이 녹음 대신 Silero VAD(agent/vad.SileroVAD)로 매 프레임의 음성/무음을
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
    from agent.vad import WINDOW_SAMPLES

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


def _convert_pcm16(raw, src_channels, src_rate, dst_channels, dst_rate):
    """16-bit PCM 바이트를 대상 채널/레이트로 변환한다 (다운믹스→리샘플→업믹스)."""
    samples = np.frombuffer(raw, dtype=np.int16)

    # 다중 채널 → 모노 다운믹스
    samples = _downmix_to_mono(samples, src_channels)

    # 레이트 변환
    if src_rate != dst_rate:
        g = gcd(dst_rate, src_rate)
        samples = resample_poly(samples, dst_rate // g, src_rate // g)

    samples = np.clip(np.round(samples), -32768, 32767).astype(np.int16)

    # 모노 → 대상 채널 업믹스(복제)
    if dst_channels > 1:
        samples = np.repeat(samples[:, None], dst_channels, axis=1).flatten()

    return samples.tobytes()


def play_wav_file(file_path, output_device_index=None, stop_event=None, loop=False):
    """wav 파일을 스피커로 재생합니다.

    출력 장치가 wav 원본 레이트를 직접 지원하는지 먼저 조회하고, 지원하지 않으면
    (raw hw 장치 등) 16kHz 직접 오픈을 시도하지 않고 곧바로 장치의 네이티브
    레이트/채널로 열어 16-bit PCM 을 소프트웨어 변환하여 재생합니다. 실패하는
    오픈 시도가 없으므로 ALSA 의 paInvalidSampleRate 경고가 뜨지 않습니다.

    stop_event(threading.Event) 를 주면 재생 중 set 되는 즉시 청크 경계에서 멈춥니다.
    loop=True 면 stop_event 가 set 될 때까지 파일을 반복 재생합니다(대기음 용도).
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

    # is_format_supported 는 구체 장치 인덱스가 필요하므로 기본 출력 장치를 해석
    if output_device_index is not None:
        info = p.get_device_info_by_index(output_device_index)
    else:
        info = p.get_default_output_device_info()
    device_index = int(info["index"])

    # 1) 장치가 wav 원본 설정을 직접 지원하면 그대로 오픈 (오픈 시도 없이 미리 조회)
    if _supports_format(p, device_index, src_channels, src_rate, fmt, "output"):
        stream = p.open(format=fmt, channels=src_channels, rate=src_rate, output=True,
                        output_device_index=output_device_index)
        out_channels, out_frames = src_channels, frames
    else:
        # 2) 미지원: 장치 네이티브 레이트/채널로 열고 소프트웨어 변환 (16-bit 만 지원)
        if width != 2:
            print(f"❌ 출력 장치가 {src_rate}Hz 를 지원하지 않고 16-bit 가 아니라 변환 불가")
            p.terminate()
            return
        dst_rate = int(info["defaultSampleRate"])
        dst_channels = 2 if int(info["maxOutputChannels"]) >= 2 else 1
        print(f"[System] 재생 네이티브 변환: {src_rate}Hz/{src_channels}ch "
              f"→ {dst_rate}Hz/{dst_channels}ch")
        out_frames = _convert_pcm16(frames, src_channels, src_rate, dst_channels, dst_rate)
        out_channels = dst_channels
        stream = p.open(format=fmt, channels=dst_channels, rate=dst_rate, output=True,
                        output_device_index=output_device_index)

    # 청크 단위로 기록 (stop_event 가 set 되면 청크 경계에서 즉시 중단, loop 면 반복)
    step = 1024 * 2 * out_channels  # frames * bytes_per_sample(2) * channels
    while True:
        for i in range(0, len(out_frames), step):
            if stop_event is not None and stop_event.is_set():
                break
            stream.write(out_frames[i:i + step])
        if not loop or (stop_event is not None and stop_event.is_set()):
            break

    stream.stop_stream()
    stream.close()
    p.terminate()
