# Research Questions

## 연구 목적

제한된 Context Token Budget에서 행동에 필수적인 제약(Constraint)과 최신 상태(Current State)를 우선 보존하는 **Constraint-Preserving Budget-Aware Memory**가 Long-Horizon Tool-Using Agent의 작업 성공률과 신뢰성을 개선하는지 평가한다.

제안 구조는 Protected Memory, Current State 및 State Versioning, 남은 예산에 따른 Flexible Memory 선택으로 구성된다. 아래 연구 질문은 「Constraint-Preserving Budget-Aware Memory for Long-Horizon LLM Agents」 문서와 제시된 다섯 질문을 바탕으로 정리했다. 성능 개선은 검증할 가설이며, 실험 결과로 전제하지 않는다.

## RQ1. 동일한 Token Budget에서의 작업 성공률

**동일한 Context Token Budget에서 Constraint-Preserving Budget-Aware Memory는 기존 Memory 방식보다 Agent의 Task Success Rate를 높이는가?**

- 동일한 Agent, Task, History 및 Token Budget에서 Memory 방식만 변경하여 비교한다.
- 주요 비교 대상은 Sliding Window, Vector Top-k Memory, Utility-per-Token Memory이다.
- 핵심 지표는 Task Success Rate이며, Constraint Violation Rate와 Current-State Accuracy를 함께 측정하여 성공률 차이의 원인을 분석한다.

## RQ2. Long Context와 External Memory의 오류 유형별 강건성

**Long Context와 External Memory는 제약 누락, 오래된 상태 사용, 관련 없는 정보의 간섭 등 Memory Corruption 유형에 대해 서로 다른 강건성(Robustness)을 보이는가?**

- 본 연구에서 Memory Corruption은 저장 데이터의 물리적 손상보다, 필요한 정보의 누락·잘못된 선택·활용으로 인해 행동이 실패하는 현상을 뜻한다.
- Long Context는 원본 Interaction History를 직접 제공하는 방식으로, External Memory는 외부에 저장·관리한 정보를 선택하여 Context에 제공하는 방식으로 구분한다. External Memory에는 기존 검색·선택 방식과 제안 구조를 포함한다.
- Constraint 수, State 변경 횟수, Noise Memory 비율, 중요 정보와 최종 Task 사이의 거리를 조절하여 유형별 Task Success Rate와 행동 오류를 비교한다.
- Full Context가 예산 내에 들어가는 조건에서는 동일 예산 비교를 수행한다. 전체 History가 예산을 초과하면 Full Context는 별도의 비용·성능 참고 기준으로 보고하고, 잘린 History를 Full Context로 취급하지 않는다.

## RQ3. Token Budget 감소에 따른 실패 양상

**Context Token Budget이 작아질수록 기존 Memory 방식에서 Constraint Violation과 오래된 State 사용이 증가하는가?**

- History와 Task의 난이도를 고정하고 Token Budget을 단계적으로 줄인다. 문서의 예시 설정은 1K, 2K, 4K, 8K tokens이다.
- Constraint Violation Rate와 오래된 State 사용률을 측정하고, Current-State Accuracy를 함께 확인한다.
- 오래된 State 사용은 최신 State가 존재하는데도 이전 버전을 행동에 사용하는 경우로 정의한다. State 누락이나 잘못된 값 생성과 구분하여 기록한다.
- 제안 방식에도 동일한 조건을 적용하여 제약 보존과 State Versioning이 이러한 오류 증가를 완화하는지 확인한다.

## RQ4. History Length와 Token Budget의 복합 효과

**Interaction History가 길어지고 사용 가능한 Context Token Budget이 감소할수록, 제안 Memory Architecture의 성능은 기존 Memory 방식과 비교하여 어떻게 변화하는가?**

- History Length와 Token Budget을 독립적으로 변화시키는 교차 실험을 수행한다.
- 각 조건에서 Task Success Rate, Constraint Violation Rate, Current-State Accuracy를 비교하여 성능 저하의 정도와 실패가 집중되는 구간을 분석한다.
- 전체 History Length와 중요 정보부터 최종 Task까지의 거리(Horizon)를 구분하여 기록한다. 단순한 History 증가와 필요한 정보를 오래 유지해야 하는 부담을 구별하기 위함이다.
- Input Token Usage, Total LLM Cost, Planning Latency를 보조 지표로 측정하여 성능·비용·신뢰성의 관계를 확인한다.

## RQ5. Budget 및 Horizon에 따른 성공률 격차

**Context Token Budget B가 작아질수록, 또는 Horizon이 길어질수록 Constraint-Preserving Budget-Aware Memory와 기존 Memory 방식 사이의 Task Success Rate 차이가 커지는가?**

- 각 Baseline에 대해 `ΔSuccess(B, H) = TSR_proposed(B, H) − TSR_baseline(B, H)`를 계산한다. `H`는 중요 정보부터 최종 Task까지의 거리이다.
- Horizon을 고정한 상태에서 Budget 감소에 따른 격차 변화를, Budget을 고정한 상태에서 Horizon 증가에 따른 격차 변화를 각각 확인한다.
- RQ4가 여러 지표의 전반적인 성능 변화를 살펴본다면, RQ5는 제안 방식의 성공률 이점이 어려운 조건에서 더 커지는지를 검증한다.
- 반복 실험으로 격차의 불확실성을 보고하며, 모든 방식이 실패하는 극단적 조건에서는 격차가 다시 줄어드는지도 확인한다.

## 공통 실험 원칙

- **Budget 정의:** `B`는 Memory를 포함한 Agent 입력 Context의 Token 상한이다. 공통 지시문, 현재 요청, Tool 정의 등 고정 입력의 비용을 동일하게 반영하고, Memory에 실제로 할당 가능한 예산과 실제 사용량을 함께 기록한다.
- **성공 기준:** Task 완료뿐 아니라 적용되는 Constraint 준수와 필요한 최신 State 사용을 포함하는 행동 수준의 성공 기준을 사전에 정의한다. 승인 요청이 필요한 Task에서는 승인 요청을 올바른 행동으로 평가한다.
- **공정한 비교:** 동일한 모델, Tool, Task 시나리오와 실행 조건을 사용하고, 반복 실행의 평균과 신뢰구간을 보고한다.
- **예산 초과 조건:** Protected Memory와 Current State 자체가 가용 예산을 초과하는 조건은 별도로 표시한다. 이 경우에도 예산을 초과하여 제안 방식에 유리하게 비교하지 않으며, 필수 정보 보존이 가능한 범위와 한계를 보고한다.
- **구성 요소 검증:** Protected Memory, State Versioning, Budget Optimization을 각각 제거하는 Ablation Study로 성능 차이에 대한 각 요소의 기여를 확인한다.
