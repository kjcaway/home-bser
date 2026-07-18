# vLLM 로 Gemma 3 서빙 (운영환경, 8GB VRAM)

운영환경에서 ollama 대신 **vLLM** 으로 로컬 LLM 을 띄우기 위한 docker-compose.
OpenAI 호환 서버라 기존 `agent/skills/hermes_api.py` 가 그대로 붙는다.

## hermes ↔ vLLM 연동

이 vLLM 서버는 **hermes 에이전트(`agent/skills/hermes_api.py`)의 LLM 백엔드**로 쓰인다.
hermes 는 OpenAI 호환 클라이언트라 `base_url` 만 바꾸면 되므로, 프로젝트 루트 `.env` 의
`HERMES_BASE_URL` 을 **이 vLLM 서버(`:8000`)의 custom endpoint** 로 가리키면
기존 ollama/hermes gateway 대신 vLLM 이 응답을 만든다. (설정값은 아래 "agent 연동" 참고.)

> 포트: vLLM 은 컨테이너 **8000 을 호스트 8000 그대로** 노출한다.
> hermes gateway 가 쓰는 8642/8643 과 겹치지 않도록 의도적으로 8000 을 유지한다.

compose 파일은 모델별로 둘 (택일 — 같은 8000 포트를 쓰므로 동시 실행 금지):

| 파일 | 모델 | HF 토큰 | 서빙 이름 |
|------|------|---------|-----------|
| `docker-compose-gemma.yml` | `google/gemma-3-4b-it` | 필요(gated) | `gemma-3-4b-it` |
| `docker-compose-qwen35.yml` | `Qwen/Qwen3-4B` | 불필요 | `qwen3-4b` |

> "Gemma 4" / "Qwen3.5-4B" 는 2026-01 기준 확인되지 않는 태그라, 각 계열 최신 실재 모델
> (Gemma 3 / Qwen3)을 채택했다. 실제 환경에 상위 버전이 있으면 해당 파일 `--model` 만 교체.

## 모델 선택

- 채택: **`google/gemma-3-4b-it`** (Gemma 3 4B instruct)
- "Gemma 4" 는 아직 없음 → Gemma 계열 최신인 Gemma 3 채택.
- 8GB VRAM 대응: bf16 로는 가중치만 ~8.6GB 라 안 들어가므로 **bitsandbytes 4bit** 로 양자화(~3GB).
- 더 가볍게 가려면: `docker-compose.yml` 의 `--model` 을 `google/gemma-3-1b-it` 로 바꾸고
  `--quantization` / `--load-format` 두 줄 삭제 (1B 는 bf16 로도 8GB 에 여유).

## 준비

1. 호스트에 NVIDIA 드라이버 + `nvidia-container-toolkit` 설치 (docker 가 `--gpus` 를 쓰게).
2. HuggingFace 에서 `google/gemma-3-4b-it` 라이선스 동의 → 토큰 발급.
3. `.env` 생성:
   ```bash
   cp .env.example .env
   # HUGGING_FACE_HUB_TOKEN 채우기
   ```

## 실행

```bash
cd dockercompose
# gemma:
docker compose -f docker-compose-gemma.yml up -d
docker compose -f docker-compose-gemma.yml logs -f   # 로딩 진행/에러 확인
# 또는 qwen:
docker compose -f docker-compose-qwen35.yml up -d

curl http://localhost:8000/v1/models   # 준비되면 모델 목록 응답
```

첫 실행은 모델 다운로드 때문에 수 분 걸린다(healthcheck `start_period` 300s).

## agent 연동

프로젝트 루트 `.env` 의 hermes 설정을 이 vLLM 서버로 돌린다 (hermes 의 custom endpoint):

```
HERMES_BASE_URL=http://127.0.0.1:8000/v1
HERMES_API_KEY=dummy          # vLLM 은 인증 불필요하지만 SDK/스위치용으로 아무 값
HERMES_MODEL=gemma-3-4b-it    # 띄운 compose 의 --served-model-name 과 일치 (qwen 이면 qwen3-4b)
HERMES_TIMEOUT=60
```

## 정지

```bash
docker compose -f docker-compose-gemma.yml down    # 띄운 파일과 동일하게
```
