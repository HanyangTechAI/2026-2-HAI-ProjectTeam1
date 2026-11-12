# Task Criticality 75% 선택 Preflight — 2026-11-12

## 요약

기존 Critical Flexible 개발 데이터 160개에 `criticality` 선택 정책을 추가해 75% 예산에서 선택만 비교했다. 기본 `full`은 정답 Critical Memory를 120/160회 포함했고, `no_packing`과 실험용 `criticality`는 각각 160/160회 포함했다. `full`의 누락 40건은 모두 긴 Critical과 짧은 distractor를 조합한 조건 C였다. `criticality`는 160개 모두에서 라벨된 Critical을 승격했고 false promotion은 없었다.

이 평가는 LLM 생성이나 Tool 행동을 실행하지 않은 selection-only preflight다. 세 정책 모두 Context 실패와 Mandatory 누락이 없었으며, 결과는 Agent 작업 성공률이나 독립적인 정책 검증을 뜻하지 않는다.

## 설계

| 항목 | 설정 |
|---|---|
| Freeze | Architecture / Parameter Freeze 1.0.4 |
| 데이터 | Seed 42–61, `file_lookup`·`approved_email` 각 20개 기본 과제, 조건 A–D 총 160 scenario |
| 예산 | scenario별 mandatory floor–all-flexible ceiling 구간의 75%; nominal budget에는 safety margin 32 tokens 포함 |
| 정책 | `full`, `no_budget_aware_packing`, 실험용 `criticality` |
| 평가 | 3개 정책 × 160 scenario = 480 Context 선택 |
| 모델 호출 | Gemini CountTokens만 사용; LLM generation 0회 |
| 데이터 SHA-256 | `74f8e7245f33b209037ac0e07b6af2f8717f880619c5736c86a6f7dd16f14130` |

데이터와 시나리오는 앞선 Critical Flexible 라벨 Preflight와 동일하다. 이 실행은 새 데이터에 대한 재현이나 holdout이 아니라 세 번째 선택 정책을 추가한 후속 분석이다. `criticality`는 선택 전에 정답 ID를 받지 않고 Query와 Memory 후보만으로 slot 근거를 판정한다.

## 결과

| 조건 | `full` Critical 포함 | `no_packing` Critical 포함 | `criticality` Critical 포함 | `criticality` 승격 |
|---|---:|---:|---:|---:|
| A | 40/40 | 40/40 | 40/40 | 40 |
| B | 40/40 | 40/40 | 40/40 | 40 |
| C | 0/40 | 40/40 | 40/40 | 40 |
| D | 40/40 | 40/40 | 40/40 | 40 |
| **전체** | **120/160 (75%)** | **160/160 (100%)** | **160/160 (100%)** | **160** |

검색 단계에서는 세 정책 모두 Critical을 160/160회 회수했고, 정답이 예산 안에 들어갈 수 있는 경우도 160/160회였다. 따라서 조건 C의 `full` 차이는 이 데이터에서 Retrieval 누락이 아니라 Flexible packing 선택에서 발생했다. 480개 Context 평가에서 pipeline failure와 Mandatory omission은 각각 0건이었다.

## 해석과 한계

- 결과는 앞선 160개 개발 데이터를 재사용하므로 독립 holdout 증거가 아니다.
- 합성 시나리오, 고정된 모델·임베딩, 75% 예산 수준에서의 선택 결과다. 다른 예산, Task 또는 실제 사용자 분포로 일반화할 수 없다.
- LLM generation과 Tool 실행을 하지 않아 첫 행동·최종 Task 성공률은 측정하지 않았다.
- `criticality`는 규칙 기반 별도 실험 정책이며 기본 `select_memory()`나 Main Runner 정책을 대체하지 않는다.

## 산출물과 재현

원본 선택 결과는 `experiments/results/criticality_dev_75_v01/`의 `policy_summary.csv`, `selection_details.csv`, `selection_details.jsonl`, `validation.json`에 있다. 기록된 Freeze는 1.0.4이며, validation에는 실행 commit hash가 저장되어 있지 않다. CountTokens는 `gemini-3.1-flash-lite`로 630회 API 요청을 했고 retry는 없었다. Embedder resolved revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`다.

새 출력 경로를 사용해 같은 개발 데이터를 다시 점검할 수 있다.

```powershell
python -m benchmark.criticality_preflight `
  --scenarios benchmark/generated/critical_flexible_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/criticality_dev_75_v01_rerun
```

