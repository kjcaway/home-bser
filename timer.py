import sys
import time
import re
import platform

def play_beep():
    """운영체제에 맞는 비프음을 출력합니다."""
    os_name = platform.system()
    if os_name == "Windows":
        import winsound
        # 주파수 1000Hz, 지속시간 500ms
        winsound.Beep(1000, 500)
    else:
        # Mac 또는 Linux 환경 (터미널 벨소리 사용)
        sys.stdout.write('\a')
        sys.stdout.flush()

def main():
    # 인자 개수 확인
    if len(sys.argv) != 2:
        print("사용법: python timer.py [시간][단위] (예: 1m, 30s)")
        sys.exit(1)

    time_input = sys.argv[1].lower()
    
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
    
    # 비프음 5번 출력
    for _ in range(5):
        play_beep()
        time.sleep(0.5) # 비프음 사이의 간격

if __name__ == "__main__":
    main()