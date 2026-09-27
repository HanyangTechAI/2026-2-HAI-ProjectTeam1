# Experiment Plan

## 1. Experiment Objective

본 실험의 목적은 장기 대화 환경에서 **Long Context와 External Memory 기반 접근법의 성능 및 효율성을 비교**하는 것이다.

동일한 LLM과 동일한 원본 conversation을 사용하되, 모델에게 과거 정보를 제공하는 방식만 변경하여 평가한다.

주요 비교 대상은 다음과 같다.

1. **Long Context**
   - 가능한 conversation history를 context window에 직접 제공한다.
2. **External Memory**
   - conversation에서 생성된 memory를 외부 저장소에 저장한다.
   - 현재 query와 관련된 memory를 retrieval하여 LLM에 제공한다.
3. **Hybrid** *(추후 실험)*
   - 최근 conversation context와 retrieved memory를 함께 제공한다.

핵심적으로 다음 질문을 확인한다.

> 동일한 장기 대화에서 Context와 External Memory는 각각 어떤 조건에서 강점과 약점을 보이는가?

---

## 2. Experimental Principle

비교의 공정성을 위해 **Memory strategy를 제외한 조건은 최대한 동일하게 유지한다.**

고정해야 하는 주요 조건:

- 동일 LLM
- 동일 system prompt
- 동일 conversation
- 동일 query
- 동일 decoding parameter
- 동일 evaluation method

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

---

## 3. Evaluation Targets

### 3.1 Long Context

전체 conversation history를 가능한 범위까지 직접 context로 제공한다.

```text
Conversation History
       +
Current Query
       |
       v
      LLM
```

모델의 최대 context window를 초과하는 경우에는 별도의 context policy를 적용하며, 사용된 정책과 실제 입력 token 수를 반드시 기록한다.

### 3.2 External Memory

conversation에서 생성된 memory를 외부 memory store에 저장한다. 현재 query가 입력되면 관련 memory를 검색하여 LLM에게 제공한다.

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

### 4.1 Answer Accuracy

최종 LLM 응답이 ground-truth answer와 일치하는지 평가한다. 가능한 경우 자동 평가를 우선 사용한다.

예:

- Exact Match
- Normalized Exact Match
- F1 Score

자유형 응답처럼 deterministic evaluation이 어려운 경우에는 별도의 LLM-based evaluator 사용을 고려한다.

### 4.2 Retrieval Accuracy

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

---

## 6. Experimental Variables

### 6.1 Conversation Length

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

### 6.2 Temporal Distance

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

### 6.3 Noise

필요한 정보 사이에 관련 없는 conversation을 추가하여 noise에 대한 robustness를 측정한다.

예시:

```text
Low Noise
Medium Noise
High Noise
```

필요한 경우 noise ratio를 정량적으로 정의한다.

### 6.4 Task Type

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

---

## 8. Evaluation Output Format

`evaluator.py`는 최소한 다음 결과를 기록할 수 있도록 구현한다.

```json
{
  "sample_id": "sample_001",
  "strategy": "external_memory",
  "task_type": "temporal_update",
  "query_at": "2026-09-27T10:00:00+09:00",

  "prediction": "report_final.pdf",
  "ground_truth": "report_final.pdf",
  "correct": true,

  "retrieved_memory_ids": ["mem_001"],
  "target_memory_ids": ["mem_001"],

  "retrieval_hit": true,
  "recall_at_k": 1.0,
  "precision_at_k": 1.0,
  "mrr": 1.0,

  "active_target_hit": true,
  "stale_memory_count": 0,
  "protected_memory_recall": null,

  "retrieval_tokens": 10,
  "input_tokens": 320,
  "output_tokens": 18,

  "retrieval_latency_ms": 12,
  "llm_latency_ms": 1420,
  "total_latency_ms": 1432
}
```

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

## 9. Aggregated Results

실험 종료 후 최소한 다음 결과를 strategy별로 집계한다.

| Strategy | Accuracy | Recall@k | Avg. Input Tokens | Avg. Latency | Cost |
|---|---:|---:|---:|---:|---:|
| Long Context | TBD | - | TBD | TBD | TBD |
| External Memory | TBD | TBD | TBD | TBD | TBD |
| Hybrid | TBD | TBD | TBD | TBD | TBD |

추가적으로 다음 조건별 결과를 분석한다.

- Conversation Length
- Temporal Distance
- Noise Level
- Task Type

---

## 10. Failure Analysis

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

Hallucination
  제공되지 않은 정보를 생성
```

실험 결과에서는 각 strategy가 어떤 유형의 failure에 취약한지 분석한다.

---

## 11. Responsibilities

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

## 12. Implementation Order

평가 파트는 다음 순서로 구현한다.

```text
1. Experiment schema 확정
       ↓
2. evaluator.py 기본 구조
       ↓
3. Exact Match / F1 구현
       ↓
4. Token / Latency logging
       ↓
5. Retrieval metric 구현
       ↓
6. Long Context baseline 평가
       ↓
7. External Memory baseline 평가
       ↓
8. 조건별 실험
       ↓
9. Failure analysis
```

초기 단계에서는 복잡한 자동 평가보다 **재현 가능한 deterministic metric과 logging pipeline을 먼저 완성하는 것**을 우선한다.
