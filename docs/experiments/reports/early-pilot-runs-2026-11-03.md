# 초기 파일럿 실행 아카이브 — 2026-11-03

## 목적과 범위

11월 3일 전후의 초기 오프라인·Gemini 파일럿 가운데 별도 보고서가 없던 네 실행을 원본 집계 기준으로 정리한다. 이 자료는 당시 파이프라인과 측정 절차를 점검한 탐색 기록이다. 일부 요약에는 평가 지표 간 모순이 있고, 초기 실행에는 현재 기준의 실행 commit·freeze·usage 회계가 없으므로 Main 또는 후속 확증 실험에 합산하지 않는다.

| 실행 디렉터리 | 구성 | Proposed 성공 | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|---:|
| `quant_pilot_v1` | 100 scenario × 전략별 1회 | 100/100 | 75/100 | 1/100 | 0/100 |
| `pilot_offline_20261103_182136` | 8 scenario × 전략별 2회 | 16/16 | 12/16 | 0/16 | 0/16 |
| `pilot_gemini` | 100 scenario × 전략별 1회 | 0/100 | 0/100 | 0/100 | 0/100 |
| `pilot_gemini_v2` | 100 scenario × 전략별 1회 | 100/100 | 75/100 | 1/100 | 0/100 |

성공 수는 각 디렉터리 `summary.json`의 `task_success` 집계(mean × n)를 옮긴 값이다. 신뢰 구간이나 전략 우위 검정으로 읽어서는 안 된다.

## 실행별 관측과 판정

- `quant_pilot_v1`과 `pilot_offline_20261103_182136`에서는 Proposed가 높은 Task success와 동시에 각각 100/100, 16/16의 `selection_failure`를 기록한다. 성공 지표와 실패 진단이 일치하지 않으므로 이 성공률은 해석하지 않는다.
- 첫 Gemini 실행 `pilot_gemini`는 네 전략 모두 0/100이다. Proposed 첫 실행의 기록은 `insufficient_context_budget`, `agent_executed=false`이며, 집계에서도 전 run이 budget failure로 표시된다. 모델의 과제 수행 성능을 측정한 유효한 실행으로 보지 않는다.
- `pilot_gemini_v2`는 Gemini `countTokens`를 사용한 재실행이다. 집계상 Proposed 100/100, Sliding Window 75/100, Vector Top-k 1/100, Utility-per-Token 0/100이었다. 다만 100개 scenario를 각각 한 번만 실행했고, tokenizer metadata의 요청 형태는 `single_user_text`였다. 출력 token과 비용이 집계되지 않았으며(`output_tokens`와 `total_llm_cost`가 null), 실행 metadata에 코드 commit과 freeze 버전이 없다.

## 해석 한계

이 네 파일럿은 설정과 계측 방식이 통일된 실험군이 아니다. 초기 두 실행에는 현재 방식의 모델 usage가 없고, 첫 Gemini 실행은 실행 전 예산 실패로 끝났다. Gemini v2도 한 번의 실행만으로 모델 변동성을 추정할 수 없고 비용 회계가 비어 있다. 따라서 기록된 수치는 탐색 결과 및 재현 단서로만 보존하며, 현행 Main 결과와 직접 비교하거나 일반화하지 않는다.

## 원본 산출물

- `experiments/results/quant_pilot_v1/`
- `experiments/results/pilot_offline_20261103_182136/`
- `experiments/results/pilot_gemini/`
- `experiments/results/pilot_gemini_v2/`

각 디렉터리의 `experiment.json`, `summary.json`, `runs.jsonl`을 원본으로 삼았다. 이 초기 실행들은 현재 기준의 실행 commit·freeze metadata가 없다.
