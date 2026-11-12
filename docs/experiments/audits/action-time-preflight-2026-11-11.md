# Action-Time Budget Preflight 보고서 (2026-11-11)

## 요약

Freeze 1.0.4의 고정 2K/H100 integration 시나리오 40개를 사용해, 첫 행동 직전의 selector/context 예산 민감도를 확인했다. 각 시나리오에서 mandatory floor와 all-flexible ceiling 사이의 다섯 예산 수준(0%, 25%, 50%, 75%, 100%)을 만들고, 기본 `full`과 `no_budget_aware_packing`을 같은 유효 예산으로 짝지어 평가했다. 총 400개 Context 평가가 모두 정상 완료됐고 필수 기억 누락이나 예산 초과는 없었다.

중간 예산에서는 두 설정의 유연 기억 선택이 일부 달랐다. 선택 집합이 달랐던 시나리오는 50%에서 2/40, 75%에서 10/40이었으며, 평균 유연 기억 회수율은 75%에서 `full` 51.7%, packing 비활성 40.0%였다. 0%와 100%에서는 두 설정이 모두 같았다. 이 audit에는 중요 유연 기억의 정답 라벨이 없고 LLM/Tool 행동도 실행하지 않았으므로, 관측 차이를 기억의 정답성이나 Task 성공률 차이로 해석할 수 없다.

## 목적과 방법

- 입력: `benchmark/generated/ablation_2k_h100_v104/cell_b2048_h100.json`의 40개 고정 integration 시나리오.
- 비교: `full` 대 `no_budget_aware_packing` (token-aware packing만 비활성화).
- 예산: 각 시나리오에서 산출한 mandatory floor와 all-flexible ceiling 사이를 0%, 25%, 50%, 75%, 100%로 나눴다. 명목 예산에는 공통 safety margin 32 tokens를 더했다.
- 실행 범위: 첫 action step 이전의 retrieval/selection 및 Context 구성. Gemini `countTokens`는 사용했지만 LLM generation call은 0회다.
- 규모: 40 scenarios × 5 levels × 2 presets = 400 Context 평가, 즉 200개의 paired 비교.
- 재현 메타데이터: freeze 1.0.4, commit `7572c1900b7aecf2b05a34627fd52517d3a108bf` (clean), 모델 `gemini-3.1-flash-lite`, tokenizer `countTokens`, safety margin 32. Embedder는 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, resolved revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`.

Floor는 시나리오별 1,262–1,325 tokens, ceiling은 1,334–1,390 tokens 범위였다. 따라서 이 실험은 고정된 단일 절대 예산 sweep이 아니라, 각 시나리오의 필수 정보량과 flexible 후보량에 맞춰 정규화한 예산 sweep이다.

## 결과

| Flexible 예산 수준 | Paired cases | 양쪽 정상 | 선택 집합이 다른 cases | Full 유연 기억 회수율 | Packing 비활성 유연 기억 회수율 |
|---:|---:|---:|---:|---:|---:|
| 0% | 40 | 40 | 0 | 0.0% | 0.0% |
| 25% | 40 | 40 | 0 | 1.7% | 1.7% |
| 50% | 40 | 40 | 2 | 15.0% | 11.7% |
| 75% | 40 | 40 | 10 | 51.7% | 40.0% |
| 100% | 40 | 40 | 0 | 100.0% | 100.0% |

두 설정 모두 전체 수준에서 context status가 `ok`였고, mandatory set 보존 및 유효 예산 준수도 400/400 평가에서 확인됐다. 0%에서는 flexible 후보를 담을 추가 여유가 없으며, 100%에서는 reference flexible pool 전체가 들어가 두 설정의 결과가 일치했다. 중간 수준에서만 선택 정책 차이가 나타났고, 차이는 75%에서 가장 자주 관찰됐다.

회수율은 시나리오별 reference flexible pool 중 선택된 비율의 평균이다. critical flexible ID를 라벨링한 시나리오는 0개였으므로, 회수율을 중요 기억 recall 또는 정답률로 부르면 안 된다. 요약표의 paired case 수는 각 예산 수준의 40개 시나리오이며, 다섯 수준을 합쳐 독립 표본 200개로 간주하지 않는다.

## 해석과 한계

이번 결과는 제한된 예산의 중간 구간에서 token-aware packing이 어떤 flexible memory를 보존하는지 선택 결과를 바꿀 수 있음을 보여준다. 하지만 비교된 것은 Context 구성 단계뿐이며 이후 LLM 행동, 도구 실행, Task Success, constraint violation은 측정하지 않았다. 데이터에는 중요한 flexible memory의 정답 라벨도 없다. 따라서 `full`이 더 정확하거나 성능이 더 좋다고 결론낼 근거는 없다.

또한 입력은 40개의 합성 2K/Horizon 100 integration 시나리오이며, 모델 generation이 없고 한 freeze/설정에 한정된다. 다른 예산·데이터 분포·모델로 일반화하거나 실사용 효과를 주장할 수 없다. 후속 검증에서는 의미적으로 중요한 flexible 기억의 사전 라벨을 포함하고, 동일 과제를 대상으로 실제 Agent/Tool 실행까지 연결해 행동 수준 지표를 측정해야 한다.

## 산출물

원본 결과는 `experiments/results/action_time_preflight_v104/`에 있다.

- `validation.json`: freeze, 입력 checksum, tokenizer/embedder metadata, 실행 규모 및 집계 결과.
- `calibration.json`: 시나리오별 floor, ceiling, mandatory/flexible reference 기억.
- `paired_summary.csv`: 수준별 요약.
- `selection_details.csv` 및 `.jsonl`: 시나리오·예산·preset별 선택 결과.

이 디렉터리는 `.gitignore` 규칙으로 추적되지 않을 수 있다. 재현 시 동일한 freeze된 입력과 config를 사용하고, 기존 결과를 덮어쓰지 않는 새 output 경로를 지정한다.
