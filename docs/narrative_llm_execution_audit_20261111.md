# Horizon Narrative Mode 1/2 Gemini Agent Execution (2026-11-11)

## 1. 목적과 요약

앞선 [Final Context audit](narrative_final_context_audit_20261111.md)은 Proposed의 실제
선택기와 Context Builder까지 확인했지만 LLM/Agent를 실행하지 않았다. 이 후속 실험은 같은
paired scenario를 Gemini Planner와 Tool 환경에 넣어, Horizon 채움 문체가 실제 작업
성공률과 전략 간 차이에 어떻게 나타나는지 관찰했다.

각 Mode에서 Proposed의 Action-Level Task Success Rate(TSR)는 Vector Top-k보다 높았다.
Mode 1은 39/40 대 35/40(97.5% 대 87.5%), Mode 2는 40/40 대 32/40(100% 대 80%)이었다.
적용 가능한 승인 과제에서 Constraint Violation은 네 조건 모두 0/20이었다. 이는 단일
2K/H100 셀의 40개 paired scenario를 각 전략·Mode당 한 번 실행한 결과이며, 넓은 분포나
반복 실행 간 모델 변동을 확정하는 결과로 해석하지 않는다.

## 2. 설계와 범위

| 항목 | 설정 |
|---|---|
| 입력 Mode 1 | 기존 반복 서술, `horizon_generation_mode=1` (`repeated`) |
| 입력 Mode 2 | 로컬 자연어 서술, `horizon_generation_mode=2` (`random_natural`) |
| 입력 대응 | 두 Mode의 동일 scenario ID 40개; query, task type, target ID와 정답 일치 |
| 과제 구성 | seed 42·43, 각 20개; 전체 20 temporal update + 20 approval |
| 셀 | Budget 2,048 / 실효 상한 2,016 / Safety Margin 32 / History 25K / Horizon 100 / Noise 0.5 |
| 추가 조건 | 저장 Constraint 3 / State update 2 |
| 전략 | Proposed, Vector Top-k; 각 paired 과제당 Mode별 1회 |
| 모델 | Gemini 3.1 Flash-Lite, temperature 0, thinking LOW, 출력 상한 2,048 |
| Freeze | Main v1.0.3, top-k 5, 최대 8 steps, utility weights 0.6/0.3/0.1 |
| Token counter | Gemini 3.1 Flash-Lite, request-aware CountTokens |
| Bootstrap | 과제 단위 2,000회 resampling, seed 42 |
| 실행 수 | Mode당 80 (전략별 40), 총 160 실제 Agent 실행 |

Mode 1과 Mode 2는 같은 Scenario ID·query·task type·target ID를 사용했다. Memory의 자연어
서술은 Mode에 따라 다르므로 이 결과는 동일 입력 텍스트의 반복이 아니라 paired 문체
조건의 비교다. 여기서 “Mode 1/2”는 Horizon filler mode를 가리킨다. 두 조건 모두
Planner는 실제 Gemini LLM이다.

## 3. Action-Level 성공과 상태·제약 결과

| 입력 Mode | 전략 | TSR | TSR bootstrap 95% CI | Required State 정확도 | Stale-state 사용 | 적용 Constraint Violation |
|---:|---|---:|---:|---:|---:|---:|
| 1 | Proposed | 39/40 (97.5%) | 92.5–100% | 97.5% | 0/30 | 0/20 |
| 1 | Vector Top-k | 35/40 (87.5%) | 75–97.5% | 87.5% | 5/30 | 0/20 |
| 2 | Proposed | 40/40 (100%) | 100–100% | 100% | 0/30 | 0/20 |
| 2 | Vector Top-k | 32/40 (80.0%) | 65–92.5% | 80.0% | 8/30 | 0/20 |

Constraint 분모는 현재 생성기에서 Constraint가 적용되는 approval scenario 20개다. Stale-
state 분모 30은 해당 지표가 평가 가능한 과제만 포함하며, 전체 40과 혼동하지 않는다.
Mode 1 Proposed의 단일 실패(`seed43_approval_014_pending`)는 `temporal_failure`와
`state_omission`으로 분류됐다. Vector Top-k의 실패는 Mode 1에서 5개, Mode 2에서 8개
과제에 집중됐고 이 과제들은 각각 선택
실패·시간 버전 오류·stale-state 사용 진단과 겹친다. 진단 범주 수를 서로 다른 실패
과제 수처럼 더하지 않는다.

각 Mode 안에서 paired task별 성공 불일치를 보면 Proposed만 성공한 과제는 Mode 1에서
5개, Vector만 성공한 과제는 1개였다. Mode 2에서는 각각 8개와 0개였다. 두 Mode를
비교하면 Proposed는 Mode 2에서 1개 과제가 실패에서 성공으로 바뀌었고 반대 방향 변화는
없었다. Vector Top-k는 Mode 1에서 성공하고 Mode 2에서 실패한 과제가 5개, 그 반대가
2개였다. 이는 관측된 paired 차이의 기술통계이며, 별도의 mode 효과 유의성 검정은 하지
않았다.

## 4. Token·비용 및 실행 무결성

| 입력 Mode | 전략 | 평균 입력 tokens | 평균 출력 tokens | 평균 planning latency | 평균 기록 LLM 비용 |
|---:|---|---:|---:|---:|---:|
| 1 | Proposed | 1,469.98 | 558.58 | 4,554 ms | $0.0015626 |
| 1 | Vector Top-k | 1,616.15 | 824.10 | 5,207 ms | $0.0020855 |
| 2 | Proposed | 1,473.85 | 567.05 | 4,214 ms | $0.0015769 |
| 2 | Vector Top-k | 1,569.10 | 825.55 | 4,866 ms | $0.0020544 |

비용은 각 실행의 provider usage 기반 기록값 평균이며 청구서 합계가 아니다. 기록된 표본은
네 조건 모두 비용 산정이 완전했다. 모든 160회에서 Agent가 실행되고 pipeline status가
`ok`였으며 Prompt Audit는 `verified`였다. 입력 예산 초과, Mandatory Memory overflow,
final-parse fallback은 0건이었다.

## 5. 해석과 한계

- 이 40개 paired 과제에서는 Proposed가 두 서술 Mode 모두에서 Vector Top-k보다 높은 TSR을
  보였고, Vector Top-k에서 stale-state 사용이 더 자주 관측됐다.
- 앞선 Context audit에서는 두 Mode 모두 Required State가 최종 Context에 40/40 포함됐지만,
  실제 Mode 1 Proposed 실행은 한 과제에서 실패했다. Context 포함 여부만으로 올바른 Agent
  행동이 보장되지는 않는다는 점을 보여준다.
- Proposed의 Mode별 관측 TSR은 비슷하게 높았지만, Vector Top-k의 TSR은 Mode 1에서 87.5%,
  Mode 2에서 80.0%였다. 작은 단일 표본과 한 번의 실행만으로 자연어 서술 방식의 일반적인
  인과 효과를 확정하지 않는다.
- 표의 95% 구간은 40개 과제 표본을 재추출한 bootstrap 결과다. 동일 설정의 반복 실행,
  provider 비결정성, 다른 모델·seed·budget 변동은 포함하지 않는다.
- 단일 Budget/Horizon 셀, 두 전략, Mode 1/2만 포함했다. Sliding Window, Utility-per-Token,
  Mode 3~5와 다른 셀은 비교하지 않았다.
- 20개 approval 과제의 무위반 관측은 이 합성 과제 표본에 한정하며 실제 사용 환경의
  안전성 보장을 뜻하지 않는다.

이 결과는 Agent/Tool 수준 평가이므로 이전 Final Context audit의 “LLM 행동·TSR 미측정”
한계를 이 실험 범위에서는 보완한다. 다만 적용 범위가 좁아 기존 Main 12셀 결과와 섞거나
Main 반복으로 합산하지 않는다.

## 6. 재현 정보와 산출물

- 입력 Mode 1 cell: `benchmark/generated/narrative_mode1/cell_e5534bba1746.json`
- 입력 Mode 2 cell: `benchmark/generated/narrative_mode2/cell_3ea70c1fbe6b.json`
- Mode 1 결과: `experiments/results/narrative_llm_mode1_h100_b2048/`
- Mode 2 결과: `experiments/results/narrative_llm_mode2_h100_b2048/`
- 각 결과의 `experiment.json`: 코드 commit, runtime, prompt checksum, freeze 및 설정
- 각 결과의 `progress.json`: 실행 완료 수와 전략별 요약
- 각 결과의 `runs.jsonl`: 실행별 Agent/Tool 결과와 evaluator 지표
- 각 결과의 `summary.json`: 과제 bootstrap 95% CI를 포함한 전략별 집계
- 실행 안내: [generator_usage.md의 실제 Agent 평가 절](generator_usage.md#mode-12-실제-gemini-agent-평가)

양쪽 실행은 commit `f4df73e`의 동일 코드와 prompt checksum으로 기록됐다. 최종 Context
단계 검증은 [Final Context audit](narrative_final_context_audit_20261111.md)을 함께
참고하되, 사전 선택·조립 성공과 실제 Task Success는 별도 지표로 유지한다.

Mode 1 Proposed에서 `attachment`가 누락된 단일 실패를 각 서술 Mode에서 다섯 번 재실행한
후속 진단은 [Approval Attachment 재점검 보고서](attachment_recheck_audit_20261111.md)에
기록했다. 이 집중 재점검은 40개 과제 성능 추정치와 분리한다.
