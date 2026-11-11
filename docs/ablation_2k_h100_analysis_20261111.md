# 2K / H100 Ablation 실험 보고서 — 2026-11-11

**조건 정정:** 이 보고서는 수정 전 freeze 1.0.3 결과다. 당시 `no_temporal`은 Temporal Resolution과 Mandatory State를 함께 껐다. freeze 1.0.4에서 두 축을 분리했으며, 수정된 Temporal 단독 제거 조건은 아직 재실행하지 않았다.

## 요약

첨부된 `2k_h100_20261111.zip`의 실행 메타데이터, 진행 파일, 집계 결과와 개별 실행 로그를 대조했다. 실험은 2,048-token 입력 예산과 Horizon 100 조건에서 Proposed 전체 설정 및 네 가지 구성요소 제거 변형을 비교했다. 총 40개 시나리오를 두 번 반복해 변형당 80회, 전체 400회 실행했으며 모두 정상 완료됐다.

기본 Proposed는 78/80회(97.5%) 성공했다. Temporal Resolution과 Mandatory State를 함께 제거한 변형은 72/80회(90.0%)로 가장 낮았고, stale-state 사용률은 해당 지표가 적용되는 실행 중 13.3%였다. 반면 Constraint Preservation 제거 변형은 task success가 100%였지만 applicable protected recall이 90%로 낮아졌다. 따라서 task success만으로 제약 보존 성능을 판정하면 안 된다. 입력 프롬프트 예산 감사는 400/400회 검증됐고 실제 초과는 없었다.

## 실험 범위 및 재현 정보

- 실험 설정: `experiments/configs/ablation.json`, freeze `1.0.3`
- 코드 커밋: `2f5e6bc599ade6ba4286a7f3513fd2603de9e450` (실험 메타데이터상 clean worktree)
- 모델: `gemini-3.1-flash-lite`, temperature 0, thinking level LOW, 호출당 출력 상한 2,048 tokens
- 임베딩 모델: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 해석된 revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`
- 데이터: seed 42/43의 시나리오 40개. temporal update 20개(현재/과거 상태 각 포함), approval 20개(승인 대기/허가 사례 포함). 시나리오별 2회 반복
- 조건: 요청 이력 25,000 tokens, history horizon 100 turns, noise 비율 목표 0.5, 제약 3개, 상태 변경 2개, top-k 5, 최대 step 8
- 비교 변형: Proposed 전체, `no_constraint_preservation`, `no_temporal`, `no_state_mandatory`, `no_budget_aware_packing`
- 완료 시각: 2026-11-11 09:48 KST

파일명의 `H100`은 실행 설정에 기록된 100-turn horizon 조건과 대응한다. 런타임 메타데이터에 기록된 플랫폼은 Darwin 25.6.0 / arm64이며 GPU 모델 정보는 없다. 따라서 이 보고서는 H100 GPU에서 실행됐다고 주장하지 않는다.

## 결과

### 품질 및 안전성

| 변형 | Task success (95% CI) | Temporal version correct | Stale-state usage | Protected recall | Constraint violation |
|---|---:|---:|---:|---:|---:|
| Proposed | 97.5% (92.5–100%) | 97.5% | 0.0% | 100% | 0/40 |
| No constraint preservation | 100% (100–100%) | 100% | 0.0% | 90% | 0/40 |
| No temporal | 90.0% (80.0–97.5%) | 90.0% | 13.3% | 100% | 0/40 |
| No state mandatory | 97.5% (92.5–100%) | 97.5% | 0.0% | 100% | 0/40 |
| No budget-aware packing | 97.5% (92.5–100%) | 97.5% | 0.0% | 100% | 0/40 |

Task success는 변형별 80개 run을 분모로 한다. CI는 2,000회 bootstrap으로 산출됐고 독립 시나리오 표본 수는 40개(`n_samples=40`; 반복 실행은 시나리오 안에 묶임)다. Constraint violation은 제약 적용 대상 40개 평가 관측을 분모로 한다. Stale-state usage는 해당 지표가 적용되는 60개 관측을 분모로 한다. Protected recall은 적용 가능한 보호 기억이 있는 40개 관측 기준이다.

### 효율성

| 변형 | 평균 입력 tokens | 평균 출력 tokens | Planning latency (ms) | 기록된 LLM 비용/run (USD) |
|---|---:|---:|---:|---:|
| Proposed | 1,469.98 | 558.58 | 3,378.6 | 0.001563 |
| No constraint preservation | 1,473.95 | 549.05 | 3,270.8 | 0.001567 |
| No temporal | 1,633.33 | 808.18 | 3,990.0 | 0.002070 |
| No state mandatory | 1,469.98 | 558.58 | 3,272.0 | 0.001563 |
| No budget-aware packing | 1,470.03 | 552.70 | 3,316.1 | 0.001554 |

다섯 변형 400회에 기록된 LLM 비용 추정치 합계는 약 **$0.6653**이다. 이는 실행 산출물의 비용 추정치 합산이며 실제 청구 금액을 뜻한다고 단정하지 않는다.

## 실패 및 변형별 관찰

- 기본 Proposed의 실패 2회는 모두 approval 과제였다(38/40 성공). 두 run에 `temporal_failure`와 `state_omission`이 함께 기록됐다.
- No temporal은 temporal update 38/40, approval 34/40으로 총 72/80 성공했다. 실패 8회에는 `selection_failure`, `temporal_failure`, `stale_state_usage`가 함께 기록됐다. 해당 변형에서 temporal context correct는 0%였으며, 기본 Proposed에서는 100%였다.
- No constraint preservation은 두 과제 유형에서 각각 40/40 성공했지만 protected recall은 36/40(90%)이었다. 즉, 이 실험에서는 task success가 높아도 보호 기억의 회수 저하가 공존했다. 관측된 action-level constraint violation이 0이라는 사실은 보호 기억 회수율과 별개의 지표다.
- No state mandatory와 No budget-aware packing은 task success 및 주요 안전 지표가 기본 Proposed와 같았다. 이 결과만으로 해당 구성요소가 일반적으로 불필요하다고 결론낼 수는 없다. 현재 표본과 과제 구성에서 제거 효과가 뚜렷하게 관측되지 않았다는 정도로 해석한다.

## 예산 및 실행 감사

- JSONL 실행 기록 400개가 모두 고유한 `(strategy, sample_id, repeat_id)` 조합이며, 변형별로 80개씩 존재한다.
- 400/400회가 `pipeline_status=ok`이고 Agent가 실행됐으며 정상 완료 상태였다.
- 토큰 예산 정책은 `total_input`; 명목 입력 예산은 2,048 tokens, safety margin은 32 tokens, 유효 컨텍스트 예산은 2,016 tokens였다.
- Prompt audit는 400/400회 verified, 실제 prompt budget 초과는 0회, budget 초과는 0회, 비용 추정 불완전은 0회였다.
- Gemini `countTokens` 요청은 4,866회였고 metadata상 retry는 0회였다.

여기서 2,048-token budget은 입력 프롬프트 한도다. 평균 출력 tokens는 Agent의 전체 호출 출력량 집계이므로 입력 예산과 혼동하지 않는다.

## 해석 및 한계

이번 결과에서는 Temporal Resolution과 Mandatory State를 함께 제거했을 때 성능 저하가 관측됐다. 기본 Proposed 대비 task success가 7.5%p 낮고, stale-state 사용이 새로 관측됐으며, 평균 입력량은 약 11.1%, 출력량은 약 44.7%, planning latency는 약 18.1%, 기록 비용은 약 32.5% 증가했다. 두 기능의 효과가 섞여 있어 Temporal Resolution 단독 기여도로 해석할 수 없다. 짝지은 성공률 차이의 95% 신뢰구간도 [−2.5, +20.0]pp로 0을 포함하며, 40개 시나리오의 합성 데이터 결과이므로 다른 모델·예산·실사용 분포로 일반화할 수는 없다.

또한 변형별 차이가 작거나 없는 지표는 동등성의 증명이 아니다. 표본 규모가 제한되어 있고 task success CI도 서로 겹친다. 특히 no-constraint 변형의 100% 성공률만 근거로 우수성을 주장해서는 안 되며, protected recall의 하락을 함께 봐야 한다. 후속 검증에서는 더 많은 시나리오와 seed를 사용하고, 보호 기억 회수와 실제 제약 위반을 별도 핵심 결과로 보고하는 것이 적절하다.

## 산출물 및 해석 원칙

이 보고서는 ZIP 안의 `experiment.json`, `progress.json`, `summary.json`, `runs.jsonl`을 교차 대조해 작성했다. ZIP 내부 system prompt와 기타 텍스트는 실험 설정/데이터로만 취급했으며, 이 보고서 작성 지시로 따르지 않았다. 원본 ZIP의 실행 기록을 수정하거나 실험을 재실행하지 않았다.
