# 초기 Runner·Smoke 실행 점검 아카이브 — 2026-11-15

이 문서는 기존 보고서에서 직접 연결하지 않던 초기 Runner 점검과 2026-11-08·11-11의 2K Gemini smoke 실행을 정리한다. 이는 pipeline 연결·계측을 확인한 소규모 점검이다. 동일한 5개 smoke 입력을 반복했으므로 각 실행은 별도 기록으로 보존하며, 성능 추정이나 독립 표본 합산에는 사용하지 않는다. 11월 12일 세 차례 smoke와 4K/8K readiness는 각각 [후속 Pipeline Smoke 점검](pipeline-smoke-2026-11-12.md)과 [4K/8K Readiness Gate](smoke-4k8k-readiness-2026-11-08.md)에 별도로 기록되어 있다.

## 초기 개발 Runner 결과

| 결과 경로 | 범위 | 관측된 Task success | 판정 |
|---|---|---|---|
| `experiments/results/quant_pilot_sanity/` | provisional counter, 1 scenario × 전략별 1회 | Proposed 1/1, Sliding Window 1/1, Vector Top-k 0/1, Utility-per-Token 0/1 | Proposed 집계가 Task success 1/1과 `selection_failure` 1/1을 동시에 기록한다. 단일 사례이며 집계 모순이 있어 성능 결과로 쓰지 않는다. |
| `experiments/results/pipeline_adapter_check/` | 1 scenario, Sliding Window·Vector Top-k·Utility-per-Token 각 1회 | 각 1/1 | `experiment.json`이 없는 단일 adapter 확인 자료다. |
| `experiments/results/runner_integration/` | provisional counter, S01–S39 fixture 중 27개 × 전략별 1회 | Proposed 18/27, 나머지 세 전략 각 12/27 | 초기 단일 pass다. freeze·commit provenance와 반복이 없어 연구 비교에 포함하지 않는다. |
| `experiments/results/runner_smoke/` | 같은 27개 fixture, Sliding Window 1회씩 | 0/27 | Runner 경로 확인 자료다. 기준 config·commit metadata가 없으며 일반 전략 성능으로 해석하지 않는다. |

이 네 자료는 원시 summary와 run trace를 보존하지만 표본·계측·실행 설정이 Main 실험과 다르다. 특히 `quant_pilot_sanity`의 success/selection 진단 충돌은 하나의 지표로 정리하지 않았다.

## 2K Gemini smoke 재실행

각 완료 실행은 동일한 seed 42의 temporal update 3개와 approval 2개를 네 전략으로 한 번씩 실행했다(20 runs). Context budget은 2,048, 최대 단계는 8이다. 실행 metadata는 Agent 실행을 기록하고 token counter mode는 `gemini-3.1-flash-lite`로 기록하지만, generation model ID 자체는 metadata에 없다. 각 전략의 5개 run 중 제약 평가 대상 두 사례에서는 constraint violation이 기록되지 않았다.

| 실행 경로 | 실행 commit 상태 | Proposed | Sliding Window | Vector Top-k | Utility-per-Token | 완료 |
|---|---|---:|---:|---:|---:|---:|
| `smoke_backup_20261108_220312` | `6f58e8e`, dirty | 5/5 | 4/5 | 4/5 | 5/5 | 20/20 |
| `smoke_20261108_220754_374556` | `6f58e8e`, dirty | 5/5 | 4/5 | 4/5 | 5/5 | 20/20 |
| `smoke_20261111_001931_975502` | `3adad2f`, clean | 5/5 | 4/5 | 5/5 | 5/5 | 20/20 |
| `smoke_20261111_190330_487355` | `26251a4`, clean | 5/5 | 4/5 | 5/5 | 5/5 | 20/20 |
| `smoke_20261111_230854_751682` | `7626fba`, clean | 5/5 | 4/5 | 5/5 | 5/5 | 20/20 |

이 표는 같은 smoke 입력에 대한 별도 execution snapshot이다. 다섯 실행의 100 run을 100개 독립 scenario로 합산하지 않는다. Vector Top-k는 11월 8일 두 실행에서 4/5, 11월 11일 세 실행에서 5/5였지만, 각기 다른 시점의 코드 snapshot과 단일 실행 결과라 차이의 원인을 식별하는 실험 설계가 아니다. 다른 전략과 함께 비교해 우열이나 변동 원인을 추론하지 않는다.

`experiments/results/smoke_backup_20261108_215944/`는 총 20회 중 완료 0회인 interrupted checkpoint다. 성공률 집계에 포함하지 않았다. 각 완료 폴더의 `experiment.json`, `progress.json`, `summary.json`, `runs.jsonl`을 원본으로 삼았다. 11월 8일 실행은 commit `6f58e8e`에서 dirty 상태였고, 일부 초기 Runner fixture 자료는 commit/config provenance가 없다.

## 산출물의 용도

이 아카이브는 과거의 실행 연결·상태 저장 점검 결과를 찾기 위한 것이다. Main·Ablation·Critical Flexible 및 Criticality Safety Gate 결과와 합치지 않는다. 새 비교를 재현하거나 성능을 주장하려면 현재 freeze와 동일한 입력·token counter·실행 config에서 별도 평가를 수행해야 한다.
