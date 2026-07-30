"""TTS 입력 텍스트 정규화.

mms-tts-kor 토크나이저의 vocab 은 26개뿐이다 (`abcdeghijklmnoprstuwy` + 따옴표류).
한국어는 uroman 로마자화를 거쳐 이 알파벳으로 들어가지만, 그 밖의 문자는 갈 곳이 없다:

- 아라비아 숫자 -> vocab 에 없어 통째로 무음
- 영문 단어 -> 로마자로 '통과'되지만 모델은 그것을 한국어 로마자로 읽는다
  (`python` -> "프이톤"), 게다가 f/q/v/x/z 는 vocab 에 아예 없어 사라진다
  ("Fox quiz vex" -> "o ui e")

그래서 합성 전에 숫자와 영문을 **한글로 바꿔서** 넣는다. 한글로 바꾸면 나머지
문장과 똑같이 uroman 을 타므로, 모델이 학습한 한국어 로마자 분포 안에 머문다.
"""

import re
import sys

# ---------------------------------------------------------------------------
# 숫자 -> 한글 (한자어 읽기)
# ---------------------------------------------------------------------------

_DIGITS = "영일이삼사오육칠팔구"
_SMALL_UNITS = ["", "십", "백", "천"]       # 4자리 그룹 내부 단위
_GROUP_UNITS = ["", "만", "억", "조"]       # 4자리 그룹 단위


def _read_number(num_str):
    """숫자 문자열을 한자어 한글 읽기로 변환합니다. (예: '30' -> '삼십', '10000' -> '만')"""
    num = int(num_str)
    if num == 0:
        return "영"

    # 4자리씩 그룹으로 나눔 (일의 자리 그룹부터)
    groups = []
    while num > 0:
        groups.append(num % 10000)
        num //= 10000

    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        group = groups[gi]
        if group == 0:
            continue
        piece = ""
        for pos in range(3, -1, -1):
            d = (group // 10 ** pos) % 10
            if d == 0:
                continue
            # '일십', '일백', '일천'은 '십', '백', '천'으로 읽음
            if not (d == 1 and pos > 0):
                piece += _DIGITS[d]
            piece += _SMALL_UNITS[pos]
        # '일만', '일억' 등도 '만', '억'으로 읽음
        if piece == "일" and gi > 0:
            piece = ""
        parts.append(piece + _GROUP_UNITS[gi])
    return " ".join(parts)


_NUMBER = re.compile(r"\d+(?:\.\d+)?")

# 자릿수 구분 쉼표. 세 자리가 정확히 이어질 때만 없앤다 ('1,000' -> '1000').
# '3,4' 같은 나열은 건드리지 않는다.
_THOUSANDS_COMMA = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def _read_number_token(match):
    """정수 또는 소수 하나를 한글 읽기로 바꿉니다. 소수부는 자리마다 낱자로 읽습니다."""
    text = match.group()
    if "." not in text:
        return _read_number(text)
    whole, frac = text.split(".", 1)
    return _read_number(whole) + "점" + "".join(_DIGITS[int(d)] for d in frac)


def normalize_numbers(text):
    """텍스트 안의 아라비아 숫자를 한글 읽기로 치환합니다. (예: '1분 30초' -> '일분 삼십초')"""
    return _NUMBER.sub(_read_number_token, _THOUSANDS_COMMA.sub("", text))


# ---------------------------------------------------------------------------
# 한글 자모 조합
# ---------------------------------------------------------------------------

_CHO = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_JUNG = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ"
_JONG = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ"


def _compose(cho, jung, jong=" "):
    """초성/중성/종성 자모를 완성형 한글 한 글자로 조합합니다."""
    index = (_CHO.index(cho) * 21 + _JUNG.index(jung)) * 28 + _JONG.index(jong)
    return chr(0xAC00 + index)


# ---------------------------------------------------------------------------
# ARPAbet 음소 -> 한글 (외래어 표기법 근사)
# ---------------------------------------------------------------------------

# 자음 음소의 초성 자모. NG 는 초성이 될 수 없어 빠져 있다.
_ONSET = {
    "P": "ㅍ", "B": "ㅂ", "T": "ㅌ", "D": "ㄷ", "K": "ㅋ", "G": "ㄱ",
    "CH": "ㅊ", "JH": "ㅈ", "F": "ㅍ", "V": "ㅂ", "TH": "ㅅ", "DH": "ㄷ",
    "S": "ㅅ", "Z": "ㅈ", "SH": "ㅅ", "ZH": "ㅈ", "HH": "ㅎ",
    "M": "ㅁ", "N": "ㄴ", "L": "ㄹ", "R": "ㄹ",
}

# 뒤에 모음이 없어 홀로 서는 자음의 표기 ('으'/'이'를 붙인 형태).
# R 은 빈 문자열 - 외래어 표기법상 모음 앞이 아닌 [r]은 적지 않는다 (car -> 카).
_STANDALONE = {
    "P": "프", "B": "브", "T": "트", "D": "드", "K": "크", "G": "그",
    "CH": "치", "JH": "지", "F": "프", "V": "브", "TH": "스", "DH": "드",
    "S": "스", "Z": "즈", "SH": "시", "ZH": "지", "HH": "흐",
    "M": "므", "N": "느", "R": "",
}

# 받침으로 쓸 수 있는 자음. P/T/K 는 조건부라 여기 없다 (_stop_as_coda 참고).
_CODA = {"M": "ㅁ", "N": "ㄴ", "NG": "ㅇ", "L": "ㄹ"}

_STOPS = {"P": "ㅂ", "T": "ㅅ", "K": "ㄱ"}

# 어말에서만 받침이 되는 유성 파열음. 규칙대로면 '으'를 붙여야 하지만(클러브) 관용 표기는
# 받침이다 (club -> 클럽, web -> 웹, job -> 잡).
# [b] 만 넣는다. [g]는 받침보다 '그'가 우세하고(tag 태그, bug 버그, egg 에그, flag 플래그),
# [d]도 '드'가 우세하다 (bed 베드).
_FINAL_VOICED_STOPS = {"B": "ㅂ"}

# 짧은 모음. 어말 파열음을 받침으로 적을지 판단하는 기준이다.
# AO([ɔː])는 장모음이라 넣지 않는다 - 넣으면 walk/talk/blog 가 웍/톡/블록이 된다.
_SHORT_VOWELS = {"AA", "AE", "AH", "EH", "IH", "UH"}

# 모음 음소 -> 중성 자모 목록. 원소가 2개면 이중모음이라 두 음절로 적는다.
_VOWEL = {
    "AA": ["ㅏ"], "AE": ["ㅐ"], "AH": ["ㅓ"], "AO": ["ㅗ"],
    "AW": ["ㅏ", "ㅜ"], "AY": ["ㅏ", "ㅣ"], "EH": ["ㅔ"], "ER": ["ㅓ"],
    "EY": ["ㅔ", "ㅣ"], "IH": ["ㅣ"], "IY": ["ㅣ"], "OW": ["ㅗ"],
    "OY": ["ㅗ", "ㅣ"], "UH": ["ㅜ"], "UW": ["ㅜ"],
}

# 활음 [w] 가 앞에 붙은 모음 (wa -> 와, wo -> 워, wi -> 위 ...)
_VOWEL_W = {
    "AA": ["ㅘ"], "AE": ["ㅙ"], "AH": ["ㅝ"], "AO": ["ㅝ"],
    "AW": ["ㅘ", "ㅜ"], "AY": ["ㅘ", "ㅣ"], "EH": ["ㅞ"], "ER": ["ㅝ"],
    "EY": ["ㅞ", "ㅣ"], "IH": ["ㅟ"], "IY": ["ㅟ"], "OW": ["ㅗ"],
    "OY": ["ㅗ", "ㅣ"], "UH": ["ㅜ"], "UW": ["ㅜ"],
}

# 활음 [j] 가 앞에 붙은 모음 (ya -> 야, yu -> 유 ...). SH 도 이 표를 쓴다 (sha -> 샤).
_VOWEL_Y = {
    "AA": ["ㅑ"], "AE": ["ㅒ"], "AH": ["ㅕ"], "AO": ["ㅛ"],
    "AW": ["ㅑ", "ㅜ"], "AY": ["ㅑ", "ㅣ"], "EH": ["ㅖ"], "ER": ["ㅕ"],
    "EY": ["ㅖ", "ㅣ"], "IH": ["ㅣ"], "IY": ["ㅣ"], "OW": ["ㅛ"],
    "OY": ["ㅛ", "ㅣ"], "UH": ["ㅠ"], "UW": ["ㅠ"],
}


def _stop_as_coda(vowel, next_phone, right_after_vowel):
    """무성 파열음 [p]/[t]/[k]을 받침으로 적을지 판단합니다.

    외래어 표기법: 짧은 모음 뒤 어말 무성 파열음은 받침으로 적고(book -> 북),
    짧은 모음과 유음·비음 이외의 자음 사이에서도 받침으로 적는다(act -> 액트).
    그 밖에는 '으'를 붙인다(take -> 테이크, desk -> 데스크).

    `right_after_vowel` 은 그 파열음이 모음 **바로** 뒤인지다. 규칙이 요구하는 조건이며,
    빠뜨리면 desk(D EH S K)의 K 가 앞 음절 '스'에 붙어 '데슥'이 된다.
    """
    if not right_after_vowel or vowel not in _SHORT_VOWELS:
        return False
    if next_phone is None:                       # 어말
        return True
    return next_phone not in ("L", "R", "M", "N", "NG")


def _arpabet_to_hangul(phones):
    """ARPAbet 음소 목록을 한글 표기로 조합합니다."""
    # 음절을 [초성, 중성, 종성] 리스트로 쌓아 두고 마지막에 조합한다.
    # 받침은 나중에 붙을 수 있어서(뒤 자음을 보고 결정) 완성형으로 바로 못 만든다.
    syllables = []
    pending = []          # 아직 모음을 만나지 못한 자음들
    prev_vowel = None     # 직전 모음 (파열음 받침 판단용)

    def flush_coda(phone, next_phone, right_after_vowel):
        """모음을 못 만난 자음 하나를 받침이나 독립 음절로 처리합니다."""
        last = syllables[-1] if syllables else None
        if phone in _CODA and last and last[2] == " ":
            last[2] = _CODA[phone]
            return
        if (phone in _STOPS and last and last[2] == " "
                and _stop_as_coda(prev_vowel, next_phone, right_after_vowel)):
            last[2] = _STOPS[phone]
            return
        if (phone in _FINAL_VOICED_STOPS and last and last[2] == " " and next_phone is None
                and right_after_vowel and prev_vowel in _SHORT_VOWELS):
            last[2] = _FINAL_VOICED_STOPS[phone]
            return
        text = _STANDALONE.get(phone, "")
        if text:
            # '프', '스' 같은 글자를 자모로 되돌려 넣는다 (뒤에 받침이 붙을 수 있으므로).
            code = ord(text) - 0xAC00
            syllables.append([_CHO[code // 588], _JUNG[(code % 588) // 28], " "])

    for i, phone in enumerate(phones):
        next_phone = phones[i + 1] if i + 1 < len(phones) else None

        if phone in _VOWEL:
            # pending 의 마지막 자음이 초성이 되고, 나머지는 받침/독립 음절로 흘려보낸다.
            glide = None
            onset_phone = None
            if pending:
                if pending[-1] in ("W", "Y"):
                    glide = pending.pop()
                if pending:
                    onset_phone = pending[-1]
                    if onset_phone == "SH":       # sh + 모음 -> 샤/셔/시 (y 활음처럼 동작)
                        glide = "Y"
                    pending.pop()

            for j, extra in enumerate(pending):
                flush_coda(extra,
                           pending[j + 1] if j + 1 < len(pending) else onset_phone,
                           j == 0)
            pending = []

            table = _VOWEL_W if glide == "W" else _VOWEL_Y if glide == "Y" else _VOWEL
            jamos = table[phone]

            cho = _ONSET.get(onset_phone, "ㅇ") if onset_phone else "ㅇ"
            # 어중의 [l]이 모음 앞에 오면 'ㄹㄹ'로 적는다 (hello -> 헬로, claude -> 클로드).
            if onset_phone == "L" and syllables and syllables[-1][2] == " ":
                syllables[-1][2] = "ㄹ"

            syllables.append([cho, jamos[0], " "])
            for extra_jamo in jamos[1:]:
                syllables.append(["ㅇ", extra_jamo, " "])
            prev_vowel = phone
        else:
            pending.append(phone)

    for j, extra in enumerate(pending):
        flush_coda(extra, pending[j + 1] if j + 1 < len(pending) else None, j == 0)

    return "".join(_compose(*syl) for syl in syllables)


# ---------------------------------------------------------------------------
# 영단어 -> ARPAbet
# ---------------------------------------------------------------------------

_cmudict_cache = None
_warned_no_cmudict = False


def _lookup_cmudict(word):
    """CMU 발음 사전에서 단어의 첫 번째 발음을 찾습니다. 없으면 None."""
    global _cmudict_cache, _warned_no_cmudict

    if _cmudict_cache is None:
        try:
            import cmudict
            _cmudict_cache = cmudict.dict()
        except ImportError:
            if not _warned_no_cmudict:
                print("[System] cmudict 미설치 - 영문은 철자 규칙으로만 읽습니다. (pip install cmudict)")
                _warned_no_cmudict = True
            _cmudict_cache = {}

    entries = _cmudict_cache.get(word.lower())
    if not entries:
        return None
    # 강세 숫자(AY1 -> AY)를 떼어 낸다. 표기법상 강세는 쓰지 않는다.
    return [re.sub(r"\d", "", p) for p in entries[0]]


# 철자 -> ARPAbet 근사 규칙. 사전에 없는 신조어·고유명사(kubernetes, anthropic 등)용
# 폴백이라 정확할 필요는 없고, 긴 철자열부터 먼저 맞춘다.
_SPELL_RULES = [
    ("tch", ["CH"]), ("sch", ["S", "K"]),
    ("th", ["TH"]), ("sh", ["SH"]), ("ch", ["CH"]), ("ph", ["F"]),
    ("ck", ["K"]), ("qu", ["K", "W"]), ("ng", ["NG"]), ("wh", ["W"]),
    ("oo", ["UW"]), ("ee", ["IY"]), ("ea", ["IY"]), ("ou", ["AW"]),
    ("ow", ["OW"]), ("ai", ["EY"]), ("ay", ["EY"]), ("oa", ["OW"]),
    ("oi", ["OY"]), ("oy", ["OY"]), ("au", ["AO"]), ("aw", ["AO"]),
    ("ue", ["UW"]), ("ui", ["UW"]), ("ei", ["IY"]), ("ie", ["IY"]),
    # r 이 붙은 모음. 자음 앞 [r]은 표기하지 않으므로(car -> 카) 한 덩어리로 잡지 않으면
    # 음절이 통째로 사라진다 (kubernetes -> 쿠베네테스).
    ("er", ["ER"]), ("ir", ["ER"]), ("ur", ["ER"]), ("ar", ["AA", "R"]), ("or", ["AO", "R"]),
    ("a", ["AE"]), ("e", ["EH"]), ("i", ["IH"]), ("o", ["OW"]), ("u", ["UW"]),
    ("b", ["B"]), ("c", ["K"]), ("d", ["D"]), ("f", ["F"]), ("g", ["G"]),
    ("h", ["HH"]), ("j", ["JH"]), ("k", ["K"]), ("l", ["L"]), ("m", ["M"]),
    ("n", ["N"]), ("p", ["P"]), ("q", ["K"]), ("r", ["R"]), ("s", ["S"]),
    ("t", ["T"]), ("v", ["V"]), ("w", ["W"]), ("x", ["K", "S"]),
    ("y", ["IY"]), ("z", ["Z"]),
]


def _spell_to_arpabet(word):
    """발음 사전에 없는 단어를 철자 규칙만으로 ARPAbet 근사값으로 바꿉니다."""
    word = word.lower()
    # 어말 묵음 e (drive, phone ...). 'ee'/'ie' 같은 이중자는 건드리지 않는다.
    if len(word) > 2 and word.endswith("e") and word[-2] not in "aeiou":
        word = word[:-1]

    phones = []
    i = 0
    while i < len(word):
        for spelling, mapped in _SPELL_RULES:
            if word.startswith(spelling, i):
                # 어두의 y 는 모음이 아니라 활음이다 (yes -> 예스).
                if spelling == "y" and i == 0 and len(word) > 1:
                    phones.append("Y")
                else:
                    phones.extend(mapped)
                i += len(spelling)
                break
        else:
            i += 1
    return phones


# ---------------------------------------------------------------------------
# 영문 -> 한글
# ---------------------------------------------------------------------------

# 알파벳 낱자 읽기. 약어(AI, USB)와 사전에 없는 대문자열에 쓴다.
_LETTER_NAMES = {
    "a": "에이", "b": "비", "c": "씨", "d": "디", "e": "이", "f": "에프",
    "g": "지", "h": "에이치", "i": "아이", "j": "제이", "k": "케이", "l": "엘",
    "m": "엠", "n": "엔", "o": "오", "p": "피", "q": "큐", "r": "알",
    "s": "에스", "t": "티", "u": "유", "v": "브이", "w": "더블유", "x": "엑스",
    "y": "와이", "z": "제트",
}

# 규칙으로 뽑은 표기가 관용 표기와 다른 단어들. 규칙 엔진은 외래어 표기법을 따르지만
# 실제로 쓰이는 말이 다른 경우(python -> 파이산 vs 파이썬)를 여기서 덮어쓴다.
_OVERRIDES = {
    "python": "파이썬", "claude": "클로드", "docker": "도커", "sonnet": "소네트",
    "opus": "오퍼스", "haiku": "하이쿠", "anthropic": "앤스로픽",
    "openai": "오픈에이아이", "chatgpt": "챗지피티", "google": "구글",
    "kubernetes": "쿠버네티스", "json": "제이슨", "nasa": "나사",
    "youtube": "유튜브", "netflix": "넷플릭스", "samsung": "삼성",
    "kakao": "카카오", "naver": "네이버", "windows": "윈도우", "linux": "리눅스",
    "ubuntu": "우분투", "wifi": "와이파이", "email": "이메일", "app": "앱",
    "ok": "오케이", "hello": "헬로", "router": "라우터", "com": "컴",
    # 단위. 모음이 없어 그냥 두면 낱자로 읽힌다 ('km' -> '케이엠').
    "km": "킬로미터", "kg": "킬로그램", "cm": "센티미터", "mm": "밀리미터",
    "ml": "밀리리터", "kb": "킬로바이트", "mb": "메가바이트", "gb": "기가바이트",
    "tb": "테라바이트", "mhz": "메가헤르츠", "ghz": "기가헤르츠",
}

# vocab 에 없어 무음이 되는 기호들. 영문 치환보다 **먼저** 돌려야 한다.
# '°C' 를 나중에 처리하면 그 안의 C 를 영문 토큰으로 먼저 집어가 '도씨'가 된다.
# 화씨는 한국어에서 숫자 앞에 오므로(28℉ -> 화씨 28도) 단순 치환이 아니라 자리를 바꾼다.
_SYMBOLS = [
    (re.compile(r"(\d+(?:\.\d+)?)\s*(?:℉|°F)"), r"화씨 \1도"),
    (re.compile(r"℃|°C|°"), "도"),
    (re.compile(r"℉|°F"), "화씨"),
    (re.compile(r"%"), "퍼센트"),
    (re.compile(r"&"), " 앤드 "),
    (re.compile(r"@"), " 골뱅이 "),
    (re.compile(r"(?<=\d)\s*~\s*(?=\d)"), " 에서 "),
]

# 낱자로 읽지 않고 단어로 읽는 대문자 약어. (NASA -> 나사)
_ACRONYM_AS_WORD = {"nasa", "nato", "unesco", "unicef", "ascii", "json", "yaml", "sql"}

# 소문자/숫자 다음의 대문자, 그리고 대문자 연속 뒤에 오는 '대문자+소문자' 경계에서 자른다.
# ChatGPT -> Chat/GPT, OpenAI -> Open/AI, iPhone -> i/Phone
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*")


def _read_letters(word):
    """단어를 알파벳 낱자로 읽습니다. (AI -> 에이아이)"""
    return "".join(_LETTER_NAMES.get(ch, "") for ch in word.lower())


def _word_to_hangul(word):
    """영단어 하나를 한글 표기로 바꿉니다."""
    key = word.lower().replace("'", "")
    if not key:
        return ""

    if key in _OVERRIDES:
        return _OVERRIDES[key]

    # 전부 대문자면 약어로 보고 낱자로 읽는다. 한 글자짜리도 마찬가지 ('C 언어' -> '씨 언어').
    if word.isupper() and key not in _ACRONYM_AS_WORD:
        return _read_letters(key)

    # 모음이 없으면 발음할 수 없는 자음 덩어리다 (mp3 의 'mp' 등).
    if not any(ch in "aeiouy" for ch in key):
        return _read_letters(key)

    phones = _lookup_cmudict(key) or _spell_to_arpabet(key)
    return _arpabet_to_hangul(phones) or _read_letters(key)


def english_to_hangul(token):
    """영문 토큰 하나를 한글로 바꿉니다. 카멜케이스는 쪼개서 각각 처리합니다.

    관용 표기는 **쪼개기 전에** 먼저 본다. YouTube 를 You/Tube 로 나눠 버리면
    'youtube' 항목이 영영 걸리지 않아 '유투브'가 된다.
    """
    if token.lower().replace("'", "") in _OVERRIDES:
        return _word_to_hangul(token)
    return "".join(_word_to_hangul(part) for part in _CAMEL_SPLIT.split(token) if part)


def normalize_english(text):
    """텍스트 안의 영문 단어를 한글 표기로 치환합니다."""
    return _LATIN_TOKEN.sub(lambda m: english_to_hangul(m.group()), text)


def normalize_symbols(text):
    """vocab 에 없는 기호를 한글 읽기로 치환합니다. (예: '85%' -> '85퍼센트')"""
    for pattern, reading in _SYMBOLS:
        text = pattern.sub(reading, text)
    return text


def normalize_for_tts(text):
    """TTS 합성 직전 정규화: 기호 -> 한글, 영문 -> 한글, 숫자 -> 한글 읽기.

    순서가 중요하다. 영문을 숫자보다 먼저 처리해야 'MP3' 같은 토큰이 'MP삼'으로
    갈라지지 않아 카멜케이스 분리가 어긋나지 않는다.
    """
    return normalize_numbers(normalize_english(normalize_symbols(text)))


if __name__ == "__main__":
    # 모델을 띄우지 않고 발음 표기만 확인하는 용도.
    #   python -m agent.text_norm "GPU 8개로 Python 실행"
    for line in sys.argv[1:] or [l.rstrip("\n") for l in sys.stdin]:
        print(f"{line}  ->  {normalize_for_tts(line)}")
