# Core Algorithms

## 1. 목적

본 문서는 `Constraint-Preserving Budget-Aware Memory` 시스템의 핵심 알고리즘을 구현 직전 수준으로 정의한다.

핵심 처리 과정은 다음과 같다.

```text
Interaction
    ↓
Memory Extraction
    ↓
Memory Store / Versioning
    ↓
Temporal Intent Detection
    ↓
General Candidate Retrieval
    ↓
Version Chain Expansion
    ↓
Temporal / Version Resolution
    ↓
Budget-Aware Selection
    ↓
Context Construction
    ↓
LLM Agent Action

Memory Store
    ↓
Protected Retrieval
    ↓
Budget-Aware Selection
```

본 프로젝트의 핵심 Contribution은 다음 세 부분이다.

```text
1. Constraint-Preserving Selection
2. Version-Aware Temporal Memory Resolution
3. Budget-Aware Flexible Memory Allocation
```

---

# 2. 공통 자료형

## 2.1 MemoryItem

```python
MemoryItem(
    memory_id,
    session_id,
    turn_id,

    memory_type,
    memory_key,
    content,

    is_active,
    is_protected,

    importance,
    token_count,

    created_at,
    valid_from,
    valid_to,

    supersedes,
    superseded_by
)
```

---

## 2.2 MemoryCandidate

Query에 따라 동적으로 계산되는 정보를 저장한다.

```python
MemoryCandidate(
    memory,

    semantic_score,
    keyword_score,
    entity_score,

    relevance_score,
    recency_score,
    utility_score,

    is_applicable,
    selection_reason
)
```

`MemoryCandidate`의 score는 Query-dependent 값이므로 `MemoryItem`에 영구 저장하지 않는다.

---

## 2.3 QueryContext

현재 Task에 대한 분석 결과이다.

```python
QueryContext(
    query,

    temporal_intent,
    target_time,
    target_range,

    entities,
    required_tools,

    current_turn,
    current_time
)
```

`temporal_intent`는 다음 중 하나이다.

```text
CURRENT
HISTORICAL
RELATIVE_TIME
UNSPECIFIED
```

---

# 3. Algorithm 1 — Memory Extraction

## 목적

새로운 Interaction에서 장기적으로 보존할 가치가 있는 Memory를 추출한다.

## Input

```text
interaction
```

## Output

```text
List[MemoryItem]
```

## 처리 항목

Memory Analyzer는 다음을 추출한다.

```text
memory_type
memory_key
content
importance
is_protected
temporal metadata
```

## Pseudocode

```text
FUNCTION EXTRACT_MEMORY(interaction):

    structured_items ← LLM_STRUCTURED_EXTRACTION(interaction)

    memories ← empty list

    FOR each item IN structured_items:

        VALIDATE memory_type

        IF item represents mutable information:
            ASSIGN memory_key

        IF item is a hard behavioral constraint:
            is_protected ← TRUE
        ELSE:
            is_protected ← FALSE

        token_count ← COUNT_TOKENS(item.content)

        memory ← MemoryItem(
            generated memory_id,
            interaction.session_id,
            interaction.turn_id,

            item.memory_type,
            item.memory_key,
            normalized content,

            TRUE,
            is_protected,

            item.importance,
            token_count,

            interaction.timestamp,
            item.valid_from OR interaction.timestamp,
            NULL,

            NULL,
            NULL
        )

        APPEND memory

    RETURN memories
```

---

# 4. Algorithm 2 — Memory Insert & Versioning

## 목적

새로운 Memory를 저장하고, 기존 State / Preference / Constraint와 충돌하거나 대체 관계가 있으면 Version Chain을 갱신한다.

## 핵심 원칙

```text
SUPERSEDED ≠ 삭제

SUPERSEDED
= 현재 기준으로 대체되었지만
  과거 시점 Query에서는 유효할 수 있는 Memory
```

현재 행동 Constraint가 대체되는 경우 기존 Constraint는 Store에 남기되 현재 Protected 대상에서는 해제한다.

## Input

```text
new_memory
memory_store
```

## Output

```text
updated memory_store
```

## Pseudocode

```text
FUNCTION INSERT_MEMORY(new_memory):

    IF new_memory.memory_key IS NULL:
        STORE new_memory
        RETURN

    previous ← FIND_ACTIVE_BY_KEY(
        session_id = new_memory.session_id,
        memory_key = new_memory.memory_key
    )

    IF previous does not exist:
        STORE new_memory
        RETURN

    IF new_memory does not replace previous:
        STORE new_memory
        RETURN

    updated_previous ← previous WITH:
        is_active = FALSE
        valid_to = new_memory.valid_from
        superseded_by = new_memory.memory_id

    IF previous.memory_type == CONSTRAINT:
        updated_previous.is_protected = FALSE

    updated_new ← new_memory WITH:
        is_active = TRUE
        supersedes = previous.memory_id

    REPLACE previous WITH updated_previous
    STORE updated_new
```

현재 행동에 적용되는 새로운 Hard Constraint는 Memory Extraction 단계에서 `is_protected = TRUE`로 생성한다.

---

## 대체 여부 판단

초기 구현에서는 다음 우선순위를 사용한다.

```text
1. 동일 memory_key
2. 새로운 값 또는 규칙이 기존 값을 명시적으로 변경
3. 시간적으로 새로운 Interaction
```

예:

```text
기존:
report.current_file = report_v1.pdf

신규:
"이제 report_final.pdf를 사용해."

→ replacement
```

반면:

```text
기존:
user.language = Python

신규:
"Python으로 FastAPI를 사용한다."

→ 반드시 replacement는 아님
```

애매한 경우 Analyzer가 `replacement=true/false`를 함께 반환하도록 할 수 있다.

---

# 5. Algorithm 3 — Temporal Intent Detection

## 목적

현재 Query가 어떤 시점의 정보를 요구하는지 판단한다.

## Input

```text
query
current_time
```

## Output

```text
temporal_intent
target_time
target_range
```

## Pseudocode

```text
FUNCTION DETECT_TEMPORAL_INTENT(query, current_time):

    temporal_expression ← EXTRACT_TEMPORAL_EXPRESSION(query)

    IF query explicitly requests current state:
        RETURN CURRENT

    IF temporal_expression is absolute past time:
        target_range ← PARSE_ABSOLUTE_TIME(temporal_expression)
        RETURN HISTORICAL, target_range

    IF temporal_expression is relative:
        target_time ← RESOLVE_RELATIVE_TIME(
            temporal_expression,
            current_time
        )
        RETURN RELATIVE_TIME, target_time

    RETURN UNSPECIFIED
```

---

## 예시

```text
"지금 보고서 뭐야?"
→ CURRENT

"8월에 쓰던 파일"
→ HISTORICAL

"두 달 전에 쓰던 파일"
→ RELATIVE_TIME

"보고서 보내줘"
→ UNSPECIFIED
```

`UNSPECIFIED`는 기본적으로 현재 유효한 State를 우선한다.

---

# 6. Algorithm 4 — Candidate Retrieval

## 목적

현재 Query와 관련된 Memory 후보를 넓게 수집한다.

초기 구현은 Semantic Retrieval을 필수로 사용하고, BM25 / Entity Match를 선택적으로 결합한다.

## Input

```text
query_context
memory_store
candidate_k
```

## Output

```text
List[MemoryCandidate]
```

## Multi-Signal Retrieval

각 Memory에 대해:

```text
Semantic Score
Keyword Score
Entity Score
```

를 계산한다.

통합 relevance:

```text
R_i =
w_s · Semantic_i
+ w_k · Keyword_i
+ w_e · Entity_i
```

초기 구현에서는:

```text
w_s + w_k + w_e = 1
```

로 둔다.

## Pseudocode

```text
FUNCTION RETRIEVE_CANDIDATES(query_context, memory_store):

    semantic_results ← SEMANTIC_SEARCH(
        query_context.query,
        top_k = K_semantic
    )

    keyword_results ← BM25_SEARCH(
        query_context.query,
        top_k = K_keyword
    )

    entity_results ← ENTITY_SEARCH(
        query_context.entities,
        top_k = K_entity
    )

    pool ← UNION(
        semantic_results,
        keyword_results,
        entity_results
    )

    candidates ← empty list

    FOR memory IN pool:

        semantic ← semantic score OR 0
        keyword ← keyword score OR 0
        entity ← entity score OR 0

        relevance ←
            w_s * semantic
            + w_k * keyword
            + w_e * entity

        ADD MemoryCandidate(
            memory = memory,
            relevance_score = relevance
        )

    RETURN candidates
```

---

## Version Chain Expansion

General Retrieval에서 `memory_key`를 가진 Versioned Memory를 발견한 경우, Temporal Resolution 전에 Store에서 해당 key의 Version Chain을 조회한다.

```text
FUNCTION EXPAND_VERSION_CHAINS(
    candidates,
    memory_store
):

    expanded ← candidates

    FOR candidate IN candidates:

        memory ← candidate.memory

        IF memory.memory_key IS NULL:
            CONTINUE

        IF NOT IS_VERSIONED(memory):
            CONTINUE

        versions ← FIND_ALL_BY_KEY(
            session_id = memory.session_id,
            memory_key = memory.memory_key
        )

        expanded ← UNION(
            expanded,
            versions
        )

    RETURN DEDUPLICATE(expanded)
```

예:

```text
Initial Retrieval:
report_v1.pdf

memory_key:
report.current_file

Version Chain Expansion:
report_v1.pdf
report_v2.pdf
report_final.pdf
```

이후 Temporal Resolver가 Query 시점에 맞는 Version을 선택한다.

---

# 7. Algorithm 5 — Temporal / Version Resolution

## 목적

현재 Query가 요구하는 시점과 맞는 Memory Version을 선택한다.

이 단계가 `SUPERSEDED Memory를 무조건 제거`하는 기존 단순 필터링과 다른 부분이다.

Protected Retrieval 경로에서 가져오는 현재 행동 Constraint는 이 Temporal Resolution을 거치지 않는다.

---

## Current Query

```text
CURRENT / UNSPECIFIED
→ ACTIVE version 우선
```

## Historical Query

```text
HISTORICAL / RELATIVE_TIME
→ target time에 유효했던 version 선택
```

유효 조건:

```text
valid_from ≤ target_time
AND
(
    valid_to > target_time
    OR
    valid_to is NULL
)
```

## Pseudocode

```text
FUNCTION RESOLVE_TEMPORAL_VERSION(
    candidates,
    query_context
):

    resolved ← empty list

    GROUP candidates BY memory_key

    FOR each group:

        IF query_context.temporal_intent
            IN {CURRENT, UNSPECIFIED}:

            active ← FIND is_active == TRUE

            IF active exists:
                KEEP active

        ELSE:

            target ← query_context.target_time
                     OR target_range

            valid_versions ← memories whose
                validity interval overlaps target

            IF exactly one valid version:
                KEEP valid version

            ELSE IF multiple:
                KEEP highest temporal relevance

    ADD memories without memory_key
    according to normal temporal scoring

    RETURN resolved
```

---

# 8. Algorithm 6 — Protected Memory Resolution

## 목적

현재 Task에 실제로 적용되는 **현재 활성화된 Protected Constraint**를 찾는다.

Protected Memory는 General Retrieval과 별도로 Memory Store에서 검색하며, Temporal Resolver를 통과하지 않는다.

## Protected Candidate 조건

```text
memory_type == CONSTRAINT
is_active == TRUE
is_protected == TRUE
```

이 조건을 만족한다고 해서 모든 Task에 무조건 포함하는 것은 아니다.

현재 Task와 Tool / Action에 적용 가능한 Constraint만 선택한다.

## Pseudocode

```text
FUNCTION RETRIEVE_PROTECTED(
    query_context,
    memory_store
):

    candidates ← FIND memories WHERE:
        memory_type == CONSTRAINT
        AND is_active == TRUE
        AND is_protected == TRUE

    protected ← empty list

    FOR memory IN candidates:

        applicability ← CHECK_APPLICABILITY(
            memory,
            query_context
        )

        IF applicability >= threshold:
            ADD memory TO protected

    RETURN protected
```

과거 Constraint는 Store에서 삭제하지 않는다.

단:

```text
is_active = false
is_protected = false
```

상태이므로 현재 행동의 Protected Retrieval에는 포함되지 않는다.

Historical Query에서 과거 Constraint 자체가 필요한 경우에는 General Retrieval 및 Temporal Resolution 경로를 사용한다.

---

## Applicability 판단

초기 구현에서는 다음 신호를 사용할 수 있다.

```text
Query relevance
Entity overlap
Tool match
Action match
```

예:

```text
Constraint:
"외부 이메일은 승인 후 발송"

Task:
"교수님에게 메일 보내줘"

→ Applicable
```

반면:

```text
Task:
"내일 일정 보여줘"

→ Not Applicable
```

---

# 9. Algorithm 7 — Current State Resolution

## 목적

현재 Task에 필요한 State를 선택한다.

## Input

```text
resolved_candidates
query_context
```

## Output

```text
required_states
```

## Pseudocode

```text
FUNCTION RESOLVE_STATE(
    candidates,
    query_context
):

    states ← candidates WHERE
        memory_type == STATE

    relevant_states ← FILTER_BY_RELEVANCE(
        states,
        query_context
    )

    FOR each memory_key:

        version ← TEMPORAL_RESOLUTION(
            relevant_states[memory_key],
            query_context
        )

        IF version exists:
            SELECT version

    RETURN selected_states
```

---

# 10. Algorithm 8 — Flexible Memory Utility

Protected Memory와 필수 State를 제외한 Memory는 Flexible Candidate가 된다.

## Utility

각 Flexible Memory에 대해:

```text
U_i =
α · Relevance_i
+ β · Importance_i
+ γ · Recency_i
+ δ · EntityMatch_i
```

단:

```text
α + β + γ + δ = 1
```

초기 가중치는 실험 전에 고정하고 이후 Ablation 또는 Validation Set을 통해 조정한다.

---

## Recency

현재 Query가 현재 중심일 경우:

```text
Δt = current_time - memory.created_at
```

또는:

```text
Δturn = current_turn - memory.turn_id
```

를 기반으로 decay한다.

예:

```text
Recency_i = exp(-λ · Δt)
```

단, Historical Query에서는 단순 최신성보다 Target Time과의 거리를 사용한다.

```text
TemporalRelevance_i
=
f(|target_time - memory_time|)
```

---

# 11. Algorithm 9 — Budget Calculation

전체 Context Budget:

```text
B_total
```

고정 비용:

```text
C_fixed =
system prompt
+ tool definitions
+ current query
+ required output overhead
```

먼저 고정 비용을 검사한다.

```text
IF C_fixed > B_total:
    RETURN INVALID_BUDGET_CONFIGURATION
```

실제 Memory Budget:

```text
B_memory =
B_total - C_fixed
```

우선 보존 비용:

```text
C_mandatory =
C_protected
+ C_required_state
```

Mandatory Memory가 가용 Memory Budget을 초과하면:

```text
IF C_mandatory > B_memory:
    RETURN INSUFFICIENT_CONTEXT_BUDGET
```

정상 조건에서 남은 예산:

```text
B_flexible =
B_memory - C_mandatory
```

---

# 12. Algorithm 10 — Mandatory Memory Selection

Protected Memory와 필수 State는 일반 Flexible Memory보다 먼저 선택한다.

필수 Memory 일부를 임의로 제거하여 Agent를 실행하지 않는다.

## Pseudocode

```text
FUNCTION SELECT_MANDATORY(
    protected,
    states,
    B_memory
):

    mandatory ← protected + states

    total_cost ← SUM token_count

    IF total_cost > B_memory:
        RETURN INSUFFICIENT_CONTEXT_BUDGET

    RETURN mandatory,
           B_memory - total_cost
```

---

# 13. Mandatory Budget Failure

Protected Memory와 필수 State의 전체 비용이 Memory Budget을 초과하는 경우:

```text
C_mandatory > B_memory
```

일부 Constraint 또는 State만 선택하지 않는다.

처리:

```text
RETURN INSUFFICIENT_CONTEXT_BUDGET
```

이 경우 Agent Action은 실행하지 않는다.

이 조건은 일반 실험과 별도로 기록한다.

---

# 14. Algorithm 11 — Budget-Aware Flexible Selection

Flexible Memory는 남은 Token Budget 안에서 Utility를 최대화하도록 선택한다.

## Objective

```text
maximize Σ U_i x_i

subject to

Σ C_i x_i ≤ B_flexible

x_i ∈ {0,1}
```

---

## V1 — Utility-per-Token Greedy

각 Memory에 대해:

```text
Efficiency_i =
U_i / token_count_i
```

## Pseudocode

```text
FUNCTION SELECT_FLEXIBLE_GREEDY(
    candidates,
    budget
):

    FOR each candidate:
        candidate.efficiency =
            candidate.utility_score
            / candidate.memory.token_count

    SORT candidates BY efficiency DESC

    selected ← empty list
    used ← 0

    FOR candidate IN candidates:

        IF used + token_count <= budget:
            SELECT candidate
            used += token_count

    RETURN selected
```

---

## V2 — 0/1 Knapsack

비교 실험 또는 Proposed Method에서 사용할 수 있다.

```text
item value  = utility
item weight = token_count
capacity    = B_flexible
```

## Pseudocode

```text
FUNCTION SELECT_FLEXIBLE_KNAPSACK(
    candidates,
    budget
):

    dp ← array of size budget + 1

    FOR candidate IN candidates:

        cost ← candidate.token_count
        value ← candidate.utility

        FOR b FROM budget DOWN TO cost:

            dp[b] ← MAX(
                dp[b],
                dp[b - cost] + value
            )

    RECONSTRUCT selected items

    RETURN selected
```

---

# 15. Algorithm 12 — Final Memory Selection

전체 Memory 선택 절차를 하나로 합치면 다음과 같다.

```text
FUNCTION SELECT_MEMORY(
    query,
    memory_store,
    token_budget
):

    query_context ← ANALYZE_QUERY(query)

    memory_budget ←
        CALCULATE_MEMORY_BUDGET(token_budget)

    IF memory_budget == INVALID_BUDGET_CONFIGURATION:
        RETURN INVALID_BUDGET_CONFIGURATION

    general_candidates ← RETRIEVE_CANDIDATES(
        query_context,
        memory_store
    )

    general_candidates ← EXPAND_VERSION_CHAINS(
        general_candidates,
        memory_store
    )

    general_candidates ← RESOLVE_TEMPORAL_VERSION(
        general_candidates,
        query_context
    )

    protected ← RETRIEVE_PROTECTED(
        query_context,
        memory_store
    )

    states ← RESOLVE_STATE(
        general_candidates,
        query_context
    )

    flexible ← general_candidates
        EXCLUDING states
        EXCLUDING protected

    flexible ← COMPUTE_UTILITY(
        flexible,
        query_context
    )

    mandatory,
    remaining_budget ← SELECT_MANDATORY(
        protected,
        states,
        memory_budget
    )

    IF mandatory == INSUFFICIENT_CONTEXT_BUDGET:
        RETURN INSUFFICIENT_CONTEXT_BUDGET

    flexible_selected ←
        SELECT_FLEXIBLE(
            flexible,
            remaining_budget
        )

    selected ←
        mandatory
        + flexible_selected

    RETURN selected
```

---

# 16. Algorithm 13 — Context Construction

선택된 Memory를 LLM에 전달할 Prompt 형태로 구성한다.

## 순서

```text
1. System Prompt
2. Tool Definitions
3. Protected Constraints
4. Current / Historical State
5. Flexible Memories
6. Current Task
```

Memory 선택 시 계산한 Token Cost와 실제 Formatting된 Prompt의 Token Count에는 차이가 발생할 수 있으므로, 최종 Context를 다시 Tokenize한다.

## Pseudocode

```text
FUNCTION BUILD_CONTEXT(
    system_prompt,
    tools,
    selected_memories,
    query,
    B_total
):

    protected ← selected where
        memory_type == CONSTRAINT
        AND is_active == TRUE
        AND is_protected == TRUE

    states ← selected where
        memory_type == STATE

    flexible ← remaining selected

    SORT flexible BY selection_priority DESC

    LOOP:

        context ← FORMAT(
            system_prompt,
            tools,
            protected,
            states,
            flexible,
            query
        )

        actual_tokens ← COUNT_TOKENS(context)

        IF actual_tokens <= B_total:
            RETURN context

        IF flexible is empty:
            RETURN INSUFFICIENT_CONTEXT_BUDGET

        REMOVE lowest-priority item
        FROM flexible
```

Protected Memory와 Required State는 최종 Context 보정 과정에서 제거하지 않는다.

---

# 17. Algorithm 14 — Agent Execution

```text
FUNCTION RUN_AGENT(task):

    selected_memories ← SELECT_MEMORY(
        task,
        memory_store,
        context_budget
    )

    IF selected_memories
        == INVALID_BUDGET_CONFIGURATION:
        LOG error
        RETURN INVALID_BUDGET_CONFIGURATION

    IF selected_memories
        == INSUFFICIENT_CONTEXT_BUDGET:
        LOG error
        RETURN INSUFFICIENT_CONTEXT_BUDGET

    context ← BUILD_CONTEXT(
        system_prompt,
        tools,
        selected_memories,
        task,
        context_budget
    )

    IF context
        == INSUFFICIENT_CONTEXT_BUDGET:
        LOG error
        RETURN INSUFFICIENT_CONTEXT_BUDGET

    action ← LLM_AGENT(context)

    result ← EXECUTE_TOOL(action)

    LOG(
        selected_memories,
        token_usage,
        action,
        result
    )

    new_memories ← EXTRACT_MEMORY(result)

    FOR memory IN new_memories:
        INSERT_MEMORY(memory)

    RETURN result
```

---

# 18. 오류 판정 알고리즘

## 18.1 Constraint Violation

```text
Constraint Violation
=
현재 활성화되어 있으며
현재 Task에 적용되는
Protected Constraint를 위반한 Tool Action이 발생한 경우
```

예:

```text
Constraint:
email.send requires approval

Action:
send_email()

→ violation
```

---

## 18.2 Temporal / Stale-State Error

```text
Temporal Error
=
Query가 요구하는 시점과 다른
Memory Version을 Agent가 행동에 사용한 경우
```

예:

```text
Query:
"현재 보고서 보내줘"

Agent:
report_v1.pdf 사용

→ Error
```

하지만:

```text
Query:
"6월 보고서 보내줘"

Agent:
report_v1.pdf 사용

→ Correct
```

---

# 19. Edge Cases

## Edge Case 1 — Mandatory Memory가 Budget보다 큼

```text
C_mandatory > B_memory
```

처리:

```text
RETURN INSUFFICIENT_CONTEXT_BUDGET
```

Protected Constraint 또는 Required State 일부를 제거한 채 Agent를 실행하지 않는다.

---

## Edge Case 2 — 동일 Key에 ACTIVE State가 여러 개

정상 상태에서는 발생해서는 안 된다.

```text
ASSERT active_count(memory_key) <= 1
```

발견 시 Store Consistency Error로 기록한다.

---

## Edge Case 3 — Historical Query인데 해당 시점 State가 없음

예:

```text
Query:
"2024년에 사용하던 파일"

Memory:
2026년부터만 존재
```

처리:

```text
No valid state found
→ Memory hallucination 금지
→ Unknown / insufficient memory 반환
```

---

## Edge Case 4 — Constraint끼리 충돌

예:

```text
A:
외부 이메일은 승인 필요

B:
교수님 이메일은 승인 불필요
```

초기 구현에서는:

```text
more specific constraint
>
general constraint
```

를 적용한다.

동일 specificity이면 현재 활성화된 최신 Constraint를 우선한다.

---

## Edge Case 5 — Query에 명확한 시간 표현이 없음

```text
temporal_intent = UNSPECIFIED
```

기본 정책:

```text
Current State 우선
```

---

## Edge Case 6 — Token Count가 Budget보다 큰 단일 Memory

```text
memory.token_count > B_flexible
```

Flexible Memory라면 제외한다.

Protected Memory 또는 Required State라면 Mandatory 비용 검사에 의해:

```text
INSUFFICIENT_CONTEXT_BUDGET
```

을 반환한다.

---

## Edge Case 7 — Retrieval에는 없지만 반드시 필요한 Constraint

이 문제가 중요하다.

Protected Memory가 일반 Retrieval Top-K 단계에서 탈락하면 이후 보존할 수 없다.

따라서 Protected Memory는 일반 Candidate Retrieval과 별도로 검색한다.

```text
General Retrieval

Protected Constraint Retrieval
```

Protected Retrieval은 다음 조건을 대상으로 한다.

```text
memory_type == CONSTRAINT
is_active == TRUE
is_protected == TRUE
```

이후 현재 Task에 대한 Applicability를 평가한다.

---

## Edge Case 8 — Fixed Input이 전체 Budget보다 큼

```text
C_fixed > B_total
```

처리:

```text
RETURN INVALID_BUDGET_CONFIGURATION
```

Memory Selection 자체를 수행하지 않는다.

---

## Edge Case 9 — Formatting 이후 Context가 Budget을 초과함

Memory Selection 결과 자체는 Budget 안에 있었지만 Formatting overhead로:

```text
TOKEN_COUNT(final_context) > B_total
```

이 될 수 있다.

처리:

```text
lowest-priority Flexible Memory 제거
↓
Context 재생성
↓
재tokenize
↓
B_total 이하가 될 때까지 반복
```

Flexible Memory를 모두 제거한 뒤에도 초과하면:

```text
RETURN INSUFFICIENT_CONTEXT_BUDGET
```

---

# 20. Proposed Retrieval Flow

최종적으로 Candidate Retrieval은 다음 구조를 사용한다.

```text
                         Query
                           │
              ┌────────────┴────────────┐
              │                         │
              ▼                         ▼
     General Memory Retrieval    Protected Retrieval
              │                         │
 Semantic / BM25 / Entity       Active Protected
              │                    Constraints
              ▼                         │
    Version Chain Expansion             │
              │                         │
              ▼                         │
      Temporal Resolution               │
              │                         │
              ▼                         ▼
       Required State             Applicability
       + Flexible Candidates       Resolution
              │                         │
              └────────────┬────────────┘
                           ▼
                   Mandatory Check
                           │
             ┌─────────────┴─────────────┐
             │                           │
          Overflow                     Success
             │                           │
             ▼                           ▼
INSUFFICIENT_CONTEXT_BUDGET    Flexible Selection
                                         │
                                         ▼
                                  Context Builder
                                         │
                                         ▼
                              Final Token Validation
```

이 구조를 통해:

```text
1. Protected Constraint가 일반 relevance ranking 때문에 사라지는 문제
2. Retrieval된 Version만 보고 잘못된 State를 선택하는 문제
3. Mandatory Memory를 임의로 버린 채 Agent를 실행하는 문제
4. Formatting overhead로 실제 Context Budget을 초과하는 문제
```

를 방지한다.

---

# 21. V1 구현 우선순위

## 반드시 구현

```text
Memory Extraction
Memory Versioning
Temporal Intent Detection
Semantic Retrieval
Version Chain Expansion
Protected Retrieval
Temporal Resolution
Protected Selection
Current State Selection
Budget Calculation
Mandatory Budget Validation
Flexible Selection
Final Context Token Validation
Context Builder
Tool Action
Automatic Evaluation
```

## 이후 추가

```text
BM25
Entity Match
Temporal Decay
Reranking
0/1 Knapsack
```

---

# 22. 핵심 Algorithm 요약

제안 방식은 최종적으로 다음 순서를 따른다.

```text
Query
↓
Temporal Intent Detection
↓
┌─────────────────────────────┐
│                             │
General Retrieval       Protected Retrieval
│                             │
Version Chain Expansion       │
│                             │
Version-Aware                Active Protected
Temporal Resolution          Constraint Selection
│                             │
Required State                Applicability
+ Flexible Candidates         │
│                             │
└──────────────┬──────────────┘
               ↓
Fixed / Mandatory Budget Check
↓
Flexible Memory Utility Calculation
↓
Budget-Aware Flexible Selection
↓
Context Construction
↓
Final Token Validation
↓
필요 시 낮은 우선순위
Flexible Memory 제거
↓
LLM Agent
↓
Tool Action
```

실패 조건:

```text
C_fixed > B_total
→ INVALID_BUDGET_CONFIGURATION

C_mandatory > B_memory
→ INSUFFICIENT_CONTEXT_BUDGET

Final Context > B_total
→ Flexible Memory 제거 후 재tokenize

Flexible Memory가 없는데도
Final Context > B_total
→ INSUFFICIENT_CONTEXT_BUDGET
```

핵심 원칙은 다음 한 문장으로 요약된다.

> **현재 Task에 적용되는 최신 Protected Constraint와 Query 시점에 맞는 Required State를 모두 확보한 뒤, 남은 Token Budget만 Flexible Memory에 사용하며, 필수 정보 자체가 Budget에 들어가지 않으면 Agent를 실행하지 않는다.**