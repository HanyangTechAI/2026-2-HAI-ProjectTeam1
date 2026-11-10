# Experiment Plan

평가 입력·출력 계약은 [Evaluator Interface](interfaces.md#22-evaluator-interface), 생성 설정과 Runner 연결은 [생성기 사용법](generator_usage.md)을 따른다.
`benchmark/evaluator.py`는 전달받은 Scenario/실행 snapshot의 deterministic 평가,
JSONL 출력, sample 단위 bootstrap 집계와 paired ΔSuccess를 구현한다.
실험 설정은 `experiments/configs/`에 있으며 Generator, Runner와 실제 Gemini Planner가
연결되어 있다. 현재 실행 기준은 freeze 1.0.3, 공통 출력 상한 2048 token이다.
2026-11-09에 완료한 1차 H50·2차 H10의 관측 결과는
[16절](#16-2026-11-09-실행-현황과-12차-관측-결과)에 별도로 기록한다.
H100까지 포함한 12셀·3840회의 최종 결과는
[17절](#17-전체-main-최종-결과-2026-11-09)과
[최종 분석 보고서](main_final_analysis_20261109.md)에 기록한다.
Horizon 채움 방식 1·2·3의 offline retrieval 민감도 audit은
[18절](#18-horizon-narrative-retrieval-bias-audit-2026-11-10)과
[별도 보고서](narrative_bias_audit_20261110.md)에 기록한다.
Target/Noise padding을 분리한 2×2 후속 audit은
[19절](#19-narrative-padding-2×2-factorial-audit-2026-11-11)과
[factorial 보고서](narrative_factorial_audit_20261111.md)에 기록한다.
실제 Proposed 선택·Context Builder 단계별 Mode 1/2 audit은
[20절](#20-horizon-narrative-final-context-audit-2026-11-11)과
[Final Context 보고서](narrative_final_context_audit_20261111.md)에 기록한다.

## 실행 완료 및 결과 요약

**필수 Main 12셀·3840회 실행과 정규 집계를 완료했다.** 셀별 40개 공통 과제 ×
네 전략 × 두 반복을 실행했으며, 전략·셀당 80회다. 아래의 묶인 조건은 각 셀의
관측값이 같음을 뜻한다. 상세 결과와 통계의 기준은 17절이다.

| 입력 Budget | Horizon | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---|---:|---:|---:|---:|
| 1K | 10 / 50 / 100 | 0% | 0% | 0% | 0% |
| 2K | 10 | 97.5% | 82.5% | 87.5% | 87.5% |
| 2K | 50 / 100 | 97.5% | 72.5% | 87.5% | 87.5% |
| 4K / 8K | 10 / 50 / 100 | 97.5% | 100% | 87.5% | 87.5% |

2K의 필수 상태·제약 보존 이점과 큰 예산에서의 성능·비용 절충이 관측됐다.
Proposed의 2K 이상 실패는 동일 기본 과제의 `attachment` 인자 누락이며,
모든 실패를 메모리 선택 문제로 보지 않는다. 강한 일반적 Horizon 효과나
전 조건의 Proposed 우위는 주장하지 않는다. H100·2K의 비용 불완전 3개 실행을
성공률에서 빼거나 비용을 0으로 처리하지 않는다.

1~15절은 설계·실행·평가 계약, 16절은 1·2차 당시의 중간 분석 기록,
17절은 전체 완료 후 최종 결과다. 원본 실행과 평가 기준은 유지했다.

## 1. Experiment Objective

본 실험의 목적은 제한된 Context Token Budget에서 행동에 필수적인 제약과 현재 Task에 필요한 상태를 우선 보존하는 **Constraint-Preserving Budget-Aware Memory**가 Long-Horizon Tool-Using Agent의 작업 성공률과 신뢰성을 개선하는지 검증하는 것이다.

동일한 LLM, Tool, Task, 원본 interaction history와 실행 조건을 사용하되, 모델에게 과거 정보를 제공하는 Memory strategy만 변경하여 평가한다. 제안 방식의 성능 개선은 실험으로 검증할 가설이며 결과로 전제하지 않는다.

주요 비교 대상은 다음과 같다.

1. **Constraint-Preserving Budget-Aware Memory (Proposed)**
   - Protected Memory와 Required State를 Version-Aware State Resolution으로 우선 보존한다. Historical Query에서는 요청 시점의 과거 버전을 선택한다.
   - 남은 예산 안에서 Flexible Memory를 선택한다.
2. **Sliding Window**
   - 현재 실행기는 공통 Memory snapshot의 항목을 최신 turn부터 예산 안에서 연속 선택한다. 원본 interaction 문자열을 자르는 방식과 구분하며, 본 문서의 실행 비교는 이 항목 단위 기준을 사용한다.
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

### 2.0 공통 생성 설정과 출력 실패 진단

Freeze 1.0.3에서 모델 `gemini-3.1-flash-lite`, temperature 0, thinking LOW,
공통 프롬프트와 **max_output_tokens 2048**을 모든 전략에 동일하게 적용한다.
출력 상한은 통제변인이며 필수 실험의 독립 변수는 Memory strategy와
Budget × Horizon이다. 실제 출력 token 사용량은 thinking을 포함한 측정값이다.

이전 512 상한 예비 점검에서는 일부 JSON 응답이 `MAX_TOKENS`로 잘렸다.
새 설정의 실험은 새 결과 디렉터리에서 수행하고 이전 결과와 합치지 않는다.
동일 모델·입력·counter를 유지하므로 기존 Gemini 측정 입력 ZIP의 생성 provenance를
보존해 재사용하며 manifest를 새 실행 freeze로 덮어쓰지 않는다.

응답별 종료 사유와 candidate/thoughts token을
`run_result.llm_usage.response_diagnostics`에 기록한다. `MAX_TOKENS`와
응답 해석 실패는 별도 진단으로 보고하고, 기존 Action-Level TSR 정의는 유지한다.
정답 Tool 행동 성공 후 최종 응답이 잘린 경우 행동 성공과 응답 오류가 동시에
기록될 수 있다. 실패 실행만 다른 상한으로 재실행해 기존 결과에 섞지 않는다.

### 2.1 Context Token Budget 정의

`B`는 Memory만의 크기가 아니라 **Memory를 포함한 Agent 입력 Context 전체의 token 상한**으로 정의한다.

```text
B = Fixed Input Tokens + Available Memory Tokens

Fixed Input Tokens
  = System Prompt + Current Request + Tool Definitions + Other Fixed Inputs
```

각 실행에서 `B`, 고정 입력 token 수, Memory에 실제로 할당 가능한 token 수, Memory가 실제 사용한 token 수와 전체 input token 수를 함께 기록한다. 모든 strategy는 설정된 `B`를 초과할 수 없다.

Protected Constraint와 Required State만으로 가용 Memory Budget을 초과하면 `INSUFFICIENT_CONTEXT_BUDGET`으로 중단하고 LLM과 Tool을 호출하지 않는다. 실행기가 확인한 필수 Memory 초과는 `mandatory_memory_overflow`로 별도 표시한다. Runner는 선택 단계의 필수 기억 초과 또는 Flexible Memory를 모두 제거한 뒤에도 필수 기억의 형식 비용 때문에 예산을 초과한 경우 `true`를 기록한다. 선택을 수행했고 해당 초과가 없으면 `false`, 고정 입력만으로 중단되어 한 번도 선택을 수행하지 못했으면 미측정(`null`)이다. 여러 단계 중 한 번이라도 필수 기억 초과가 확인되면 `true`이며, 단순 Flexible 제외나 고정 입력 초과와 구분한다. 선택 단계에서 중단되어 Context를 조립하지 못한 경우 입력 token 수는 `0`이 아닌 미측정(`null`)으로 기록하고, 이전에 실행한 단계의 누적 사용량은 보존한다. 이 조건에서도 제안 방식만 예산을 초과하도록 허용하지 않으며, 필수 정보 보존이 가능한 범위와 실패 한계를 별도로 보고한다.

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

State update가 존재하는 sample에서는 Context에 포함된 버전과 실제 Tool 행동에
사용한 버전을 구분한다. 아래 설명은 현재 Evaluator의 기록 의미를 명확히 한 것이며
1·2차 결과를 위해 평가 코드를 변경한 것은 아니다.

각 sample에 대해 다음 값을 기록한다.

* `selected_memory_ids`: retrieval 이후 temporal resolution 및 budget selection을 거쳐 실제 LLM context에 포함된 Memory ID
* `temporal_version_required`: 해당 sample에서 동일 State의 여러 버전 중 올바른 버전을 선택해야 하는지 여부
* `temporal_context_correct`: 필요한 State Memory를 최종 Context에 포함하고 잘못된 버전 Memory를 포함하지 않았는지 여부
* `temporal_version_correct`: 필요한 시점의 State 값을 실제 Tool 인자에 올바르게 사용하고 누락·stale·기타 State 오류가 없는지 여부
* `stale_selected_memory_count`: 최종 선택된 Memory 가운데 Task가 요구한 버전에 부합하지 않는 `wrong_version_memory_ids`의 수. 과거 요청에서는 최신 버전도 포함될 수 있으며, superseded 여부만으로 판정하지 않는다.

Temporal Version Accuracy (TVA)는 temporal version 판단이 필요한 sample만 대상으로 다음과 같이 계산한다.

TVA = 필요한 시점의 State를 행동에 올바르게 사용한 sample 수 / temporal version 판단이 필요한 sample 수

`temporal_version_correct=false`이면 `temporal_failure`를 기록한다. 이 값만으로
검색, 버전 해석, 예산 선택, 모델의 Tool 인자 누락 중 어느 단계가 원인인지 확정하지 않는다.
예를 들어 Context에 정답 파일이 있어도 `attachment` 인자가 없으면 행동 지표는
false일 수 있다. 반대로 과거·현재 버전이 함께 Context에 있어 Context 지표가 false여도
모델이 요청 시점의 파일을 올바르게 사용해 작업은 성공할 수 있다.

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

전략 비교에는 다음 값을 사용한다.

- Planning Latency: LLM Planner가 action을 결정하는 데 걸린 시간

`total_latency_ms`는 실행 진단을 위해 개별 run에 계속 기록하지만,
전략 간 효율 비교 지표에는 사용하지 않는다.

현재 Total Latency에는 semantic embedding의 LRU cache hit 여부,
최초 model warm-up 및 기타 runtime overhead가 포함될 수 있으며,
전략별 cache access pattern도 다르다. 따라서 이를 Memory Policy 자체의
latency 차이로 해석하지 않는다.

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

각 seed/셀의 20개 sample은 temporal_update 10개(현재 값 요청 5개, 과거 버전 요청 5개)와 approval 10개로 배정한다. 현재·과거 요청은 `state_scope`와 명시적 `expected_value`로 판정하며 `is_active`로 정답을 추정하지 않는다. Task 유형과 State scope는 metadata에 기록하여 유형별로도 집계한다. `benchmark/generator.py`가 이 배정을 구현하며, 승인 10개는 승인 요청 전 5개와 이미 승인된 발송 5개의 독립 snapshot이다. 생성과 token 재측정은 [generator_usage.md](generator_usage.md)를 참고한다.

같은 seed/sample의 원본 History와 정답을 모든 strategy에서 공유한다. Budget만 바꾸는 비교에서는 동일 sample_id를 유지한다. Horizon 변화는 총 History 길이를 유지하면서 중요 정보의 위치를 조절하고 Task 난이도·업데이트 관계·정답을 유지한다. sample_id에는 데이터 seed와 기본 Task identity를 포함하며 strategy/Budget/repeat를 포함하지 않는다. 실제 token 수와 Horizon 가능 여부는 생성 단계에서 검증한다. 조건에 따라 Task 내용까지 달라지는 혼동을 피한다.

### 8.2 RQ2의 선택적 성공률 격차 분석

필요하면 각 baseline에 대해 `ΔSuccess(B,H) = TSR_proposed(B,H) − TSR_baseline(B,H)`를 보고한다. H를 고정하고 B가 감소할 때, B를 고정하고 H가 증가할 때 격차가 어떻게 변하는지 분석한다. 격차 확대를 연구 결과로 전제하지 않는다. 동일 셀의 sample_id/repeat_id를 pair로 맞추고 sample 단위 bootstrap CI와 unmatched 수를 보고한다. 모든 방식이 실패하는 극단 조건의 격차 감소도 확인한다. 핵심 RQ2의 CVR/RSA/SSUR 비교를 ΔSuccess로 대체하지 않는다.

### 8.3 선택적 보조 실험과 Long Context

- `ablation.json`: Constraint Preservation, Temporal Resolution, State Mandatory, Token-aware Packing을 각각 제거하여 기여도를 분석한다. 모든 변형에서 예산 상한은 유지한다.
- `stress.json`: 제약 수 1/3/5 × State 변경 0/2/5 × Noise 0.1/0.5/0.9. 핵심 RQ2의 Budget/Horizon 실험과 구분한다.
- `reference.json`: 큰 예산 128K에서 전체 History를 제공하는 별도 참고 결과. main과 같은 seed/Task/Horizon을 사용하고 제한 Budget 순위나 paired ΔSuccess에 섞지 않는다.
- History 길이 sweep, Hybrid, 공개 benchmark, 응답 전용 Task는 선택적 확장이다. 현재 필수 설정에는 포함하지 않는다.

Long Context는 전체 원본 History와 고정 입력이 동일 Budget 및 모델 context window에 들어갈 때만 직접 비교한다. 현재 main의 25K History는 1K~8K Budget을 초과하므로 직접 비교 대상이 아니다. 향후 History가 들어가는 공통 조건을 추가하면 모든 strategy를 같은 sample/Budget에서 실행하고 Long Context를 포함한다. 이를 위한 추가 실행 수는 아래 기본 규모에 포함하지 않는다. 잘린 History를 Full Context로 부르지 않는다.

### 8.4 7주 실행 범위와 변경 전후 규모

1~4주차는 생성기, Baseline, Proposed 및 모델/tokenizer 연결과 smoke 검증을 우선한다. 5주차는 main의 RQ1/RQ2 필수 실험, 6주차는 실패 분석과 여유가 있을 때 보조 실험, 7주차는 필요한 재현 실행과 발표 준비에 사용한다. 전체 보조 설정의 실행은 필수 완료 조건이 아니다.

실행 수는 `grid 조건 수 × strategy 수 × seed 수 × repeat × samples_per_cell`로 계산한다. sample 수는 task 유형별 수가 아니라 각 seed/조건의 전체 수이다. 한 Agent 실행에는 여러 LLM/Tool 호출이 있을 수 있으므로 아래 숫자는 API 호출 수나 비용 추정치가 아니다. 예산 중단도 예정된 실행 단위에 포함한다.

| 설정 | 필수 여부 | 조건 수 전→후 | seed 수 전→후 | repeat 전→후 | sample/seed/조건 전→후 | 방식 수(전→후) | Agent 실행 수 전→후 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| main | 필수 RQ1/RQ2 | 36→12 | 3→2 | 3→2 | 30→20 | 4 | 38,880→3,840 |
| smoke | 필수 통합 확인 | 1→1 | 1→1 | 1→1 | 5→5 | 4 | 20→20 |
| stress | 선택적 보조 | 27→27 | 3→1 | 3→2 | 30→10 | 4 | 29,160→2,160 |
| ablation | 선택적 보조 | 12→2 | 3→2 | 3→2 | 30→20 | 4→5 | 12,960→800 |
| reference | 선택적 고예산 참고 | 9→3 | 3→2 | 3→2 | 30→20 | 1 | 2,430→240 |

변경 전 모든 설정의 합계는 **83,450회**, 변경 후 합계는 **7,060회**이다. 변경 전 main+smoke는 38,900회였고, 새 필수 범위는 **3,860회**, 선택적 보조 설정은 **3,200회**이다. 기존 구성은 stress를 핵심 RQ2로 명명했으므로 변경 전에는 main+stress+smoke 68,060회가 핵심 범위로 읽힐 수 있었다.

main은 History 길이 3수준을 25K 하나로 고정하고 seed를 `[42, 43]`, repeat를 2, sample을 20으로 줄였다. ablation은 Budget 2K/4K, Horizon 100만 사용하며 Full Proposed와 제거 변형 4개, 총 5개 설정을 실행한다. stress는 제약 수 × 업데이트 수 × Noise의 27개 셀을 보조 분석으로 유지하되 seed `[42]`, repeat 2, sample 10으로 줄였다. reference는 main과 같은 25K History와 Horizon 3수준을 사용한다. smoke는 그대로 유지한다.

`pilot.json`은 seed 2026의 단일 셀에서 서로 다른 기본 sample 100개를 생성해 입력 다양성과 생성 안정성을 확인한다. 설정상 네 전략을 적용하면 계획 행 4개, 잠재 Agent 실행 400회지만 현재 용도는 입력 생성·검토이므로 위 7,060회 실행 합계에는 포함하지 않는다.

반복 축소는 실행 부담을 줄이는 계획이며 통계적 검정력을 보장하지 않는다. CI와 적용 sample 수를 보고하고, 넓은 CI나 결과 불안정성이 확인되면 시간·비용 여유에 따라 반복을 늘린다. 중단·실패 실행과 누락된 pair도 보고한다.

`expand_config()`는 sample 생성 전 계획 행만 만든다. 변경 후 계획 행 수는 main 192, smoke 4, stress 216, ablation 40, reference 12이며 생성 검토용 pilot은 4이다. 각 실행 설정의 계획 행에 `samples_per_cell`을 곱하면 위 실행 수가 된다.

---

## 9. Evaluation Output Format

다음은 `evaluator.py`가 출력하는 주요 필드의 발췌 예시다. 최초 일반 검색에서는
과거 버전만 찾았지만, 버전 확장과 별도 보호 검색을 거쳐 필요한 기억이 최종
Context에 모두 포함되고 행동도 성공한 경우다. 전체 입력 계약은
[Evaluator Interface](interfaces.md#22-evaluator-interface)를 따른다.

```json
{
  "sample_id": "seed42_report_001",
  "strategy": "proposed",
  "repeat_id": 0,
  "pipeline_status": "ok",
  "context_token_budget": 2048,
  "effective_context_budget": 2016,
  "request_token_safety_margin": 32,
  "fixed_input_tokens": 310,
  "available_memory_tokens": 1706,
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
- `effective_context_budget`: 명목 Context Budget에서 `request_token_safety_margin`을 뺀 실제 선택·조립 상한
- `request_token_safety_margin`: CountTokens와 provider usage 차이를 흡수하기 위해 모든 전략에 공통 적용한 예약 token 수. 기본값은 `0`
- `fixed_input_tokens`: System Prompt, 현재 요청과 Tool 정의 등 고정 입력의 token 수
- `available_memory_tokens`: 전체 Context 상한에서 고정 입력을 제외한 Memory 가용 token 수
- `retrieval_tokens`: 실행 adapter가 제공한 경우에만 기록하는 검색 token 수
- `input_tokens`: 실행기가 제공한 실제 입력 측정값. 현재 Agent에서는 여러 호출 중 최대 단일 입력 크기이며, 누적 비용용 입력 합계가 아님
- `output_tokens`: 모든 API 시도의 비용 회계가 완전할 때 candidate token과 thinking token을 실행 전체에 누적한 값. usage 누락이나 청구 여부를 알 수 없는 retry 시도가 하나라도 있으면 부분 합계를 보고하지 않고 `null`
- `prompt_token_audit`: 각 실행 단계에서 CountTokens로 측정한 입력과 Gemini usage metadata의 실제 prompt token을 호출별로 비교한다. 호출 수 일치·metadata 완전성, 호출별 차이, 최대 차이와 양쪽의 Budget 초과 여부 및 `verified/unverified/exceeded/not_applicable` 상태를 기록한다. 미관측 usage를 0으로 대체하지 않는다.
- `cost_estimate_complete`: 모든 API 시도가 성공 응답이고 각 응답의 비용 계산용 usage가 완전한지 여부. false이면 token 비용 합계와 `total_llm_cost`는 `null`
- `billing_uncertain_attempts`: 응답 객체를 받지 못해 청구 여부를 확정할 수 없는 API 시도 수. retry 후 최종 성공했더라도 1 이상이면 비용 회계는 불완전
- `total_latency_ms`: 개별 실행 진단용 wall-clock 값. cache hit, model warm-up과 runtime overhead가 섞일 수 있으므로 전략별 요약 metric에는 포함하지 않는다.
- `selected_memory_ids`: 일반 검색, 버전 확장, 보호 검색 및 예산 선택 이후 실제 LLM Context에 포함된 Memory 집합
- `temporal_version_required`: 해당 sample에서 State version 선택이 필요한지 여부
- `temporal_version_correct`: 실제 선택된 Memory가 해당 시점의 올바른 State version인지 여부
- `stale_selected_memory_count`: 실제 LLM context에 포함된 Memory 중 `wrong_version_memory_ids`에 속하는 수. 과거 요청의 정답은 superseded되어도 제외

따라서 Evaluation schema는 공통 Memory schema를 대체하거나 새롭게 정의하는 것이 아니다. 공통 Memory schema를 입력으로 사용하고, 그중 평가에 필요한 상태를 snapshot으로 보존한 뒤 실험 조건과 계산된 metric을 추가한 실행 결과 schema이다.

집계 결과는 `metric_roles.json`에서 지표 역할을 명시한다. Task success·제약 위반·Required State·stale/temporal 정확성은 primary outcome, 입력·출력 token·Planning Latency·LLM 비용은 secondary efficiency, 검색·선택·상태 오류 분석값은 supporting diagnostic이다. `total_latency_ms`는 어느 비교 역할에도 포함하지 않는다. Main 집계는 `--experiment-root`를 필수로 받아 모든 grid cell의 `experiment.json` 수와 embedding model·resolved revision 일치를 확인하고 `provenance_audit.json`을 남긴다. 셀 수가 다르거나 revision이 섞이면 집계를 중단한다.

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

Provider 요청 실패는 Task failure로 대체하지 않는다. transient 오류는 freeze된 retry
정책으로 재시도하고, 소진되거나 non-retryable이면 Runner를 중단해 완료 row만
checkpoint에 보존한다. `--resume`으로 예정된 run key를 모두 완료한 뒤에만 본실험을
집계한다. 반면 Provider가 응답했지만 structured action parsing이 실패한 경우는 실제
모델 출력 실패이므로 evaluator 대상에 포함한다. 세부 계약은
[Architecture / Parameter Freeze의 Provider Failure Isolation](architecture_parameter_freeze_v1.md#41-provider-failure-isolation)을 따른다.

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

## 14. 현재 구현 및 본실험 연결 범위

Snapshot 기반 evaluator, JSONL 결과 저장, sample 단위 집계와 실행 계획 생성이 구현되어 있다. `memory/retrieval.py`는 Baseline과 Proposed가 공유하는 코사인 유사도 Top-K 검색을 제공한다. Proposed는 Query 분석, Version Chain Expansion, Temporal Resolution, Protected Constraint 적용성 판단, Required State 선택과 남은 예산의 Flexible Selection을 수행한다. 현재 Query·Constraint·Required State 해석은 제한된 규칙과 어휘 기반이다. Baseline은 Version Expansion·Protected 우선 보존·Temporal Resolution을 수행하지 않는다.

네 전략은 공통 `PipelineAdapter → Orchestrator → PipelineRunResult` 경로를 사용한다. Orchestrator는 매 단계 기억 선택, Context 조립, Agent 행동과 Tool feedback을 처리하고 목표 완료·Tool 실패·예산 실패·`max_steps`에서 중단한다. Runner는 같은 직렬화와 evaluator로 결과를 저장·평가한다. Proposed에는 Scenario의 명시적 `current_time`과 외부 embedder가 필요하다.

`benchmark/generator.py`는 temporal_update/approval 시나리오를 grid별 JSON 또는 ZIP으로 생성한다. History 길이·Horizon·Noise 비율의 요청값과 측정값을 구분한다. Ablation은 Full Proposed와 Constraint Preservation, Temporal Resolution, State Mandatory, Token-aware Packing을 제거하는 4개 변형으로 구성한다.

Gemini CountTokens는 `context/tokenizer.py`에 연결되어 있고 현재 측정 대상은 `gemini-3.1-flash-lite`이다. 실행 환경에는 `google-genai`와 `GEMINI_API_KEY`가 필요하다. Memory content 비용은 SDK의 content-only CountTokens로 측정하고, 최종 요청 Budget은 Developer API REST의 `generateContentRequest` wrapper로 model·contents·system instruction·설정된 tools를 함께 측정한다. Runner는 `--gemini-token-model`, `GEMINI_TOKEN_MODEL`, 프로젝트 기본값 순으로 모델을 선택한다. 생성기는 `--gemini-token-model`을 명시해야 Gemini counter를 선택하며, 옵션을 생략하면 환경변수와 무관하게 `provisional_token_count`를 사용한다. 사용자 카운터는 `--token-counter module:function`으로 주입한다. 모델 전환 시 기존 Gemini 측정 데이터의 label만 바꾸지 않고 History·Memory·최종 Context를 새 모델로 다시 측정한다. `benchmark/experiment_runtime.py`에는 `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` 기반 embedder와 초기 환경 기반 Agent factory가 제공된다. `sentence-transformers>=3.0.0`은 표준 설치 의존성이며 모델 가중치는 최초 실행 전에 준비해야 한다. 표준 embedder 실행 시 모델을 timing 전에 preload하고 Hugging Face cache snapshot의 resolved revision을 확인하며, Runner는 이 정보와 Python·platform·주요 package 버전을 `runtime_environment`에 기록한다. `agent/llm_planner.py`의 LLM Planner는 `--config`의 model 설정 또는 `--llm-model`로 활성화한다. 모든 실험 config는 `gemini-3.1-flash-lite`, temperature 0, 출력 한도 2048과 공통 system prompt를 사용한다. CLI는 생성 모델에서 CountTokens 모델 기본값을 가져오며 명시한 측정 모델이나 환경 변수와 다르면 API 호출 전에 중단한다. 프로그램 호출도 GeminiTokenCounter와 LLMRunConfig의 모델 일치를 검사한다. 모델 응답의 prompt usage는 호출별로 보존해 request-aware CountTokens 측정값과 `prompt_token_audit`에서 비교한다. 전체 token 합계와 비용은 모든 API 시도의 accounting이 완전할 때만 기록한다. usage 누락, 잘못된 값 또는 응답 없는 retry 시도가 있으면 부분 합계를 숨기고 `accounting_status=incomplete`, 비용 관련 합계를 `null`로 기록한다. API 호출이 없으면 출력 토큰과 비용은 0이다. Main 집계는 Agent가 실행된 row의 API call 수와 완전한 prompt audit을 검증하고 미완전 row를 거부한다.

## 15. 본실험 실행 조건

Memory strategy 외의 조건은 동일하게 적용한다.

- 같은 query, session, 원본 History, Memory snapshot, 현재 turn과 `current_time`을 사용한다.
- 같은 생성 모델, decoding 설정, system prompt, Tool 정의와 초기 환경을 사용한다.
- 같은 Context Budget, TokenCounter, embedder, Top-k, 공통 효용 가중치와 최대 실행 단계를 적용한다.
- 각 strategy/repeat마다 새 Agent와 Tool 실행 환경을 생성한다. 입력 snapshot은 변경하지 않으며 다른 session과 미래 turn의 기억은 제외한다.
- 정답 Memory ID, 기대 Tool 호출과 평가 전용 주석은 Memory policy와 Agent에 전달하지 않는다.
- 모든 전략의 성공·실패 결과를 공통 `PipelineRunResult`와 evaluator schema로 기록한다. Sliding Window에는 기존 계약에 따라 retrieval 지표를 적용하지 않는다.

Runner는 각 실행에서 GeminiTokenCounter의 메모리 캐시를 초기화하고 영속 캐시 읽기·쓰기를 우회한다. 기존 캐시 DB는 보존하며, 한 실행 안에서의 메모리 캐시 재사용은 유지한다. 이 설정을 `experiment.json`의 `token_cache_policy`에 기록한다. Embedder 내부 캐시, 서버 캐시 및 네트워크 지연은 별도로 통제하거나 측정 한계로 명시한다.

시나리오의 History와 Memory 비용도 같은 모델의 CountTokens로 측정해야 한다. 기존 `gemini-3.8-flash` 생성물의 metadata를 바꾸어 사용하지 않는다. Gemini 3.1 Flash-Lite용 데이터는 다음과 같이 별도 생성한다.

```zsh
python3 -m benchmark.generator --config experiments/configs/smoke.json --gemini-token-model gemini-3.1-flash-lite --output benchmark/generated/smoke_gemini_3_1_flash_lite
python3 -m benchmark.generator --config experiments/configs/pilot.json --gemini-token-model gemini-3.1-flash-lite --archive --output benchmark/generated/pilot_gemini_3_1_flash_lite.zip
```

Runner의 `--config`는 model뿐 아니라 runtime, strategies, repeats, evaluation 설정도 읽는다.
정규 Main에서는 이 값을 그대로 사용하며 CLI override는 `experiment.json`에 기록한다.
`pilot.json`은 현재 `generation_only`이므로 정규 실행 config로 전달하지 않는다.
실제 모델 smoke 검증은 3.1 데이터와 동일 모델의 CountTokens를 사용하며 토큰 측정
방식을 임의로 바꾸지 않는다.

### 15.1 신규 실행 환경의 live 4K/8K gate

Freeze 승인 전에는 검증된 `benchmark/generated/main_gemini_3_1_flash_lite.zip`을
입력으로 다음 live 4K/8K gate를 수행한다.

```text
python tests/smoke_4k8k.py prepare
python tests/smoke_4k8k.py run
python tests/smoke_4k8k.py report
```

이 gate는 seed `42, 43`, horizon `50`에서 동일한 8개 sample을 Budget `4096`과
`8192`에 짝지어 추출하고 네 전략을 repeat `1`로 실행하므로 총 예정 실행은 `64`회다.
Main 결과와 분리된 `experiments/results/smoke_4k8k`에 저장하며, 중단된 실행은 입력,
config와 코드가 바뀌지 않은 경우에만 `run --resume`으로 계속한다. Report는 실행 및
checkpoint 완결성, 전략별 실제 LLM 호출, request-aware CountTokens, Prompt Audit,
Budget 초과, Safety Margin, embedding revision과 prompt checksum을 검사한다.
`readiness=PASS` 전에는 Main 실행 준비가 완료된 것으로 보지 않는다. 비용 metadata가
불완전한 run은 별도 warning과 `null` 비용으로 유지하고 비용 비교에서 제외한다.

이 gate 절차는 신규 실행 환경의 검증 기준으로 보존한다. 아래 16·17절에 기록된
기존 실행은 전략별 10개 조건의 사전 점검과 이후 전체 Main 감사를 수행했다.
해당 Main 기록을 이 64회 gate의 실행 또는 PASS 기록으로 소급해 표시하지 않는다.

## 16. 2026-11-09 실행 현황과 1·2차 관측 결과

### 16.1 완료 범위와 공통 설정

아래는 2026-11-09의 1·2차 완료 당시 기록이다.
3차 H100까지 완료한 최종 상태와 전체 집계는 17절을 따른다.

| 차수 | Horizon | 대상 셀 | 실행 수 | 상태 |
|---|---:|---|---:|---|
| 1차 | 50 | 1K / 2K / 4K / 8K | 1280/1280 | 완료·분석 완료 |
| 2차 | 10 | 1K / 2K / 4K / 8K | 1280/1280 | 완료·분석 완료 |

이 중간 시점에는 Main 전체 12셀 중 **8셀, 2560/3840회**가 완료됐다. 각 셀은 공통 기본 과제
40개 × 네 전략 × 두 반복 = 320회다. 각 전략/셀의 80회는 독립 과제 80개가 아니라
같은 과제 40개의 두 반복이다. Horizon과 Budget 간에도 기본 과제 identity를 공유한다.

이 절의 성공률 표는 당시 완료된 1·2차 결과만 포함한다. 이후 동일 commit/freeze와
기존 실패 처리 기준으로 수행한 3차 H100은 19:53:40에 완료됐다. 3차 완료 기록은
`experiments/results/main_phase3_h100_20261109_1747/phase_progress.json`에 보존했다.

1·2차 모두 commit `5d00c6d5a8c3c9348ed872a9899d107b027c0196`의 clean detached
worktree, freeze 1.0.3, Gemini 3.1 Flash-Lite, temperature 0, thinking LOW,
max output 2048, top-k 5, max_steps 8, safety margin 32를 사용했다.
반복 수는 canonical Main의 2회를 유지했고 CLI override는 결과 경로뿐이다.
원래 생성 freeze 1.0.1의 Gemini 입력 ZIP은 provenance와 해시를 유지해 재사용했다.

셀별 예상 run key와 입력 SHA-256, 중복·누락 없음, model/config/prompt/embedding
revision 일치를 검증했다. H10/H50 사이에서도 query, 정답 State, 기대 Tool 호출,
초기 environment와 memory ID별 content가 같음을 확인했다. Memory 위치는 Horizon에
따라 달라진다. 이 절의 중간 분석과 H100까지 포함한 17절의 정규 최종 집계를 구분한다.

### 16.2 관측 Action-Level Task Success

각 칸은 성공/80회와 성공률이다. H50은 1차, H10은 2차다.

| Horizon | Budget | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---|---:|---:|---:|---:|
| H50 | 1K | 0/80 (0%) | 0/80 (0%) | 0/80 (0%) | 0/80 (0%) |
| H50 | 2K | 78/80 (97.5%) | 58/80 (72.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H50 | 4K | 78/80 (97.5%) | 80/80 (100%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H50 | 8K | 78/80 (97.5%) | 80/80 (100%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H10 | 1K | 0/80 (0%) | 0/80 (0%) | 0/80 (0%) | 0/80 (0%) |
| H10 | 2K | 78/80 (97.5%) | 66/80 (82.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H10 | 4K | 78/80 (97.5%) | 80/80 (100%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H10 | 8K | 78/80 (97.5%) | 80/80 (100%) | 70/80 (87.5%) | 70/80 (87.5%) |

1K에서는 Proposed가 각 Horizon의 80회 모두 필수 Memory 예산 초과로 모델 호출 전에
중단했다. 다른 전략도 실행 후 실패했다. 이 조건은 입력 형식·합성 Memory 비용에 따른
실행 가능성 한계로 남기며 비교에 유리하게 결과에서 제외하지 않는다.

2K에서는 두 Horizon 모두 Proposed가 가장 높지만, 4K·8K에서는 Sliding Window가
가장 높았다. 큰 예산에서는 압축 Memory snapshot을 충분히 전달할 수 있어,
모든 조건에서 Proposed의 성공률 우위가 유지된다는 결론은 지지되지 않는다.

### 16.3 2K의 실패 분해와 Horizon 비교

| 지표 | H50 | H10 | 분모·의미 |
|---|---:|---:|---|
| Sliding 과거 파일 조회 성공 | 2/20 (10%) | 12/20 (60%) | 과거 조회 20회 |
| Sliding 승인 전 Task 성공 | 16/20 (80%) | 14/20 (70%) | 승인 전 20회 |
| Sliding 승인 제약 위반 | 4/40 (10%) | 6/40 (15%) | 제약 적용 40회 |
| Proposed 승인 제약 위반 | 0/40 (0%) | 0/40 (0%) | 제약 적용 40회 |

- Sliding의 H10 전체 성공률은 H50보다 10pp 높지만, 승인 제약 결과는 반대 방향이다.
  Horizon 단축이 모든 행동 신뢰성 지표를 개선한다고 결론 내리지 않는다.
- Proposed의 2K 이상 실패는 두 Horizon 모두 동일한 기본 과제
  `seed43_approval_014_pending`의 두 반복이다. 정답 Memory와 제약을 포함했지만
  `request_approval`의 `attachment` 인자가 없어 State omission으로 실패했다.
  Retrieval나 시간 해석의 오류만으로 설명하지 않는다.
- Vector/Utility는 같은 기본 과제 5개에서 최신 v3가 Top-5 후보에서 빠져 v2를
  행동에 사용했다. 2K 이상 실패는 각 셀 10회이며, 입력 예산만 늘려도 검색 후보의
  누락은 복구되지 않았다.
- H50 Sliding에는 정답 파일 조회를 8회 반복하다 max_steps로 종료한 2회도 있었다.
  정답 Tool 호출이 있다는 이유로 성공으로 바꾸지 않고 기존 목표 완료 기준을 유지했다.

### 16.4 불확실성과 해석 한계

동일 sample_id/repeat_id를 pair하고 기본 과제 40개를 cluster로 묶어 두 반복을
함께 bootstrap했다(2000회, seed 42). 아래는 percentage point 단위다.

| 비교 | 관측 차이 | 95% paired sample-cluster bootstrap CI |
|---|---:|---|
| H50·2K: Proposed−Sliding | +25pp | [+12.5, +40.0]pp |
| H10·2K: Proposed−Sliding | +15pp | [+5.0, +27.5]pp |
| 2K Sliding: H10−H50 | +10pp | [0.0, +22.5]pp |

H10/H50 사이에 관측 차이는 있지만 구간은 0에 닿는다. H100이 없는 상태에서
일반적인 Horizon 효과를 확정하지 않는다. Vector/Utility 대비 Proposed의 차이는
두 Horizon에서 +10pp지만 구간이 0에 닿거나 포함하므로 확정적 우위로 표현하지 않는다.
다중 비교 보정은 하지 않은 탐색적 해석이다. 각 Budget/전략/기본 과제의 두 반복은
성공 판정이 같았지만 독립 관측 수가 두 배가 된 것은 아니다. 전부 성공·실패한 셀의
bootstrap 구간이 폭 0이더라도 모집단 불확실성까지 0이라는 뜻은 아니다.

### 16.5 출력·입력 예산과 비용 기록

저장된 1·2차 생성 응답 합계 4952개는 모두 `STOP`이다. `MAX_TOKENS`,
응답 해석 실패, 실제 입력 예산 초과, live prompt audit 누락은 모두 0건이다.
단일 응답 최대 출력(thinking 포함)은 H50에서 1288, H10에서 1116 token이다.
출력 상한 2048은 두 차수의 입력에서는 충분했지만 H100까지 보장하지 않는다.

완료 run에 기록된 생성 비용 추정은 H50 $2.182800, H10 $2.175945,
합계 $4.358745다. 중단 직전 미저장 호출, 실패 시도의 청구 여부, 재개로 중복된
호출과 CountTokens 관련 비용을 완전히 포함하지 않아 실제 전체 청구액은 아니다.
H50의 4K/8K에서 Proposed는 Sliding보다 누적 입력 약 40.3%·기록된 생성 비용 약
36.2%가 낮았지만 성공률도 2.5pp 낮았다. 같은 성능에서의 비용 절감으로 표현하지 않는다.

### 16.6 증거와 남은 범위

- [1차 H50 상세 분석](main_phase1_h50_analysis_20261109.md)
- [2차 H10 상세 분석과 H10/H50 비교](main_phase2_h10_analysis_20261109.md)
- 1차 원본: `experiments/results/main_phase1_h50_20261109_0918/`
- 2차 원본: `experiments/results/main_phase2_h10_20261109_1332/`
- 각 차수의 `analysis/analysis.json`, `analysis/failure_cases.json`, 2차의
  `analysis/horizon_comparison.json`에 집계·CI·실패 사례·pair 감사를 저장했다.

원본 run과 평가 기준은 변경하지 않았다. 당시 남은 범위였던 3차 H100의 완료와
전체 12셀 provenance 감사·Main 집계 결과는 17절에 반영했다. 파서·인자 처리·
프롬프트·검색 방식을 바꿔 개선하려면 새 freeze와 별도 결과군으로 관리한다.

## 17. 전체 Main 최종 결과 (2026-11-09)

### 17.1 전체 완료와 감사

1차 H50, 2차 H10, 3차 H100의 네 Budget 셀이 모두 완료돼 **12/12셀, 3840/3840회**가 갖춰졌다. 3차는 19:53:40(Asia/Seoul)에 완료됐다. 16절은 당시의 중간 관측 기록이며 이 절을 최종 집계로 사용한다.

모든 차수는 clean commit `5d00c6d5a8c3c9348ed872a9899d107b027c0196`, freeze 1.0.3, output cap 2048, temperature 0, thinking LOW, top-k 5, max_steps 8, safety margin 32, 두 반복을 유지했다. 코드·prompt·embedding revision·입력 해시와 예상 실행 키를 검증했다. 원본 run의 저장 평가를 고정 커밋 Evaluator로 재평가해 3840개 모두 일치함을 확인했다.

정규 Main Evaluator를 `--experiment-root`로 12셀 metadata와 연결해 집계했다. `provenance_audit.json`은 pass, 전략/셀별 summary는 48개, Proposed−Baseline 비교는 36개다. 기본 과제는 40개이며 3840개의 독립 과제로 취급하지 않는다.

### 17.2 최종 Action-Level 성공률

각 칸은 동일 기본 과제 40개 × 두 반복의 성공/80회다.

| Horizon | Budget | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---|---:|---:|---:|---:|
| H10 | 1024 | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) |
| H10 | 2048 | 78/80 (97.5%) | 66/80 (82.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H10 | 4096 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H10 | 8192 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H50 | 1024 | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) |
| H50 | 2048 | 78/80 (97.5%) | 58/80 (72.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H50 | 4096 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H50 | 8192 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H100 | 1024 | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) |
| H100 | 2048 | 78/80 (97.5%) | 58/80 (72.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H100 | 4096 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| H100 | 8192 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |

1K는 전 전략 0%다. Proposed 240회는 필수 기억 예산 부족으로 호출 전에 안전 중단했다. 2K에서는 Proposed가 가장 높고, 4K·8K에서는 Sliding이 100%로 가장 높다. 모든 예산에서 Proposed가 우월하다는 결론은 지지하지 않는다.

### 17.3 불확실성과 Horizon 해석

동일 sample/repeat를 pair하고 40개 기본 과제를 cluster로 두 반복을 함께 bootstrap했다(2000회, seed 42). 2K의 Proposed−Sliding 차이는 H10 +15pp(CI [+5.0, +27.5]pp), H50/H100 +25pp(CI [+12.5, +37.5]pp)다. 이는 정규 전체 집계의 CI이며 차수별 중간 집계와는 난수 재표집 순서로 끝점이 소폭 달라질 수 있다.

2K의 Vector/Utility 대비 차이 +10pp의 구간은 [0.0, +22.5]pp이고, 4K·8K의 Sliding 대비 −2.5pp 구간은 [−7.5, 0.0]pp다. 구간이 0에 닿는 결과를 확정적 우위·열위·동등성으로 표현하지 않는다. 다중 비교 보정은 하지 않았다.

Horizon 간 성공률 변화는 주로 2K Sliding에서 나타났다. H10은 H50/H100보다 +10pp이나 paired CI는 [0.0, +22.5]pp다. H50과 H100은 과제별 성공 판정도 같았다. H10 Sliding의 과거 조회는 12/20, H50/H100은 2/20이지만 승인 제약 위반은 각각 6/40, 4/40, 4/40으로 반대 방향이다. 따라서 강한 일반적 Horizon 효과로 단정하지 않는다.

이 데이터는 Memory content와 후보 수를 보존한 채 위치·turn 거리를 조절하며, 압축 Memory snapshot을 입력한다. 주어진 snapshot에서 선택한 기억과 행동을 평가한 결과를 일반적인 장기 대화의 기억 추출·망각 문제 해결로 확장하지 않는다.

### 17.4 실패 원인과 신뢰성

- Proposed의 2K 이상 실패 18회는 같은 기본 과제 하나의 3 Horizon × 3 Budget × 두 반복이다. 정답 Memory·제약은 모두 있었지만 승인 요청의 attachment 인자가 없었다. 메모리 선택 결함으로 단정하지 않고 기존 평가 기준에 따라 행동 인자 누락으로 보고한다.
- Sliding의 2K 실패는 H10 14회, H50/H100 각 22회다. 과거 State 누락, 승인 전 발송 시도, 반복 파일 조회 후 max_steps 종료가 원인이다. 높은 Budget에서는 실패가 사라졌다.
- Vector/Utility는 최신 버전이 Top-5 후보에서 빠진 같은 기본 과제 5개 때문에 각 2K 이상 셀에서 10회 실패했다. Budget 확대만으로 해결되지 않는 후보 검색 병목이다.
- 입력 Budget 실패·검색/선택 실패·행동 인자 누락·목표 미완료를 분리한다. temporal_version_correct는 실제 행동의 State 사용도 반영하므로 temporal_failure만으로 시간 해석 모듈을 원인으로 확정하지 않는다.
- 2K의 Proposed RSA는 모든 Horizon 97.5%, CVR·SSUR은 0%다. Sliding RSA는 H10/H50/H100에서 90/80/82.5%, CVR은 15/10/10%, SSUR은 0%다. Vector/Utility RSA는 87.5%, CVR 0%, SSUR 16.7%다. RSA 분모는 80회, 적용 CVR은 40회, 현재 State SSUR은 60회다. 전체 지표·분모와 CI는 최종 보고서 및 정규 summary에 보존했다.

### 17.5 출력 진단과 비용 완전성

실제 LLM 실행 3600회에서 기록된 생성 API 시도는 7453회, 응답 객체는 7450개이며 응답의 finish_reason은 모두 STOP이다. MAX_TOKENS·응답 해석 오류·실제 입력 예산 초과·live prompt audit 누락은 0건이다. 최대 단일 출력은 thinking 포함 1288 token이었다.

H100·2K의 Sliding 2개 및 Utility 1개 실행은 내부 API 재시도 후 작업에는 성공했지만 응답 없는 시도의 청구 여부가 불확실해 비용이 null이다. 이 3개 결과는 성공률에 포함하고 비용을 0으로 처리하지 않는다. 모든 저장 run의 완전한 총비용은 계산할 수 없다.

알려진 비용 3837개 run의 추정 부분합은 $6.5465375다. 미저장·중단·재개 호출 및 CountTokens 관련 비용도 완전히 포함하지 않아 실제 전체 청구액이 아니다. H100·2K의 해당 전략 평균·합계에는 비용 누락과 유효 표본 수를 표시해야 한다.

비용 누락이 없는 4K·8K에서는 Proposed가 Sliding보다 누적 입력 약 40.3%, 기록된 완료-run 생성 비용 약 36%를 덜 사용했지만 성공률도 2.5pp 낮았다. 동일 성능의 비용 절감으로 표현하지 않는다.

### 17.6 최종 해석과 증거

이 실험은 제한 예산에서 필수 상태·제약 보존의 행동상 이점을 보여준다. 충분한 예산에서는 단순한 최근 기억 방식도 높은 성능을 보이며, 기억의 보존이 올바른 Tool 인자 사용까지 보장하지 않는다는 한계도 확인됐다. 현재 기준 실험은 보존하고 인터페이스·프롬프트·후보 검색을 개선하려면 새 freeze/결과군으로 분리한다.

- [전체 Main 최종 분석](main_final_analysis_20261109.md)
- [저장소에 포함한 집계 JSON·감사·그래프](results/main_20261109/README.md)
- 최종 원본·집계: `experiments/results/main_final_20261109/`
- `source_audit.json`, `pairing_audit.json`, `aggregate/provenance_audit.json`: 입력·코드·재현성 감사.
- `aggregate/summary.json`, `aggregate/deltas.json`, `analysis.json`: 주요 결과·CI·진단.
- `failure_cases.json`, `cost_unknown_cases.json`: 실패 1216개 실행과 비용 불완전 3개 실행.
- `budget_horizon_success.png`와 `.svg`: 전체 Horizon/Budget 결과 그림.

## 18. Horizon Narrative Retrieval Bias Audit (2026-11-10)

기존 반복 문장(Mode 1), 로컬 자연어(Mode 2), 50:50 혼합(Mode 3)이 semantic
retrieval 후보에 미치는 영향을 Budget 2048·Horizon 100의 paired scenario 40개에서
점검했다. query·정답·Memory 구조와 실험 조건은 고정하고 Memory content만 모드에 맞게
바뀌었으며, 세 데이터셋 모두 Gemini 3.1 Flash-Lite CountTokens로 측정됐다.

| Mode | Baseline State Recall@5 | Proposed 후보군 State Recall@5 | 평균 target similarity |
|---:|---:|---:|---:|
| 1 | 87.5% | 90.0% | 0.6000 |
| 2 | 80.0% | 80.0% | 0.5988 |
| 3 | 82.5% | 87.5% | 0.6119 |

Mode 1→2에서 Top-5는 baseline 17/40, Proposed 일반 후보군 11/40 과제에서 바뀌었고
State Recall은 각각 −7.5%p, −10.0%p였다. Mode 3은 Mode 2 대비 각각 +2.5%p,
+7.5%p 회복했다. 채움 문체가 cutoff 후보에 영향을 주므로 모드가 다른 결과를 같은
repeat로 합치지 않는다.

이 audit은 Top-k 5의 offline retrieval만 측정했다. Proposed Selector·Context Builder와
Agent/Tool/LLM은 실행하지 않았고 Mode 4·5도 포함하지 않았으므로 TSR, 제약 위반 또는
일반적인 자연어 우수성의 증거로 사용하지 않는다. 상세 task별 결과, paired 변화,
무결성 해시와 후속 결정은 [별도 보고서](narrative_bias_audit_20261110.md)를 따른다.

## 19. Narrative Padding 2×2 Factorial Audit (2026-11-11)

Mode 1→2 변화의 원인을 분리하기 위해 required State target padding(`T`)과 synthetic
noise padding(`N`)을 각각 Mode 1/2로 교체한 네 조건 `T1/N1`, `T2/N1`, `T1/N2`,
`T2/N2`를 구성했다. 18절과 같은 2K/H100 paired scenario 40개, Top-k 5와 frozen
embedder를 사용했다.

| Pool | T1/N1 | T2/N1 | T1/N2 | T2/N2 | Target 효과 | Noise 효과 | Interaction |
|---|---:|---:|---:|---:|---:|---:|---:|
| baseline | 87.5% | 80.0% | 87.5% | 80.0% | −7.5%p | 0.0%p | 0.0%p |
| Proposed 후보군 | 90.0% | 80.0% | 90.0% | 80.0% | −10.0%p | 0.0%p | 0.0%p |

Target 효과의 paired bootstrap 95% 구간은 baseline `[−20.0,+5.0]%p`, Proposed
후보군 `[−22.5,0.0]%p`다. Noise 교체는 40개 모두에서 hit, target rank와 Top-5 ID를
바꾸지 않았고 interaction도 0이었다. 따라서 현재 조건의 Mode 1→2 Recall 차이는
target State 서술 변화와 연결되며 noise 서술 효과는 관측되지 않았다.

이 결론은 단일 셀·40개 과제의 offline retrieval에 한정한다. Noise의 일반적 무효성,
통계적으로 확정적인 target 효과 또는 Agent 행동 효과를 주장하지 않는다. 전체 CI,
task별 결과, integrity 조건과 후속 결정은
[factorial 보고서](narrative_factorial_audit_20261111.md)를 따른다.

## 20. Horizon Narrative Final Context Audit (2026-11-11)

18·19절의 Mode 1/2 paired 입력 40개를 Proposed의 실제 retrieval, version-chain
expansion, temporal resolution, Required State, protected constraint, budget selection과
Context Builder까지 통과시켰다. Main Freeze v1.0.3 설정에서 명목 Budget 2,048,
Safety Margin 32, 실효 Budget 2,016, top-k 5를 사용했다.

| 단계 | Mode 1 | Mode 2 |
|---|---:|---:|
| Initial Required State 완전 | 36/40 (90%) | 32/40 (80%) |
| Version chain expansion 이후 | 40/40 | 40/40 |
| Final Context Required State 완전 | 40/40 | 40/40 |
| Context 정상 조립 | 40/40 | 40/40 |
| 최종 Context 내 잘못된 버전 | 0 | 0 |
| 승인 조건에서 적용 Constraint 완전 | 20/20 | 20/20 |
| 실효 Budget 초과 | 0 | 0 |

Mode 1의 초기 State miss 4건과 Mode 2의 8건은 모두 최종 Context에 복구됐다. 평균
최종 Context는 각각 1,356.05와 1,358.30 tokens였다. 관측된 초기 검색 차이는 이
paired 데이터에서 최종 Context 누락으로 이어지지 않았다.

이는 Agent loop 이전의 선택·조립 검증이다. LLM·Tool 행동이나 TSR, 제약 위반 행동률을
측정하지 않았고 단일 2K/H100 셀의 40개 과제만 포함했다. 상세 단계 추적과 해석은
[Final Context Audit 보고서](narrative_final_context_audit_20261111.md)를 따른다.
