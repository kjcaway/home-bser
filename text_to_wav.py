import argparse

from agent.tts import TextToSpeech

# ==========================================
# 1. 실행 인자 파싱
# ==========================================
# --name : 생성할 wav 파일명
# --text : wav 로 변환할 텍스트 (따옴표로 묶어서 전달)
parser = argparse.ArgumentParser(description="텍스트를 wav 파일로 변환하는 스크립트")
parser.add_argument("--name", required=True, help="생성할 wav 파일명 (예: output.wav)")
parser.add_argument("--text", required=True, help='wav 로 변환할 텍스트 (따옴표로 묶어서 전달)')
parser.add_argument(
    "--device",
    choices=["cpu", "cuda"],
    default="cpu",
    help="TTS 모델을 실행할 디바이스 (기본값: cpu)",
)
args = parser.parse_args()

# .wav 확장자가 없으면 자동으로 붙여줍니다.
output_file = args.name if args.name.lower().endswith(".wav") else f"{args.name}.wav"
print(f"[System] 실행 디바이스: {args.device}")

# ==========================================
# 2. TTS 모델 로드 및 wav 파일 생성
# ==========================================
if __name__ == "__main__":
    print("[System] TTS 모델(facebook/mms-tts-kor)을 불러오는 중입니다. 잠시만 기다려주세요...")
    tts = TextToSpeech(args.device)
    print("[System] 모델 로드 완료!")

    print(f"🗣️ 텍스트를 음성으로 변환 중: \"{args.text}\"")
    tts.synthesize_to_file(args.text, output_file)
    print(f"💾 wav 파일 저장 완료: {output_file}")
