import re
import subprocess
import sys


# ==========================================
# 타이머 문맥 패턴 매칭 함수
# ==========================================
def check_timer_intent(sentence: str) -> bool:
    # 1. 공백 제거 및 소문자 변환 (비교를 단순화하기 위함)
    clean_text = sentence.replace(" ", "").lower()

    # 2. 명시적 핵심 키워드 검사
    direct_keywords = ["타이머", "스탑워치", "스톱워치", "초시계", "시계바늘", "카운트다운", "타임업", "타이마", "타이먼지"]
    if any(kw in clean_text for kw in direct_keywords):
        return True

    # 3. 문맥적 추론 패턴: [시간 숫자 + 단위(분/초/시간)] + [동사/행동]
    # 예: "3분뒤에", "10초만" 등의 패턴 감지
    time_unit_pattern = r"(\d+)(분|초|시간)"
    has_time_unit = bool(re.search(time_unit_pattern, clean_text))

    # 타이머/스탑워치와 자주 쓰이는 행동 동사 및 명사
    action_verbs = [
        "재줘", "마춰", "맞춰", "세팅", "셋팅", "알려", "깨워", "울려",
        "측정", "카운트", "스타트", "시작", "돌려", "체크", "남았"
    ]

    # 시간 단위가 존재하고, 관련 행동 동사가 문장에 포함되어 있다면 True로 추론
    if has_time_unit and any(verb in clean_text for verb in action_verbs):
        return True

    # 4. 간접적 표현 추론 (시간 단위가 없더라도 행동 자체가 명확한 경우)
    indirect_expressions = ["시간재", "시간측정", "시간측정", "초읽기"]
    if any(exp in clean_text for exp in indirect_expressions):
        return True

    return False


# ==========================================
# 시간추출 함수
# ==========================================
def extract_time_unit(sentence: str) -> str | None:
    """
    한글 문장에서 분/초 단위를 찾아 '1m' 또는 '30s' 형태로 반환합니다.
    분과 초가 함께 있다면 초 단위로 합산하여 반환합니다.
    """
    # 공백을 제거하고 소문자로 통일하여 분석을 단순화합니다.
    clean_text = sentence.replace(" ", "").lower()

    # 1. [숫자+분 + 숫자+초] 복합 패턴 처리 (예: 1분 30초 -> 90s)
    complex_pattern = r"(\d+)분(\d+)초"
    complex_match = re.search(complex_pattern, clean_text)
    if complex_match:
        minutes = int(complex_match.group(1))
        seconds = int(complex_match.group(2))
        total_seconds = (minutes * 60) + seconds
        return f"{total_seconds}s"

    # 2. [숫자+분] 단독 패턴 처리 (예: 5분 뒤에 -> 5m)
    minute_pattern = r"(\d+)분"
    minute_match = re.search(minute_pattern, clean_text)
    if minute_match:
        return f"{minute_match.group(1)}m"

    # 3. [숫자+초] 단독 패턴 처리 (예: 10초만 -> 10s)
    second_pattern = r"(\d+)초"
    second_match = re.search(second_pattern, clean_text)
    if second_match:
        return f"{second_match.group(1)}s"

    # 4. 영문 혼용 패턴 처리 (예: 5m 세팅해줘 -> 5m)
    eng_pattern = r"(\d+)(m|s)"
    eng_match = re.search(eng_pattern, clean_text)
    if eng_match:
        return f"{eng_match.group(1)}{eng_match.group(2)}"

    # 매칭되는 시간 단위가 없을 경우 None 반환
    return None


# ==========================================
# 시간 인자를 한국어 표현으로 변환하는 함수
# ==========================================
def format_time_korean(time_arg: str) -> str:
    """'10s' / '5m' 형태의 시간 인자를 음성 안내용 한국어 표현으로 변환합니다.

    예: '10s' -> '10초', '5m' -> '5분', '90s' -> '1분 30초'
    """
    value = int(time_arg[:-1])
    unit = time_arg[-1]
    total_seconds = value * 60 if unit == "m" else value

    minutes, seconds = divmod(total_seconds, 60)
    parts = []
    if minutes:
        parts.append(f"{minutes}분")
    if seconds or not parts:
        parts.append(f"{seconds}초")
    return " ".join(parts)


# ==========================================
# 타이머 스크립트 실행 함수
# ==========================================
def run_timer_script(time_arg, output_device_index=None):
    print(f"타이머 스크립트를 호출합니다. 설정 시간: {time_arg}")

    try:
        # sys.executable은 현재 실행 중인 파이썬 인터프리터 경로를 자동으로 가져옵니다. (예: python, python3)
        # subprocess.Popen으로 타이머를 자식 프로세스로 띄우고 종료를 기다리지 않습니다.
        # 이렇게 해야 설정 시간(sleep) 동안 메인 에이전트가 블록되지 않고
        # 곧바로 다음 호출어 대기 루프로 돌아갈 수 있습니다.
        # 출력 장치 인덱스를 함께 넘겨, 알람음이 메인 에이전트와 동일한 스피커로
        # 재생되도록 한다. (운영환경에서 기본 출력으로 빠지면 ALSA/JACK 폴백 노이즈가
        # 발생하고 알람이 실제 스피커로 나가지 않는 문제를 방지)
        cmd = [sys.executable, "timer.py", time_arg]
        if output_device_index is not None:
            cmd += ["--output-device", str(output_device_index)]
        subprocess.Popen(cmd)
        print("타이머 스크립트를 백그라운드로 실행했습니다.")

    except FileNotFoundError:
        print("timer.py 파일을 찾을 수 없습니다. 같은 폴더에 있는지 확인해주세요.")


# ==========================================
# 타이머 스킬 진입점
# ==========================================
def handle(user_sentence: str, tts) -> bool:
    """타이머 스킬 진입점.

    문장이 타이머 요청이면 시간을 추출해 timer.py를 실행하고 True를 반환합니다.
    타이머 요청이 아니면 아무 것도 하지 않고 False를 반환하여
    디스패처가 다음 스킬로 넘어가도록 합니다.

    tts 는 speak(text) 메서드를 가진 TTS 엔진입니다.
    """
    # Step 1: 타이머를 원하는 문장인지 의도 추론
    if not check_timer_intent(user_sentence):
        return False

    # Step 2: 문장에서 시간 매칭 및 단위 변환
    time_argument = extract_time_unit(user_sentence)
    if not time_argument:
        print("-> 타이머 명령인 것 같지만, 정확한 시간을 인식하지 못했습니다. (예: 3분, 10초)")
        tts.speak(f"인지된 음성은 {user_sentence} 입니다. 타이머 명령인 것 같지만, 정확한 시간을 인식하지 못했습니다.")
        return True

    print(f"-> 의도 확인 완료! 추출된 시간 파라미터: {time_argument}")

    # Step 3: 외부 타이머 스크립트 실행
    # 알람음이 메인 에이전트와 같은 스피커로 나가도록 출력 장치 인덱스를 전달한다.
    output_device_index = getattr(tts, "output_device_index", None)
    tts.speak(f"{format_time_korean(time_argument)} 뒤에 알람을 실행합니다.")
    run_timer_script(time_argument, output_device_index)
    return True
