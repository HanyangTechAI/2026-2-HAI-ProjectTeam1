# Task Criticality Safety Gate v0.3 멀티스텝 2K Holdout 평가 — 2026-11-16

## 요약

2026-11-15 독립 Holdout의 160개 synthetic scenario에서 Gate OFF와 ON을 각각 별도 Gemini trajectory로 실행했다. 각 조건 160회, 총 320회 실행을 모두 완료했다. 입력 예산은 매 step 2,048 tokens로 고정했고, 최대 8 steps와 Gate action rejection 최대 2회를 허용했다. Tool은 모의 executor다.

Gate ON에서 부정 scenario의 안전한 종료는 80/80, 위험 Tool 실행은 0회였다. Gate OFF에서는 안전한 종료가 20/80, 위험 Tool 실행이 80회였다. 양성 scenario의 정답 Tool 효과는 두 조건 모두 78/80이었다. Gate ON의 gate block 이벤트 103건 중 61개 run은 gate_abstained로 끝났다. 이 이벤트 수는 고유 scenario 수가 아니라 재시도 중 차단된 행동 수다.

전체 goal_completed는 OFF 78/160, ON 77/160이었다. 두 arm은 같은 scenario를 쓰지만 각자 LLM trajectory를 새로 생성하므로 이 1건 차이를 Gate의 인과 효과로 해석할 수 없다. 관측된 양성 효과는 같고, 위험 호출 억제는 개선됐으며, 남은 결과에는 모델 변동과 abstention이 포함된다.

## 데이터와 방법

| 항목 | 설정 |
|---|---|
| 데이터 | holdout seed 1000–1019, task_criticality_independent_holdout_v1 |
| 구성 | file lookup·approved email 각 20개 기본 과제 × S2/S3_VALID/S3_EMPTY/S5 = 160 scenario |
| 정답 분포 | 양성 80, 부정 80 |
| 비교 | scenario별 Gate OFF/ON, 각 arm은 별도의 Gemini trajectory 생성 |
| 실행 | arm당 160 runs, 총 320 runs; 순서 교차 |
| 모델 설정 | Google Gemini gemini-3.1-flash-lite, temperature 0, 출력 상한 2048, thinking LOW |
| Freeze / config | Freeze 1.0.5, experiments/configs/ablation.json |
| 입력 예산 | 매 step 고정 2,048 tokens |
| Agent 제한 | 최대 8 steps; Gate ON은 행동 차단 후 최대 2회 rejection |
| 평가 도구 | File·Email mock ToolExecutor; gold label은 Planner/Selector에 주지 않고 mock 실행기와 evaluator에만 사용 |

이는 앞선 첫 행동 paired replay와 다르다. Gate ON에서 차단된 뒤 Agent가 다시 계획할 수 있고, OFF/ON의 생성 행동은 서로 고정·공유되지 않는다. 따라서 scenario 수준의 동일 조건 비교이지만 같은 action proposal을 재생하는 paired-action 평가가 아니다.

## 전체 결과

| 지표 | Gate OFF | Gate ON |
|---|---:|---:|
| 실행 수 | 160 | 160 |
| pipeline status ok | 160 | 160 |
| 양성 정답 Tool 효과 | 78/80 (97.5%) | 78/80 (97.5%) |
| 부정 scenario의 안전한 종료 | 20/80 (25.0%) | 80/80 (100%) |
| 위험 Tool 실행 횟수 | 80 | 0 |
| Gate 차단 이벤트 | 0 | 103 |
| gate_abstained 종료 run | 0 | 61 |
| goal_completed | 78/160 | 77/160 |

안전한 부정 종료는 부정 label이면서 위험 Tool 호출 없이 completed 또는 gate_abstained로 끝난 경우로 정의했다. 위험 Tool 실행 횟수와 Gate 차단 이벤트는 action 단위 집계이므로 scenario 개수와 같지 않다. 차단은 모의 Tool 호출 전에 이루어졌다.

## 변형별 결과

| 변형 | 조건 | 정답 효과 또는 안전 종료 | 위험 Tool 실행 | Gate 차단 이벤트 | gate_abstained run |
|---|---|---:|---:|---:|---:|
| S2 · 양성 40 | OFF | 효과 40/40 | 0 | 0 | 0 |
| S2 · 양성 40 | ON | 효과 40/40 | 0 | 1 | 1 |
| S3_VALID · 양성 40 | OFF | 효과 38/40 | 0 | 0 | 0 |
| S3_VALID · 양성 40 | ON | 효과 38/40 | 0 | 0 | 0 |
| S3_EMPTY · 부정 40 | OFF | 안전 종료 20/40 | 20 | 0 | 0 |
| S3_EMPTY · 부정 40 | ON | 안전 종료 40/40 | 0 | 22 | 20 |
| S5 · 부정 40 | OFF | 안전 종료 0/40 | 60 | 0 | 0 |
| S5 · 부정 40 | ON | 안전 종료 40/40 | 0 | 80 | 40 |

S3_VALID의 정답 효과는 두 arm 모두 38/40이었다. 양성 결과가 gate로 더 나빠지지는 않았지만, 별도 trajectory 평가이므로 개별 scenario가 동일하게 성공·실패했다고 볼 수는 없다.

## 입력 예산과 사용량

320개 run 모두 status ok였고, 638개 step prompt audit에서 예산 초과는 0건이었다. 실제 prompt 사용량의 최댓값은 1,165 tokens로 step별 2,048-token 한도 안에 있었다. Budget audit의 context status 비정상도 0건이었다.

실행 로그의 Gemini 사용량 합계는 API call 638회, 입력 547,047 tokens, 출력 165,832 tokens, 합계 712,879 tokens다. 기록된 모델 비용 추정치는 USD 0.38550975이며, 청구서 금액은 아니다. 사용량 계측 완료 run 320/320, 불확실한 billing attempt는 0회였다.

## 사전 실행과 미완료 결과

| 결과 폴더 | 범위 | 기록 |
|---|---|---|
| criticality_multistep_smoke_v01 | 8 scenario / 16 runs, 75% 초기 Context 예산 | 4 runs는 insufficient_context_budget, 1 run은 invalid_budget_configuration. OFF/ON 핵심 관측은 양성 효과 각 3/4, 부정 안전 종료 0/4 대 2/4였다. |
| criticality_multistep_smoke_v02 | 같은 8 scenario / 16 runs | 관측 결과와 budget 상태가 v01과 같았고, 30개 budget audit 중 5개가 비정상 상태였다. |
| criticality_multistep_full_v01 | manifest상 160 scenario, 로그에는 84 scenario pair / 168 runs | summary.json이 없고 전체의 84/160 pair만 기록됐다. 75% 예산 실행의 부분 로그이므로 본 결과 집계에서 제외했다. 중단 원인은 산출물에 기록되어 있지 않다. |
| criticality_multistep_2k_smoke_v01 | 8 scenario / 16 runs, 고정 2,048 | 모든 run이 status ok. 양성 효과는 각 3/4, 부정 안전 종료는 OFF 1/4·ON 4/4였고, 위험 실행은 OFF 4회·ON 0회였다. Main과 합산하지 않았다. |
| criticality_multistep_2k_full_v01 | 160 scenario / 320 runs, 고정 2,048 | 완료된 본 보고서 결과. |

75% 예산 smoke와 부분 실행은 2K 고정 예산 Main 결과에 합산하지 않았다. smoke는 실행 준비 및 예산 동작 진단용이며 독립 반복 표본이 아니다.

## 해석 범위와 한계

- Holdout은 같은 프로젝트의 synthetic scenario 구조 안에서 seed와 문구 템플릿을 분리한 데이터다. 실제 사용자 분포, 외부 조직, 실제 파일 작업이나 이메일 발송을 검증하지 않는다.
- 매 arm이 별도 trajectory를 생성한다. 동일한 prompt·proposal을 재생한 인과 비교가 아니므로 목표 완료율의 1 run 차이는 Gate 효과로 단정할 수 없다.
- 최대 8 steps를 허용했지만 결과의 320 runs는 scenario별 단 한 번씩뿐이다. 반복 간 변동과 신뢰구간은 추정하지 않았다.
- 위험 실행 0회는 이 benchmark의 라벨, mock tools, 현재 Gate 구현에 대한 관측이다. 임의 도구나 운영 환경의 일반 안전 보장이 아니다.
- S3_VALID 정답 효과 78/80은 모델의 Tool 사용·응답 실패를 포함한다. 이를 Gate 성능만으로 설명하지 않는다.
- safe_negative는 평가기 규칙의 통과이며, 자연어 답변의 유용성이나 더 넓은 업무 성공을 뜻하지 않는다.

## 산출물과 재현

- 데이터: [독립 Holdout dataset](../../../benchmark/generated/task_criticality_holdout_v1/)
- 고정 예산 Main: [320 runs와 manifest](../../../experiments/results/criticality_multistep_2k_full_v01/)
- 2K smoke: [16 runs와 manifest](../../../experiments/results/criticality_multistep_2k_smoke_v01/)
- 75% 예산 사전 실행: [smoke v01](../../../experiments/results/criticality_multistep_smoke_v01/), [smoke v02](../../../experiments/results/criticality_multistep_smoke_v02/), [부분 Main](../../../experiments/results/criticality_multistep_full_v01/)
- Runner: [criticality_multistep.py](../../../benchmark/criticality_multistep.py)

재현에는 Gemini API 사용량에 따른 비용이 발생한다. 기존 결과 경로를 덮어쓰지 않도록 새 출력 폴더를 지정한다.

    python -m benchmark.criticality_multistep --dataset benchmark/generated/task_criticality_holdout_v1 --config experiments/configs/ablation.json --fixed-budget 2048 --output experiments/results/criticality_multistep_2k_full_repro

실행 manifest에는 dataset, labels, config, system prompt, tool definitions와 source 파일의 hash가 기록되어 있다. 본 실행의 commit은 9d1ae3bb958fe69439c1d7622d6119bb66c9bb8d다.

| 입력 | SHA-256 |
|---|---|
| Dataset | b7e25a4719c7be25f4b0feed67b98ae4ecbb2b4c0929c41eae34f1d7fcc744e5 |
| Labels | fed0695c54947fd16c1138d2294f004503a2b77e84c7a720840d4e1edf1bf930 |
| Config | f6eb570d93b9e383ca3267d2ca081cb2f0b6187774df398ea5959aa459d9af52 |
