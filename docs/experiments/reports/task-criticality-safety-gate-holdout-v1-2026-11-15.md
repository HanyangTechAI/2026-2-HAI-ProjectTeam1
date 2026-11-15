# Task Criticality Safety Gate v0.3 독립 Holdout LLM 평가 — 2026-11-15

## 요약

별도 seed와 holdout 전용 질문·근거 템플릿으로 만든 160개 synthetic scenario에서 Gemini 첫 행동을 생성하고, 같은 저장 proposal을 Gate OFF/ON으로 각각 재생했다. 두 조건에서 Tool 실행 전 Context와 제안은 같았다. 모델은 정답 행동 80개 중 78개를 정확히 제안·실행했고, 2개는 `file_lookup / S3_VALID`에서 Tool 없이 응답했다. 부정 사례 80개 중 20개에는 안전한 승인 요청을, 60개에는 위험한 Tool 행동을 제안했다.

Gate OFF에서는 위험한 행동 60개가 실행됐다. Gate ON은 그 60개를 모두 차단했고 안전한 승인 요청 20개와 정답 효과 78개를 허용했다. 정답 행동 오차단은 0건이었다. 따라서 이 독립 holdout에서 Gate가 고정된 첫 행동을 실행 전에 걸러낸 결과를 확인했지만, 최종 Task 완료, 다중 step 회복 또는 실제 외부 Tool 안전성은 측정하지 않았다.

## Holdout 데이터

| 항목 | 설정 |
|---|---|
| 생성기 | [`benchmark/task_criticality_holdout.py`](../../../benchmark/task_criticality_holdout.py), template version `task_criticality_independent_holdout_v1` |
| Main | seed 1000–1019, file lookup·approved email 각 20개 기본 과제 × S2/S3_VALID/S3_EMPTY/S5 = 160 scenario |
| Smoke | seed 1200, 각 family 1개 기본 과제 × 네 변형 = 8 scenario; 실행 준비 점검이며 Main과 합산하지 않음 |
| 신규성 | 앞선 Task Criticality stress/Gate 데이터와 다른 seed 및 holdout 전용 질의·근거 문구; 공유 stress 생성기에서 scenario 구조를 가져와 holdout 템플릿으로 다시 작성 |
| 데이터 검증 | 생성 시 정답 근거, 무효 근거, S5 상충 slot, scenario/label 대응 및 token count를 검증; Runner validation에서 Context drift와 예산 실패 각각 0건 |
| Token count | Gemini `gemini-3.1-flash-lite` CountTokens, request-aware budgeting |

Holdout은 동일 synthetic benchmark 안에서 seed와 템플릿을 분리한 평가다. 실제 사용자 자료, 외부 조직 또는 자연 발생 분포를 대표하는 데이터는 아니다.

## 실행 설정

| 항목 | 설정 |
|---|---|
| Freeze / config | Freeze 1.0.5; `experiments/configs/ablation.json` |
| 모델 | Google Gemini `gemini-3.1-flash-lite`, temperature 0, 출력 상한 2048, thinking `LOW` |
| 예산 | Mandatory floor와 all-flexible ceiling 사이 75% |
| 제안 생성 | Main 160 Gemini calls; scenario마다 저장한 첫 행동 1개 |
| 비교 | 각 proposal을 Gate OFF/ON으로 paired replay, Main 320 pipeline runs |
| 실행 제한 | `max_steps=1`; 모의 FileTool·EmailTool 사용, 실제 파일 변경·이메일 발송 없음 |
| 임베딩 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, resolved revision `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| Main 검증 | 0 context drift, 0 selection budget failure, 320/320 pipeline status `ok` |

## Main 결과

| 변형 | 제안 수 | 정답 행동 제안·효과 | Tool abstention | 안전한 승인 요청 | 위험 제안 | OFF 위험 실행 | ON 차단 |
|---|---:|---:|---:|---:|---:|---:|---:|
| S2 | 40 | 40/40 | 0 | 0 | 0 | 0 | 0 |
| S3_VALID | 40 | 38/40 | 2 | 0 | 0 | 0 | 0 |
| S3_EMPTY | 40 | 해당 없음 | 0 | 20 | 20 | 20 | 20 |
| S5 | 40 | 해당 없음 | 0 | 0 | 40 | 40 | 40 |
| **전체** | **160** | **78/80** | **2** | **20** | **60** | **60** | **60** |

두 abstention은 서로 다른 `file_lookup / S3_VALID` scenario에서 발생했다. 모델은 파일명을 말했지만 Tool을 호출하지 않았다. Gate OFF/ON 모두 같은 saved proposal을 사용했으므로 두 조건에서 각각 한 번씩 기록됐지만, 서로 다른 실패 제안은 2개다.

| Gate 조건 | Tool 실행 합계 | 위험 Tool 실행 | 안전한 승인 요청 실행 | 정답 Tool 효과 | 정답 제안 오차단 |
|---|---:|---:|---:|---:|---:|
| OFF | 158 | 60 | 20 | 78/80 | — |
| ON | 98 | 0 | 20 | 78/80 | 0 |

Tool 실행 합계는 `email.request_approval` 20건을 포함한다. Gate ON의 60개 차단 이벤트는 `slot_not_valid_candidate`였으며, `file.find_file` 40건과 `email.send_email` 20건으로 기록됐다. Gate는 S3_VALID의 모델 abstention을 정답 행동으로 바꾸지 않았고, 잘못된 제안의 실행만 차단했다.

## Smoke 및 사용량

| 결과 폴더 | Scenario / paired replay | Gemini generation | 모델 비용 추정 | CountTokens API 호출 / retry |
|---|---:|---:|---:|---:|
| `criticality_holdout_smoke_v1/` | 8 / 16 | 8 | USD 0.0043675 | 12 / 0 |
| `criticality_holdout_v1/` | 160 / 320 | 160 | USD 0.0875775 | 233 / 0 |

Smoke에서는 정답 행동 4/4, 위험 제안 차단 3/3, 안전한 승인 요청 1건을 확인했고 오차단은 없었다. 본 결과와 합산하지 않는다. 비용은 proposal metadata의 모델 요금 기반 추정치이지 청구서 확인값은 아니다. CountTokens 호출 수와 Gemini generation 호출 수는 별도다.

## 해석 범위와 한계

- 데이터 seed와 질의·근거 템플릿은 앞선 v0.3 stress LLM 평가와 다르므로 해당 실행과의 직접 재사용은 피했다. 하지만 둘 다 같은 synthetic generator와 S2/S3/S5 benchmark 구조를 사용하므로 외부 분포 검증은 아니다.
- Main은 각 scenario당 Gemini 첫 행동 한 번, 각 replay당 한 step만 평가했다. Gate 차단 뒤 재계획, 반복 간 변동, 다중 step 목표 완료와 최종 Task Success Rate는 측정하지 않았다.
- S3_VALID 파일 lookup 2건의 abstention은 Gate의 오차단이 아니라 모델이 Tool을 호출하지 않은 결과다. 양성 사례 결과는 78/80이며 80/80으로 해석하지 않는다.
- FileTool·EmailTool은 모의 executor다. 60/60 차단은 이 dataset·저장 action·gate 구현에 대한 결과이며 실제 이메일, 파일 시스템 또는 임의 위험 행동에 대한 일반 안전 보장이 아니다.
- 결과 manifest의 source fingerprint는 LLM replay 및 gate 소스는 기록하지만 holdout dataset generator 소스는 포함하지 않는다. 아래에는 생성 dataset/label SHA를 기록하고, 현재 workspace의 generator SHA를 참고용으로 병기한다. 실행 commit은 manifest에 기록되어 있지 않다.

## 산출물과 재현

- Main dataset: [`benchmark/generated/task_criticality_holdout_v1/`](../../../benchmark/generated/task_criticality_holdout_v1/) — dataset, labels, manifest.
- Smoke dataset: [`benchmark/generated/task_criticality_holdout_smoke_v1/`](../../../benchmark/generated/task_criticality_holdout_smoke_v1/).
- Main 실행: [`experiments/results/criticality_holdout_v1/`](../../../experiments/results/criticality_holdout_v1/) — proposal JSONL, paired details·summary CSV, manifest, validation.
- Smoke 실행: [`experiments/results/criticality_holdout_smoke_v1/`](../../../experiments/results/criticality_holdout_smoke_v1/).
- Runner: [`criticality_llm_first_action.py`](../../../benchmark/criticality_llm_first_action.py); adapter: [`experimental_safety_gate.py`](../../../pipeline/experimental_safety_gate.py).

Main 데이터 생성·검증 및 paired replay 재현 명령은 다음과 같다. Dataset 생성 단계는 실제 Gemini CountTokens API를 사용하며, propose 단계에서는 Gemini generation 호출과 사용량에 따른 비용이 발생한다.

```powershell
python -m benchmark.task_criticality_holdout generate `
  --counter gemini `
  --seed-start 1000 `
  --cases-per-family 20 `
  --output benchmark/generated/task_criticality_holdout_v1/dataset.json

python -m benchmark.task_criticality_holdout validate `
  --counter gemini `
  --dataset benchmark/generated/task_criticality_holdout_v1/dataset.json

python -m benchmark.criticality_llm_first_action `
  --phase propose `
  --dataset benchmark/generated/task_criticality_holdout_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/criticality_holdout_v1

python -m benchmark.criticality_llm_first_action `
  --phase replay `
  --dataset benchmark/generated/task_criticality_holdout_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/criticality_holdout_v1
```

Smoke는 생성 시 `--seed-start 1200 --cases-per-family 1 --output benchmark/generated/task_criticality_holdout_smoke_v1/dataset.json`을 사용하고, validate/propose/replay에서 해당 dataset 경로를 지정한다. Proposal 단계와 replay 단계 모두 `--max-cases 8`을 전달하며 output은 `experiments/results/criticality_holdout_smoke_v1`로 설정한다.

| Artifact | SHA-256 |
|---|---|
| Main dataset | `b7e25a4719c7be25f4b0feed67b98ae4ecbb2b4c0929c41eae34f1d7fcc744e5` |
| Main labels | `fed0695c54947fd16c1138d2294f004503a2b77e84c7a720840d4e1edf1bf930` |
| Smoke dataset | `4bab5de9116c85a34f72f0ca99dd9ee37583bfafd6334a5a82685b9ae7dc2dc2` |
| Smoke labels | `3e421b7e0399e4d52c71961405f90adf3e89bc5056047d2c29f1ae3c3c52ff82` |
| Shared config | `f6eb570d93b9e383ca3267d2ca081cb2f0b6187774df398ea5959aa459d9af52` |
| Current holdout generator `benchmark/task_criticality_holdout.py`* | `a0aa84e2a010aab41c1395cde423e416439f116ba7879a067c5d9153578337f0` |
| Current shared generator `benchmark/task_criticality_stress.py`* | `d1f5fb3784d6f9c065bf1c77f07060de35141155866218b9b388cae965b30ff5` |

\* 결과 manifest가 아닌 현재 workspace에서 계산한 참고 fingerprint다.
