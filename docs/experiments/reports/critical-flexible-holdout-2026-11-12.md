# Critical Flexible 75% 단일 Step Holdout 결과

2026-11-12 기준

## 요약

앞서 수행한 라벨 preflight와 32개 scenario Agent pilot의 후속으로, 별도 seed와 holdout 전용 문장 템플릿을 쓴 75% 예산 평가를 수행했다. 40개 기본 과제(파일 검색 20개, 승인된 이메일 발송 20개)를 조건 A–D로 확장한 160개 scenario를 두 preset에서 짝지어 실행했다. 총 320회에서 첫 Tool 행동과 작업별 Tool 효과가 모두 검증된 횟수는 `full` 117/160 (73.1%), `no_budget_aware_packing` 153/160 (95.6%)였다. 짝 차이는 Full 기준 −22.5%p였다.

가장 큰 차이는 긴 Critical Memory와 짧은 distractor가 함께 놓인 조건 C였다. `full`은 0/40, packing 비활성은 39/40의 첫 행동을 정확히 수행했다. 반면 A는 Full이 39/40 대 36/40으로 앞섰고, B와 D는 preset 간 차이가 없었다. 따라서 holdout 결과는 모든 조건에서 한쪽 preset이 일관되게 우세하다는 뜻이 아니라, 앞선 preflight에서 드러난 특정 길이·예산 경계가 실제 첫 행동 오류로 이어지는지 확인한 결과다.

## 실험 설계와 검증

| 항목 | 설정 |
| --- | --- |
| 데이터 | `critical_flexible_v1`, holdout seed 100–119, 2개 task family × 20개 기본 과제 × 4조건 = 160 scenario |
| 질문 템플릿 | `critical_flexible_holdout_templates_v1`; 기존 pilot과 별도 문장 템플릿 |
| 예산 | scenario별 mandatory floor–all-flexible ceiling 구간의 75%, safety margin 32 tokens |
| 비교 | `proposed/full` 대 `proposed/no_budget_aware_packing`, 같은 scenario의 paired 실행 |
| 실행 | scenario·preset당 1회, Gemini 3.1 Flash Lite, `max_steps=1`; 총 320 runs / 160 pairs |
| 동결·누출 점검 | Freeze 1.0.4, 데이터 validation에 `algorithm_outcome_inspected=false`; budget calibration은 outcome-blind로 수행 |
| 실행 검증 | 320/320 prompt budget audit 통과, 실제 예산 초과 0, 1 step 이외의 run 0 |

평가 지표 `first_action_correct`는 첫 Tool/action과 필수 인자가 예상값과 일치하고 작업별 Tool 효과까지 확인된 경우에만 성공으로 센다. 예를 들어 파일 검색은 정답 파일명이 들어간 호출과 검색 결과 성공을 확인한다. `max_steps=1` 때문에 최종 `goal_completed`는 지표에서 제외한다. 이 정의와 trace 검증은 [first-action evaluator](../../../benchmark/first_action_evaluator.py)에 구현돼 있다.

## 결과

### 조건별 paired 결과

| 조건 | Full | Packing 비활성 | Full−비활성 | 둘 다 성공 / 둘 다 실패 / Full만 성공 / 비활성만 성공 |
| --- | ---: | ---: | ---: | ---: |
| A | 39/40 (97.5%) | 36/40 (90.0%) | +7.5%p | 36 / 1 / 3 / 0 |
| B | 40/40 (100%) | 40/40 (100%) | 0.0%p | 40 / 0 / 0 / 0 |
| C | 0/40 (0%) | 39/40 (97.5%) | −97.5%p | 0 / 1 / 0 / 39 |
| D | 38/40 (95.0%) | 38/40 (95.0%) | 0.0%p | 38 / 2 / 0 / 0 |
| **전체** | **117/160 (73.1%)** | **153/160 (95.6%)** | **−22.5%p** | **114 / 4 / 3 / 39** |

전체 paired 결과에서 두 preset 모두 성공한 scenario는 114개, 모두 실패한 scenario는 4개였다. Full만 성공한 경우는 3개, packing 비활성만 성공한 경우는 39개다.

### Task family별 결과

| Task family | Full | Packing 비활성 | 차이 |
| --- | ---: | ---: | ---: |
| `file_lookup` (20개 기본 과제, 80 scenario) | 57/80 (71.3%) | 73/80 (91.3%) | −20.0%p |
| `approved_email` (20개 기본 과제, 80 scenario) | 60/80 (75.0%) | 80/80 (100%) | −25.0%p |

조건 C에서 파일 검색 Full 20건은 Critical Memory를 selector/context에 싣지 못해 잘못된 파일명을 넣었고, 승인 이메일 Full 20건은 첫 action이 예상 action과 달랐다. 파일 검색 예시에서는 Critical Memory가 검색 후보로 회수됐지만 selector 선택과 최종 Context에서 제외된 기록이 확인된다. 조건 C의 packing 비활성 실패 1건과 별도로, 나머지 첫 Tool 호출 누락은 Full 3건과 packing 비활성 6건이었다.

## 해석과 한계

- 결과는 별도 seed와 질문 템플릿의 holdout에서 preflight의 조건 C 경계가 실제 첫 행동 오류로 재현됐다는 점을 지지한다. 전체 차이 −22.5%p 중 조건 C의 차이가 −39건으로 가장 큰 기여를 했다.
- 일반 pipeline의 `task_success`는 두 preset 모두 0/160이다. 모든 실행이 `max_steps=1`에서 끝나기 때문에 이 값을 end-to-end 성공률로 읽을 수 없다. 본 보고서는 trace 기반의 단일 첫 행동 정확도만 비교한다.
- preset·scenario당 반복은 한 번뿐이고 독립 기본 과제는 40개(각 조건별 40 scenario가 같은 기본 과제를 공유)다. 신뢰구간이나 일반적인 효과 크기를 주장하지 않으며, 결과는 합성된 두 task family와 Freeze 1.0.4, 특정 모델·예산 구성에 한정한다.
- 조건 A에서 Full이 3건 더 성공하고 B·D에서는 차이가 없어, 조건 C 외의 결과도 포함해 해석해야 한다. 제약 위반은 320회에서 관측되지 않았지만, 이것만으로 다단계 실행의 안전성이나 작업 완료를 평가할 수는 없다.

## 산출물과 재현 정보

- 생성기: [critical_flexible_holdout.py](../../../benchmark/critical_flexible_holdout.py)
- Holdout 입력: `benchmark/generated/critical_flexible_holdout_v1/holdout_75_step1.json`
- 입력 검증: `benchmark/generated/critical_flexible_holdout_v1/holdout_75_step1_validation.json`
- 실행 원본·설정: `experiments/results/critical_flexible_holdout_75_v104/runs.jsonl`, `experiment.json`, `summary.json`
- First-action 평가: `experiments/results/critical_flexible_holdout_first_action_v104/first_action_summary.json`, `paired_first_action.csv`, `failure_details.csv`
- source dataset SHA-256: `54518bb0eaca2216094090174f567da5696ca234e671f2f647632bc6f186b733`
- budgeted dataset SHA-256: `37e8e9092bd155c3dcc35504f7c89f8c847c773d8c0a3b0956c6b8b1de1549dc`
- Agent 실행 commit: `b72fb6453401524a54a07cb0d8ec0d2ef3463c28` (clean)
