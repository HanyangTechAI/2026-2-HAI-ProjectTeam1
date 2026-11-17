# Task Criticality Safety Gate 오류 주입 후 실시간 재계획 평가 — 2026-11-17

## 요약

양성 file lookup·approved email Holdout에 첫 Tool 인자 오류를 결정적으로 주입하고, Gate가 오류 행동을 막은 뒤 Gemini가 다시 계획해 회복하는지 평가했다. Main 후보 80개 중 최초 Gemini 제안이 gold action과 일치한 78개에 오류를 주입했다. 이 78개에서 Gate ON은 잘못된 첫 호출 78/78을 Tool 실행 전에 막았고, 후속 계획에서 올바른 mock Tool 효과 78/78을 회복했다. 최종 goal 완료는 77/78 (98.7%)이었으며, 저장된 base-case cluster bootstrap 95% CI는 96.1%–100%다.

같은 오류를 Gate 없이 전달한 arm은 올바른 Tool 효과와 goal 완료가 각각 38/78이었다. 오류가 없는 Gate ON 대조 arm은 정답 Tool 효과 78/80, goal 완료 77/80이었다. 두 이탈 proposal은 오류 주입 대상에서 제외했으며 대조 arm에는 포함했다.

이는 정해진 인자 오류를 주입한 양성 synthetic 작업의 controlled recovery 시험이다. 자연 발생 오류율, 부정 시나리오, 실제 파일·이메일 side effect, 운영 환경의 일반 안전성을 측정하지 않았다.

## 데이터와 방법

| 항목 | 설정 |
|---|---|
| Holdout dataset | seed 1500–1519, 20개 기본 과제 × file lookup·approved email × S2/S3_VALID = 80개 양성 scenario |
| Template | task_criticality_independent_holdout_v1 |
| Smoke dataset | seed 1300–1301, 양성 8개 scenario; 전체 생성 dataset 16개 중 S2/S3_VALID 선택 |
| 최초 행동 | scenario당 Gemini 제안을 한 번 생성해 arm 간 공유 |
| 오류 주입 | 선택된 최초 Tool action의 인자를 결정적으로 변형 |
| 후속 행동 | 실제 Gemini가 새로 생성; 최대 8 steps |
| 예산 / Gate | step별 입력 예산 2,048 tokens; Gate action rejection 최대 2회 |
| 비교 arm | clean_on: 오류 없는 제안 + Gate; fault_on: 변형된 제안 + Gate; fault_off: 같은 변형 제안 + Gate 비활성 |
| 실행 설정 | Freeze 1.0.6, experiments/configs/ablation.json |
| 모델 | Google Gemini gemini-3.1-flash-lite, temperature 0, 출력 상한 2048, thinking LOW |
| Tool / 평가 | 모의 File·Email Tool; gold는 proposal 선택 또는 Planner 입력에 전달하지 않고 오류 대상 선정과 결과 평가에 사용 |

80개 모두 clean_on을 실행했다. 최초 제안이 정답인 78개만 fault_on과 fault_off를 실행했고, 최초 제안이 부적합한 2개는 오류 주입 arm에서 제외했다. 이에 따라 Main 결과는 240회가 아니라 236회(80 + 78 + 78)다. Smoke도 후보 8개 모두 적합해 24회 실행했다.

## Main 결과

| Arm | 분모 | 올바른 mock Tool 효과 | 최종 goal 완료 | 최초 변형 행동 차단 |
|---|---:|---:|---:|---:|
| clean_on | 80 | 78/80 (97.5%) | 77/80 (96.3%) | 해당 없음 |
| fault_off | 78 | 38/78 (48.7%) | 38/78 (48.7%) | 0/78 |
| fault_on | 78 | 78/78 (100%) | 77/78 (98.7%) | 78/78 |

fault_on의 78개 모든 변형 action은 첫 Tool 실행 전에 Gate가 차단했다. 그 뒤 새 Gemini 행동으로 78개 모두 올바른 Tool 효과를 냈고, 77개에서 최종 goal checker를 통과했다. 한 run은 Tool 효과를 얻었지만 최종 goal 완료 조건을 충족하지 않았다.

fault_off에서는 변형된 첫 Tool action이 모의 Tool에 전달됐다. 실행 전체에서 올바른 Tool 효과와 최종 goal 완료는 각각 38/78이었다. 이 비교는 같은 최초 proposal과 결정적 변형을 공유하고, 후속 행동은 arm별 Gemini 호출로 생성한다.

## Smoke 결과

| Arm | runs | 올바른 mock Tool 효과 | goal 완료 | 최초 변형 행동 차단 |
|---|---:|---:|---:|---:|
| clean_on | 8 | 8/8 | 8/8 | 해당 없음 |
| fault_off | 8 | 4/8 | 4/8 | 0/8 |
| fault_on | 8 | 8/8 | 7/8 | 8/8 |

Smoke의 오류 후 goal 완료는 7/8이며 95% cluster bootstrap CI는 62.5%–100%다. 이 8개는 실행 준비용 작은 표본으로 Main에 합산하지 않았다.

## 예산과 모델 사용량

Main 236개 run의 pipeline status는 모두 ok였고 예산으로 중단된 run은 0개였다. prompt audit은 547건, 실제 prompt 최댓값은 1,257 tokens로 고정 2,048-token 한도 안에 있었다.

Main 사용량 합계는 API call 391회, 기록된 추정 비용 USD 0.24246125다. 비용 집계는 공유한 최초 proposal을 arm마다 중복 계산하지 않았고, 청구 금액은 아니다. Smoke는 API call 40회, 추정 비용 USD 0.0246045였다.

## 해석 범위와 한계

- Main 80개는 file lookup과 approved email의 양성 S2/S3_VALID 변형만 포함한다. 이 결과로 S3_EMPTY, S5 또는 일반 부정 요청에서의 차단·오차단 비율을 추정하지 않는다.
- 78개 오류 대상은 모두 최초 Gemini 제안이 정답인 사례다. 오류 후 recovery 효과는 조건화된 controlled denominator 78을 기준으로 하며 최초 행동 제안의 일반 정확도를 나타내지 않는다.
- fault_off에서 도구 실행은 mock 환경에서만 일어났다. 실제 파일을 변경하거나 이메일을 보낸 실행은 없다.
- arm별 후속 LLM 호출이 새로 생성되며 각 scenario는 한 번만 실행했다. 결과는 이 오류 규칙·시드·모델 호출에 대한 관측이며 장기 반복 변동을 추정하지 않는다.
- 이 시험은 Safety Gate의 오류 차단 후 재계획을 평가한다. Memory 선택 정책이나 전체 ablation 성능의 비교 결과가 아니다.

## 산출물과 재현

- Main dataset: [recovery_holdout_1500](../../../benchmark/generated/recovery_holdout_1500/)
- Smoke dataset: [recovery_smoke_1300](../../../benchmark/generated/recovery_smoke_1300/)
- Main proposals·runs·summary·manifest: [criticality_recovery_full_v1](../../../experiments/results/criticality_recovery_full_v1/)
- Smoke 산출물: [criticality_recovery_smoke_v1](../../../experiments/results/criticality_recovery_smoke_v1/)
- Runner: [criticality_recovery.py](../../../benchmark/criticality_recovery.py)

기존 dataset을 사용한 Main replay 명령은 다음과 같다. 최초 proposal 및 후속 재계획에 Gemini API 비용이 발생하므로 새 output 경로를 사용한다.

    python -m benchmark.criticality_recovery --dataset benchmark/generated/recovery_holdout_1500 --config experiments/configs/ablation.json --fixed-budget 2048 --output experiments/results/criticality_recovery_full_repro

Main manifest의 commit은 49ea20a09510288d450e3ce1cb9ab4c5859fd6c2이며 실행 source fingerprint는 manifest에 보존되어 있다.

| 입력 | SHA-256 |
|---|---|
| Dataset | 51d6ad24c213069fbd84735daeb45b4e7df89c02a0605d2a9f7c1893cd406cc4 |
| Labels | d022814dc34a3c03faffbd76d1896f7ef40b86cda2b6d7d70e23b4695d77c553 |
| Config | 3d5a48cd434238f4ff61506f5a994014d85902886e1d410aeaa396ff1b298800 |
