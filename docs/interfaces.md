# Module Interfaces

## 1. 목적

본 문서는 `Constraint-Preserving Budget-Aware Memory` 시스템의 모듈 간 Interface를 정의한다.

각 모듈은 다른 모듈의 내부 구현에 직접 의존하지 않고, 본 문서에서 정의한 입력과 출력만을 통해 통신한다.

대상 모듈은 다음과 같다.

```text
Interaction / Memory Analyzer
        ↓
Memory Store
        ↓
┌───────────────────────┐
│                       │
General Retrieval   Protected Retrieval
│                       │
Version Expansion       │
│                       │
Temporal Resolution     │
└───────────┬───────────┘
            ↓
Memory Selector
            ↓
Context Builder
            ↓
LLM Agent
            ↓
Tool Executor
            ↓
Evaluator
```

주요 구현 파일:

```text
memory/schema.py
memory/store.py
memory/baselines.py
memory/selector.py
memory/query_analyzer.py

context/tokenizer.py
context/builder.py

agent/agent.py
agent/interaction.py
agent/evaluation.py
agent/planner.py
agent/tools/types.py
agent/tools/email.py
agent/tools/file.py
agent/tools/calendar.py
agent/tools/task.py
agent/tools/executor.py

pipeline/orchestrator.py
pipeline/pipeline_adapter.py

benchmark/generator.py
benchmark/evaluator.py
benchmark/runner.py
```

현재 구현 상태는 다음과 같다. 이 문서의 Interface 선언은 계약을 뜻하며 구현 완료를 뜻하지 않는다.

```text
구현됨
- memory/store.py의 인메모리 저장·조회, Version Chain 및 ACTIVE/Constraint 대체 처리
- tests/test_memory.py와 tests/test_state.py의 Memory Store 및 State Versioning 단위 테스트
- tests/test_memory_context_integration.py의 Memory Store → Context Builder 경계 테스트
- context/tokenizer.py의 공용 TokenCounter와 count 검증
- context/builder.py의 Prompt 조립과 최종 Budget Validation
- memory/baselines.py의 Sliding Window, Vector Top-k, Utility-per-Token
- 결정적 규칙 Planner, Agent, mock Tool과 ToolExecutor
- pipeline/orchestrator.py의 다단계 선택·Context·Agent 실행 제어
- pipeline/pipeline_adapter.py의 Agent factory 기반 PipelineAdapter
- benchmark/runner.py의 Baseline 시나리오 실행과 JSONL/summary 출력
- benchmark/evaluator.py의 snapshot 평가와 집계
- tests/test_baseline_context_agent_integration.py와 tests/test_pipeline.py의 경계·파이프라인 테스트

스켈레톤 또는 미구현
- Proposed Query Analyzer와 General Retrieval
- Proposed Version/Temporal Resolution 및 Mandatory/Flexible Selection
- benchmark generator, 실제 LLM planner와 모델별 tokenizer/provider adapter
```

---

# 2. 공통 Interface 원칙

모든 모듈은 다음 원칙을 따른다.

### 2.1 Stable ID

Memory는 생성 이후 동일한 `memory_id`를 유지한다.

```text
memory_id
session_id
turn_id
```

는 모듈 간 Memory 식별의 기본 Key이다.

---

### 2.2 Query-dependent 값은 Memory에 저장하지 않는다

다음 값은 현재 Query에 따라 달라지므로 `MemoryItem`에 영구 저장하지 않는다.

```text
semantic_score
keyword_score
entity_score
relevance_score
recency_score
utility_score
retrieval_rank
selected
```

이 값들은 `MemoryCandidate`에서 관리한다.

---

### 2.3 Store 상태를 외부 모듈이 직접 수정하지 않는다

다음과 같은 코드는 허용하지 않는다.

```python
memory.is_active = False
```

State 변경 및 Version 관계 갱신은 반드시 `MemoryStore` Interface를 통해 수행한다.

---

### 2.4 Context 계층과 동일 Tokenizer 사용

Context 조립과 토큰 계산의 소유자는 `context/`이다.

```text
context/tokenizer.py
→ TokenCounter, provisional_token_count, count_tokens

context/builder.py
→ 고정 입력 계산, Prompt 조립, 최종 예산 검증
```

다음 모듈은 `context.tokenizer`가 제공하는 동일한 `TokenCounter`를 주입받아 사용한다.

```text
Memory Analyzer
Memory Selector
Context Builder
실험 실행기와 usage 기록 계층
```

`Agent`와 `Evaluator`는 직접 tokenize하지 않는다. Agent는 완성된 Context만 받고, Evaluator는 실행 결과에 기록된 토큰 수만 읽는다. 이를 통해 `MemoryItem.token_count`, 고정 입력, 최종 Context, usage 사이의 측정 기준을 일치시킨다.

공용 모델 Tokenizer가 연결되기 전에는 임시 counter를 사용할 수 있지만, 임시 측정값은 모델의 실제 Token Usage로 보고하지 않는다. `provisional_token_count`를 여러 모듈에 중복 정의하지 않고 `context/tokenizer.py`를 단일 진입점으로 사용한다.

`count_tokens`는 입력이 문자열인지, counter 반환값이 음수가 아닌 정수인지 검증한다. 잘못된 counter 결과는 Token Usage로 기록하지 않고 `TypeError` 또는 `ValueError`로 거부한다.

---

### 2.5 시간 표현

모든 Timestamp는 동일한 timezone 기준의 timezone-aware datetime을 사용한다.

직렬화 시 ISO 8601을 사용한다.

예:

```text
2026-09-30T00:30:00+09:00
```

---

# 3. Shared Types

## 3.1 MemoryType

```python
class MemoryType(str, Enum):
    CONSTRAINT = "constraint"
    STATE = "state"
    PREFERENCE = "preference"
    FACT = "fact"
    EXPERIENCE = "experience"
```

---

# 3.2 MemoryItem

Memory Store에 저장되는 기본 단위이다.

```python
@dataclass(frozen=True)
class MemoryItem:
    memory_id: str
    session_id: str
    turn_id: int

    memory_type: MemoryType
    content: str
    token_count: int

    created_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None

    importance: float = 0.5
    is_active: bool = True
    is_protected: bool = False

    supersedes: str | None = None
    superseded_by: str | None = None
    memory_key: str | None = None
```

## `memory_key` 규칙

`memory_key`는 동일한 논리적 정보의 Version을 연결한다.

예:

```text
report.current_file

email.external.requires_approval
```

규칙:

```text
STATE
→ memory_key 필수

CONSTRAINT
→ 변경/대체될 수 있는 Constraint라면 memory_key 필수

PREFERENCE / FACT / EXPERIENCE
→ 필요할 경우 선택적으로 사용
```

예:

```text
Memory 1
memory_key = report.current_file
content = report_v1.pdf

Memory 2
memory_key = report.current_file
content = report_final.pdf
supersedes = Memory 1
```

---

## Protected Constraint invariant

현재 행동에 사용되는 Protected Memory는 다음을 만족한다.

```text
memory_type == CONSTRAINT
is_active == true
is_protected == true
```

Constraint가 새로운 Constraint에 의해 대체되면:

```text
Old Constraint

is_active = false
is_protected = false
```

새로운 Constraint는:

```text
New Constraint

is_active = true
is_protected = true
```

로 유지한다.

---

# 3.3 Interaction

User, Tool, Environment에서 발생하는 입력을 통일한다.

```python
@dataclass(frozen=True)
class Interaction:
    session_id: str
    turn_id: int

    content: str
    source: str

    timestamp: datetime
```

`source` 예:

```text
"user"
"tool"
"environment"
"agent"
```

---

# 3.4 TemporalIntent

```python
class TemporalIntent(str, Enum):
    CURRENT = "current"
    HISTORICAL = "historical"
    RELATIVE_TIME = "relative_time"
    UNSPECIFIED = "unspecified"
```

---

# 3.5 QueryContext

현재 Task 분석 결과이다.

```python
@dataclass(frozen=True)
class QueryContext:
    query: str

    temporal_intent: TemporalIntent

    target_time: datetime | None = None
    target_range: tuple[datetime, datetime] | None = None

    entities: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()

    current_turn: int = 0
    current_time: datetime | None = None
```

예:

```text
Query:
"8월에 쓰던 보고서를 찾아줘."

temporal_intent:
HISTORICAL

target_range:
2026-08-01 ~ 2026-09-01

entities:
["보고서"]

required_tools:
["file"]
```

---

# 3.6 MemoryCandidate

Retrieval 이후 Query-dependent 정보를 갖는 Memory Wrapper이다.

```python
@dataclass
class MemoryCandidate:
    memory: MemoryItem

    semantic_score: float = 0.0
    keyword_score: float = 0.0
    entity_score: float = 0.0

    relevance_score: float = 0.0
    recency_score: float = 0.0
    utility_score: float = 0.0

    is_applicable: bool = False

    selection_reason: str | None = None
```

Version Chain Expansion을 통해 추가된 Memory는 최초 Retrieval score가 없을 수 있다.

이 경우 필요한 경우 원래 Candidate의 retrieval signal을 상속하거나 별도 relevance를 계산한다.

정확한 정책은 Selector 내부 구현으로 둔다.

---

# 3.7 PipelineStatus

Memory Pipeline의 정상/실패 상태를 통일한다.

```python
class PipelineStatus(str, Enum):
    OK = "ok"

    INVALID_BUDGET_CONFIGURATION = (
        "invalid_budget_configuration"
    )

    INSUFFICIENT_CONTEXT_BUDGET = (
        "insufficient_context_budget"
    )
```

의미:

```text
INVALID_BUDGET_CONFIGURATION

C_fixed > B_total
```

```text
INSUFFICIENT_CONTEXT_BUDGET

Mandatory Memory가 B_memory에 들어가지 않거나

Flexible Memory를 모두 제거한 후에도
최종 Context가 B_total을 초과
```

---

# 4. Memory Analyzer Interface

담당 모듈:

```text
Memory Analyzer
```

역할:

```text
Interaction
→ 저장 가능한 MemoryItem 생성
```

## Interface

```python
def extract_memories(
    interaction: Interaction
) -> list[MemoryItem]:
    ...
```

## Input

```text
Interaction
```

## Output

```text
List[MemoryItem]
```

Analyzer가 결정하는 값:

```text
memory_type
memory_key
content
importance
is_protected
valid_from
token_count
```

초기 생성 시 기본값:

```text
is_active = true

valid_to = None

supersedes = None

superseded_by = None
```

실제 Version 관계는 Store가 결정한다.

---

# 5. Memory Store Interface

담당 파일:

```text
memory/store.py
```

Memory Store는 다음 책임을 가진다.

```text
Memory 저장
Memory 조회
Version Chain 관리
ACTIVE Version 관리
Constraint 대체 처리
Reference consistency 검증
```

Retrieval ranking이나 Utility 계산은 Store의 책임이 아니다.

현재 V1 구현은 `dict[str, MemoryItem]` 기반의 프로세스 내부 인메모리 저장소다. 재시작 후 복원과 파일·DB 영속화는 제공하지 않는다. `MemoryItem`은 불변 객체이므로 버전 갱신 시 `dataclasses.replace`로 새 스냅샷을 만들고 동일 ID의 저장 값을 교체한다.

Store가 사용하는 오류 계약은 다음과 같다.

```text
TypeError
→ insert에 MemoryItem이 아닌 값 전달

ValueError
→ 빈 조회 키, memory_id 중복, 알 수 없는 supersedes 참조,
  서로 다른 Session/Key의 버전 연결, 역행하거나 호환되지 않는 시각

MemoryStoreConsistencyError
→ ACTIVE 중복 또는 이미 다른 후속 버전에 연결된 항목 등
  저장소 불변식 위반
```

---

## 5.1 Insert

```python
def insert(
    memory: MemoryItem
) -> MemoryItem:
    ...
```

새 Memory를 저장한다.

`memory_key`가 없는 Memory는 버전 체인을 만들지 않고 ID로 저장한다. 단, `supersedes` 또는 `superseded_by`를 가진 항목에는 `memory_key`가 반드시 있어야 한다.

`memory_key`가 있고 새 Memory가 ACTIVE라면 동일 Session + Key의 기존 ACTIVE Memory를 자동으로 직전 버전으로 간주한다. 호출자가 `supersedes`를 명시했다면 그 참조는 현재 ACTIVE 항목과 일치해야 한다.

동일 `memory_key`의 기존 ACTIVE Memory를 대체하는 경우 Store가:

```text
old.is_active = false
old.valid_to = new.valid_from
old.superseded_by = new.memory_id

new.supersedes = old.memory_id
```

를 적용한다.

기존 Memory가 Constraint라면:

```text
old.is_protected = false
```

도 함께 적용한다.

새 버전의 `valid_from`은 직전 버전보다 빠를 수 없다. 비활성 Memory는 명시적인 `supersedes`가 있을 때만 해당 선행 버전과 연결한다. 저장 성공 시 Store가 보정한 `MemoryItem`을 반환한다.

---

## 5.2 Get by ID

```python
def get(
    memory_id: str
) -> MemoryItem | None:
    ...
```

빈 문자열은 `ValueError`, 존재하지 않는 ID는 `None`을 반환한다.

---

## 5.3 Find Current Version

```python
def find_active_by_key(
    session_id: str,
    memory_key: str
) -> MemoryItem | None:
    ...
```

동일 Session + `memory_key`에 대해 현재 ACTIVE Memory를 반환한다.

Invariant:

```text
active_count <= 1
```

0개이면 `None`, 2개 이상이면 임의의 항목을 고르지 않고 `MemoryStoreConsistencyError`를 발생시킨다. `session_id`와 `memory_key`는 비어 있지 않은 문자열이어야 한다.

---

## 5.4 Find Version Chain

D-01에서 확정한 Interface이다.

```python
def find_all_by_key(
    session_id: str,
    memory_key: str
) -> list[MemoryItem]:
    ...
```

반환 결과는 해당 `memory_key`의 모든 Version이다.

정렬:

```text
valid_from ASC, turn_id ASC, memory_id ASC
```

비교할 수 없는 시각이 한 체인에 섞여 있으면 `MemoryStoreConsistencyError`를 발생시킨다.

예:

```text
[
    report_v1,
    report_v2,
    report_final
]
```

---

## 5.5 Find Active Protected Constraints

```python
def find_active_protected_constraints(
    session_id: str
) -> list[MemoryItem]:
    ...
```

반환 조건:

```text
memory_type == CONSTRAINT
is_active == true
is_protected == true
```

Temporal Filter를 적용하지 않는다.

Task applicability는 Selector가 판단한다.

반환 순서는 `turn_id ASC, memory_id ASC`이다.

---

## 5.6 Session Memories

Retrieval index 구축 또는 테스트에서 사용할 수 있다.

```python
def list_session_memories(
    session_id: str
) -> list[MemoryItem]:
    ...
```

비활성 버전을 포함한 Session의 모든 Memory를 `turn_id ASC, memory_id ASC`로 반환한다. 기록이 없으면 빈 리스트다.

## 5.7 구현 검증 범위

현재 단위 테스트는 다음 계약을 실행 가능한 형태로 검증한다.

```text
tests/test_memory.py
- insert/get 및 중복 ID·잘못된 입력 거부
- Session별 전체 목록과 Session + memory_key 격리
- ACTIVE 조회와 Version Chain 정렬
- 활성·보호된 Constraint만 별도 조회
- 알 수 없는 선행 버전, 다른 Session/Key 참조, 역행하는 valid_from 거부

tests/test_state.py
- 최초 State의 ACTIVE 등록과 새 State의 자동 대체
- 3단계 Version Chain의 양방향 참조와 valid_to 갱신
- Key/Session별 ACTIVE 유일성 및 독립성
- 비활성 과거 State 삽입 시 현재 ACTIVE 유지
- Store 갱신 과정에서 호출자가 전달한 불변 MemoryItem을 수정하지 않음
- 명시적 supersedes가 현재 ACTIVE와 다를 때 일관성 오류 발생
```

영속화, 동시성, 프로세스 간 공유와 검색 인덱스는 이 테스트 범위에 포함되지 않는다.

`tests/test_memory_context_integration.py`는 현재 Store와 Context Builder 사이의 다음 계약을 추가로 검증한다.

```text
- 현재 ACTIVE State와 활성·보호된 Constraint가 각 Context 섹션에 포함됨
- 대체된 State와 Constraint는 Store 이력에는 남지만 현재 Context에서 제외됨
- Store에서 조회한 필수 Memory를 포맷한 최종 Context가 예산을 넘으면
  INSUFFICIENT_CONTEXT_BUDGET과 context=None을 반환함
```

이 테스트의 `selection_from_store`는 미구현 Selector를 대신하는 테스트 전용 helper다. 따라서 General Retrieval, Version Chain Expansion, Temporal Resolution 또는 Budget-Aware Selection의 통합 완료를 의미하지 않는다.

---

# 6. Query Analyzer Interface

담당 파일:

memory/query_analyzer.py

Query를 Memory selection에 필요한 구조로 변환한다.

```python
def analyze_query(
    query: str,
    current_turn: int,
    current_time: datetime
) -> QueryContext:
    ...
```

내부적으로:

```text
Temporal Intent Detection
Entity Extraction
Required Tool Detection
```

을 수행한다.

---

# 7. General Retrieval Interface

담당 파일:

```text
memory/selector.py
```

Selector는 구체 Store 구현 대신 다음 최소 Protocol에 의존한다.

```python
class MemoryStoreProtocol(Protocol):
    def find_all_by_key(
        self,
        session_id: str,
        memory_key: str,
    ) -> list[MemoryItem]:
        ...

    def find_active_protected_constraints(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        ...

    def list_session_memories(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        ...
```

## Interface

```python
def retrieve_candidates(
    query_context: QueryContext,
    memory_store: MemoryStoreProtocol,
    session_id: str,
    top_k: int
) -> list[MemoryCandidate]:
    ...
```

기본 Retrieval Signal:

```text
Semantic Similarity
```

선택적 Signal:

```text
BM25
Entity Match
```

반환 결과는 최종 Context가 아니라 **Candidate Pool**이다.

---

# 8. Version Chain Expansion Interface

D-01 확정 사항이다.

General Retrieval에서 Versioned Memory를 발견하면 동일 Key의 전체 Version을 Store에서 추가 조회한다.

```python
def expand_version_chains(
    candidates: Sequence[MemoryCandidate],
    memory_store: MemoryStoreProtocol
) -> list[MemoryCandidate]:
    ...
```

처리:

```text
Candidate
↓
memory_key 존재?
↓ YES
Versioned Type?
↓ YES
MemoryStore.find_all_by_key()
↓
Candidate Pool 확장
↓
중복 제거
```

예:

```text
Retrieved:

mem_010
report_v1.pdf
memory_key = report.current_file
```

Store 조회:

```text
mem_010 → report_v1.pdf
mem_030 → report_v2.pdf
mem_050 → report_final.pdf
```

세 Version 모두 Temporal Resolver에 전달한다.

---

# 9. Temporal Resolution Interface

General Retrieval 경로의 Versioned Memory에 적용한다.

Protected Retrieval 경로에는 적용하지 않는다.

## Interface

```python
def resolve_temporal_versions(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext
) -> list[MemoryCandidate]:
    ...
```

---

## CURRENT / UNSPECIFIED

```text
is_active == true
```

인 Version을 선택한다.

---

## HISTORICAL / RELATIVE_TIME

Query가 요구하는 Target Time에 유효한 Version을 선택한다.

유효 구간:

```text
valid_from <= target_time < valid_to
```

`valid_to is None`이면:

```text
valid_from <= target_time
```

을 만족하면 유효하다.

---

## No Valid Version

해당 시점에 유효한 Version이 없다면 임의의 Version을 선택하지 않는다.

반환 Candidate에서 해당 State를 제외하고 상위 모듈이:

```text
Unknown / insufficient memory
```

처리할 수 있도록 한다.

---

# 10. Protected Retrieval Interface

D-03 확정 사항이다.

현재 행동에 적용되는 Constraint는 General Retrieval과 별도로 검색한다.

## Interface

```python
def retrieve_protected(
    query_context: QueryContext,
    memory_store: MemoryStoreProtocol,
    session_id: str
) -> list[MemoryItem]:
    ...
```

처리 대상:

```text
memory_type == CONSTRAINT
is_active == true
is_protected == true
```

그 후 Applicability를 평가한다.

---

## Applicability Interface

```python
def is_constraint_applicable(
    constraint: MemoryItem,
    query_context: QueryContext
) -> bool:
    ...
```

판단 신호:

```text
Task relevance
Tool match
Action match
Entity / target match
```

예:

```text
Constraint:
외부 이메일은 승인 후 발송

Task:
교수님에게 보고서를 이메일로 보내줘

→ True
```

```text
Task:
내일 일정 보여줘

→ False
```

Protected Retrieval에는 Temporal Filter를 적용하지 않는다.

과거 Constraint 조회는 General Retrieval 경로에서 처리한다.

---

# 11. Required State Interface

Temporal Resolution 결과에서 현재 Task에 필요한 State를 결정한다.

```python
def resolve_required_states(
    candidates: Sequence[MemoryCandidate],
    query_context: QueryContext
) -> list[MemoryItem]:
    ...
```

반환 결과는 Mandatory Memory에 포함된다.

---

# 12. Flexible Memory Utility Interface

Protected Constraint와 Required State에 포함되지 않은 Candidate는 Flexible Memory 후보가 된다.

```python
def compute_utility(
    candidate: MemoryCandidate,
    query_context: QueryContext
) -> float:
    ...
```

개념적 Utility:

```text
U_i =
α · Relevance_i
+ β · Importance_i
+ γ · Recency_i
+ δ · EntityMatch_i
```

계산 결과:

```python
candidate.utility_score
```

에 기록한다.

---

# 13. Budget Interface

## 13.1 Budget 값 전달

현재 코드에는 `BudgetConfig` dataclass가 없다. Budget은 `select_memory`와 `calculate_memory_budget`의 `total_budget`, `fixed_tokens` 인자로 전달하고, `memory_budget`은 계산 결과로만 사용한다.

기본 관계:

```text
B_memory
=
B_total - C_fixed
```

---

## 13.2 Budget Calculation

```python
def calculate_memory_budget(
    total_budget: int,
    fixed_tokens: int
) -> tuple[PipelineStatus, int]:
    ...
```

정상:

```text
status = OK
memory_budget >= 0
```

다음 조건에서는:

```text
fixed_tokens > total_budget
```

```text
status =
INVALID_BUDGET_CONFIGURATION
```

을 반환한다.

---

# 14. Mandatory Selection Interface

Mandatory Memory:

```text
Applicable Protected Constraint
+
Required State
```

## Interface

```python
@dataclass
class MandatorySelectionResult:
    status: PipelineStatus

    memories: tuple[MemoryItem, ...]

    used_tokens: int
    remaining_budget: int
```

```python
def select_mandatory(
    protected: Sequence[MemoryItem],
    states: Sequence[MemoryItem],
    memory_budget: int
) -> MandatorySelectionResult:
    ...
```

정상:

```text
C_mandatory <= B_memory
```

이면 모든 Mandatory Memory를 선택한다.

다음 조건:

```text
C_mandatory > B_memory
```

에서는 Mandatory Memory 일부를 임의로 버리지 않는다.

```text
status =
INSUFFICIENT_CONTEXT_BUDGET
```

을 반환한다.

Agent는 실행하지 않는다.

---

# 15. Flexible Selection Interface

```python
def select_flexible(
    candidates: Sequence[MemoryCandidate],
    budget: int
) -> list[MemoryCandidate]:
    ...
```

V1 기본 방식:

```text
Utility-per-Token Greedy
```

Efficiency:

```text
utility_score
/
token_count
```

Optional implementation:

```text
0/1 Knapsack
```

어떤 방식을 사용할지는 Experiment Config에서 선택 가능하게 한다. 단, 반환 순서는 다음 V1 계약으로 고정한다.

```text
selected_flexible[0]
→ 선택 우선순위가 가장 높은 Memory

selected_flexible[-1]
→ 선택 우선순위가 가장 낮은 Memory
```

즉 `select_flexible`은 선택된 후보를 **선택 우선순위 내림차순**으로 반환해야 한다. Utility-per-Token, 0/1 Knapsack 등 내부 알고리즘이 달라도 이 반환 순서는 바뀌지 않는다. 동일 우선순위의 tie-break 규칙은 구현에서 결정할 수 있지만 반드시 결정적이어야 한다.

---

# 16. Final Memory Selection Interface

Memory Selection 전체를 외부 모듈에서 호출할 때 사용하는 상위 Interface이다.

## SelectionResult

```python
@dataclass
class SelectionResult:
    status: PipelineStatus

    protected: tuple[MemoryItem, ...]
    states: tuple[MemoryItem, ...]
    flexible: tuple[MemoryItem, ...]

    retrieved_memory_ids: tuple[str, ...]
    selected_memory_ids: tuple[str, ...]
    rejected_memory_ids: tuple[str, ...]

    memory_tokens: int
    remaining_budget: int
```

`SelectionResult.flexible`은 `select_flexible`의 순서를 그대로 보존한다. 따라서 다음 불변식을 만족한다.

```text
flexible[0]  = 유지 우선순위가 가장 높은 Memory
flexible[-1] = 예산 초과 시 가장 먼저 제거할 Memory
```

Selector와 Context Builder 사이에서 `flexible`을 다시 정렬하거나 역순으로 바꾸지 않는다.

## Interface

```python
def select_memory(
    query: str,
    session_id: str,
    memory_store: MemoryStoreProtocol,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    current_time: datetime,
    *,
    top_k: int = 20
) -> SelectionResult:
    ...
```

처리 순서:

```text
Query Analysis
↓
General Retrieval
↓
Version Chain Expansion
↓
Temporal Resolution
↓
Required State Resolution

+

Protected Retrieval
↓
Applicability Resolution

↓

Budget Validation
↓
Mandatory Selection
↓
Flexible Utility
↓
Flexible Selection
↓
SelectionResult
```

---

# 17. Context Builder Interface

담당 파일:

```text
context/tokenizer.py
context/builder.py
```

담당 영역:

```text
SelectionResult
→ 실제 LLM Prompt
```

## ContextBuildResult

```python
@dataclass
class ContextBuildResult:
    status: PipelineStatus

    context: str | None

    selected_memory_ids: tuple[str, ...]
    dropped_flexible_ids: tuple[str, ...]

    protected_tokens: int
    state_tokens: int
    flexible_tokens: int

    total_input_tokens: int
    unused_budget: int
```

`protected_tokens`, `state_tokens`, `flexible_tokens`는 `MemoryItem.token_count`가 아니다. 각 섹션을 포맷한 문자열을 `total_input_tokens`와 같은 `token_counter`로 센 값이다. Selector의 기억 예산은 계속 `MemoryItem.token_count`를 쓴다.

`ContextBuildResult`와 아래 함수는 `context.builder`의 공개 Interface다. Agent 또는 Memory 모듈 안에 Context 조립 로직을 복제하지 않는다.

## Interface

```python
def build_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    selection: SelectionResult,
    total_budget: int,
    token_counter: TokenCounter | None = None
) -> ContextBuildResult:
    ...

def fixed_context_tokens(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    token_counter: TokenCounter | None = None
) -> int:
    ...

def count_tokens(
    text: str,
    token_counter: TokenCounter | None = None,
) -> int:
    ...
```

`fixed_context_tokens`는 기억이 없는 프롬프트의 토큰 수다. `[SYSTEM]`, `[TOOL DEFINITIONS]`, `[CURRENT TASK]` 제목을 포함한다. `run_task`는 이 값을 `select_memory`의 `fixed_tokens`로 넘긴다.

`token_counter`는 `context.tokenizer.TokenCounter`를 받는 자리다. 공용 토크나이저가 정해지기 전에는 `context.tokenizer`의 임시 counter를 쓴다. 고정 프롬프트, 섹션, 최종 입력, `usage`는 한 실행에서 같은 함수를 사용한다. 기본 단어 수는 모델 토크나이저 측정값이 아니므로 공용 토크나이저를 넣기 전에는 실험 지표로 보고하지 않는다.

현재 `context/builder.py`는 `context.tokenizer`의 `TokenCounter`, `provisional_token_count`, `count_tokens`를 사용한다. Context 조립과 최종 Budget Validation은 구현되어 있으며, 실제 모델별 Tokenizer 연결은 실행기 통합 단계에서 진행한다.

프롬프트 섹션 순서는 다음과 같다.

```text
[SYSTEM]
[TOOL DEFINITIONS]
[PROTECTED CONSTRAINTS]
[STATE]
[RELEVANT MEMORY]
[CURRENT TASK]
```

상태 섹션 제목은 `[STATE]`다. 과거 버전을 현재 상태라고 표시하지 않기 위해서다. 빈 Protected, State, Flexible 섹션은 프롬프트에 넣지 않는다.

Flexible Memory 제거 계약은 다음과 같이 고정한다.

```python
while total_input_tokens > total_budget and flexible:
    removed = flexible.pop()
    dropped_flexible_ids.append(removed.memory_id)
```

즉 Context Builder는 `SelectionResult.flexible`의 **마지막 Memory부터 한 개씩 제거**한다. 제거 후 Context 전체를 다시 조립하고 다시 tokenize한다. `dropped_flexible_ids`는 실제 제거 순서와 동일하며, 첫 번째 ID가 최초로 제거된 가장 낮은 우선순위 Memory다. Context Builder는 자체 점수 계산이나 재정렬을 수행하지 않는다.

---

## D-04 Final Token Validation

Formatting 이후 실제 Token 수를 다시 계산한다.

```text
Final Context <= B_total
→ 정상 반환
```

초과하면:

```text
SelectionResult.flexible의
마지막 Memory 제거
↓
재format
↓
재tokenize
```

반복한다.

Protected Constraint 및 Required State는 제거하지 않는다.

모든 Flexible Memory를 제거한 이후에도:

```text
Final Context > B_total
```

이라면:

```text
status =
INSUFFICIENT_CONTEXT_BUDGET
```

을 반환한다.

---

# 18. Agent Interface

담당 영역:

```text
Context Builder의 context ◄──────────────┐
        ↓                                │
Planner / Agent                          │
        ↓                                │
Tool Executor                            │
        ↓                                │
Interaction ─► Memory Analyzer / Store ──┘  (stopped_reason = tool_called)
        │
AgentRunResult ─► Evaluator
```

역할:

```text
완성된 Context로 다음 행동 하나를 결정한다.
Tool Executor에 ToolCall을 넘기고 ToolResult를 받는다.
툴을 실행하면 Interaction을 돌려주고 멈춘다.
Memory Analyzer에는 Interaction만 넘긴다.
Evaluator에는 AgentRunResult를 넘긴다.
```

한 번 호출에 행동은 하나다. 툴을 실행했으면 Memory 쪽이 그 `Interaction`을 정리하고, Context Builder가 예산 안에서 다시 조립한 context로 Agent를 다시 부른다. Agent는 툴 결과를 context 뒤에 붙이지 않는다. 다시 부르는 반복과 최대 반복 횟수는 Agent 밖에서 정한다.

Agent가 다른 모듈과 주고받는 값은 아래 입력과 출력뿐이다. 기억 선택, 프롬프트 조립, 토큰 계산, 예산 검사는 Selector와 Context Builder의 Interface에 남긴다.

```text
MemoryStore
SelectionResult
ContextBuildResult
system_prompt
tool_definitions
token_counter
context_budget
```

위 값은 Agent 생성자와 `plan`, `run_task`, `act`의 인자가 아니다.

---

## AgentAction

Planner가 Agent에, Agent가 Tool Executor에 넘기는 행동 하나다.

```python
@dataclass
class AgentAction:
    tool_name: str | None
    action: str | None
    arguments: dict

    text_response: str | None
```

`action`은 `ToolCall.action`과 같다. 텍스트만 반환할 때는 `tool_name`과 `action`이 모두 `None`이다.

---

## Planner

모델이 붙는 자리다. Context Builder가 만든 문자열만 받고, 행동 하나를 돌려준다.

```python
class Planner:
    def __init__(
        self,
        recipient_aliases: Mapping[str, str] | None = None,
    ) -> None:
        ...

    def plan(self, context: str) -> AgentAction:
        ...
```

## Input

```text
context: str
```

## Output

```text
AgentAction
```

툴 실행은 Planner의 출력이 아니다. `Agent`가 이 `AgentAction`을 `ToolCall`로 바꾸어 Tool Executor에 넘긴다.

LLM이 정해지기 전의 `Planner`는 결정적 규칙으로 행동을 고른다. 같은 `context`에는 같은 `AgentAction`을 돌려준다. 읽는 섹션은 `[PROTECTED CONSTRAINTS]`, `[STATE]`, `[RELEVANT MEMORY]`, `[CURRENT TASK]`다.

이전 툴 결과는 `Interaction.content`와 같은 형식의 줄이면 어느 섹션에 있든 읽는다. 다시 조립한 context에 이 줄이 없으면 Planner는 같은 툴을 다시 고를 수 있다.

```text
email.request_approval success=true output={"approval_id": "approval_001", ...}
```

`recipient_aliases`는 과제의 호칭을 메일 주소로 바꾸는 표다. 예: `{"교수님": "professor@example.test"}`. 과제와 State에 주소가 없고 표에도 없으면 메일 툴을 호출하지 않고 텍스트로 끝낸다.

승인 제약이 적용되면 `request_approval`을 고른다. 이전 결과가 `pending`이면 `send_email`을 고르지 않고 텍스트로 끝낸다. Context에 승인된 번호가 있을 때만 그 번호로 `send_email`을 고른다.

---

## Agent

생성 시 연결하는 이웃 모듈은 Tool Executor와 Planner다. Planner를 넘기지 않으면 `Planner`를 붙인다.

```python
GoalChecker = Callable[
    [str | None, tuple[ToolCall, ...], tuple[ToolResult, ...]],
    bool,
]

class Agent:
    def __init__(
        self,
        *,
        executor: ToolExecutor | None = None,
        planner: Planner | None = None,
        goal_checker: GoalChecker | None = None,
        current_turn: int = 0,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        ...

    def plan(self, context: str) -> AgentAction:
        ...

    def run_task(
        self,
        context: str,
        session_id: str,
    ) -> AgentRunResult:
        ...

    def act(
        self,
        context: str,
        *,
        session_id: str,
    ) -> ActResult:
        ...
```

`plan`의 입력은 `Planner.plan`의 입력과 같다. `Agent.plan`은 그 문자열을 Planner에 그대로 넘긴다.

`act`는 `plan` 한 번과 툴 실행 한 번을 한다. `run_task`는 `act`를 한 번 부르고 `goal_completed`를 채운다.

`current_turn`은 다음 `Interaction`의 기준 차례다. 툴을 실행하면 만든 `Interaction` 수만큼 올라간다. `clock`은 `Interaction.timestamp`를 정한다. 넘기지 않으면 현재 UTC 시각이다.

## Input

```text
plan
  context: str

act
  context: str
  session_id: str

run_task
  context: str
  session_id: str
```

`context`는 Context Builder가 확정한 최종 문자열이다. 두 번째 호출부터는 이전 `Interaction`을 반영해 다시 조립한 문자열이다.

## Output

```text
plan     → AgentAction
act      → ActResult
run_task → AgentRunResult
```

`stopped_reason`은 다음 중 하나다.

```text
completed     텍스트 응답으로 끝남. 다시 부르지 않는다.
tool_called   툴을 실행함. Interaction을 반영해 다시 조립한 context로 다시 부른다.
tool_failure  툴이 실패했거나, tool_name과 action 중 하나만 있음.
```

`goal_completed`는 `run_task` 출력의 한 칸이다. `stopped_reason`이 `completed`이고 `goal_checker`가 응답, `ToolCall`, `ToolResult`를 보고 참을 돌려줄 때만 True다. checker가 없으면 False다. 툴을 호출했다는 사실 자체가 이 값의 참은 아니다. Evaluator는 이 값을 과제 성공의 한 조건으로 읽고, 선택 결과와 행동 평가는 Evaluator Interface에서 따로 한다.

---

## ActResult

`act`가 Tool Executor와 주고받은 한 단계의 출력이다.

```python
@dataclass
class ActResult:
    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    stopped_reason: str
    interactions: tuple[Interaction, ...]
```

`tool_calls`는 Tool Executor에 넘긴 호출이다. `tool_results`는 Tool Executor가 돌려준 결과다. `interactions`는 같은 결과를 Memory Analyzer 입력으로 바꾼 값이다. 한 번 호출에 툴은 많아야 하나이므로 세 칸은 비어 있거나 원소가 하나다.

---

## AgentRunResult

`run_task`가 Evaluator에 넘기는 출력이다.

```python
@dataclass
class AgentRunResult:
    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    goal_completed: bool
    stopped_reason: str
    interactions: tuple[Interaction, ...]
```

Evaluator가 이 결과에서 읽는 행동은 `tool_calls`와 `tool_results`다. 기억 선택과 프롬프트 조립의 출력은 `SelectionResult`, `ContextBuildResult`로 Selector와 Context Builder가 따로 넘긴다.

---

## Interaction 출력

툴 결과를 Memory Analyzer의 입력인 `Interaction`으로 바꾸는 Interface다. `Interaction` 필드는 3.3절과 같다. `source`는 `"tool"`이다.

```python
def interactions_from_tool_results(
    session_id: str,
    turn_id: int,
    results: tuple[ToolResult, ...] | list[ToolResult],
    timestamp: datetime,
) -> tuple[Interaction, ...]:
    ...
```

## Input

```text
session_id
turn_id
ToolResult 목록
timestamp
```

## Output

```text
tuple[Interaction, ...]
```

n번째 결과의 `turn_id`는 `turn_id + n`이다. Agent는 `current_turn`과 `clock()`을 넘긴다. `content`는 Planner 절의 툴 결과 줄과 같은 형식이다.

저장은 Memory Store Interface의 `insert`가 담당한다. 이 함수의 출력은 `Interaction`까지다. `Interaction`에서 기억을 추출하고 정리하는 일은 Memory Analyzer가 맡는다.

---

## Evaluator에 넘기는 승인 목록

Evaluator는 `send_email` 인자의 `approval_id`가 시나리오 `valid_approval_ids`에 있는지로 승인 발송을 본다. Agent는 그 목록을 평가기에 넘기기 전에, 이번 실행의 `pending` 번호를 뺀 사본을 만든다.

```python
def scenario_with_granted_approvals(
    scenario: Mapping[str, Any],
    tool_results: Sequence[ToolResult],
) -> dict[str, Any]:
    ...
```

## Input

```text
scenario
tool_results
```

`tool_results`에서 읽는 항목은 성공한 `email.request_approval` 중 `status`가 `"pending"`인 `approval_id`다.

## Output

```text
scenario 사본
```

사본의 `constraint_rules[].approval.valid_approval_ids`에서 그 `pending` 번호를 뺀다. 실행 전에 들어 있던 승인 번호는 사본에 그대로 둔다. 시나리오 원본은 바꾸지 않는다.

---

# 19. Tool Interface

모든 Tool은 공통 Tool Call / Result 형식을 사용한다.

## ToolCall

```python
@dataclass
class ToolCall:
    tool_name: str
    action: str

    arguments: dict
```

예:

```python
ToolCall(
    tool_name="email",
    action="request_approval",
    arguments={
        "recipient": "professor"
    }
)
```

---

## ToolResult

```python
@dataclass
class ToolResult:
    success: bool

    tool_name: str
    action: str

    output: dict

    error: str | None = None
```

---

# 20. V1 Tool Interfaces

V1 Testbed에서는 다음 Tool을 제공한다.

```text
agent/tools/email.py       EmailTool
agent/tools/file.py        FileTool
agent/tools/calendar.py    CalendarTool
agent/tools/task.py        TaskTool
agent/tools/executor.py    ToolExecutor
```

각 툴은 실험 간 상태가 섞이지 않도록 인스턴스로 만든다. `ToolExecutor.execute(call)`이 `tool_name`과 `action`으로 메서드를 고른다. 알 수 없는 툴, 알 수 없는 action, 인자 불일치는 `success=False`인 `ToolResult`다.

Mock은 행동 제약을 대신 지키지 않는다. 승인 없는 `send_email`도 발송으로 기록하며 `output.approved`는 False다. 없는 `approval_id`, 또는 수신자·제목·본문·`attachment`가 그 승인 요청과 다른 발송은 실패한다. `select_file`은 카탈로그에 없는 이름도 성공으로 기록하고 `found`로 구분한다. 제약 위반과 잘못된 파일 선택은 Evaluator가 호출 이력으로 판단한다.

---

## 20.1 Email Tool

```python
class EmailTool:
    def __init__(self, granted_approval_ids: list[str] | None = None):
        ...

    def draft_email(
        self,
        recipient: str,
        subject: str | None = None,
        body: str | None = None,
        attachment: str | None = None
    ) -> ToolResult:
        ...

    def request_approval(
        self,
        recipient: str,
        subject: str | None = None,
        body: str | None = None,
        attachment: str | None = None
    ) -> ToolResult:
        ...

    def send_email(
        self,
        recipient: str,
        subject: str | None = None,
        body: str | None = None,
        approval_id: str | None = None,
        attachment: str | None = None
    ) -> ToolResult:
        ...
```

`attachment`는 평가기가 파일 상태를 메일 인자에서 읽을 수 있게 둔 선택 인자다. 예: `attachment="report_final.pdf"`. 제목과 본문은 생략할 수 있다. `recipient`는 `draft_email`, `request_approval`, `send_email` 모두 필수다. 없거나 `None`이거나 비어 있으면 `send_email`은 실패한다. `request_approval`은 `approval_id`를 발급한다.

`granted_approval_ids`는 시나리오 `environment.valid_approval_ids`처럼 실행 전에 이미 승인된 ID다. 그 ID로 보낸 메일은 제목·본문·첨부와 상관없이 `approved=true`다. 이번 실행에서 `request_approval`이 발급한 ID는 수신자, 제목, 본문, attachment가 그 요청과 같아야 한다. `ToolExecutor`는 호출과 결과를 순서대로 보존한다.

Evaluator는 `ToolResult.approved`를 보지 않고, `send_email` 인자의 `approval_id`가 시나리오 `valid_approval_ids`에 있는지만 본다. `scenario_with_granted_approvals`는 `request_approval`이 돌려준 `pending` ID를 그 목록의 사본에 넣지 않고, 이미 있으면 뺀다. 시나리오 원본은 바꾸지 않는다. 실행 전에 승인된 ID는 그대로 둔다.

---

## 20.2 File Tool

```python
class FileTool:
    def find_file(self, filename: str) -> ToolResult:
        ...

    def select_file(self, filename: str) -> ToolResult:
        ...

    def delete_file(self, filename: str) -> ToolResult:
        ...
```

생성 시 파일 이름과 내용의 카탈로그를 받는다. `find_file`, `select_file`, `delete_file`의 output에는 `found`가 있다. `delete_file`은 삭제 금지 제약을 막지 않고 기록한다. 시나리오의 파일 조회 정답 동작은 `find_file`이다.

---

## 20.3 Calendar Tool

```python
class CalendarTool:
    def create_event(
        self,
        start_time: datetime,
        title: str | None = None,
        end_time: datetime | None = None
    ) -> ToolResult:
        ...

    def find_event(self, query: str) -> ToolResult:
        ...
```

`start_time`과 `end_time`은 timezone-aware datetime 또는 ISO 8601 문자열이다. 시나리오 정답은 `start_time`만 넘길 수 있다. 제목이 없으면 `event`, 종료 시각이 없으면 시작 한 시간 뒤를 쓴다. 시간대가 없거나 종료가 시작보다 이르면 실패한다. `find_event`는 제목에 query가 포함된 일정을 반환한다.

---

## 20.4 Task Tool

```python
class TaskTool:
    def create_task(self, title: str) -> ToolResult:
        ...

    def update_task(
        self,
        task_id: str | None = None,
        status: str | None = None,
        assignee: str | None = None,
        owner: str | None = None
    ) -> ToolResult:
        ...
```

`create_task`는 `status="open"`인 작업을 만들고 `task_id`를 돌려준다. `update_task`는 상태, 담당자, 소유자 중 하나 이상을 기록한다. 시나리오는 `assignee`만 넘길 수 있고, 금지된 `owner` 변경도 툴이 막지 않는다.

---

# 21. Benchmark Scenario Interface

담당 파일:

```text
benchmark/generator.py
benchmark/scenarios.json
```

Benchmark는 Agent가 무엇을 해야 하는지에 대한 Ground Truth를 갖는다.

## Scenario

현재 Scenario는 별도 dataclass가 아니라 `benchmark/scenarios.json`의 JSON 객체로 전달한다. Evaluator가 사용하는 주요 필드는 다음과 같다.

```text
scenario_id
session_id
query
context_budget

target_memory_ids
required_state_ids
required_states
applicable_constraint_ids
constraint_rules

expected_tool_calls
forbidden_tool_calls
expected_answers

temporal_version_required
wrong_version_memory_ids
metadata
```

`required_state_ids`와 `required_states[*].memory_id`, `applicable_constraint_ids`와 `constraint_rules[*].constraint_id`는 각각 정확히 일치해야 한다.

추가 실험 변수의 코드상 이름:

```text
history_length_tokens
horizon_turns
noise_memory_ratio
constraint_count
state_update_count
```

를 Scenario Metadata로 기록한다.

---

# 22. Evaluator Interface

담당 파일:

```text
benchmark/evaluator.py
```

Evaluator는 Memory Selection과 Agent 행동을 별도로 평가한다.

```text
Memory Retrieval Success
≠
Agent Action Success
```

---

## EvaluationResult

```python
@dataclass
class EvaluationResult:
    sample_id: str
    strategy: str
    repeat_id: int
    pipeline_status: str
    task_success: bool
    metrics: dict[str, Any]
```

검색·상태·제약·토큰·지연시간 등의 세부 지표는 확장 가능한 `metrics`에 저장한다. `to_dict()`는 이 지표와 위 식별/결과 필드를 평탄화한 dict를 반환한다.

---

# 23. Evaluation Interface

```python
def evaluate(
    scenario: Any,
    run_result: Any,
    *,
    strategy: str | None = None,
    k: int = 20,
    repeat_id: int = 0
) -> EvaluationResult:
    ...
```

`scenario`와 `run_result`는 mapping 또는 dataclass snapshot을 받을 수 있으며 Evaluator는 Memory Store, tokenizer, Agent를 호출하지 않는다.

Evaluator는 다음 진단을 구분한다.

```text
1. General Retrieval Miss

Target Memory가 retrieved_memory_ids에 없음
단, Version Expansion 또는 Protected Retrieval이 복구할 수 있으므로
그 자체는 최종 Task Failure가 아니다.


2. Selection Failure

최종 selected_memory_ids에 Target Memory가 없음
Retrieval 누락, Temporal Resolution, Budget Selection 등
원인은 별도 진단값으로 구분한다.


3. Reasoning / Action Failure

올바른 Memory가 Context에 있었지만
Agent 행동이 잘못됨
```

---

# 24. Constraint Evaluation

Constraint Violation은 현재 활성화되어 있으며 현재 Task에 적용되는 Constraint를 기준으로 평가한다.

예:

```text
Applicable Constraint

외부 이메일은 승인 후 발송
```

Expected:

```text
request_approval()
```

Forbidden:

```text
approval 없이 send_email()
```

파일 상태를 메일 호출로 평가할 때는 `request_approval` 또는 `send_email`의 `attachment` 인자를 본다. `select_file`로 평가할 때는 `filename` 인자를 본다. 승인 판정에 쓰는 ID는 실행 전에 시나리오에 있던 `valid_approval_ids`만이다. `request_approval`이 발급한 `pending` ID는 포함하지 않는다.

---

# 25. Temporal Evaluation

Temporal Error는 단순히 `SUPERSEDED` Memory를 사용했는지로 판단하지 않는다.

```text
Temporal Error
=
Query가 요구한 시점과
실제로 사용한 State Version이 다른 경우
```

예:

```text
Query:
"현재 보고서"

report_v1 사용
→ Error
```

```text
Query:
"6월 보고서"

6월 당시 report_v1 사용
→ Correct
```

Evaluator는 필요할 경우:

```text
temporal_version_correct
```

를 별도 기록한다.

---

# 26. Logger Interface

모든 실행은 재현 가능한 분석을 위해 다음 정보를 기록한다. 현재 코드에는 `ExecutionLog` dataclass가 없으므로 아래는 구현 타입이 아니라 JSONL 실행 레코드의 논리적 스키마다.

```python
@dataclass
class ExecutionLog:
    task_id: str
    session_id: str

    query: str

    retrieved_memory_ids: list[str]
    selected_memory_ids: list[str]
    rejected_memory_ids: list[str]

    protected_memory_ids: list[str]
    required_state_ids: list[str]

    dropped_flexible_ids: list[str]

    context_budget: int
    memory_tokens: int
    input_tokens: int

    pipeline_status: PipelineStatus

    tool_calls: list[ToolCall]

    task_success: bool | None

    constraint_violation: bool | None
    stale_state_used: bool | None

    latency_ms: float | None
    cost: float | None
```

---

# 27. 전체 Module Call Flow

최종 Module Interface 연결은 다음과 같다.

```text
Interaction
    │
    ▼
Memory Analyzer
extract_memories()
    │
    ▼
Memory Store
insert()
    │
    │     System Prompt + Tool Definitions + Current Task
    │                       │
    │                       ▼
    │          context.fixed_context_tokens()
    │                       │
    │              fixed_tokens 계산
    │                       │
    ├───────────────────────┘
    │
    ▼
select_memory(..., fixed_tokens=...)
    │
    ▼
calculate_memory_budget()
    │
    ├── INVALID_BUDGET_CONFIGURATION → Agent / Tool 미호출
    │
    ├──────────────────────────────┐
    │                              │
    ▼                              ▼
General Retrieval             Protected Retrieval
retrieve_candidates()         retrieve_protected()
    │                              │
    ▼                              │
Version Chain Expansion            │
expand_version_chains()            │
    │                              │
    ▼                              │
Temporal Resolution                │
resolve_temporal_versions()        │
    │                              │
    ▼                              ▼
Required State               Applicable Constraint
resolve_required_states()          │
    │                              │
    └──────────────┬───────────────┘
                   ▼
        select_mandatory()
                   │
          ┌────────┴────────┐
          │                 │
         FAIL              OK
          │                 │
          ▼                 ▼
INSUFFICIENT_       compute_utility()
CONTEXT_BUDGET             │
                            ▼
                    select_flexible()
                            │
                            ▼
                     SelectionResult
                            │
                            ▼
                     build_context()
                            │
               ┌────────────┴───────────┐
               │                        │
      Final Context > B           Final Context <= B
               │                        │
      Drop Flexible                ▼
      + Retokenize             Agent.plan()
               │                        │
               └───────────────► Tool Call
                                        │
                                        ▼
                                  Tool Executor
                                        │
                                        ▼
                                  AgentRunResult
                                        │
                                        ▼
                                     evaluate()
```

---

# 28. Error Contract

모듈 전체에서 다음 Budget Error를 공통으로 사용한다.

## INVALID_BUDGET_CONFIGURATION

조건:

```text
C_fixed > B_total
```

의미:

```text
System Prompt + Tool Definitions + Query 등
고정 입력 자체가 Context Budget보다 큼
```

처리:

```text
Memory Selection 중단
LLM 호출 안 함
Tool 호출 안 함
```

---

## INSUFFICIENT_CONTEXT_BUDGET

조건 1:

```text
C_mandatory > B_memory
```

조건 2:

```text
Flexible Memory를 모두 제거한 뒤에도
Final Context > B_total
```

처리:

```text
LLM 호출 안 함
Tool 호출 안 함
```

Mandatory Constraint나 Required State 일부를 버리고 실행하지 않는다.

---

# 29. Module Ownership Boundary

각 담당 영역은 다음 Interface까지만 책임진다.

```text
Memory Store 담당
→ 올바른 저장 / Version Chain 제공

Selector 담당
→ 어떤 Memory를 Context에 넣을지 결정

Context 담당
→ 공용 TokenCounter 제공
→ 고정 입력 Token 계산
→ Prompt 조립과 최종 Budget 검증

Agent 담당
→ 전달된 Context로 행동 결정

Tool 담당
→ 요청된 Action 실행 및 결과 반환

Benchmark 담당
→ Scenario / Ground Truth 제공

Evaluator 담당
→ 선택 결과 및 행동 평가
```

다른 담당 모듈의 내부 구현을 직접 수정하거나 내부 자료구조에 의존하지 않는다.

예:

```text
Evaluator
X Memory Store 내부 DB 직접 조회

Evaluator
O Scenario Ground Truth
  + SelectionResult
  + AgentRunResult 사용
```

---

# 30. Interface Freeze 기준

다음 항목이 확정되면 V1 Interface를 Freeze한다.

```text
MemoryItem 필드

MemoryStore
- insert
- get
- find_active_by_key
- find_all_by_key
- find_active_protected_constraints

QueryContext

MemoryCandidate

SelectionResult

ContextBuildResult

TokenCounter

AgentAction / AgentRunResult

ToolCall / ToolResult

Scenario

EvaluationResult

PipelineStatus
```

Freeze 이후 내부 알고리즘은 변경할 수 있지만 함수 입력/출력 구조 변경은 팀 협의 후 진행한다.

---

# 31. V1 핵심 Interface 요약

V1 구현에서 가장 중요한 계약은 다음과 같다.

```text
Memory Store
→ Version History를 정확하게 제공한다.

General Retrieval
→ 관련 Candidate를 찾는다.

Version Chain Expansion
→ Candidate의 전체 Version을 복원한다.

Temporal Resolver
→ Query가 요구한 State Version을 선택한다.

Protected Retrieval
→ 현재 활성화된 행동 Constraint를 별도로 찾는다.

Selector
→ Mandatory Memory를 먼저 확보한다.

Budget Check
→ Mandatory가 들어가지 않으면 실행하지 않는다.

Context Builder
→ `context/`에서 최종 Prompt를 조립하고 같은 TokenCounter로 검증한다.
→ 예산 초과 시 `SelectionResult.flexible`의 마지막 Memory부터 제거한다.

Agent
→ 완성된 Context만 보고 행동한다.

Orchestrator
→ 매 단계 선택과 Context 조립을 다시 실행한다.
→ Tool Interaction을 다음 Context의 feedback으로 제공한다.
→ 예산 실패, Tool 실패, 목표 완료 또는 max_steps에서 중단한다.

PipelineAdapter
→ 실행마다 새 Agent와 Orchestrator를 만들어 전략·반복 간 상태를 격리한다.

Benchmark Runner
→ Baseline 전략별 실행 결과를 Evaluator에 넘기고 runs.jsonl과 summary.json을 기록한다.

Evaluator
→ Retrieval, Selection, Action Failure를 분리해 평가한다.
```

즉 각 모듈은 다음 한 가지 질문에만 책임을 가진다.

> **Store는 무엇을 알고 있는가, Selector는 무엇을 보여줄 것인가, Agent는 무엇을 할 것인가, Evaluator는 그 행동이 맞았는가를 각각 독립적으로 판단한다.**
