# Research Questions

## 연구 목적

제한된 Context Token Budget에서 행동에 필수적인 제약(Constraint)과 현재 Task에 필요한 상태(State)를 우선 보존하는 **Constraint-Preserving Budget-Aware Memory**가 Long-Horizon Tool-Using Agent의 작업 성공률과 행동 신뢰성을 개선하는지 평가한다.

제안 구조는 Protected Memory, Version-Aware State Resolution, 남은 예산에 따른 Flexible Memory 선택으로 구성된다. 본 연구에서는 동일한 기억 공간에서 제안 방식이 기존 Memory 방식보다 실제 작업을 더 정확하게 수행하는지 확인하고, Context Token Budget 감소와 Horizon 증가와 같은 어려운 조건에서도 이러한 장점이 유지되는지를 검증한다. 성능 개선은 검증할 가설이며 실험 결과로 전제하지 않는다.

## RQ1. 동일한 Token Budget에서의 작업 성공률

**동일하게 제한된 Context Token Budget에서, 제약과 현재 Task에 필요한 상태를 우선 보존하는 Proposed 방식은 최근 대화만 유지하거나 Query와 유사한 정보를 검색하는 기존 방식보다 Agent의 실제 작업 성공률을 높이는가?**

- 동일한 Agent, Model, Tool, Task, Interaction History 및 Context Token Budget에서 Memory 방식만 변경하여 비교한다.
- 주요 비교 대상은 Sliding Window, Vector Top-k Memory, Utility-per-Token Memory이다.
- 핵심 지표는 **Action-Level Task Success Rate(TSR)**이다.
- Action-Level TSR은 최종 답변 문장의 정확성만 평가하지 않고, 실제 Tool 행동과 Task 완료 여부까지 포함하여 판단한다.
- 예를 들어 최신 보고서를 올바르게 선택했는지, 이메일을 발송하기 전에 필요한 승인을 요청했는지와 같은 행동을 평가한다.
- Constraint Violation Rate, Required-State Accuracy, Stale-State Usage Rate를 함께 측정하여 작업 실패의 원인을 분석한다.
- Long Context는 전체 Interaction History가 동일한 Context Token Budget 안에 들어가는 조건에서는 직접 비교하고, 이를 초과하는 경우 별도의 비용·성능 참고 기준으로 사용한다.

## RQ2. Budget 감소와 Horizon 증가에 대한 강건성

**Context Token Budget이 감소하거나 중요한 정보와 현재 Task 사이의 거리(Horizon)가 증가할 때, Proposed 방식은 기존 Memory 방식보다 Constraint Violation과 잘못된 State 사용을 더 잘 억제하는가?**

- Context Token Budget을 단계적으로 감소시키며 각 Memory 방식의 행동 성능 변화를 비교한다.
- 행동에 필요한 Constraint 또는 State가 등장한 시점과 최종 Task 사이의 거리인 Horizon을 증가시키며 장기간 정보를 유지하는 능력을 평가한다.
- 주요 지표는 Task Success Rate, Constraint Violation Rate, Required-State Accuracy, Stale-State Usage Rate이다.
- 예를 들어 오래전에 주어진 **“이메일 발송 전 승인 필요”** Constraint를 최종 행동까지 유지하는지 확인한다.
- State가 여러 차례 변경된 경우 현재 Task가 요구하는 시점에 유효한 State를 올바르게 사용하는지 평가한다. 현재 시점의 Task라면 `report_v1.pdf` 대신 `report_final.pdf`와 같은 최신 유효 상태를 사용해야 한다.
- Historical Query에서는 단순히 최신 State가 아니라 Query가 가리키는 시점에 유효했던 State가 정답이 될 수 있으므로, `is_active` 여부만으로 오래된 State 사용 오류를 판정하지 않는다.
- Budget을 고정한 상태에서 Horizon이 증가할 때와 Horizon을 고정한 상태에서 Budget이 감소할 때 Proposed와 Baseline 사이의 성능 격차가 어떻게 변화하는지 분석한다.
- 필요하면 각 Baseline에 대해 다음 값을 계산한다.

```text
ΔSuccess(B, H)
  = TSR_proposed(B, H) − TSR_baseline(B, H)
```

- `B`는 Context Token Budget이며, `H`는 행동에 필요한 중요 정보와 최종 Task 사이의 거리이다.

## 공통 실험 원칙

- **Budget 정의:** `B`는 Memory만의 크기가 아니라 Memory를 포함한 Agent 입력 Context 전체의 Token 상한이다. System Prompt, 현재 요청, Tool 정의 등 고정 입력 비용을 동일하게 반영한다.
- **성공 기준:** Task 완료뿐 아니라 적용되는 Constraint 준수와 현재 Task에 필요한 State의 올바른 사용을 포함하는 행동 수준의 성공 기준을 사전에 정의한다.
- **Action-Level 평가:** 단순한 자연어 응답 정확도만으로 성공 여부를 판단하지 않고, 실제 Tool Call과 행동 결과를 함께 평가한다.
- **State 평가:** 현재 Task에서는 현재 유효한 State를 사용해야 하며, Historical Query에서는 요청 시점에 유효했던 과거 State가 정답일 수 있다.
- **공정한 비교:** 동일한 Model, System Prompt, Tool, Task Scenario, Interaction History, Token Budget 및 실행 조건을 사용하고 Memory strategy만 변경한다.
- **Long Context 비교:** 전체 History가 동일 Budget 안에 들어가는 경우 직접 비교하고, Budget을 초과하는 Full Context 실행은 동일 Budget 비교가 아닌 별도의 비용·성능 참고 기준으로 보고한다.
- **예산 초과 조건:** Protected Constraint와 Required State 등 필수 Memory 자체가 가용 Memory Budget에 들어가지 않으면 `INSUFFICIENT_CONTEXT_BUDGET`으로 중단하며, 필수 정보를 일부 제거한 상태로 Agent를 실행하지 않는다.
- **반복 및 불확실성:** 동일 sample과 조건에서 반복 실행하고 평균과 95% 신뢰구간을 함께 보고한다.
- **실패 분석:** General Retrieval 누락, 최종 Context의 필수 Memory 누락, Temporal Version 오류, Constraint Violation, Stale-State Usage, State Omission 등을 구분하여 기록한다.
- **효율성 평가:** Input Token Usage, Total LLM Cost, Planning Latency를 보조 지표로 측정하여 성능·비용·신뢰성 간 trade-off를 분석한다.
- **구성 요소 검증:** Protected Memory, State Versioning, Budget Optimization을 각각 제거하는 Ablation Study를 보조 실험으로 수행하여 제안 방식의 각 구성 요소가 성능에 미치는 영향을 분석한다.