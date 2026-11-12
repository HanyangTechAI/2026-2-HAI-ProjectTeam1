# Task Criticality v0.2 S2/S3/S5 Preflight — 2026-11-13

## 요약

11월 12일 S2/S3/S5 stress preflight와 같은 160개 scenario에서 업데이트된 `pack_task_criticality()` v0.2의 선택 결과를 점검했다. 특히 S3의 예시·취소·다른 프로젝트 정보처럼 판정 가능한 무효 Evidence를 가진 Memory를 승격뿐 아니라 일반 fallback 후보에서도 제외하도록 바뀌었다.

S2와 S3_VALID에서 gold slot 포함은 40/40회를 유지했고, S3_VALID의 무효 Evidence 노출은 74개에서 0개로 줄었다. S3_EMPTY에서는 missing slot 감지 40/40회를 유지하면서 무효 정보 노출을 76개에서 0개로 줄였다. S5의 상충 slot은 40/40회 감지했지만 두 상충 Memory는 계속 Context에 남았다.

이번 결과는 선택 전용 검증이다. LLM Agent나 Tool을 실행하지 않았으며, slot이 비거나 상충할 때 실행을 차단하는 동작도 검증하지 않았다.

## 설계

| 항목 | 설정 |
|---|---|
| Freeze | Architecture / Parameter Freeze 1.0.4 |
| 데이터 | 11월 12일과 동일한 `task_criticality_s235_v1` 개발 dataset 및 label, 160 scenario |
| 변형 | S2 40개, S3_VALID 40개, S3_EMPTY 40개, S5 40개 |
| 예산 | scenario별 Mandatory floor–all-flexible ceiling 구간의 75% |
| 비교 정책 | `full`, `no_budget_aware_packing`, 업데이트된 `criticality` |
| 평가 | 3개 정책 × 160 scenario = 480 Context 선택 |
| 모델 호출 | Gemini CountTokens; LLM generation 0회 |
| 데이터 / 라벨 SHA-256 | `3a5b8980ac0ae29fc5231c7738e058d5cbf8aea1e332cfcb5c4190f4cb113534` / `45e8b67e0f4e3d37ebeb3e1cbf920003863c08b233a59f5d3b233c52b3192eb7` |

선택 정책에는 oracle label을 넘기지 않았다. 선택 후 결과 집계에 label을 사용해 gold 포함, 무효 항목 노출, 누락 감지, 상충 감지를 계산했다. 데이터 SHA가 이전 실행과 같으므로 독립 데이터 검증은 아니다.

## v0.1 대비 결과

아래 값은 scenario 수 또는 40개 scenario에 포함된 Memory 항목의 합계다. `무효 노출`과 `상충 노출`은 scenario 비율이 아니다.

| 변형·지표 | `full` | `no_packing` | `criticality` 이전 실행 | `criticality` v0.2 |
|---|---:|---:|---:|---:|
| S2 gold 포함 | 0/40 | 0/40 | 40/40 | 40/40 |
| S3_VALID gold 포함 | 40/40 | 24/40 | 40/40 | 40/40 |
| S3_VALID 무효 Evidence 노출 | 74 | 74 | 74 | **0** |
| S3_EMPTY missing slot 감지 | 0/40 | 0/40 | 40/40 | 40/40 |
| S3_EMPTY 무효 Evidence 노출 | 76 | 80 | 76 | **0** |
| S5 상충 slot 감지 | 0/40 | 0/40 | 40/40 | 40/40 |
| S5 상충 Memory 노출 | 80 | 80 | 80 | 80 |

S2·S3_VALID의 `criticality`는 기존처럼 gold 정보를 모두 포함했다. S3의 무효 Memory가 Context에 남지 않도록 바뀐 반면, S5에서는 모호성 감지 후에도 안전한 근거로 분류된 두 상충 항목이 filler 경로로 선택될 수 있어 Context 노출이 지속됐다. 따라서 `ambiguous_slots` 또는 `unresolved_slots` 결과를 실제 실행에서 안전 조치로 연결하는 호출부가 별도로 필요하다.

480개 Context에서 실패와 Mandatory omission은 0건이었다. v0.2 실행의 CountTokens API 요청은 881회, retry는 0회였고 embedder resolved revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`였다.

## 구현 범위와 한계

- 무효 판정은 Query에서 프로젝트를 하나로 식별하고 지원하는 action을 감지한 경우에만 적용한다. 판정 가능한 무효 cue나 프로젝트 불일치가 발견되면 해당 `MemoryItem` 전체를 제외한다. 부분 내용 삭제는 수행하지 않는다.
- 판정은 정규식과 제한된 한국어·영어 cue에 의존한다. 임의 표현, 새로운 도메인, 복합적인 자연어 부정을 일반적으로 이해한다고 볼 수 없다.
- 서로 다른 유효 값이 있는 상충 slot은 승격을 보류하고 ambiguity를 표시하지만, Context에서 두 후보를 제거하거나 Agent를 차단하지 않는다.
- 결과는 같은 development dataset에 대한 selection-only 분석이다. Agent의 사실 판단, Tool call, 완료율 또는 별도 holdout 성능은 측정하지 않았다.
- validation에는 Freeze 1.0.4와 데이터 checksum이 있지만 실행 commit hash는 기록되지 않았다.

## 산출물과 재현

원본 결과는 `experiments/results/task_criticality_s235_v02/`의 `policy_summary.csv`, `selection_details.csv`, `validation.json`에 있다. 동일 dataset으로 새 결과 경로에 다시 실행할 수 있다.

```powershell
python -m benchmark.task_criticality_stress_preflight `
  --dataset benchmark/generated/task_criticality_s235_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/task_criticality_s235_v02_rerun
```

