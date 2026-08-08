"""배치(정기 실행) 잡 모음. 모듈 하나가 잡 하나다.

**저장소 루트에서** `python -m batch.<잡이름>` 으로 실행한다 — 파일을 직접 부르면
`sys.path[0]` 이 `batch/` 가 되어 `import agent` 가 깨지고, 상대경로 설정도 어긋난다.
설정은 루트 `.env` 가 아니라 `batch/.env` 를 읽는다 (`batch/config.py`).
"""
