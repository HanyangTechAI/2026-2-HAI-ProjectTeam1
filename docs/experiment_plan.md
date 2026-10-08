# Experiment Plan

평가 코드 및 설정 사용법: [evaluation_usage.md](evaluation_usage.md).
`benchmark/evaluator.py`는 전달받은 Scenario/실행 snapshot의 deterministic 평가,
JSONL 출력, sample 단위 bootstrap 집계와 paired ΔSuccess를 구현한다.
실험 설정은 `experiments/configs/`에 있으며, 실제 Agent 호출과 데이터 생성은
각 담당 모듈에서 연결해야 한다.

## 1. Experiment Objective

본 실험의 목적은 제한된 Context Token Budget에서 행동에 필수적인 제약과 현재 Task에 필요한 상태를 우선 보존하는 **Constraint-Preserving Budget-Aware Memory**가 Long-Horizon Tool-Using Agent의 작업 성공률과 신뢰성을 개선하는지 검증하는 것이다.

동일한 LLM, Tool, Task, 원본 interaction history와 실행 조건을 사용하되, 모델에게 과거 정보를 제공하는 Memory strategy만 변경하여 평가한다. 제안 방식의 성능 개선은 실험으로 검증할 가설이며 결과로 전제하지 않는다.

주요 비교 대상은 다음과 같다.

1. **Constraint-Preserving Budget-Aware Memory (Proposed)**
   - Protected Memory와 Required State를 Version-Aware State Resolution으로 우선 보존한다. Historical Query에서는 요청 시점의 과거 버전을 선택한다.
   - 남은 예산 안에서 Flexible Memory를 선택한다.
2. **Sliding Window**
   - 최신 interaction부터 Budget이 허용하는 범위까지 제공한다.
3. **Vector Top-k Memory**
   - 현재 요청과의 vector similarity를 기준으로 Memory를 선택한다.
4. **Utility-per-Token Memory**
   - 예상 utility를 token cost로 나눈 값을 기준으로 Memory를 선택한다.
5. **Long Context Reference**
   - 원본 interaction history 전체가 예산과 모델 context window 안에 들어갈 때 직접 제공한다.
   - 전체 History가 설정 Budget을 초과하면 동일 예산 baseline이 아니라 별도의 비용·성능 참고 기준으로 보고한다.
6. **Hybrid** *(추후 실험)*
   - 최근 conversation context와 retrieved memory를 함께 제공한다.

핵심적으로 다음 질문을 확인한다.

> 동일한 Budget에서 제안 방식이 기존 Memory 방식보다 행동 수준의 Task Success Rate를 높이며, Budget 감소와 Horizon 증가에 따른 실패를 완화하는가?

---

## 2. Experimental Principle

비교의 공정성을 위해 **Memory strategy를 제외한 조건은 최대한 동일하게 유지한다.**

고정해야 하는 주요 조건:

- 동일 LLM
- 동일 system prompt
- 동일 conversation
- 동일 query
- 동일 Tool 및 Tool interface
- 동일 Task scenario와 ground truth
- 동일 decoding parameter
- 동일 evaluation method
- 동일 Context Token Budget
- 동일 반복 횟수와 random seed 정책

즉, 다음 형태로 평가한다.

```text
Same Conversation
      |
      +-------------------+
      |                   |
      v                   v
Long Context        External Memory
      |                   |
      v                   v
     Same LLM           Same LLM
      |                   |
      +---------+---------+
                |
                v
            Evaluator
```

### 2.1 Context Token Budget 정의

`B`는 Memory만의 크기가 아니라 **Memory를 포함한 Agent 입력 Context 전체의 token 상한**으로 정의한다.

```text
B = Fixed Input Tokens + Available Memory Tokens

Fixed Input Tokens
  = System Prompt + Current Request + Tool Definitions + Other Fixed Inputs
```

각 실행에서 `B`, 고정 입력 token 수, Memory에 실제로 할당 가능한 token 수, Memory가 실제 사용한 token 수와 전체 input token 수를 함께 기록한다. 모든 strategy는 설정된 `B`를 초과할 수 없다.

Protected Constraint와 Required State만으로 가용 Memory Budget을 초과하면 `INSUFFICIENT_CONTEXT_BUDGET`으로 중단하고 LLM과 Tool을 호출하지 않는다. 실행기가 확인한 필수 Memory 초과는 `mandatory_memory_overflow`로 별도 표시한다. 이 조건에서도 제안 방식만 예산을 초과하도록 허용하지 않으며, 필수 정보 보존이 가능한 범위와 실패 한계를 별도로 보고한다.

### 2.2 Full Context 비교 원칙

- 전체 History가 `B` 안에 들어가면 Long Context를 다른 방식과 동일 Budget에서 비교한다.
- 전체 History가 `B`를 초과하면 잘린 History를 `Full Context`라고 부르지 않는다.
- 잘린 History를 사용하는 경우 별도의 `Truncated Context` 또는 `Sliding Window` baseline으로 기록한다.
- Budget을 초과한 진짜 Full Context 실행은 가능할 경우 비용·성능 참고 기준으로만 보고하며 동일 Budget 순위 비교에서 제외한다.

### 2.3 반복 실행과 불확실성

각 조건은 사전에 정한 횟수만큼 반복한다. 평균과 함께 95% 신뢰구간을 보고하며, stochastic decoding을 사용하는 경우 seed 또는 반복 실행 정책을 기록한다. 동일 sample과 반복 번호에서는 모든 strategy에 같은 실행 조건을 적용한다.

---

## 3. Evaluation Targets

### 3.1 Long Context

전체 interaction history가 설정 Budget과 모델 context window 안에 들어가는 조건에서 원본 History를 직접 제공한다.

```text
Conversation History
       +
Current Query
       |
       v
      LLM
```

전체 History가 설정 Budget 또는 모델 context window를 초과하면 Long Context 동일 Budget 조건에서 제외한다. 잘린 History를 사용한 실행은 `Truncated Context` 또는 `Sliding Window`로 분류하며, 사용된 정책과 실제 입력 token 수를 반드시 기록한다.

### 3.2 External Memory

conversation에서 생성된 memory를 외부 memory store에 저장한다. 현재 query가 입력되면 관련 memory를 검색하여 LLM에게 제공한다.

External Memory에는 Vector Top-k, Utility-per-Token 및 Proposed 방식을 포함하며, 각 방식은 동일한 Memory store 입력과 Budget 조건을 사용한다.

```text
Conversation
    |
    v
Memory Store
    |
    v
Retriever
    |
    v
Top-k Memory
    +
Current Query
    |
    v
   LLM
```

retrieval 결과 자체도 평가할 수 있도록 검색된 `memory_id`를 기록한다.

### 3.3 Hybrid

추후 실험에서는 최근 context와 retrieved memory를 동시에 제공한다.

```text
Recent Context
     +
Retrieved Memory
     +
Current Query
     |
     v
    LLM
```

Hybrid 실험에서는 Context와 Memory가 각각 차지하는 token 수를 별도로 기록한다.

---

## 4. Evaluation Dimensions

### 4.1 Action-Level Task Success Rate

Task Success는 단순한 최종 답변 일치가 아니라 다음 조건을 모두 만족하는 행동 수준의 성공으로 정의한다.

1. Task의 목표를 완료한다.
2. 해당 시점에 적용되는 Constraint를 위반하지 않는다.
3. 행동에 필요한 State를 Task가 요구하는 시점·버전에 맞게 사용한다.
4. 승인이 필요한 Task에서는 승인 전 실행하지 않고 올바르게 승인을 요청한다.

sample별 성공 여부를 이진 값으로 기록하고, 조건별 `Action-Level Task Success Rate (TSR)`를 계산한다. Task별 세부 성공 조건과 ground truth는 실험 전에 고정한다.

### 4.2 Constraint 및 State 지표

- **Constraint Violation Rate (CVR):** 적용 대상 Constraint 가운데 Agent 행동이 위반한 비율. 실행 단위 위반 여부도 함께 기록한다.
- **Required-State Accuracy (RSA):** 행동에 필요한 State 항목 가운데 Task가 요구하는 시점·버전의 값을 올바르게 사용한 비율. 현재 값 요청과 과거 버전 요청을 모두 포함한다.
- **Stale-State Usage Rate (SSUR):** 현재 값을 요구하는 State 항목 가운데, 최신 유효 값 대신 superseded된 이전 값을 행동에 사용한 비율. 과거 시점·버전을 명시적으로 요청한 항목은 분모와 분자에서 제외한다.
- **State Omission Rate:** 필요한 State를 행동에서 누락한 비율.
- **State Error Rate:** 필요한 State 항목 가운데 기대값과 다르고 SSUR의 stale 사용으로 분류되지 않는 값을 사용한 비율. 과거 버전 요청에 최신 값을 사용한 경우도 포함하며, History에 없는 값만을 뜻하지 않는다.

각 `required_states` 항목에 `state_scope=current|historical`을 지정한다. 기존 입력은
생략 시 `current`로 처리하지만 과거 버전 요청은 반드시 `historical`로 명시한다.
`expected_value`와 정답 Memory ID는 Task가 요구한 버전으로 설정한다. 예를 들어
"이전 보고서 v1을 찾아줘"에서 v1 사용은 정답이고 최신 v2 사용은 `state_error`다.
"현재 보고서를 보내줘"에서 superseded된 v1 사용은 `stale_state_usage`다.
버전 비교 Task는 현재·과거 항목을 각각 정의하고 호출 pattern으로 구분한다.

RSA는 올바르게 사용한 항목 수 / 모든 필수 State 항목 수로 계산한다.
SSUR은 이전 값을 잘못 사용한 현재 항목 수 / 현재 값을 요구하는 항목 수로
계산한다. 현재 항목이 없으면 SSUR은 null이며 집계에서 제외한다. 실행별 비율의
macro 평균과 해당 실행 수를 보고한다.

Stale-State Usage는 오래된 Memory의 검색 여부가 아니라 **현재 값을 요구하는
최종 행동에서 이전 State를 실제 사용했는지**를 기준으로 판정한다.
`stale_values`는 현재 항목의 superseded된 값만 지정하며 과거 항목에서는 무시한다.
검색·선택의 버전 오류와 행동의 stale usage는 별도 필드로 기록한다.

### 4.3 Answer Accuracy

최종 LLM 응답이 ground-truth answer와 일치하는지 평가한다. 가능한 경우 자동 평가를 우선 사용한다.

예:

- Exact Match
- Normalized Exact Match
- F1 Score

자유형 응답처럼 deterministic evaluation이 어려운 경우에는 별도의 LLM-based evaluator 사용을 고려한다.

### 4.4 Retrieval Accuracy

External Memory 방식에서는 최종 답변뿐만 아니라 **필요한 memory를 실제로 retrieval했는지**도 평가한다.

가능한 경우 각 evaluation sample에 필요한 전체 `target_memory_ids`를 기록한다.
최초 General Retrieval Top-K는 별도 Protected Retrieval을 포함하지 않으므로,
Recall@k, Precision@k, Hit@k, MRR은 전체 target 중 `applicable_constraint_ids`를
제외한 일반 검색 대상만으로 계산한다. 일반 검색 대상이 없으면 해당 지표는
적용하지 않는다. 보호 제약의 최종 Context 포함 여부는
`applicable_protected_recall`로 별도 평가한다.

평가 후보:

- Recall@k
- Precision@k
- Hit@k
- MRR

최초 일반 검색에서 필요한 항목을 놓쳤는지는 `general_retrieval_miss`로 기록한다.
이후 버전 확장이나 별도 보호 검색으로 복구될 수 있으므로, 최초 검색 누락만으로
최종 실패 원인을 확정하지 않는다. 최종 Context에서 필수 항목이 빠지면
`selection_failure`로 기록하고, 예산 누락 근거가 있으면
`budget_selection_failure`도 기록한다. 행동 성공 여부는 별도로 평가한다.

### 4.5 Temporal Version Accuracy

State update가 존재하는 sample에서는 retrieval 이후 실제 LLM context에 포함된 Memory가 해당 시점에 유효한 버전인지 평가한다.

각 sample에 대해 다음 값을 기록한다.

* `selected_memory_ids`: retrieval 이후 temporal resolution 및 budget selection을 거쳐 실제 LLM context에 포함된 Memory ID
* `temporal_version_required`: 해당 sample에서 동일 State의 여러 버전 중 올바른 버전을 선택해야 하는지 여부
* `temporal_version_correct`: 필요한 temporal version을 올바르게 선택했는지 여부
* `stale_selected_memory_count`: 최종 선택된 Memory 가운데 Task가 요구한 버전에 부합하지 않는 `wrong_version_memory_ids`의 수. 과거 요청에서는 최신 버전도 포함될 수 있으며, superseded 여부만으로 판정하지 않는다.

Temporal Version Accuracy (TVA)는 temporal version 판단이 필요한 sample만 대상으로 다음과 같이 계산한다.

TVA = 올바른 temporal version을 선택한 sample 수 / temporal version 판단이 필요한 sample 수

`temporal_version_correct=false`이면 `temporal_failure`를 기록한다. 이 값만으로
검색, 버전 해석, 예산 선택 중 어느 단계가 원인인지 확정하지 않는다.

---

## 5. Efficiency Metrics

정확도만으로 Long Context와 External Memory를 비교하지 않는다. 다음 효율성 지표를 함께 기록한다.

### Token Usage

각 요청에 대해 다음 값을 기록한다.

- Context tokens
- Retrieved memory tokens
- Query tokens
- Total input tokens
- Output tokens

### Latency

가능한 경우 다음을 측정한다.

- Retrieval latency
- LLM inference latency
- Total latency

### Cost

API 기반 모델을 사용하는 경우 실제 token usage를 이용하여 요청별 비용을 계산한다.

최종적으로 다음 trade-off를 분석한다.

```text
Accuracy
  vs
Token Usage
  vs
Latency
  vs
Cost
```

### Planning Latency

Tool 실행 시간을 제외하고 Agent가 입력을 받은 뒤 실행 계획 또는 첫 Tool action을 결정하기까지의 시간을 가능한 경우 별도로 측정한다. 측정할 수 없는 실행 환경에서는 `not_available`로 기록하고 Total latency로 대체하지 않는다.

---

## 6. Experimental Variables

### 6.1 Context Token Budget

기본 Budget grid는 다음과 같다.

```text
1K / 2K / 4K / 8K tokens
```

모델과 Tool 정의의 고정 입력이 1K 조건을 성립시키지 못하는 경우 실제 적용 가능한 최솟값을 사전에 정하고 변경 사유를 기록한다. RQ2에서는 History와 Task 난이도를 고정한 채 Budget만 단계적으로 줄인다.

### 6.2 Conversation Length

필수 실험에서 History Length는 25K tokens로 고정한다. History 길이를 변화시키는 별도 실험은 선택적 보조 분석이며 Budget/Horizon 실험의 필수 축이 아니다. 전체 History 길이와 중요한 정보부터 Task까지의 거리(Horizon)는 별도 기록한다.

### 6.3 Horizon / Temporal Distance

필요한 정보가 현재 query에서 얼마나 멀리 떨어져 있는지를 측정한다.

```text
Near
Medium
Far
```

또는 turn distance:

```text
10 turns
50 turns
100 turns
```

이를 통해 오래된 정보를 찾는 능력을 평가한다.

`History Length`는 전체 interaction의 길이이고, `Horizon (H)`은 행동에 필요한 중요 정보가 제시된 시점부터 최종 Task까지의 turn 또는 token 거리이다. 두 값을 별도 변수로 기록하여 단순한 History 증가와 장기간 정보 유지 부담을 구분한다.

### 6.4 Noise

필수 실험에서는 Noise Memory ratio를 0.5로 고정한다. Noise 수준을 변화시키는 robustness 분석은 선택적 `stress.json`에서 수행한다.

예시:

```text
Low Noise
Medium Noise
High Noise
```

필요한 경우 noise ratio를 정량적으로 정의한다.

Noise Memory ratio는 후보 또는 제공 Memory token 중 최종 행동에 불필요한 Memory token의 비율로 정의하고, 사전에 정한 Low/Medium/High 수준으로 조절한다.

### 6.5 Constraint와 State Update

필수 실험에서는 Constraint 수 3, State 변경 횟수 2로 고정한다. 다음 변수의 변화는 핵심 RQ2와 구분하는 선택적 stress 분석이다.

- 적용되는 Constraint 수
- State 변경 횟수
- 이전 State와 최신 State 사이의 거리
- 누락되거나 잘못 선택된 필수 Memory의 비율

Memory Corruption은 저장 장치의 물리적 손상이 아니라 필요한 정보의 누락, 잘못된 선택 또는 활용으로 행동이 실패하는 현상으로 정의한다.

### 6.6 Task Type

필수 `main.json`은 `temporal_update`와 `approval` Tool 행동 Task를 사용한다. `temporal_update`에는 현재 값 요청과 Historical Query를 포함한다. Single Fact, Multi-hop, Abstention 및 응답 전용 문제는 선택적 확장이며 Action-Level TSR과 별도 집계한다. 기존 평가 기능은 유지한다.

#### Single Fact Retrieval

하나의 과거 사실을 찾아야 하는 문제.

#### Multi-hop Reasoning

여러 시점의 정보를 결합해야 하는 문제.

#### Temporal / Update

시간에 따라 변경된 정보에서 Task가 요구하는 현재 값 또는 특정 과거 버전을
찾아야 하는 문제. 현재·과거 버전 비교도 포함한다.

#### Abstention

conversation에 정답 정보가 존재하지 않는 경우 모델이 임의의 답을 생성하지 않는지 평가한다.

---

## 7. Dataset Strategy

필수 실험은 통제된 synthetic Tool 행동 데이터로 수행한다. 공개 benchmark 적용은 7주 내 구현 여유가 있을 때 수행하는 선택적 보조 실험이다.

### Existing Benchmark

선택적 확장으로 장기 Agent/Memory 공개 benchmark 적용을 검토한다.

후보:

- LongMemEval
- LoCoMo
- MemoryAgentBench

최종 benchmark는 프로젝트 진행 상황과 구현 호환성을 고려하여 결정한다.

### Fresh Synthetic Dataset

공개 benchmark의 contamination 가능성을 보완하기 위해 실험 과정에서 새로운 synthetic conversation을 생성한다.

예:

```text
Turn 12
Project Zelora-71 uses Database-X13.

...

Turn 250
What database does Project Zelora-71 use?
```

모델이 사전학습으로 알 수 없는 entity와 관계를 사용한다.

Synthetic dataset에서는 다음 변수를 통제·기록한다. 필수 sweep은 Budget과 Horizon뿐이며 나머지 값은 고정한다. 아래 사실 응답 예시는 선택적 확장이고 필수 Tool 행동 구성은 8.1절을 따른다.

- Conversation length
- Fact position
- Temporal distance
- Noise ratio
- Number of relevant facts
- Fact update 여부
- Constraint 수
- State update 횟수
- Context Token Budget

---

## 8. Research Question별 실험 설계

기준 문서는 [research_questions.md](research_questions.md)이며 핵심 RQ는 두 개다. 아래는 실행 계획이며 측정 결과가 아니다.

| RQ | 비교 및 통제 | 독립 변수 | 주요 지표 | 분석 |
| --- | --- | --- | --- | --- |
| RQ1 | 동일 Agent, Model, Tool, Task, History, Budget에서 Proposed와 Sliding Window, Vector Top-k, Utility-per-Token 비교 | Memory strategy | Action-Level TSR, CVR, RSA, SSUR | 동일 Budget/Horizon 셀의 평균과 95% CI |
| RQ2 | History 길이, Task 구성, Noise, 제약 수, 업데이트 수 고정 | Budget 1K/2K/4K/8K × Horizon 10/50/100 turns | TSR, CVR, RSA, SSUR; omission/error 진단 | Horizon 고정/Budget 감소 및 Budget 고정/Horizon 증가에 따른 행동 오류 억제 비교 |

### 8.1 필수 main 실험과 데이터 통제

`main.json`의 12개 Budget × Horizon 셀을 네 방식에 공통 적용한다. RQ1은 각 셀의 작업 성공률 비교이고 RQ2는 같은 결과의 Budget/Horizon 추세 비교다. History Length는 25K tokens로 고정하며 독립적인 필수 sweep으로 두지 않는다. Noise 0.5, Constraint 수 3, State 변경 수 2도 고정한다.

각 seed/셀의 20개 sample은 temporal_update 10개(현재 값 요청 5개, 과거 버전 요청 5개)와 approval 10개로 배정한다. 현재·과거 요청은 `state_scope`와 명시적 `expected_value`로 판정하며 `is_active`로 정답을 추정하지 않는다. Task 유형과 State scope는 metadata에 기록하여 유형별로도 집계한다. 이 배정은 향후 생성기의 구현 계약이며 현재 자동 생성되지는 않는다.

같은 seed/sample의 원본 History와 정답을 모든 strategy에서 공유한다. Budget만 바꾸는 비교에서는 동일 sample_id를 유지한다. Horizon 변화는 총 History 길이를 유지하면서 중요 정보의 위치를 조절하고 Task 난이도·업데이트 관계·정답을 유지한다. sample_id에는 데이터 seed와 기본 Task identity를 포함하며 strategy/Budget/repeat를 포함하지 않는다. 실제 token 수와 Horizon 가능 여부는 생성 단계에서 검증한다. 조건에 따라 Task 내용까지 달라지는 혼동을 피한다.

### 8.2 RQ2의 선택적 성공률 격차 분석

필요하면 각 baseline에 대해 `ΔSuccess(B,H) = TSR_proposed(B,H) − TSR_baseline(B,H)`를 보고한다. H를 고정하고 B가 감소할 때, B를 고정하고 H가 증가할 때 격차가 어떻게 변하는지 분석한다. 격차 확대를 연구 결과로 전제하지 않는다. 동일 셀의 sample_id/repeat_id를 pair로 맞추고 sample 단위 bootstrap CI와 unmatched 수를 보고한다. 모든 방식이 실패하는 극단 조건의 격차 감소도 확인한다. 핵심 RQ2의 CVR/RSA/SSUR 비교를 ΔSuccess로 대체하지 않는다.

### 8.3 선택적 보조 실험과 Long Context

- `ablation.json`: Protected Memory, State Versioning, Budget Optimization을 각각 제거하여 기여도 분석. 예산 상한은 유지한다.
- `stress.json`: 제약 수 1/3/5 × State 변경 0/2/5 × Noise 0.1/0.5/0.9. 핵심 RQ2의 Budget/Horizon 실험과 구분한다.
- `reference.json`: 큰 예산 128K에서 전체 History를 제공하는 별도 참고 결과. main과 같은 seed/Task/Horizon을 사용하고 제한 Budget 순위나 paired ΔSuccess에 섞지 않는다.
- History 길이 sweep, Hybrid, 공개 benchmark, 응답 전용 Task는 선택적 확장이다. 현재 필수 설정에는 포함하지 않는다.

Long Context는 전체 원본 History와 고정 입력이 동일 Budget 및 모델 context window에 들어갈 때만 직접 비교한다. 현재 main의 25K History는 1K~8K Budget을 초과하므로 직접 비교 대상이 아니다. 향후 History가 들어가는 공통 조건을 추가하면 모든 strategy를 같은 sample/Budget에서 실행하고 Long Context를 포함한다. 이를 위한 추가 실행 수는 아래 기본 규모에 포함하지 않는다. 잘린 History를 Full Context로 부르지 않는다.

### 8.4 7주 실행 범위와 변경 전후 규모

1~4주차는 생성기, Baseline, Proposed 및 모델/tokenizer 연결과 smoke 검증을 우선한다. 5주차는 main의 RQ1/RQ2 필수 실험, 6주차는 실패 분석과 여유가 있을 때 보조 실험, 7주차는 필요한 재현 실행과 발표 준비에 사용한다. 전체 보조 설정의 실행은 필수 완료 조건이 아니다.

실행 수는 `grid 조건 수 × strategy 수 × seed 수 × repeat × samples_per_cell`로 계산한다. sample 수는 task 유형별 수가 아니라 각 seed/조건의 전체 수이다. 한 Agent 실행에는 여러 LLM/Tool 호출이 있을 수 있으므로 아래 숫자는 API 호출 수나 비용 추정치가 아니다. 예산 중단도 예정된 실행 단위에 포함한다.

| 설정 | 필수 여부 | 조건 수 전→후 | seed 수 전→후 | repeat 전→후 | sample/seed/조건 전→후 | 방식 수 | Agent 실행 수 전→후 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| main | 필수 RQ1/RQ2 | 36→12 | 3→2 | 3→2 | 30→20 | 4 | 38,880→3,840 |
| smoke | 필수 통합 확인 | 1→1 | 1→1 | 1→1 | 5→5 | 4 | 20→20 |
| stress | 선택적 보조 | 27→27 | 3→1 | 3→2 | 30→10 | 4 | 29,160→2,160 |
| ablation | 선택적 보조 | 12→2 | 3→2 | 3→2 | 30→20 | 4 | 12,960→640 |
| reference | 선택적 고예산 참고 | 9→3 | 3→2 | 3→2 | 30→20 | 1 | 2,430→240 |

변경 전 모든 설정의 합계는 **83,450회**, 변경 후 합계는 **6,900회**이다. 변경 전 main+smoke는 38,900회였고, 새 필수 범위는 **3,860회**, 선택적 보조 설정은 **3,040회**이다. 기존 구성은 stress를 핵심 RQ2로 명명했으므로 변경 전에는 main+stress+smoke 68,060회가 핵심 범위로 읽힐 수 있었다.

main은 History 길이 3수준을 25K 하나로 고정하고 seed를 `[42, 43]`, repeat를 2, sample을 20으로 줄였다. ablation은 Budget 2K/4K, Horizon 100만 사용한다. stress는 제약 수 × 업데이트 수 × Noise의 27개 셀을 보조 분석으로 유지하되 seed `[42]`, repeat 2, sample 10으로 줄였다. reference는 main과 같은 25K History와 Horizon 3수준을 사용한다. smoke는 그대로 유지한다.

반복 축소는 실행 부담을 줄이는 계획이며 통계적 검정력을 보장하지 않는다. CI와 적용 sample 수를 보고하고, 넓은 CI나 결과 불안정성이 확인되면 시간·비용 여유에 따라 반복을 늘린다. 중단·실패 실행과 누락된 pair도 보고한다.

`expand_config()`는 sample 생성 전 계획 행만 만든다. 변경 후 계획 행 수는 main 192, smoke 4, stress 216, ablation 32, reference 12이다. 여기에 각 설정의 samples_per_cell을 곱하면 위 실행 수가 된다.

---

## 9. Evaluation Output Format

다음은 `evaluator.py`가 출력하는 주요 필드의 발췌 예시다. 최초 일반 검색에서는
과거 버전만 찾았지만, 버전 확장과 별도 보호 검색을 거쳐 필요한 기억이 최종
Context에 모두 포함되고 행동도 성공한 경우다. 전체 입력 계약은
[evaluation_usage.md](evaluation_usage.md)를 따른다.

```json
{
  "sample_id": "seed42_report_001",
  "strategy": "proposed",
  "repeat_id": 0,
  "pipeline_status": "ok",
  "context_token_budget": 2048,
  "fixed_input_tokens": 310,
  "available_memory_tokens": 1738,
  "history_length_tokens": 10000,
  "horizon_turns": 50,
  "constraint_count": 1,
  "state_update_count": 2,
  "noise_memory_ratio": 0.5,
  "retrieved_memory_ids": ["report_v1"],
  "selected_memory_ids": ["report_final", "approval_rule"],
  "target_memory_ids": ["report_final", "approval_rule"],
  "retrieval_hit": false,
  "recall_at_k": 0.0,
  "general_retrieval_miss": true,
  "general_retrieval_missing_target_ids": ["report_final"],
  "recovered_target_ids": ["report_final"],
  "selection_failure": false,
  "missing_selected_target_ids": [],
  "budget_selection_failure": false,
  "applicable_protected_recall": 1.0,
  "temporal_version_required": true,
  "temporal_version_correct": true,
  "stale_memory_count": 1,
  "stale_selected_memory_count": 0,
  "task_success": true,
  "constraint_violation_count": 0,
  "constraint_violation_rate": 0.0,
  "required_state_accuracy": 1.0,
  "current_state_accuracy": 1.0,
  "current_state_requirement_count": 1,
  "stale_state_usage_count": 0,
  "stale_state_usage_rate": 0.0,
  "stale_state_used": false,
  "state_omission_count": 0,
  "state_error_count": 0,
  "approval_behavior_correct": true,
  "mandatory_memory_overflow": null,
  "input_tokens": 390,
  "output_tokens": 18,
  "planning_latency_ms": 250,
  "total_latency_ms": 1200,
  "total_llm_cost": null,
  "failure_types": []
}
```

최초 검색 누락이 복구됐으므로 `general_retrieval_miss`는 true지만,
`selection_failure`와 `task_success`는 각각 false와 true다.
`current_state_accuracy`는 `required_state_accuracy`와 동일 값인 호환용 출력
필드다. 새 집계에서는 `required_state_accuracy`를 사용한다. 과거 항목만 있는
실행에서는 `current_state_requirement_count=0`, `stale_state_usage_count=0`이고
`stale_state_usage_rate`와 `stale_state_used`는 null이다.

## Memory Schema와 Evaluation Schema의 분리 이유

공통 Memory schema와 Evaluation output schema는 저장 대상과 목적이 서로 다르므로 동일한 형태를 사용하지 않는다.

공통 Memory schema는 **Memory 하나의 영속적인 상태**를 표현한다. 따라서 `session_id`, `turn_id`, `memory_type`, `content`, `importance`, `created_at`, `valid_from`, `valid_to` 등 Memory 생성·저장·갱신에 필요한 정보를 포함한다.

반면 Evaluation schema는 **특정 실험 실행 한 번의 결과**를 표현한다. 따라서 다음 정보를 중심으로 기록한다.

- 어떤 Memory를 검색했는지
- 정답 Memory를 검색했는지
- 검색 당시 Memory가 활성·보호·대체 상태였는지
- 설정된 최대 토큰과 실제 사용 토큰이 얼마인지
- 최종 응답과 retrieval 성능이 어떠했는지

Evaluation schema의 선택적 `retrieved_memories`는 별도의 Memory schema가 아니라,
공통 Memory schema에서 평가에 필요한 필드만 가져온 **최초 일반 검색 시점의 상태
snapshot**이다. 현재 evaluator는 이 snapshot을 입력에서 출력으로 보존하지만,
deterministic 판정에는 직접 사용하지 않는다.

```json
{
  "memory_id": "mem_001",
  "is_active": false,
  "is_protected": true,
  "supersedes": null,
  "superseded_by": "mem_002",
  "token_count": 10
}
```

이 snapshot을 기록하는 이유는 실험 이후 Memory store의 상태가 변경되더라도, 실행 당시 검색된 Memory가 최신 상태였는지, 이미 대체된 Memory였는지, 보호 대상이었는지를 재현할 수 있도록 하기 위함이다.

`retrieved_memory_ids`와 `retrieved_memories`를 모두 기록하는 이유는 다음과 같다.

- `retrieved_memory_ids`: 최초 General Retrieval Top-K의 ID 순서. 일반 검색 대상에 대한 Recall@k, Precision@k, Hit@k, MRR 계산에 사용
- `retrieved_memories`: 제공된 경우 최초 검색 당시 상태 snapshot을 보존해 사후 분석에 사용. 현재 stale 판정은 이 snapshot 대신 `wrong_version_memory_ids`와 ID 교집합으로 계산
- `target_memory_ids`: evaluation sample의 정답 Memory 집합
- `context_token_budget`: 해당 실험 실행에 설정된 전체 Agent 입력 Context의 token 상한
- `fixed_input_tokens`: System Prompt, 현재 요청과 Tool 정의 등 고정 입력의 token 수
- `available_memory_tokens`: 전체 Context 상한에서 고정 입력을 제외한 Memory 가용 token 수
- `retrieval_tokens`: 실행 adapter가 제공한 경우에만 기록하는 검색 token 수
- `input_tokens`: 실행기가 제공한 실제 입력 측정값. 현재 Agent에서는 여러 호출 중 최대 단일 입력 크기이며, 누적 비용용 입력 합계가 아님
- `output_tokens`: 실행기가 제공한 출력 token 수. 현재 Agent에서는 실행 전체의 누적 출력이 아님
- `selected_memory_ids`: 일반 검색, 버전 확장, 보호 검색 및 예산 선택 이후 실제 LLM Context에 포함된 Memory 집합
- `temporal_version_required`: 해당 sample에서 State version 선택이 필요한지 여부
- `temporal_version_correct`: 실제 선택된 Memory가 해당 시점의 올바른 State version인지 여부
- `stale_selected_memory_count`: 실제 LLM context에 포함된 Memory 중 `wrong_version_memory_ids`에 속하는 수. 과거 요청의 정답은 superseded되어도 제외

따라서 Evaluation schema는 공통 Memory schema를 대체하거나 새롭게 정의하는 것이 아니다. 공통 Memory schema를 입력으로 사용하고, 그중 평가에 필요한 상태를 snapshot으로 보존한 뒤 실험 조건과 계산된 metric을 추가한 실행 결과 schema이다.

```text
Common Memory Schema
        |
        | retrieval
        v
Retrieved Memory Snapshot
        +
Experiment Configuration
        +
Evaluation Metrics
        |
        v
Evaluation Result
```

이 구조에서 최초 일반 검색 누락(`general_retrieval_miss`), 최종 Context의 필수
Memory 누락(`selection_failure`), 근거가 확인된 예산 누락
(`budget_selection_failure`)을 별도로 기록한다. 시점 버전 선택 오류
(`temporal_failure`)와 실제 행동에서의 오래된 State 사용(`stale_state_usage`)도
구분한다. 원인 추정에 필요한 중간 기록이 없으면 원인을 단정하지 않는다.

External Memory 방식에서는 추가로 다음 정보를 기록한다.

```json
{
  "retrieved_memory_ids": [
    "mem_018",
    "mem_042",
    "mem_103"
  ],
  "target_memory_ids": [
    "mem_042"
  ],
  "retrieval_hit": true
}
```

---

## 10. Aggregated Results

실험 종료 후 최소한 다음 결과를 strategy별로 집계한다.

| Strategy | TSR | CVR | RSA | SSUR | TVA | Recall@k | Avg. Input Tokens | Avg. Planning Latency | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Proposed | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Sliding Window | TBD | TBD | TBD | TBD | TBD | - | TBD | TBD | TBD |
| Vector Top-k | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Utility-per-Token | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |


고예산 참고 결과는 다음 별도 표로 보고한다. 전체 History가 같은 Budget에 들어가는 직접 비교 조건을 추가한 경우에만 해당 동일 Budget 표에 Long Context를 포함한다.

| 별도 참고 설정 | Budget | Action-Level TSR | CVR | RSA | SSUR | Tokens | Cost |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Long Context Reference | 131072 | TBD | TBD | TBD | TBD | TBD | TBD |

필수 결과는 Budget × Horizon × strategy와 Task 유형/State scope별로 분석한다. Noise, Constraint 수, State 변경 수, History 길이별 분석은 선택적 보조 결과로 구분한다. 고예산 Long Context Reference는 별도 표에 보고한다.

모든 주요 집계에는 반복 횟수, 평균, 95% 신뢰구간과 지표 적용 실행 수를 포함한다. RQ2의 선택적 ΔSuccess 분석에는 baseline별 차이와 CI를 추가한다. 위 TBD 표는 출력 계획이며 측정 결과가 아니다.

---

## 11. Failure Analysis

단순 평균 성능뿐만 아니라 실패 지표를 분석한다. `failure_types`에 기록되는
현재 코드의 명칭은 다음과 같다. 여러 유형이 한 실행에 동시에 기록될 수 있다.

| 유형 | 기록 조건 |
|---|---|
| `budget_failure` | 파이프라인이 중단됐거나 입력 예산을 넘은 경우 |
| `selection_failure` | 필수 target이 최종 Context에서 빠진 경우 |
| `budget_selection_failure` | 최종 누락에 예산 원인 기록이 있는 경우 |
| `temporal_failure` | 필요한 버전 선택이 틀린 경우 |
| `constraint_failure` | 금지 행동 또는 적용 제약 위반이 있는 경우 |
| `approval_failure` | 승인 없이 실행했거나 필요한 승인 요청이 없는 경우 |
| `state_omission` | 행동에 필요한 State 값을 누락한 경우 |
| `stale_state_usage` | 현재 값을 요구하는 행동에 superseded된 이전 State 값을 사용한 경우 |
| `state_error` | 행동에 기대값과 다르고 stale 사용으로 분류되지 않는 값을 사용한 경우. 과거 요청에 최신 값을 사용한 경우도 포함 |
| `reasoning_action_failure` | 다른 기록된 실패 없이 작업이 성공하지 못한 경우 |

`general_retrieval_miss`는 최초 검색의 진단 지표이며 `failure_types`에는 넣지
않는다. `reasoning_action_failure`도 실제 원인이 추론인지, Tool 실행인지 등을
확정하는 값은 아니다. `Context Failure`, `Hallucination`, `active_target_hit`,
`protected_memory_recall`은 현재 evaluator의 출력 유형이나 필드가 아니다.

실험 결과에서는 각 strategy가 어떤 유형의 failure에 취약한지 분석한다.

---

## 12. Responsibilities

본 프로젝트에서는 팀원 간 작업 충돌을 방지하기 위해 담당 영역을 분리한다.

### Evaluation 담당

담당 범위:

- `experiment_plan.md`
- `evaluator.py`
- Evaluation metric 정의
- 실험 결과 집계
- Long Context / Memory 비교 평가
- Failure analysis

Evaluation 코드는 다른 모듈의 내부 구현을 직접 수정하지 않는다. 필요한 입력은 각 담당 모듈에서 제공하는 interface를 통해 전달받는다.

예:

```text
Memory Module
    |
    | retrieved memories
    v
Evaluator

Long Context Module
    |
    | generated response
    v
Evaluator
```

---

## 13. Implementation Order

평가 파트는 다음 순서로 구현한다.

```text
1. RQ별 성공 기준과 Experiment schema 확정
       ↓
2. evaluator.py 기본 구조
       ↓
3. Exact Match / F1 구현
       ↓
4. Token / Latency logging
       ↓
5. Retrieval metric 구현
       ↓
6. Sliding Window / Vector Top-k / Utility-per-Token baseline 평가
       ↓
7. Proposed / Long Context Reference 평가
       ↓
8. 조건별 실험
       ↓
9. Failure analysis / ΔSuccess / Ablation 분석
```

초기 단계에서는 복잡한 자동 평가보다 **재현 가능한 deterministic metric과 logging pipeline을 먼저 완성하는 것**을 우선한다.

## 14. 현재 실행 가능 범위

Offline evaluator의 snapshot 평가·JSONL 집계·plan-only는 사용 가능하다. `benchmark/generator.py`는 빈 파일이며 grid에 맞는 데이터 생성, Task/scope 배정과 sample identity 통제를 아직 구현해야 한다. Memory Store, Query Analyzer, 검색·선택 함수는 스켈레톤이고 Baseline/Ablation 실행기는 연결되지 않았다. 모델/provider가 null이며 실제 LLM planner, tokenizer/API 사용량 측정 및 실행 결과 adapter가 필요하다.

현재 `agent/agent.py`와 `prototype/run_demo.py`는 원격 main에서 삭제된 `agent/context.py`를 import한다. 따라서 Context Builder 연결 복구 전에는 Agent/데모도 실행할 수 없다. 실험 계획, 합성 token 수, oracle snapshot 테스트는 실제 모델 성능 측정 결과가 아니다.
