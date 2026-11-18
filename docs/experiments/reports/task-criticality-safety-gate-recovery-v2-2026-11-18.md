# Task Criticality Safety Gate 오류 주입 복구와 Tool 실패 재시도 비교 — 2026-11-18

## 요약

양성 Task Criticality Holdout에서 Gemini의 최초 Tool proposal을 공유하고, 정답 proposal의 인자 하나를 결정적으로 바꿔 Safety Gate와 Tool 실패 후 재계획을 비교했다. Main 후보 80개 중 최초 제안이 gold와 일치한 79개를 오류 주입 비교에 포함했다. Gate ON은 잘못된 첫 Tool 호출을 79/79 차단했고, 후속 Gemini 계획으로 올바른 mock Tool 효과와 최종 goal을 각각 79/79 회복했다. Gate OFF에서 실패한 Tool 호출 뒤 최대 한 번 새 계획을 허용한 arm도 최종 효과와 goal을 79/79 달성했다. 다만 이 arm은 변형된 첫 호출을 79/79 실제 Tool에 전달했고, 그중 40건은 Tool 실패를 냈다.

따라서 이 실험에서 Gate와 재계획은 최종 목표 완료율이 같았지만, 첫 오류 호출의 Tool 도달 여부는 달랐다. Gate는 첫 호출을 막았고, 재시도는 호출 뒤 실패를 받아 복구했다. Gate 없는 무재시도 arm의 올바른 Tool 효과와 goal 완료는 39/79였다. 이 결과는 synthetic positive holdout과 mock Tool의 controlled fault injection 평가이며 실제 외부 side effect나 일반 환경의 안전성을 측정하지 않았다.

## 데이터와 방법

| 항목 | 설정 |
|---|---|
| Main 후보 | seed 1700–1719에서 생성된 양성 file lookup·approved email 80 scenarios; S2 및 S3_VALID만 선택 |
| Smoke 후보 | seed 1600–1601에서 생성된 동일 유형의 양성 scenario 8개 |
| 최초 proposal | scenario마다 Gemini 첫 행동 1개를 생성하고 모든 arm에서 공유 |
| 주입 오류 | file lookup의 filename 또는 approved email의 approval_id를 결정적으로 변형 |
| 후속 계획 | 실제 Gemini가 Tool feedback을 포함한 새 Context를 받고 다시 계획; 최대 8 steps |
| Arms | clean_on: 오류 없는 Gate ON 대조; fault_on: 변형 호출을 Gate로 차단; fault_off: Gate OFF·Tool 실패 재시도 없음; fault_off_retry: Gate OFF·Tool 실패 후 추가 계획 최대 1회 |
| 실행 설정 | Freeze 1.0.6, `experiments/configs/ablation.json`, Gemini 3.1 Flash Lite, temperature 0, thinking LOW, 출력 상한 2,048 |
| 단계 제한 | 매 step 입력 예산 2,048 tokens, 최대 8 steps, Gate action rejection 최대 2회 |
| Tool과 평가 | 모의 File·Email Tool; 정답 라벨은 오류 주입 대상 선정과 사후 평가에만 사용 |

최초 Gemini proposal이 정답이었던 79개에만 fault arm을 실행했다. 부적합 최초 제안 1개는 clean_on 대조에는 남겼지만 fault arm에는 넣지 않았다. 그러므로 Main은 80 + 79 × 3 = 317 runs다. Gate 오류 차단 arm과 재시도 arm의 동일 79개 pair에서는 양쪽 모두 goal을 완료했지만, 재시도 arm은 잘못된 첫 action을 실행했다.

`fault_off_retry`는 같은 Tool call을 그대로 반복하는 설정이 아니다. 단일 Tool call이 실패하면 최대 한 번 Orchestrator가 최신 Tool feedback을 담은 Context로 다시 계획한다. 파일 이름 오류는 mock File Tool에서 `found=false`라는 정상 결과로 돌아와 일반적인 피드백 기반 재계획을 거쳤다. 승인 ID 오류 40건은 Tool 실패로 기록됐고, 한 번의 재계획 허용 arm에서 모두 복구됐다.

## Main 결과

| Arm | runs | 올바른 mock Tool 효과 | 최종 goal 완료 | 변형 첫 action 차단 | 변형 첫 action 실행 | Tool 실패 건수 |
|---|---:|---:|---:|---:|---:|---:|
| clean_on | 80 | 79/80 (98.8%) | 79/80 (98.8%) | 해당 없음 | 해당 없음 | 0 |
| fault_on | 79 | 79/79 (100%) | 79/79 (100%) | 79/79 | 0/79 | 0 |
| fault_off | 79 | 39/79 (49.4%) | 39/79 (49.4%) | 0/79 | 79/79 | 40 |
| fault_off_retry | 79 | 79/79 (100%) | 79/79 (100%) | 0/79 | 79/79 | 40, 재계획 후 복구 |

fault_on의 저장된 base-case cluster bootstrap 95% CI는 goal 완료율 100%–100%다. fault_on과 fault_off_retry는 79개 pair 모두 goal 성공이었으며 paired goal 차이는 0이다. 차이는 안전 경로에 있다. Gate ON은 잘못된 최초 Tool 호출을 한 번도 실행하지 않았고, retry arm은 79개 모두 실행했으며 승인 ID 오류로 인한 40개 Tool 실패 뒤 재계획했다.

fault_off의 40개 `tool_failure` 종료는 모두 승인 ID가 변형된 approved email 사례였다. 나머지 39개 file lookup에서는 잘못된 filename에 대해 Tool이 성공 상태와 `found=false`를 반환했고, 일반적인 결과 피드백 후 올바른 효과를 얻었다. 재시도 arm은 승인 ID Tool 실패 뒤 새 계획을 허용해 40개 모두 회복했다.

## Smoke 결과

| Arm | runs | 올바른 mock Tool 효과 | 최종 goal 완료 | 변형 첫 action 차단 | 변형 첫 action 실행 | Tool 실패 건수 |
|---|---:|---:|---:|---:|---:|---:|
| clean_on | 8 | 8/8 | 8/8 | 해당 없음 | 해당 없음 | 0 |
| fault_on | 8 | 8/8 | 8/8 | 8/8 | 0/8 | 0 |
| fault_off | 8 | 4/8 | 4/8 | 0/8 | 8/8 | 4 |
| fault_off_retry | 8 | 8/8 | 8/8 | 0/8 | 8/8 | 4, 재계획 후 복구 |

Smoke는 Main과 합산하지 않았다. 표본 8개에서 Main과 같은 방향을 확인한 준비용 결과다.

## 예산과 사용량

Main 317개 run은 모두 `pipeline_status=ok`였으며, 예산 초과 중단은 0건이다. 단, fault_off 무재시도 arm의 40개 실행은 `stopped_reason=tool_failure`로 끝났다. prompt audit 793건의 실제 prompt 최댓값은 1,257 tokens로 고정 2,048-token 한도 내에 있었다. 사용량 집계는 API call 556회, 추정 비용 USD 0.35198이다. 공유한 최초 proposal 비용은 arm별로 중복 합산하지 않았다.

Smoke 32개 run에서는 prompt audit 81건, 실제 prompt 최댓값 1,238 tokens, budget stop 0건이었다. API call 57회와 추정 비용 USD 0.03514625를 기록했다. 비용은 provider 사용량 메타데이터에서 계산한 추정치이며 청구 금액이 아니다.

## 해석 범위와 한계

- 생성 dataset에는 S2, S3_EMPTY, S3_VALID, S5가 있지만 이 recovery runner는 양성 S2·S3_VALID file lookup 및 approved email 80개만 골라 평가했다. 부정 요청이나 S3_EMPTY·S5에서의 차단과 오차단을 추정하지 않는다.
- 오류 주입 분모 79는 최초 Gemini 제안이 이미 정답이었던 사례로 조건화되어 있다. 일반적인 첫 행동 정확도나 자연 오류율이 아니다.
- Gate와 재시도 arm 모두 최종 goal 79/79였으나, Gate만 변형 첫 action을 Tool에 전달하기 전에 차단했다. 이 결과를 실제 서비스의 side effect 감소율로 일반화할 수 없다.
- Tool은 전부 mock이다. 실제 이메일 발송이나 파일 변경은 없었다.
- 단일 synthetic seed 구간과 1회 실행을 사용했다. 모델·데이터 분포 변화, 반복 실행 변동, latency trade-off는 평가하지 않았다.
- 재시도 arm은 명시적인 Tool failure 이후 재계획하는 경로다. Tool이 성공 상태로 반환한 잘못된 결과는 Gate가 아니라 일반적인 Tool feedback에 따라 후속 계획에서 회복될 수 있다.

## 산출물과 재현

- Main dataset: [recovery_v2_holdout](../../../benchmark/generated/recovery_v2_holdout/)
- Smoke dataset: [recovery_v2_smoke](../../../benchmark/generated/recovery_v2_smoke/)
- Main proposal·runs·summary·manifest: [recovery_v2_full](../../../experiments/results/recovery_v2_full/)
- Smoke 산출물: [recovery_v2_smoke 결과](../../../experiments/results/recovery_v2_smoke/)
- Runner: [criticality_recovery.py](../../../benchmark/criticality_recovery.py)
- 이전 v1 결과: [2026-11-17 recovery 평가](task-criticality-safety-gate-recovery-2026-11-17.md)

새 output 경로로 재실행하면 Gemini proposal과 후속 계획 호출이 새로 생성되어 API 비용이 발생한다.

    python -m benchmark.criticality_recovery --dataset benchmark/generated/recovery_v2_holdout --config experiments/configs/ablation.json --fixed-budget 2048 --output experiments/results/recovery_v2_full_repro

Main manifest에는 실행 commit과 전체 source fingerprint가 함께 기록돼 있다. 기록된 commit은 `820a0f4541c4c0d9648e0f01d44d985773072a4f`다.

| 입력 | SHA-256 |
|---|---|
| Main dataset | `b7f889241ac68ea9c080a24f7f17437c532e153a5abed74bfb1d1ff1d91c599e` |
| Main labels | `ba0f149e7bbef3b877edd98f98005a12c0a45f07fa82a1e87422cd835c048aab` |
| Config | `3d5a48cd434238f4ff61506f5a994014d85902886e1d410aeaa396ff1b298800` |
| Smoke dataset | `2b999320cd05995632562288d08aa8d31e616e0e384bd460fa575be3800905cf` |
| Smoke labels | `52a0cb20f68fb76bca981a681230d448342c5054dab10a6e8b281302e7ef34c9` |