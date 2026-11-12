# Critical Flexible 단일 Step Agent Pilot — 2026-11-12

## 요약

라벨이 있는 Critical Flexible 데이터로 75% 예산에서 실제 Gemini Agent의 **첫 선택과 첫 Tool 행동**을 탐색적으로 확인했다. 32개 시나리오를 두 preset으로 실행해 64개 run을 모두 완료했다. `full`은 조건 C의 8/32회에서 Critical Memory를 선택하지 못했고, `no_budget_aware_packing`은 0/32회였다. Expected first Tool call은 각각 24/32회와 32/32회에서 관측됐다.

다만 `max_steps=1` override 때문에 모든 run이 `goal_completed=false`로 끝났다. 그러므로 두 preset 모두의 Task success 0/32는 최종 작업 성공률 비교 결과가 아니다. 이 실행은 첫 step의 기억 선택·행동을 보는 pilot으로만 해석한다.

## 설계와 실행 범위

| 항목 | 설정 |
|---|---|
| freeze / commit | 1.0.4 / `62e5d970c72872adcb65f45565db564f973a6dfb` (clean) |
| 시나리오 | `benchmark/generated/critical_flexible_v1/pilot_75_step1.json`; seed 42–45, 두 과제 유형 × 조건 A–D = 32개 |
| 예산 | 각 scenario의 mandatory floor–all-flexible ceiling 사이 75% 수준, request safety margin 32 tokens |
| 비교 | `proposed/full` 대 `proposed/no_budget_aware_packing` |
| 실행 | preset당 32회, repeat 1, 총 64회; `max_steps=1` |
| Agent | Gemini `gemini-3.1-flash-lite`, temperature 0; embedder revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |

Pilot 입력은 사전 검증된 Critical Flexible 데이터 중 4개 seed만 사용한다. 한 기본 과제마다 조건 A–D를 만들었으며, 조건 C가 긴 Critical과 짧은 distractor를 결합한다.

## 결과

| 첫 step 지표 | Full | Packing 비활성 |
|---|---:|---:|
| 완료 run | 32/32 | 32/32 |
| Evaluator Task success | 0/32 | 0/32 |
| Expected Tool call 포함 | 24/32 | 32/32 |
| Critical target 선택 실패 | 8/32 | 0/32 |
| Budget selection omission | 8/32 | 0/32 |
| Required State 정확도 (`approved_email`, n=16) | 12/16 (75%) | 16/16 (100%) |
| Constraint violation (`approved_email`, n=16) | 0/16 | 0/16 |

선택 실패 8건은 모두 Full의 조건 C에서 발생했다. 4개 seed의 `file_lookup`에서는 핵심 파일명을 context에 넣지 못해 정답 파일명을 인자로 하는 Expected Tool call이 없었다. `approved_email`에서는 Required State 누락 4건이 함께 발생했다. 같은 8개 `no_budget_aware_packing` run은 Expected Tool call을 포함했고 Critical target도 선택했다.

그 외 56개 run도 `reasoning_action_failure`로 집계됐지만, 이를 곧바로 추론 오류로 볼 수는 없다. 64/64회가 `stopped_reason=max_steps`, `goal_completed=false`였으며, 첫 Tool 결과를 받은 뒤 작업 완료를 명시할 다음 Agent step이 없었다. 예를 들어 file lookup과 승인된 이메일의 첫 Tool 호출 및 도구 결과가 성공으로 기록된 run도 `max_steps=1`에서 평가상 Task success가 되지 않았다.

두 preset 모두 16개 승인 이메일 run에서 Constraint violation은 없었다. 평균 첫-step 입력은 Full 893.2 tokens, packing 비활성 913.5 tokens였고, 추정 생성 비용은 run당 각각 약 `$0.000591`, `$0.000607`이었다. 한 번씩 실행한 탐색 결과이므로 이 차이로 성능·비용 우위를 주장하지 않는다.

## 해석과 다음 단계

Preflight에서 관측한 75%·조건 C의 Context 선택 차이가 Agent의 첫 행동에서도 나타났다. 이 pilot에서 확인된 실질적인 정보는 Full preset의 8개 Critical omission과 그에 따른 첫 Tool call 누락이다. 이후 별도 seed·템플릿의 160-pair holdout에서도 조건 C의 큰 첫 행동 격차가 재현됐다. 반면 이 pilot과 holdout 모두 최종 작업 완료, 여러 단계 Tool 흐름, 장기 성공률은 측정하지 못했다. 후속 비교는 [75% Holdout 보고서](critical-flexible-holdout-2026-11-12.md)를 참고한다.

다음 end-to-end 평가에서는 동일한 고정 입력과 두 preset을 유지하면서 `max_steps`를 충분히 늘려 Tool 결과 후 Agent가 완료를 판단할 수 있게 해야 한다. 기본 과제별 반복도 추가해야 하며, single-step pilot의 0/32 성공률을 후속 결과와 합산하면 안 된다.

## 산출물과 재현

원본 실행은 `experiments/results/critical_flexible_pilot_75_v104/`에 있다. `experiment.json`은 freeze·모델·prompt checksum·override를, `progress.json`과 `summary.json`은 집계를, `runs.jsonl`은 run별 Tool 호출·선택·평가 기록을 보존한다.

아래는 동일한 파일과 설정으로 별도 결과 경로에 재실행하는 명령이다. Agent generation을 호출하므로 API 사용량이 발생한다.

```powershell
python -m benchmark.runner --config experiments/configs/ablation.json --scenarios benchmark/generated/critical_flexible_v1/pilot_75_step1.json --strategies proposed --proposed-ablations full no_budget_aware_packing --repeats 1 --max-steps 1 --output experiments/results/critical_flexible_pilot_75_v104_repro --checkpoint-interval 1
```
