"""오디오 장치 탐색/해석 (이름 → PyAudio 인덱스).

USB 마이크/스피커의 PyAudio 인덱스는 부팅·재연결마다 바뀌므로 인덱스를 고정값으로
쓸 수 없다. 이 모듈은 장치 이름 패턴으로 실제 인덱스를 찾아주는 함수들만 담는다
(스트림 열기·녹음은 audio_record.py, 재생은 audio_play.py).
"""

import re

import pyaudio


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


# ALSA 장치 이름 끝에 붙는 '(hw:카드,디바이스)' 번호. 같은 하드웨어라도 부팅/재연결
# 때마다 카드 번호가 바뀌므로(예: "USB PnP Sound Device: Audio (hw:1,0)" →
# "... (hw:2,0)"), 이름 비교에서는 이 꼬리표를 떼고 본다.
_HW_SUFFIX_RE = re.compile(r"\s*\(hw:\d+(?:,\d+)?\)\s*$")


def _normalize_device_name(name):
    """장치 이름 비교용 정규화: 앞뒤 공백 제거 → 끝의 '(hw:N,M)' 제거 → 소문자화."""
    return _HW_SUFFIX_RE.sub("", str(name).strip()).lower()


def find_device_by_name(audio, name_pattern, kind="input"):
    """이름이 name_pattern 으로 시작하는 첫 번째 입/출력 장치의 인덱스를 반환한다.

    USB 장치의 PyAudio 인덱스는 연결 순서/부팅마다 바뀌므로 인덱스를 고정값으로
    쓸 수 없다. 반면 장치 이름은 하드웨어에 따라오므로 이름으로 찾는다.

    비교는 대소문자를 무시한 **접두사(prefix) 일치**다. 완전일치를 요구하면 ALSA 가
    이름 끝에 붙이는 '(hw:N,M)' 번호가 부팅마다 바뀌어 매칭이 깨지기 때문이다
    (그래서 패턴/장치 이름 양쪽 모두 _normalize_device_name 으로 이 꼬리표를 떼고
    비교한다 — .env 에 hw 번호까지 통째로 붙여넣은 값도 매칭된다).

    접두사로 일치하는 장치가 하나도 없을 때만 기존 동작인 부분일치로 폴백한다
    (프리셋 기본값 "USB" 처럼 이름 중간에 들어가는 패턴이 깨지지 않도록).
    못 찾으면 None 을 반환한다.
    """
    channel_key = "maxInputChannels" if kind == "input" else "maxOutputChannels"
    needle = _normalize_device_name(name_pattern)

    prefix_matches = []
    substring_matches = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if int(info.get(channel_key, 0)) <= 0:
            continue
        haystack = _normalize_device_name(info["name"])
        if haystack.startswith(needle):
            prefix_matches.append((i, info["name"]))
        elif needle in haystack:
            substring_matches.append((i, info["name"]))

    matches = prefix_matches or substring_matches
    if not matches:
        return None

    how = "접두사 일치" if prefix_matches else "부분일치 폴백"
    index, name = matches[0]
    label = "입력" if kind == "input" else "출력"
    if len(matches) > 1:
        others = ", ".join(f"[{i}] {n}" for i, n in matches[1:])
        print(f"[System] {label} 장치 이름 '{name_pattern}' 에 여러 장치가 일치합니다 "
              f"({how} / 선택: [{index}] {name} / 나머지: {others})")
    else:
        print(f"[System] {label} 장치 이름 '{name_pattern}' → [{index}] {name} ({how})")
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
