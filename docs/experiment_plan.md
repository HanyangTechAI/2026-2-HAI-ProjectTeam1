# Experiment Plan

평가 코드 및 설정 사용법: [evaluation_usage.md](evaluation_usage.md).
`benchmark/evaluator.py`는 전달받은 Scenario/실행 snapshot의 deterministic 평가,
JSONL 출력, sample 단위 bootstrap 집계와 paired ΔSuccess를 구현한다.
실험 설정은 `experiments/configs/`에 있으며, 실제 Agent 호출과 데이터 생성은
각 담당 모듈에서 연결해야 한다.

## 1. Experiment Objective

본 실험의 목적은 제한된 Context Token Budget에서 행동에 필수적인 제약과 최신 상태를 우선 보존하는 **Constraint-Preserving Budget-Aware Memory**가 Long-Horizon Tool-Using Agent의 작업 성공률과 신뢰성을 개선하는지 검증하는 것이다.

동일한 LLM, Tool, Task, 원본 interaction history와 실행 조건을 사용하되, 모델에게 과거 정보를 제공하는 Memory strategy만 변경하여 평가한다. 제안 방식의 성능 개선은 실험으로 검증할 가설이며 결과로 전제하지 않는다.

주요 비교 대상은 다음과 같다.

1. **Constraint-Preserving Budget-Aware Memory (Proposed)**
   - Protected Memory, Current State와 State Versioning을 우선 보존한다.
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

Protected Memory와 Current State만으로 가용 Memory Budget을 초과하는 조건은 `mandatory_memory_overflow`로 별도 표시한다. 이 조건에서도 제안 방식만 예산을 초과하도록 허용하지 않으며, 필수 정보 보존이 가능한 범위와 실패 한계를 별도로 보고한다.

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

### 4.1 Task Success Rate

Task Success는 단순한 최종 답변 일치가 아니라 다음 조건을 모두 만족하는 행동 수준의 성공으로 정의한다.

1. Task의 목표를 완료한다.
2. 해당 시점에 적용되는 Constraint를 위반하지 않는다.
3. 행동에 필요한 최신 Current State를 사용한다.
4. 승인이 필요한 Task에서는 승인 전 실행하지 않고 올바르게 승인을 요청한다.

sample별 성공 여부를 이진 값으로 기록하고, 조건별 `Task Success Rate (TSR)`를 계산한다. Task별 세부 성공 조건과 ground truth는 실험 전에 고정한다.

### 4.2 Constraint 및 State 지표

- **Constraint Violation Rate (CVR):** 적용 대상 Constraint 가운데 Agent 행동이 위반한 비율. 실행 단위 위반 여부도 함께 기록한다.
- **Current-State Accuracy (CSA):** 행동에 필요한 State 항목 가운데 최신 유효 값을 올바르게 사용한 비율.
- **Stale-State Usage Rate (SSUR):** 최신 State가 존재하지만 Agent가 superseded된 이전 값을 행동에 사용한 비율.
- **State Omission Rate:** 필요한 State를 행동에서 누락한 비율.
- **State Fabrication/Error Rate:** History에 없는 값 또는 최신·과거 버전 모두와 일치하지 않는 값을 사용한 비율.

Stale-State Usage는 오래된 Memory의 검색 여부가 아니라 **최종 행동에서 이전 State를 실제 사용했는지**를 기준으로 판정한다. 따라서 stale retrieval과 stale usage를 별도 필드로 기록한다.

### 4.3 Answer Accuracy

최종 LLM 응답이 ground-truth answer와 일치하는지 평가한다. 가능한 경우 자동 평가를 우선 사용한다.

예:

- Exact Match
- Normalized Exact Match
- F1 Score

자유형 응답처럼 deterministic evaluation이 어려운 경우에는 별도의 LLM-based evaluator 사용을 고려한다.

### 4.4 Retrieval Accuracy

External Memory 방식에서는 최종 답변뿐만 아니라 **필요한 memory를 실제로 retrieval했는지**도 평가한다.

가능한 경우 각 evaluation sample에 정답과 관련된 `target_memory_id`를 기록한다.

평가 후보:

- Recall@k
- Precision@k
- Hit@k
- MRR

이를 통해 다음 두 실패를 구분한다.

```text
Retrieval Failure
필요한 memory 자체를 찾지 못함

Reasoning Failure
필요한 memory를 찾았지만 LLM이 정답 생성에 실패함
```
### 4.5 Temporal Version Accuracy

State update가 존재하는 sample에서는 retrieval 이후 실제 LLM context에 포함된 Memory가 해당 시점에 유효한 버전인지 평가한다.

각 sample에 대해 다음 값을 기록한다.

* `selected_memory_ids`: retrieval 이후 temporal resolution 및 budget selection을 거쳐 실제 LLM context에 포함된 Memory ID
* `temporal_version_required`: 해당 sample에서 동일 State의 여러 버전 중 올바른 버전을 선택해야 하는지 여부
* `temporal_version_correct`: 필요한 temporal version을 올바르게 선택했는지 여부
* `stale_selected_memory_count`: 최종 선택된 Memory 가운데 이미 superseded된 stale Memory의 수

Temporal Version Accuracy (TVA)는 temporal version 판단이 필요한 sample만 대상으로 다음과 같이 계산한다.

TVA = 올바른 temporal version을 선택한 sample 수 / temporal version 판단이 필요한 sample 수

이를 통해 필요한 Memory 자체를 검색하지 못한 Retrieval Failure와, 필요한 버전이 후보에 존재하지만 잘못된 버전을 선택한 Temporal Failure를 구분한다.

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

모델과 Tool 정의의 고정 입력이 1K 조건을 성립시키지 못하는 경우 실제 적용 가능한 최솟값을 사전에 정하고 변경 사유를 기록한다. RQ3에서는 History와 Task 난이도를 고정한 채 Budget만 단계적으로 줄인다.

### 6.2 Conversation Length

장기 interaction의 길이에 따라 성능이 어떻게 변하는지 측정한다.

예시:

```text
Short
Medium
Long
Very Long
```

또는 token 기준으로:

```text
10K
25K
50K
100K+
```

실제 값은 사용하는 모델의 context window와 dataset에 맞추어 결정한다.

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
500 turns
```

이를 통해 오래된 정보를 찾는 능력을 평가한다.

`History Length`는 전체 interaction의 길이이고, `Horizon (H)`은 행동에 필요한 중요 정보가 제시된 시점부터 최종 Task까지의 turn 또는 token 거리이다. 두 값을 별도 변수로 기록하여 단순한 History 증가와 장기간 정보 유지 부담을 구분한다.

### 6.4 Noise

필요한 정보 사이에 관련 없는 conversation을 추가하여 noise에 대한 robustness를 측정한다.

예시:

```text
Low Noise
Medium Noise
High Noise
```

필요한 경우 noise ratio를 정량적으로 정의한다.

Noise Memory ratio는 후보 또는 제공 Memory token 중 최종 행동에 불필요한 Memory token의 비율로 정의하고, 사전에 정한 Low/Medium/High 수준으로 조절한다.

### 6.5 Constraint와 State Update

Memory corruption에 대한 강건성을 평가하기 위해 다음 변수를 독립적으로 조절한다.

- 적용되는 Constraint 수
- State 변경 횟수
- 이전 State와 최신 State 사이의 거리
- 누락되거나 잘못 선택된 필수 Memory의 비율

Memory Corruption은 저장 장치의 물리적 손상이 아니라 필요한 정보의 누락, 잘못된 선택 또는 활용으로 행동이 실패하는 현상으로 정의한다.

### 6.6 Task Type

최소한 다음 유형을 고려한다.

#### Single Fact Retrieval

하나의 과거 사실을 찾아야 하는 문제.

#### Multi-hop Reasoning

여러 시점의 정보를 결합해야 하는 문제.

#### Temporal / Update

시간에 따라 변경된 정보에서 현재 유효한 값을 찾아야 하는 문제.

#### Abstention

conversation에 정답 정보가 존재하지 않는 경우 모델이 임의의 답을 생성하지 않는지 평가한다.

---

## 7. Dataset Strategy

평가는 가능하면 두 종류의 데이터로 진행한다.

### Existing Benchmark

장기 Agent/Memory 평가를 위해 공개된 benchmark를 사용한다.

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

Synthetic dataset에서는 다음 변수를 직접 통제한다.

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

| RQ | 비교 및 통제 | 독립 변수 | 주요 종속 변수 | 분석 |
|---|---|---|---|---|
| RQ1 | Proposed vs Sliding Window vs Vector Top-k vs Utility-per-Token; Agent, Task, History, Budget 고정 | Memory strategy | TSR, CVR, CSA | 동일 Budget에서 strategy별 평균과 95% CI 비교 |
| RQ2 | Long Context와 External Memory 계열 비교 | Constraint 수, State update 횟수, Noise Memory ratio, Horizon | TSR, failure type, stale usage | corruption 유형별 강건성 비교 |
| RQ3 | History와 Task 난이도 고정 | Budget 1K/2K/4K/8K | CVR, SSUR, CSA, omission/error rate | Budget 감소에 따른 오류 증가 추세와 Proposed의 완화 효과 |
| RQ4 | 모든 Memory strategy에 동일한 교차 조건 적용 | History Length × Budget; Horizon 별도 기록 | TSR, CVR, CSA, tokens, cost, planning latency | 성능 저하 구간과 성능·비용·신뢰성 trade-off 분석 |
| RQ5 | Proposed와 각 baseline을 쌍별 비교 | Budget `B`, Horizon `H` | `ΔSuccess(B,H)` | Budget 고정/Horizon 변화와 Horizon 고정/Budget 변화의 성공률 격차 및 CI 분석 |

### 8.1 RQ4 교차 실험

History Length와 Token Budget을 독립적으로 변화시키는 full-factorial grid를 기본으로 한다. 각 `(History Length, B)` 셀에서 동일한 sample과 반복 조건으로 모든 strategy를 실행한다. Horizon은 History Length와 별도로 기록하고, 가능한 경우 Near/Medium/Far 층화 결과도 보고한다.

### 8.2 RQ5 성공률 격차

각 baseline에 대해 다음 값을 계산한다.

```text
ΔSuccess(B, H)
  = TSR_proposed(B, H) - TSR_baseline(B, H)
```

- `H`를 고정하고 `B`가 감소할 때 격차가 커지는지 확인한다.
- `B`를 고정하고 `H`가 증가할 때 격차가 커지는지 확인한다.
- paired bootstrap 또는 동일 sample 기반의 적절한 방법으로 95% 신뢰구간을 계산한다.
- 모든 방식의 TSR이 바닥 수준에 도달하는 극단 조건에서는 격차가 다시 감소하는지도 별도로 확인한다.

### 8.3 Ablation Study

제안 방식의 각 구성 요소 기여를 확인하기 위해 동일 조건에서 다음 ablation을 수행한다.

1. Protected Memory 제거
2. State Versioning 제거
3. Budget Optimization 제거

각 ablation은 완전한 Proposed 방식과 TSR, CVR, CSA, SSUR, TVA 및 token usage를 비교한다.
---

## 9. Evaluation Output Format

`evaluator.py`는 최소한 다음 결과를 기록할 수 있도록 구현한다.

```json
{
  "sample_id": "sample_001",
  "strategy": "external_memory",
  "context_token_budget": 2048,
  "fixed_input_tokens": 310,
  "available_memory_tokens": 1738,
  "history_length_tokens": 12000,
  "horizon_turns": 100,
  "constraint_count": 3,
  "state_update_count": 2,
  "noise_memory_ratio": 0.5,
  "retrieved_memories": [
    {
      "memory_id": "mem_001",
      "is_active": false,
      "is_protected": true,
      "supersedes": null,
      "superseded_by": "mem_002",
      "token_count": 10
    }
  ],
  "retrieved_memory_ids": ["mem_001"],
  "selected_memory_ids": ["mem_001"],
  "target_memory_ids": ["mem_002"],

  "retrieval_hit": false,
  "active_target_hit": false,

  "temporal_version_required": true,
  "temporal_version_correct": false,

  "stale_memory_count": 1,
  "stale_selected_memory_count": 1,

  "task_success": false,
  "constraint_violation_count": 0,
  "constraint_violation_rate": 0.0,
  "current_state_accuracy": 0.0,
  "stale_state_used": true,
  "state_omission_count": 0,
  "state_error_count": 0,
  "approval_behavior_correct": null,
  "mandatory_memory_overflow": false,
  "protected_memory_recall": 1.0,
  "retrieval_tokens": 10,
  "input_tokens": 320,
  "output_tokens": 18,
  "planning_latency_ms": 250,
  "total_latency_ms": 1200,
  "total_llm_cost": 0.0012
}
```
## Memory Schema와 Evaluation Schema의 분리 이유

공통 Memory schema와 Evaluation output schema는 저장 대상과 목적이 서로 다르므로 동일한 형태를 사용하지 않는다.

공통 Memory schema는 **Memory 하나의 영속적인 상태**를 표현한다. 따라서 `session_id`, `turn_id`, `memory_type`, `content`, `importance`, `created_at`, `valid_from`, `valid_to` 등 Memory 생성·저장·갱신에 필요한 정보를 포함한다.

반면 Evaluation schema는 **특정 실험 실행 한 번의 결과**를 표현한다. 따라서 다음 정보를 중심으로 기록한다.

- 어떤 Memory를 검색했는지
- 정답 Memory를 검색했는지
- 검색 당시 Memory가 활성·보호·대체 상태였는지
- 설정된 최대 토큰과 실제 사용 토큰이 얼마인지
- 최종 응답과 retrieval 성능이 어떠했는지

Evaluation schema의 `retrieved_memories`는 별도의 Memory schema가 아니라, 공통 Memory schema에서 평가에 필요한 필드만 가져온 **검색 시점의 상태 snapshot**이다.

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

- `retrieved_memory_ids`: Recall@k, Precision@k, Hit@k, MRR 등 ID 기반 metric 계산
- `retrieved_memories`: Temporal Failure, stale retrieval, protected memory 누락 등 상태 기반 failure analysis
- `target_memory_ids`: evaluation sample의 정답 Memory 집합
- `context_token_budget`: 해당 실험 실행에 설정된 전체 Agent 입력 Context의 token 상한
- `fixed_input_tokens`: System Prompt, 현재 요청과 Tool 정의 등 고정 입력의 token 수
- `available_memory_tokens`: 전체 Context 상한에서 고정 입력을 제외한 Memory 가용 token 수
- `retrieval_tokens`: 검색된 Memory가 실제 사용한 토큰
- `input_tokens`, `output_tokens`: 전체 LLM 요청의 실제 토큰 사용량
-  `selected_memory_ids`: retrieval된 후보 중 temporal resolution 및 budget selection 이후 실제 LLM context에 포함된 Memory 집합
- `temporal_version_required`: 해당 sample에서 State version 선택이 필요한지 여부
- `temporal_version_correct`: 실제 선택된 Memory가 해당 시점의 올바른 State version인지 여부
- `stale_selected_memory_count`: 실제 LLM context에 포함된 Memory 중 superseded된 stale Memory의 수

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

이 구조를 통해 공통 Memory schema와의 호환성을 유지하면서도 다음 실패를 구분할 수 있다.

- 필요한 Memory를 검색하지 못한 Retrieval Failure
- 대체된 Memory를 검색한 Temporal/Stale Failure
- 보호 Memory를 누락한 Protected Memory Failure
- 필요한 Memory를 검색했지만 답변에 실패한 Reasoning Failure
- 최대 토큰 조건을 초과한 Budget Failure

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

| Strategy | TSR | CVR | CSA | SSUR | TVA | Recall@k | Avg. Input Tokens | Avg. Planning Latency | Cost |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Proposed | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Sliding Window | TBD | TBD | TBD | TBD | TBD | - | TBD | TBD | TBD |
| Vector Top-k | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Utility-per-Token | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD | TBD |
| Long Context Reference | TBD | TBD | TBD | TBD | TBD | - | TBD | TBD | TBD |

추가적으로 다음 조건별 결과를 분석한다.

- Conversation Length
- Temporal Distance
- Noise Level
- Task Type
- Context Token Budget
- Constraint Count
- State Update Count
- Horizon
- History Length × Token Budget

모든 주요 집계에는 반복 횟수, 평균과 95% 신뢰구간을 포함한다. RQ5 결과에는 baseline별 `ΔSuccess(B,H)`와 그 신뢰구간을 추가한다.

---

## 11. Failure Analysis

단순 평균 성능뿐만 아니라 실패 원인을 분석한다.

실패 유형 예시:

```text
Retrieval Failure
  필요한 memory 검색 실패

Context Failure
  필요한 정보가 context에 있었지만 활용 실패

Reasoning Failure
  필요한 정보를 제공받았지만 추론 실패

Temporal Failure
  과거 정보와 최신 정보를 잘못 구분

Constraint Failure
  적용되는 제약을 누락하거나 위반

State Omission
  행동에 필요한 State를 사용하지 않음

State Fabrication/Error
  History에 없거나 어떤 유효 버전과도 일치하지 않는 State를 사용

Approval Failure
  승인이 필요한 행동을 승인 없이 실행하거나 필요한 승인 요청을 하지 않음

Hallucination
  제공되지 않은 정보를 생성
```

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
