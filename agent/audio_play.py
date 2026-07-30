"""wav 재생: 장치가 원본 레이트를 거부하면 네이티브 설정으로 변환해 재생한다.

출력 경로의 함수만 모았다 (입력은 audio_record.py, 장치 해석은 audio_device.py).
"""

import os
import wave
from math import gcd

import numpy as np
import pyaudio
from scipy.signal import resample_poly

from agent.audio_format import downmix_to_mono, supports_format


def _convert_pcm16(raw, src_channels, src_rate, dst_channels, dst_rate):
    """16-bit PCM 바이트를 대상 채널/레이트로 변환한다 (다운믹스→리샘플→업믹스)."""
    samples = np.frombuffer(raw, dtype=np.int16)

    # 다중 채널 → 모노 다운믹스
    samples = downmix_to_mono(samples, src_channels)

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
    if supports_format(p, device_index, src_channels, src_rate, fmt, "output"):
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
