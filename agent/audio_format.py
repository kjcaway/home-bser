"""오디오 포맷 조회/변환 헬퍼 (입력·출력 공용).

마이크 캡처(mic_stream.MicStream)와 재생(audio_play.play_wav_file)은 서로 반대
방향이지만 같은 문제를 갖는다: 장치가 원하는 레이트/채널을 지원하지 않으면
네이티브 설정으로 열고 소프트웨어 변환해야 한다. 그 두 경로가 공통으로 쓰는
저수준 함수만 이 모듈에 모았다 — 오디오 계열 모듈 중 아무 것도 import 하지 않는
잎(leaf) 모듈이므로, 양쪽에서 가져다 써도 순환 import 가 생기지 않는다.
"""

import numpy as np


def supports_format(audio, device_index, channels, rate, fmt, kind="input"):
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


def downmix_to_mono(samples, channels):
    """다채널 int16 샘플 배열을 float64 모노로 다운믹스한다 (모노면 dtype 만 변환).

    채널 수의 배수로 딱 떨어지지 않는 꼬리 샘플은 버려 reshape 오류를 막는다.
    """
    if channels > 1:
        usable = (len(samples) // channels) * channels
        return samples[:usable].reshape(-1, channels).mean(axis=1)
    return samples.astype(np.float64)
