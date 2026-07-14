import sys
import time
import re
import argparse

from agent.audio_io import play_wav_file
from agent.config import TIMER_ALARM_FILE


def main():
    # 인자 파싱: 시간(필수) + 출력 장치 인덱스(선택)
    parser = argparse.ArgumentParser(
        description="타이머 알람 (예: python timer.py 30s)")
    parser.add_argument("time", help="시간[단위] (예: 1m, 30s)")
    parser.add_argument(
        "--output-device", type=int, default=None,
        help="알람음을 재생할 스피커 출력 장치 인덱스 (기본: 시스템 기본 출력). "
             "운영환경에서는 메인 에이전트가 실제 스피커 인덱스를 넘겨줍니다.",
    )
    args = parser.parse_args()

    time_input = args.time.lower()

    # 정규표현식으로 숫자와 단위(m 또는 s) 분리
    match = re.match(r'^(\d+)(m|s)$', time_input)
    if not match:
        print("잘못된 입력 형식입니다. '1m', '30s' 와 같이 입력해주세요.")
        sys.exit(1)

    value = int(match.group(1))
    unit = match.group(2)

    # 초(second) 단위로 변환
    if unit == 'm':
        seconds = value * 60
    else:
        seconds = value

    print(f"[{time_input}] {seconds}초 후에 알람이 울립니다...")

    # 지정된 시간만큼 대기
    time.sleep(seconds)

    print("시간이 되었습니다!")

    # 알람음 2번 재생 (메인 에이전트가 지정한 스피커 인덱스로 재생)
    for _ in range(2):
        play_wav_file(TIMER_ALARM_FILE, args.output_device)


if __name__ == "__main__":
    main()
