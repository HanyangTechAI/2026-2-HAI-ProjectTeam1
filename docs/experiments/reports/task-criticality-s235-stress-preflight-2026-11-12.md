# Task Criticality S2/S3/S5 Stress Preflight — 2026-11-12

## 요약

파일 검색과 승인 이메일의 핵심 정보 추출을 겨냥한 합성 스트레스 데이터 160개를 만들고, `full`, `no_packing`, 실험용 `criticality` 선택 정책을 비교했다. 총 480개 Context를 평가했으며 LLM generation은 실행하지 않았다.

`criticality`는 중요도만으로는 놓친 정보가 있는 S2에서 gold slot을 40/40회 승격했다. S3의 유효 정보가 있는 변형에서는 gold를 40/40회 Context에 포함했고, 유효 정보가 없는 변형에서는 missing slot을 40/40회 감지했다. S5의 상충 정보도 40/40회 감지했다. 다만 S3에서 무효 후보가 Context에 계속 포함됐고, S5의 상충 정보 두 개도 모두 Context에 남았다. 이 판정은 Agent나 Tool 실행을 차단하지 않는다.

따라서 이 실험은 슬롯 근거·모호성 판정이 만들어내는 **선택 신호**를 확인한 개발용 모듈 평가다. Agent가 이를 올바르게 사용하거나 작업을 성공적으로 완료하는지는 검증하지 않았다.

이 보고서는 11월 12일의 초기 helper 실행(`task_criticality_s235_dev_v1`)을 보존한다. 무효 Evidence를 fallback 선택에서도 제외하도록 갱신한 뒤 같은 dataset으로 재실행한 결과는 [v0.2 후속 Preflight](task-criticality-s235-v02-preflight-2026-11-13.md)에 기록했다.

## 설계

| 항목 | 설정 |
|---|---|
| Freeze | Architecture / Parameter Freeze 1.0.4 |
| 데이터 | Seed 200–219, `file_lookup`·`approved_email` 각 20개 기본 과제, 4개 변형 총 160 scenario |
| 변형 | S2 중요도 역전, S3 유효·무효 단서 및 정보 부재, S5 상충 slot 값 |
| 정책 | `full`, `no_budget_aware_packing`, `criticality` |
| 평가 | 3개 정책 × 160 scenario = 480 Context 선택 |
| 모델 호출 | Gemini CountTokens; LLM generation 0회 |
| 데이터 SHA-256 | `3a5b8980ac0ae29fc5231c7738e059d5cbf8aea1e332cfcb5c4190f4cb113534` |
| 라벨 SHA-256 | `45e8b67e0f4e3d37ebeb3e1cbf920003863c08b233a59f5d3b233c52b3192eb7` |

S2는 gold memory의 importance를 낮추고 짧은 distractor들의 importance를 높였다. S3_VALID에는 예시·취소·다른 프로젝트 등의 단서가 있는 무효 항목과 유효 gold가 함께 있고, S3_EMPTY에는 gold 정보 없이 일반 기록만 남는다. S5는 파일명 또는 승인 번호가 서로 다른 두 Memory에 들어 있는 모호성 조건이다. 정답 라벨은 정책 입력으로 전달하지 않고 선택 후 결과를 계산하는 데 사용했다.

## 결과

각 변형은 40개 scenario다. `Gold 포함`은 정답 Memory가 있을 때 Context 안에 들어간 scenario 수, `Gold 승격`은 criticality policy가 직접 승격한 항목 수다. `무효 노출`과 `상충 노출`은 Context에 포함된 Memory 개수의 변형별 합계다.

| 변형·관측 | `full` | `no_packing` | `criticality` |
|---|---:|---:|---:|
| S2 Gold 포함 / 승격 | 0/40 / 0 | 0/40 / 0 | **40/40 / 40** |
| S3_VALID Gold 포함 / 승격 | 40/40 / 0 | 24/40 / 0 | **40/40 / 40** |
| S3_VALID 무효 Memory 노출 | 74 | 74 | 74 |
| S3_EMPTY missing slot 감지 | 0/40 | 0/40 | **40/40** |
| S3_EMPTY 무효 Memory 노출 | 76 | 80 | 76 |
| S5 상충 slot 감지 | 0/40 | 0/40 | **40/40** |
| S5 상충 Memory 노출 | 80 | 80 | 80 |

세 정책에서 모든 480개 Context는 정상 생성됐고 Mandatory omission과 false promotion은 0건이었다. S3의 무효 노출 수와 S5의 상충 노출 수는 scenario 성공률이 아니라 해당 변형 40개에 걸친 Memory 항목 합계다.

## 해석과 한계

- S2와 S3_VALID의 결과는 이 제한된 합성 데이터에서 `criticality`의 slot 근거 승격이 gold 포함률을 높일 수 있음을 보여준다.
- S3_EMPTY와 S5에서는 잘못된 값의 승격을 피하고 결손·모호성을 표시했지만, 그 정보가 최종 Context에 남아 있다. 호출부는 감지 결과를 보고 별도의 응답 제한이나 사람 확인을 구현해야 한다.
- 슬롯 판정은 정규식·어휘 cue 및 지정된 프로젝트 식별 형식에 의존한다. 일반적인 자연어 추론이나 새로운 도메인을 평가하지 않았다.
- 전체 평가는 selection-only development set이며, LLM Agent, Tool 효과, 반복 간 변동, 독립 holdout을 평가하지 않았다.
- 결과는 기본 Proposed policy를 변경하지 않는다. `criticality`는 별도 preflight에서 호출한 실험 정책이다.

## 산출물과 재현

Dataset과 oracle label은 `benchmark/generated/task_criticality_s235_v1/`에 있고, 원본 선택 결과는 `experiments/results/task_criticality_s235_dev_v1/`의 `policy_summary.csv`, `selection_details.csv`, `validation.json`에 있다. CountTokens는 `gemini-3.1-flash-lite`로 1,297회 API 요청을 했고 retry 2회가 기록됐다. Embedder resolved revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`다. validation은 Freeze 1.0.4를 기록하지만 실행 commit hash는 포함하지 않는다.

새 경로에서 데이터와 라벨을 재생성하고 selection preflight를 수행하는 명령이다. CountTokens API 사용량이 발생하므로 원본 결과 디렉터리를 덮어쓰지 않는다.

```powershell
python -m benchmark.task_criticality_stress generate `
  --counter gemini `
  --seed-start 200 `
  --cases-per-family 20 `
  --output benchmark/generated/task_criticality_s235_v1/repro_dataset.json

python -m benchmark.task_criticality_stress_preflight `
  --dataset benchmark/generated/task_criticality_s235_v1/repro_dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/task_criticality_s235_dev_v1_rerun
```

