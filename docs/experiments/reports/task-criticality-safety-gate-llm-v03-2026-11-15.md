# Task Criticality Safety Gate v0.3 실제 LLM 첫 행동 평가 — 2026-11-15

## 요약

Freeze 1.0.5의 75% Task Criticality Context에서 Gemini가 생성한 첫 행동을 저장한 뒤, 같은 행동을 Gate OFF와 ON으로 각각 재생했다. 본 평가의 160개 scenario에서 LLM은 정답 Tool 행동 80개를 모두 제안했고, 정보가 없거나 상충하는 80개 중 20개에는 안전한 승인 요청을, 나머지 60개에는 위험한 Tool 행동을 제안했다. Gate ON은 위험한 호출 60개를 모두 실행 전에 막고 승인 요청 20개와 정답 행동 80개를 허용했다. 정답 행동을 잘못 막은 사례는 없었다.

이는 실제 LLM이 제안한 행동에 대한 첫 step·모의 Tool paired replay 결과다. 기존 offline 평가의 고정 `ScriptedPlanner`와 달리 모델 제안을 사용했지만, 같은 synthetic stress dataset을 재사용했고 `max_steps=1`로 제한했다. 따라서 독립 holdout, end-to-end Task 성공률 또는 일반적인 운영 안전성으로 해석하지 않는다.

## 설계와 계측

| 항목 | 설정 |
|---|---|
| 데이터 | `task_criticality_s235_v1`, 160 scenario: S2·S3_VALID·S3_EMPTY·S5 각 40개; file lookup 및 approved email 두 family |
| 예산 | 각 scenario의 Mandatory floor와 all-flexible ceiling 사이 75% |
| 모델 | Google Gemini `gemini-3.1-flash-lite`, temperature 0, 출력 상한 2048, thinking `LOW` |
| Freeze / config | Freeze 1.0.5; `experiments/configs/ablation.json` |
| 제안 생성 | scenario마다 LLM 첫 행동 1회 생성, 160 proposal 저장 |
| 비교 | 각 저장 proposal을 Gate OFF/ON으로 각각 재생: 320 pipeline run; paired 조건 사이 Context·행동 제안은 동일 |
| 실행 제한 | `max_steps=1`; FileTool·EmailTool 모의 executor 사용, 외부 파일·이메일 효과 없음 |
| 임베딩 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, resolved revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| 검증 | 본 실행 0 context drift, 0 budget failure, 320/320 pipeline status `ok` |

먼저 별도 smoke 경로에서 네 변형 각 2개씩 총 8 scenario를 확인했다. 8개 제안을 생성해 16회 paired replay를 수행했고, 위험 제안 3개를 모두 차단했으며 정답 행동을 잘못 막지 않았다. Smoke는 실행 준비 점검이며 본 결과와 합산하지 않는다.

## 본 실행 결과

| 변형 | 제안 수 | 정답 Tool 행동 | 안전한 승인 요청 | 위험 제안 | OFF 위험 실행 | ON 차단 | ON 오차단 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S2 | 40 | 40 | 0 | 0 | 0 | 0 | 0 |
| S3_VALID | 40 | 40 | 0 | 0 | 0 | 0 | 0 |
| S3_EMPTY | 40 | 0 | 20 | 20 | 20 | 20 | 0 |
| S5 | 40 | 0 | 0 | 40 | 40 | 40 | 0 |
| **전체** | **160** | **80** | **20** | **60** | **60** | **60** | **0** |

| Gate 조건 | Tool 실행 | 그중 위험 실행 | 안전한 승인 요청 실행 | 정답 효과 확인 | 정답 제안 오차단 |
|---|---:|---:|---:|---:|---:|
| OFF | 160 | 60 | 20 | 80/80 | — |
| ON | 100 | 0 | 20 | 80/80 | 0 |

Tool 실행 합계에는 안전하게 승인을 요청한 `email.request_approval` 20건이 들어간다. Gate ON의 차단 이벤트는 `slot_not_valid_candidate`였고 `file.find_file` 40건, `email.send_email` 20건으로 기록됐다. S3_EMPTY의 20개 승인 이메일 요청은 위험한 발송으로 바뀌지 않고 두 조건에서 모두 유지됐다. 모델은 160회 모두 첫 행동을 제안했으며 모델 abstention은 없었다.

## Smoke와 본 실행의 계측

| 결과 폴더 | Scenario / paired replay | Gemini generation | 검증 비용 추정치 | CountTokens API 호출 / retry |
|---|---:|---:|---:|---:|
| `criticality_llm_v03_smoke/` | 8 / 16 | 8 | USD 0.0044755 | 12 / 0 |
| `criticality_llm_v03_main/` | 160 / 320 | 160 | USD 0.090242 | 235 / 0 |

비용은 proposal metadata의 모델 요금표 기반 추정치이며 청구서 확인값이 아니다. 모델 generation 호출 수와 tokenizer `countTokens` API 호출 수는 별도 계측이다.

## 해석과 한계

- Gate OFF는 위험 제안 60개를 실행했고 ON은 모두 차단했다. 이 차이는 이번 데이터에서 gate의 실행 직전 검사가 작동한 증거이며, LLM이 항상 위험 제안을 만든다거나 gate가 모든 위험을 잡는다는 의미는 아니다.
- 같은 160개 synthetic stress scenario와 label을 이전 선택 preflight 및 offline gate 평가에서 재사용했다. 새 seed/template의 독립 holdout이 아니다.
- 한 scenario당 생성된 첫 행동 하나만 평가했고 각 replay는 `max_steps=1`이다. 차단 후 재계획·회복, multi-step 목표 완료, 최종 Task Success Rate는 측정하지 않았다.
- 파일·이메일 executor는 모의 구현이다. 실제 이메일 발송, 파일 변경 또는 외부 부작용을 평가하지 않았다.
- Safety Gate는 실험용 `CriticalityGatedAdapter` 경로다. 기본 `select_memory()`와 기본 Proposed Runner의 동작이나 보장으로 일반화하지 않는다.
- 평가에 사용한 데이터를 바꾸지 않고 saved proposal을 OFF/ON 양쪽에 재생했다. 따라서 비교는 paired지만, 모델 제안의 분산·반복 간 안정성을 추정하지 않는다.

## 원본 산출물과 재현 정보

- Smoke: [`experiments/results/criticality_llm_v03_smoke/`](../../../experiments/results/criticality_llm_v03_smoke/) — `experiment_manifest.json`, `llm_proposals.jsonl`, paired `first_action_details.csv`, `first_action_summary.csv`, `validation.json`.
- Main: [`experiments/results/criticality_llm_v03_main/`](../../../experiments/results/criticality_llm_v03_main/) — 같은 산출물 구성.
- 데이터: [`task_criticality_s235_v1`](../../../benchmark/generated/task_criticality_s235_v1/); config: [`ablation.json`](../../../experiments/configs/ablation.json).
- 실행 코드: [`criticality_llm_first_action.py`](../../../benchmark/criticality_llm_first_action.py); 실행 gate: [`experimental_safety_gate.py`](../../../pipeline/experimental_safety_gate.py).
- 재현은 propose 단계에서 실제 Gemini 호출이 발생하며 과금될 수 있다. 동일한 API 설정·환경을 준비한 뒤 다음처럼 proposal 생성과 paired replay를 분리한다.

```powershell
python -m benchmark.criticality_llm_first_action `
  --phase propose `
  --dataset benchmark/generated/task_criticality_s235_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/criticality_llm_v03_main

python -m benchmark.criticality_llm_first_action `
  --phase replay `
  --dataset benchmark/generated/task_criticality_s235_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/criticality_llm_v03_main
```

Smoke 재현은 두 명령 모두에 `--max-cases 8`을 추가하고 output을 `experiments/results/criticality_llm_v03_smoke`로 바꾼다. 실행 validation에 기록된 데이터 SHA-256은 `3a5b8980ac0ae29fc5231c7738e059d5cbf8aea1e332cfcb5c4190f4cb113534`, label SHA-256은 `45e8b67e0f4e3d37ebeb3e1cbf920003863c08b233a59f5d3b233c52b3192eb7`, config SHA-256은 `f6eb570d93b9e383ca3267d2ca081cb2f0b6187774df398ea5959aa459d9af52`다. 실행 시각은 smoke 2026-11-15 22:23, main 2026-11-15 22:35 (KST)이다.

| 실행 코드 / 구성 | SHA-256 |
|---|---|
| `benchmark/criticality_llm_first_action.py` | `822941e3f25676d4849ef68dbde3758c731a253360ecafa16ed216c7149f1afc` |
| `benchmark/action_time_preflight.py` | `6d4b5d14c8ee5a71b7e0523546df3ba543c554add84b57b7ba1471d3b4de7978` |
| `memory/task_criticality.py` | `b963d6f81782685e417862482a6820bdf163646e8ea86a08cdbce68ca6435573` |
| `memory/criticality_policy.py` | `336c3414b1a5ae9dc842a4cc74b31e3c8cb73a9069e09b3c569c8fa93696f94e` |
| `pipeline/experimental_safety_gate.py` | `79086cbe4750cfd93dc4cc2717c0d7482656eddb182566de6356914d825bcda6` |
