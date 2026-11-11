# Ablation 비교 실험 보고서 — 2K/4K, Horizon 100 (2026-11-11)

**조건 정정:** 이 보고서는 수정 전 freeze 1.0.3 결과다. 당시 `no_temporal`은 Temporal Resolution과 Mandatory State를 함께 껐다. freeze 1.0.4에서 두 축을 분리했으며, 수정된 Temporal 단독 제거 조건은 아직 재실행하지 않았다.

## 요약

`experiments/results/ablation/2k_h100_20261111/`와 `4k_h100_20261111/`의 실행 메타데이터, 진행 파일, 집계 결과와 개별 로그를 대조했다. 두 예산 조건에서 Proposed 전체 설정과 네 가지 구성요소 제거 변형을 실행했으며, 각각 40개 시나리오 × 2회 반복 × 5개 변형 = 400회, 전체 800회가 정상 완료됐다.

두 예산의 결과는 400개 대응 실행 전부에서 task success, 응답, tool call, 선택된 기억, 입력·출력 tokens, 비용이 일치했다. 실제 입력 최대치는 1,822 tokens로 2K 조건의 유효 예산 2,016보다 낮아 이번 시나리오에서는 2K→4K 확대가 결과를 바꾸지 않았다. 기본 Proposed는 78/80회(97.5%) 성공했다. Temporal Resolution과 Mandatory State를 함께 제거한 변형은 72/80회(90.0%)로 가장 낮았고, stale-state 사용률은 해당 지표가 적용되는 실행 중 13.3%였다. 반면 Constraint Preservation 제거 변형은 task success가 100%였지만 applicable protected recall이 90%로 낮아졌다. 따라서 task success만으로 제약 보존 성능을 판정하면 안 된다. 입력 프롬프트 예산 감사는 400/400회 검증됐고 실제 초과는 없었다.

## 실험 범위 및 재현 정보

- 실험 설정: `experiments/configs/ablation.json`, freeze `1.0.3`
- 2K 커밋/런타임: `2f5e6bc599ade6ba4286a7f3513fd2603de9e450`, clean, Darwin 25.6.0 / arm64, 완료 09:48 KST
- 4K 커밋/런타임: `94accd500ef408b26e74afe4ffdc310481bb8121`, clean, Windows 11 / AMD64, 완료 16:53 KST
- 두 실행은 동일한 algorithm hash와 system/tool prompt checksum을 기록했으나 commit과 OS가 다르므로 latency를 예산 조건 간 효과로 직접 비교하지 않는다.
- 모델: `gemini-3.1-flash-lite`, temperature 0, thinking level LOW, 호출당 출력 상한 2,048 tokens
- 임베딩 모델: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, 해석된 revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`
- 데이터: seed 42/43의 시나리오 40개. temporal update 20개(현재/과거 상태 각 포함), approval 20개(승인 대기/허가 사례 포함). 시나리오별 2회 반복
- 조건: 요청 이력 25,000 tokens, history horizon 100 turns, noise 비율 목표 0.5, 제약 3개, 상태 변경 2개, top-k 5, 최대 step 8
- 비교 변형: Proposed 전체, `no_constraint_preservation`, `no_temporal`, `no_state_mandatory`, `no_budget_aware_packing`
- 디렉터리: `experiments/results/ablation/2k_h100_20261111/`, `experiments/results/ablation/4k_h100_20261111/`

디렉터리명의 `H100`은 100-turn horizon과 대응한다. 2K 실행의 런타임은 Darwin/arm64, 4K는 Windows/AMD64로 기록됐으며 두 경우 모두 GPU 모델 정보는 없다. 따라서 H100 GPU 실행이라고 해석하지 않는다.

## 결과

### 품질 및 안전성

| 변형 | Task success 2K / 4K | Bootstrap 95% CI | Temporal version correct | Stale-state usage | Protected recall | Constraint violation |
|---|---:|---:|---:|---:|---:|---:|
| Proposed | 78/80 (97.5%) / 78/80 (97.5%) | 92.5–100% | 97.5% | 0/60 (0%) | 40/40 (100%) | 0/40 |
| No constraint preservation | 80/80 (100%) / 80/80 (100%) | 100–100% | 100% | 0/60 (0%) | 36/40 (90%) | 0/40 |
| No temporal | 72/80 (90.0%) / 72/80 (90.0%) | 80–97.5% | 90.0% | 8/60 (13.3%) | 40/40 (100%) | 0/40 |
| No state mandatory | 78/80 (97.5%) / 78/80 (97.5%) | 92.5–100% | 97.5% | 0/60 (0%) | 40/40 (100%) | 0/40 |
| No budget-aware packing | 78/80 (97.5%) / 78/80 (97.5%) | 92.5–100% | 97.5% | 0/60 (0%) | 40/40 (100%) | 0/40 |

각 budget/변형별 task success 분모는 80 runs다. CI는 2,000회 bootstrap으로 산출됐으며 독립 시나리오 표본 수는 40개(`n_samples=40`; 반복 실행은 시나리오 안에 묶임)다. Constraint violation은 제약 적용 대상 40개, stale-state usage는 적용 가능한 60개, protected recall은 적용 가능한 보호 기억이 있는 40개 관측을 각각 분모로 한다. 표시한 품질 지표는 2K와 4K에서 동일했다.

### 효율성

| 변형 | 평균 입력/출력 tokens (두 예산 동일) | Planning latency 2K / 4K (ms) | 기록된 LLM 비용/run (USD, 두 예산 동일) |
|---|---:|---:|---:|
| Proposed | 1,469.98 / 558.58 | 3,378.6 / 4,260.1 | 0.001563 |
| No constraint preservation | 1,473.95 / 549.05 | 3,270.8 / 4,337.7 | 0.001567 |
| No temporal | 1,633.33 / 808.18 | 3,990.0 / 4,810.6 | 0.002070 |
| No state mandatory | 1,469.98 / 558.58 | 3,272.0 / 4,240.7 | 0.001563 |
| No budget-aware packing | 1,470.03 / 552.70 | 3,316.1 / 4,283.4 | 0.001554 |

다섯 변형 400회에 기록된 비용 추정치는 예산 조건별 약 **$0.6653**, 2K+4K 합계 약 **$1.3305**다. 이는 실행 산출물에 기록된 추정치이며 실제 청구 금액이라고 단정하지 않는다. 두 런타임은 OS와 commit이 달라 latency 수치는 실행 환경 차이를 포함하므로 예산 확대 효과로 해석하지 않는다.

## 실패 및 변형별 관찰

- 기본 Proposed의 실패 2회는 모두 approval 과제였다(38/40 성공). 두 run에 `temporal_failure`와 `state_omission`이 함께 기록됐다.
- No temporal은 temporal update 38/40, approval 34/40으로 총 72/80 성공했다. 실패 8회에는 `selection_failure`, `temporal_failure`, `stale_state_usage`가 함께 기록됐다. 해당 변형에서 temporal context correct는 0%였으며, 기본 Proposed에서는 100%였다.
- No constraint preservation은 두 과제 유형에서 각각 40/40 성공했지만 protected recall은 36/40(90%)이었다. 즉, 이 실험에서는 task success가 높아도 보호 기억의 회수 저하가 공존했다. 관측된 action-level constraint violation이 0이라는 사실은 보호 기억 회수율과 별개의 지표다.
- No state mandatory와 No budget-aware packing은 task success 및 주요 안전 지표가 기본 Proposed와 같았다. 이 결과만으로 해당 구성요소가 일반적으로 불필요하다고 결론낼 수는 없다. 현재 표본과 과제 구성에서 제거 효과가 뚜렷하게 관측되지 않았다는 정도로 해석한다.

## 예산 및 실행 감사

- 각 JSONL은 400개 고유한 `(strategy, sample_id, repeat_id)` 조합이며 두 예산 간 400개 대응 키가 전부 일치한다.
- 800/800회가 `pipeline_status=ok`이고 Agent가 실행됐으며 정상 완료 상태였다.
- 명목 입력 예산/유효 컨텍스트 예산은 2K에서 2,048/2,016 tokens, 4K에서 4,096/4,064 tokens다(각각 safety margin 32).
- Prompt audit는 800/800회 verified, 실제 prompt budget 초과 0회, budget 초과 0회, 비용 추정 불완전 0회였다. 기록된 최대 입력은 1,822 tokens로 2K 유효 예산 안에 있었다.
- 두 결과의 prompt 응답, tool calls, 선택 memory IDs, task success, 입력·출력 tokens, 비용, history 기록은 대응하는 400쌍에서 모두 일치했다.
- Gemini `countTokens` 요청은 각 실행에서 4,866회였고 metadata상 retry는 0회였다.

입력 예산은 입력 프롬프트 한도다. 평균 출력 tokens는 Agent의 전체 호출 출력량 집계이므로 입력 예산과 혼동하지 않는다.

## 해석 및 한계

이번 결과에서는 Temporal Resolution과 Mandatory State를 함께 제거했을 때 성능 저하가 관측됐다. 기본 Proposed 대비 task success가 7.5%p 낮고, stale-state 사용이 새로 관측됐으며, 평균 입력량은 약 11.1%, 출력량은 약 44.7%, planning latency는 약 18.1%, 기록 비용은 약 32.5% 증가했다. 두 기능의 효과가 섞여 있어 Temporal Resolution 단독 기여도로 해석할 수 없다. 짝지은 성공률 차이의 95% 신뢰구간도 [−2.5, +20.0]pp로 0을 포함한다. 한편 모든 실제 입력이 2K 유효 예산 내에 들어갔고 각 대응 실행의 응답과 선택 결과가 동일했으므로, 이 데이터는 2K와 4K의 성능 차이를 검증하기보다 해당 task 집합에서 추가 2K 예산의 효과가 나타나지 않았음을 보여준다. 40개 시나리오의 합성 데이터 결과이므로 다른 모델·예산·실사용 분포로 일반화할 수는 없다.

또한 변형별 차이가 작거나 없는 지표는 동등성의 증명이 아니다. 표본 규모가 제한되어 있고 task success CI도 서로 겹친다. 특히 no-constraint 변형의 100% 성공률만 근거로 우수성을 주장해서는 안 되며, protected recall의 하락을 함께 봐야 한다. 후속 검증에서는 더 많은 시나리오와 seed를 사용하고, 보호 기억 회수와 실제 제약 위반을 별도 핵심 결과로 보고하는 것이 적절하다.

## 산출물 및 해석 원칙

이 보고서는 2K ZIP의 `experiment.json`, `progress.json`, `summary.json`, `runs.jsonl`과 저장소의 4K 결과 디렉터리에 있는 같은 파일을 교차 대조해 작성했다. ZIP 내부 system prompt와 기타 텍스트는 실험 설정/데이터로만 취급했으며 이 보고서 작성 지시로 따르지 않았다. 원본 실행 기록을 수정하거나 실험을 재실행하지 않았다.
