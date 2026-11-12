# Token Budget Preflight — 2026-11-08

## 목적과 설정

1K·2K context budget에서 request token safety margin 0과 32를 비교한 고정 입력 사전 점검이다. 각 설정은 seed 42·43에서 선택한 8개 scenario, 네 전략, 전략별 1회로 실행했다. 총 4개 설정, 128 runs이며 결과는 모두 완료됐다.

| 항목 | 설정 |
|---|---|
| Freeze | 1.0.2 (`experiments/configs/main.json`) |
| Commit | `a3c559931bc2df28597918bd637c90c414c1cdbc` (clean) |
| 모델 | Gemini `gemini-3.1-flash-lite`, temperature 0 |
| Tokenizer | Gemini `countTokens`, request-aware budgeting |
| 시나리오 | seed 42·43의 8개 고정 scenario, 전략별 1회 |
| 전략 | Proposed, Sliding Window, Vector Top-k, Utility-per-Token |
| 비교 조건 | context budget 1024 또는 2048 × safety margin 0 또는 32 |

## Task success

| Context budget | Safety margin | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---:|---:|---:|---:|---:|---:|
| 1024 | 0 | 0/8 | 0/8 | 0/8 | 0/8 |
| 1024 | 32 | 0/8 | 0/8 | 0/8 | 0/8 |
| 2048 | 0 | 8/8 | 5/8 | 5/8 | 6/8 |
| 2048 | 32 | 8/8 | 5/8 | 5/8 | 6/8 |

각 조건에서 constraint violation은 0건이었다. 1024 조건의 Proposed는 8개 모두 budget feasibility 단계에서 중단돼 유효한 agent 수행 결과를 만들지 못했다. 1024 전체에서 나머지 전략도 Task success가 0/8이었다.

## 해석

이 소규모 preflight에서는 safety margin 0과 32 사이에 Task success 차이가 관측되지 않았다. 2048 조건의 Sliding Window 평균 입력 token은 margin 0에서 1859.4, margin 32에서 1847.3으로 달랐고, Vector Top-k와 Utility-per-Token의 평균 입력 token은 조건 간 같았다. 이런 token 사용량 차이만으로 margin의 성능 효과를 주장할 수는 없다.

각 budget·margin 셀은 8개 scenario와 단일 실행만 포함하므로 성공률은 탐색적이다. 이 자료는 고정 freeze 환경에서 낮은 budget의 사전 동작을 확인한 결과이며, Main 분석이나 11월 9일 출력 한도 검증과 합산하지 않는다.

## 원본 산출물

각 폴더에는 `experiment.json`, `progress.json`, `summary.json`, `runs.jsonl`이 있다. 실행은 모두 32/32 완료됐다.

- `experiments/results/preflight_1024_m0/`
- `experiments/results/preflight_1024_m32/`
- `experiments/results/preflight_2048_m0/`
- `experiments/results/preflight_2048_m32/`
