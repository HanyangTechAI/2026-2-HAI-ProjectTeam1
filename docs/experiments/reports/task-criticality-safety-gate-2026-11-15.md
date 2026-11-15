# Task Criticality Safety Gate v0.3 오프라인 평가 — 2026-11-15

## 요약

`CriticalityGatedAdapter`가 필수 근거가 없거나 상충하는 파일·승인 정보를 바탕으로 한 Tool 행동을 실행 전에 막는지 고정 스크립트 행동으로 점검했다. `task_criticality_s235_v1`의 160개 scenario에서 gate를 끈 조건과 켠 조건을 각각 실행했다. Freeze 1.0.4 결과와 approval-fix 재실행(Freeze 1.0.5) 모두 집계 결과는 같았다.

Gate를 끈 경우 정보가 비거나 상충하는 80개 scenario에서 잘못된 Tool 호출이 실행됐다. Gate를 켠 경우 이 호출 80개를 모두 ToolExecutor 도달 전에 막았고, 올바른 근거가 있는 나머지 80개 행동은 모두 허용해 기대한 Tool 효과를 확인했다. 이 평가는 고정 행동에 대한 실행 차단 검증이며 LLM 성능 또는 실제 Agent의 안전 행동률을 측정하지 않았다.

## 설계

| 항목 | 설정 |
|---|---|
| 데이터 | `task_criticality_s235_v1`, 독립 scenario 160개: S2 40, S3_VALID 40, S3_EMPTY 40, S5 40 |
| 비교 | 같은 scenario에서 gate OFF / ON, 각 160회; 결과 폴더별 320 pipeline runs |
| 예산 | scenario별 Mandatory floor와 all-flexible ceiling 사이의 75% |
| 행동 | 고정 `ScriptedPlanner`; 유효 label은 기대 Tool call, S3_EMPTY/S5는 의도적으로 근거 없는 행동을 반환 |
| 실행 | `max_steps=1`; 파일·이메일 ToolExecutor 앞에서 gate 검사 |
| LLM generation | 0회 |
| Token Count | Gemini 3.1 Flash-Lite의 request-aware CountTokens 사용; 각 validation에 API call 501회, retry 0회 기록 |
| 임베딩 | multilingual MiniLM-L12-v2, resolved revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| 데이터 / label SHA-256 | `3a5b8980ac0ae29fc5231c7738e059d5cbf8aea1e332cfcb5c4190f4cb113534` / `45e8b67e0f4e3d37ebeb3e1cbf920003863c08b233a59f5d3b233c52b3192eb7` |

Gate ON/OFF는 동일한 선택 결과와 Context token 수를 사용했다. 이 비교에서 Gate는 Context 선택을 바꾸지 않고, Planner가 낸 행동을 검사해 필요하면 abstain으로 바꾼다. 유효 행동은 최종 Context에 있는 고유한 slot 근거와 Tool 인자가 일치할 때만 통과한다.

## 결과

| 조건 | Scenario 수 | 실행된 잘못된 Tool 호출 | 잘못된 차단 | Tool 실행 | 기대 효과 확인 |
|---|---:|---:|---:|---:|---:|
| Gate OFF · Freeze 1.0.4 | 160 | 80 (50%) | 0 | 160 | 80 |
| Gate ON · Freeze 1.0.4 | 160 | 0 | 0 | 80 | 80 |
| Gate OFF · Freeze 1.0.5 | 160 | 80 (50%) | 0 | 160 | 80 |
| Gate ON · Freeze 1.0.5 | 160 | 0 | 0 | 80 | 80 |

Gate ON에서 허용된 80개는 S2와 S3_VALID 각 40개다. 정보 부재(S3_EMPTY)와 상충(S5) 각 40개는 모두 차단됐다. 따라서 이 고정 입력에서는 유효 행동 80/80회를 허용하고, 실행되면 안 되는 행동 80/80회를 막았다. `false_abstentions=0`은 이 테스트에서 label상 행동이 유효한 경우를 뜻한다.

## Freeze 1.0.5 재실행 차이

`task_criticality_s235_gate_v03/`와 `task_criticality_s235_gate_v03_approval_fix/`의 validation은 같은 데이터·label SHA를 기록하며 집계 CSV는 동일하다. 상세 CSV는 재실행에서 차단 원인 추적 필드가 추가됐다. 공통 상세 필드에서 달라진 40행은 승인 이메일의 S3_EMPTY/S5 사례뿐이다. 앞선 결과는 `attachment`의 `tool_argument_not_grounded`로 차단했고, Freeze 1.0.5 결과는 필수 `approval_id`의 `slot_not_valid_candidate`로 차단했다. 두 결과 모두 Tool 실행 전 차단이었으므로 집계 안전 지표는 바뀌지 않았다.

두 validation에서 Gate 구현과 criticality helper의 source SHA는 같고 evaluator SHA는 달랐다. 실행 commit SHA와 전체 config checksum은 validation에 저장되어 있지 않다. 이전 결과를 완전한 bit-for-bit 재현으로 간주하지 말고 각 결과 폴더의 `validation.json`과 상세 CSV를 기준으로 비교한다.

## 해석 범위와 한계

- `ScriptedPlanner`가 label에 맞춰 유효 행동 또는 의도적으로 잘못된 행동을 반환했다. 실제 LLM이 이 행동을 얼마나 자주 선택하는지는 측정하지 않았다.
- synthetic development 데이터 160개, 단일 실행 step, 파일 조회·승인 이메일 네 변형에 한정된다. 다중 step, 다른 Tool, 실제 사용자 입력, 일반화 성능은 검증하지 않았다.
- gate는 criticality helper의 slot 판정과 최종 Context에서 추출 가능한 근거에 의존한다. 정규식·제한된 cue가 틀린 경우까지 포괄하는 일반 안전 보장은 아니다.
- 따라서 80/80 차단은 이 테스트의 scripted action에 대한 결과다. LLM Task Success Rate나 운영 환경의 안전성 개선치로 보고하지 않는다.
- 이 adapter는 실험용 선택 경로다. 기본 `select_memory()` 또는 기본 Proposed Runner를 대체하지 않는다.

## 산출물과 재현

- Freeze 1.0.4 결과: `experiments/results/task_criticality_s235_gate_v03/`
- Freeze 1.0.5 approval-fix 재실행: `experiments/results/task_criticality_s235_gate_v03_approval_fix/`
- 각 폴더의 `safety_summary.csv`, `safety_details.csv`, `validation.json`에 집계, scenario별 gate 이벤트, 데이터 및 source hash가 있다.
- 실행 gate: [`pipeline/experimental_safety_gate.py`](../../../pipeline/experimental_safety_gate.py); 평가기: [`benchmark/criticality_safety_evaluator.py`](../../../benchmark/criticality_safety_evaluator.py).

현재 `experiments/configs/ablation.json`은 Freeze 1.0.5다. 새 출력 경로를 지정해 최신 설정으로 재실행할 수 있다.

```powershell
python -m benchmark.criticality_safety_evaluator `
  --dataset benchmark/generated/task_criticality_s235_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/task_criticality_s235_gate_v03_rerun
```

Freeze 1.0.4 실행은 해당 시점의 config와 evaluator를 사용한 별도 결과로 보존한다. 이 보고서는 [S2/S3/S5 v0.2 선택 Preflight](task-criticality-s235-v02-preflight-2026-11-13.md)의 후속이며, helper의 선택 결과와 실행 직전 gate의 행동 통제를 구분한다.
