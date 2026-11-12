# 실험용 Packing·Task Criticality

## 적용 범위

이 문서는 실험용 packing 코드의 위치와 재현 절차를 설명한다. `memory.experimental_packing.pack_flexible()`와 `memory.task_criticality.pack_task_criticality()`는 기본 `memory.selector.select_memory()` 또는 실행 파이프라인의 정책을 바꾸지 않는다. 결과를 기본 Proposed 정책으로 해석하거나 적용하지 않는다.

## Alpha 길이 패널티 실험

`pack_flexible()`는 기존 Utility 점수에서 token 길이 패널티를 조절한다.

```text
score = utility_score / max(token_count, 1) ** alpha
```

`alpha=1`은 비교용 기존 Utility-per-Token 기준, `alpha=0`은 token 길이 패널티가 없는 효용 순위다. 두 경우 모두 Greedy 선택과 hard budget 검증을 유지한다. 개발용 LLM 실험은 `alpha=0`, `0.2`, `1`을 비교한다.

### 사전 조건

- 개발 dataset은 `benchmark/generated/critical_flexible_v1/dataset.json`처럼 `critical_flexible_v1` generator가 만든 파일이어야 한다. Seed 시작값 42, task family별 20개 기본 과제, 총 160 scenario를 요구한다.
- Preflight는 holdout dataset을 거부하며 Freeze 1.0.4 ablation config와 dataset manifest의 SHA-256을 확인한다.
- Gemini CountTokens·Agent 실행에는 `GEMINI_API_KEY`가 필요하다. SentenceTransformer embedder는 첫 실행 때 모델 가중치를 내려받을 수 있다.
- 아래 명령의 새 출력 경로는 미리 존재하면 안 된다. 결과 덮어쓰기를 막기 위해 도구가 기존 경로에서 중단한다.

개발 dataset이 없으면 Gemini CountTokens를 사용해 새 경로에 생성한다. 이후 명령의 dataset 경로를 생성한 파일로 바꿔 사용한다.

```powershell
python -m benchmark.critical_flexible_generator `
  --counter gemini `
  --seed-start 42 `
  --cases-per-family 20 `
  --output benchmark/generated/critical_flexible_v1/alpha_dev_dataset.json
```

### Selection preflight

75% 예산 수준만 점검하는 예시다. 기본 개발 alpha 다섯 값과 조건 A–D의 160개 scenario를 처리한다.

```powershell
python -m benchmark.packing_policy_preflight `
  --scenarios benchmark/generated/critical_flexible_v1/dataset.json `
  --config experiments/configs/ablation.json `
  --alphas 0 0.1 0.2 0.5 1 `
  --levels 0.75 `
  --output experiments/results/packing_alpha_dev_rerun
```

모든 예산 수준을 평가하려면 `--levels 0 0.25 0.5 0.75 1`을 지정한다. `policy_summary.csv`는 조건·예산·alpha별 Critical recall, packing 제외, Context 실패와 예산 초과를 기록한다. 이 단계는 LLM을 호출하지 않는 선택 preflight이며 Agent 행동 결과가 아니다.

### Condition C Agent 비교

`prepare`는 preflight 세부 결과에서 조건 C·75% 예산의 개발 scenario 40개를 만든다.

```powershell
python -m benchmark.packing_alpha_llm prepare `
  --dataset benchmark/generated/critical_flexible_v1/dataset.json `
  --policy-details experiments/results/packing_alpha_dev_rerun/policy_details.jsonl `
  --output benchmark/generated/critical_flexible_v1/dev_alpha_c75_rerun.json
```

세 alpha arm을 같은 시나리오에서 각각 실행한다.

```powershell
python -m benchmark.packing_alpha_llm run `
  --alpha 0 `
  --scenarios benchmark/generated/critical_flexible_v1/dev_alpha_c75_rerun.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/packing_alpha_llm_0_rerun

python -m benchmark.packing_alpha_llm run `
  --alpha 0.2 `
  --scenarios benchmark/generated/critical_flexible_v1/dev_alpha_c75_rerun.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/packing_alpha_llm_02_rerun

python -m benchmark.packing_alpha_llm run `
  --alpha 1 `
  --scenarios benchmark/generated/critical_flexible_v1/dev_alpha_c75_rerun.json `
  --config experiments/configs/ablation.json `
  --output experiments/results/packing_alpha_llm_1_rerun
```

중단된 동일 실행을 이어갈 때만 해당 `run` 명령에 `--resume`을 추가한다. 결과 비교는 세 arm의 40개 실행이 모두 끝난 뒤 수행한다.

```powershell
python -m benchmark.packing_alpha_llm compare `
  --alpha-0 experiments/results/packing_alpha_llm_0_rerun `
  --alpha-02 experiments/results/packing_alpha_llm_02_rerun `
  --alpha-1 experiments/results/packing_alpha_llm_1_rerun `
  --output experiments/results/packing_alpha_llm_comparison_rerun
```

`first_action_correct`는 첫 도구 행동, 필수 인자와 작업별 Tool 효과가 모두 맞아야 성공이다. Runner는 `max_steps=1`로 고정하므로 이 값은 최종 Task 성공률이 아니다. 비교 도구는 입력 budget audit 누락과 실제 budget 초과도 확인한다.

### 기록된 결과

[2026-11-12 alpha 개발 실험 보고서](../experiments/reports/packing-alpha-development-2026-11-12.md)는 alpha 0/0.2/1을 비교한 40개 조건 C 결과와 한계를 기록한다. 개발 자료의 alpha 결과는 별도의 seed·템플릿 holdout 검증을 대신하지 않는다.

## Task Criticality 슬롯 helper

`memory.task_criticality.pack_task_criticality()`는 필수 slot을 가진 Flexible memory를 일반 점수보다 먼저 보존하려는 별도 helper다.

```python
result = pack_task_criticality(
    candidates,
    query=query,
    mandatory=mandatory_memories,
    budget=remaining_flexible_budget,
)
```

`candidates`는 `MemoryCandidate` 목록, `mandatory`는 필수 `MemoryItem` 목록이다. `budget`에는 전체 Context 예산이 아니라 Mandatory 비용을 뺀 Flexible 예산을 전달한다. 현재 규칙은 지원하는 파일 검색에서 `filename`, 승인 이메일 발송에서 `attachment`와 `approval_id` slot을 찾는다. Query에서 프로젝트를 하나로 식별할 수 있으면 slot 근처의 근거를 평가하고, 프로젝트가 다르거나 섞인 Memory 및 예시·취소·무효 등 제한된 cue가 감지된 Memory는 후보 전체를 제외한다. 한 Memory에 유효·무효 정보가 같이 있으면 부분 수정하지 않고 그 Memory 전체를 뺀다. 값이 충돌하면 해당 slot 승격을 보류한다. 확보된 유효 slot 후보를 예산 우선순위로 선택하고, 남은 budget은 alpha=1 packing으로 채운다.

함수 결과에는 `selected`, `promoted_ids`, `required_slots`, `grounded_slots`, `ambiguous_slots`, `unresolved_slots`, `action`, `excluded_invalid_ids`, `exclusion_reasons`, `slot_statuses`가 포함된다. 결손·모호성 감지 결과만으로 helper가 Agent나 Tool 실행을 차단하지는 않으므로 호출자가 별도 처리해야 한다. 상충 후보는 승격에서 제외되지만 fallback 후보로는 남을 수 있다.

이 helper는 현재 실험용 규칙 코드다. Intent와 값 추출은 정규식·어휘 cue 및 특정 프로젝트 ID 형식에 의존한다. 지원하지 않는 intent에는 무효 근거 필터링이나 slot 승격 없이 일반 alpha packing을 적용한다. 적용 가능한 slot 후보가 12개 이하면 조합 탐색으로 slot coverage를 우선하고, 그보다 많으면 Greedy coverage를 사용한다. Pipeline 통합, 광범위한 데이터 평가와 실제 LLM 성능 검증은 이 구현에 포함되지 않는다.

### 선택 전용 평가

- [Criticality 75% Preflight](../experiments/reports/task-criticality-75-selection-preflight-2026-11-12.md)는 기존 160개 개발 scenario에서 `full`, `no_packing`, `criticality`를 비교한다.
- [S2/S3/S5 Stress Preflight](../experiments/reports/task-criticality-s235-stress-preflight-2026-11-12.md)는 중요도 역전, 유효·무효 단서, 정보 부재와 상충 slot에 대한 선택 결과를 기록한다.
- [S2/S3/S5 v0.2 Preflight](../experiments/reports/task-criticality-s235-v02-preflight-2026-11-13.md)는 같은 데이터에서 S3 무효 Evidence 노출이 74/76개에서 0개로 줄고 S5 상충은 여전히 Context에 남은 결과를 비교한다.

세 preflight는 모두 LLM generation을 실행하지 않았다. 감지된 결손이나 모호성은 Context 구성 또는 Agent 실행을 자동으로 차단하지 않는다.
