# 4K/8K Smoke Readiness Gate — 2026-11-08

## 목적과 설정

이 gate는 4K·8K budget 실행의 재현성 및 계측 완결성을 확인한다. 고정 입력으로 Gemini 요청이 실제 실행됐는지, request-aware CountTokens와 Prompt Audit이 적용됐는지, budget overflow가 있었는지 검증한다. 전략 성능을 추정하는 평가가 아니다.

| 항목 | 설정 |
|---|---|
| Readiness | `PASS` |
| Commit | `d323049505b47dc73fdffaab3188ceb455317c40` (clean) |
| Freeze | 1.0.2 |
| 모델 | Gemini `gemini-3.1-flash-lite` |
| Safety margin | 32 tokens |
| 입력 | `benchmark/generated/main_gemini_3_1_flash_lite.zip`에서 추출한 8개 scenario |
| 규모 | budget별 8 scenarios × 4 strategies × 1 repeat = 32 runs; 전체 64 runs |
| 최대 단계 | 8 |

## Gate 관측 결과

| Context budget | 완료 | 실제 LLM 실행 | 실제 prompt 확인 | Counted/actual overflow | 미완전 비용 계측 | 최대 counted/actual prompt |
|---:|---:|---:|---:|---:|---:|---:|
| 4096 | 32/32 | 32/32 | 61/61 calls | 0/0 | 0 | 2473/2474 tokens |
| 8192 | 32/32 | 32/32 | 61/61 calls | 0/0 | 0 | 2473/2474 tokens |

두 budget 모두 미검증 prompt run 0, positive token delta 최대 1, pipeline status `ok` 32/32였다. 각 전략은 budget별로 8개 run에서 LLM을 실행했다. Embedding revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`로 기록됐다.

## 판정과 한계

완료 수, LLM 호출, prompt 계측, budget overflow, 비용 계측 완결성 기준을 통과해 readiness는 `PASS`다. 이는 해당 실행 경로에서 4K·8K gate를 진행할 운영 준비가 확인됐다는 뜻이다. 이 smoke의 Task success나 전략 간 차이를 연구 성능 결과로 해석하지 않는다.

## 원본 산출물

- `experiments/results/smoke_4k8k/smoke_gate_report.json`
- `experiments/results/smoke_4k8k/budget_4096/`
- `experiments/results/smoke_4k8k/budget_8192/`
