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
memory/selector.py

agent/agent.py
agent/context.py
agent/interaction.py
agent/evaluation.py
agent/tools/email.py
agent/tools/file.py
agent/tools/calendar.py
agent/tools/task.py
agent/tools/executor.py

benchmark/generator.py
benchmark/evaluator.py
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

### 2.4 동일 Tokenizer 사용

다음 모듈은 동일한 Tokenizer를 사용한다.

```text
Memory Analyzer
Memory Selector
Context Builder
Evaluator
```

이를 통해 `token_count`와 실제 Context Token 사용량의 불일치를 최소화한다.

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

    importance: float = 0.5

    is_active: bool = True
    is_protected: bool = False

    memory_key: str | None = None

    created_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    supersedes: str | None = None
    superseded_by: str | None = None
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
@dataclass
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
@dataclass
class QueryContext:
    query: str

    temporal_intent: TemporalIntent

    target_time: datetime | None
    target_range: tuple[datetime, datetime] | None

    entities: list[str]
    required_tools: list[str]

    current_turn: int
    current_time: datetime
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

---

## 5.1 Insert

```python
def insert(
    memory: MemoryItem
) -> MemoryItem:
    ...
```

새 Memory를 저장한다.

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

---

## 5.2 Get by ID

```python
def get(
    memory_id: str
) -> MemoryItem | None:
    ...
```

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

권장 정렬:

```text
valid_from ASC
```

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

---

## 5.6 Session Memories

Retrieval index 구축 또는 테스트에서 사용할 수 있다.

```python
def list_session_memories(
    session_id: str
) -> list[MemoryItem]:
    ...
```

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

## Interface

```python
def retrieve_candidates(
    query_context: QueryContext,
    memory_store: MemoryStore,
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
    candidates: list[MemoryCandidate],
    memory_store: MemoryStore
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
    candidates: list[MemoryCandidate],
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
    memory_store: MemoryStore
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
    candidates: list[MemoryCandidate],
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

## 13.1 BudgetConfig

```python
@dataclass
class BudgetConfig:
    total_budget: int

    fixed_tokens: int
    memory_budget: int
```

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

    memories: list[MemoryItem]

    used_tokens: int
    remaining_budget: int
```

```python
def select_mandatory(
    protected: list[MemoryItem],
    states: list[MemoryItem],
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
    candidates: list[MemoryCandidate],
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

어떤 방식을 사용할지는 Experiment Config에서 선택 가능하게 한다.

---

# 16. Final Memory Selection Interface

Memory Selection 전체를 외부 모듈에서 호출할 때 사용하는 상위 Interface이다.

## SelectionResult

```python
@dataclass
class SelectionResult:
    status: PipelineStatus

    protected: list[MemoryItem]
    states: list[MemoryItem]
    flexible: list[MemoryItem]

    retrieved_memory_ids: list[str]
    selected_memory_ids: list[str]
    rejected_memory_ids: list[str]

    memory_tokens: int
    remaining_budget: int
```

## Interface

```python
def select_memory(
    query: str,
    session_id: str,
    memory_store: MemoryStore,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    current_time: datetime
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

## Interface

```python
def build_context(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    selection: SelectionResult,
    total_budget: int,
    token_counter: Callable[[str], int] | None = None
) -> ContextBuildResult:
    ...

def fixed_context_tokens(
    system_prompt: str,
    tool_definitions: str,
    query: str,
    token_counter: Callable[[str], int] | None = None
) -> int:
    ...
```

`fixed_context_tokens`는 기억이 없는 프롬프트의 토큰 수다. `[SYSTEM]`, `[TOOL DEFINITIONS]`, `[CURRENT TASK]` 제목을 포함한다. `run_task`는 이 값을 `select_memory`의 `fixed_tokens`로 넘긴다.

`token_counter`는 Memory Analyzer, Selector, Evaluator와 같은 토크나이저를 넣는 자리다. 공용 토크나이저가 정해지기 전에는 공백 기준 단어 수를 세는 `provisional_token_count`를 쓴다. 고정 프롬프트, 섹션, 최종 입력, `usage`는 그 실행에 넣은 같은 함수로 센다. 기본 단어 수는 모델 토크나이저 측정값이 아니므로, 공용 토크나이저를 넣기 전에는 실험 지표로 보고하지 않는다.

프롬프트 섹션 순서는 다음과 같다.

```text
[SYSTEM]
[TOOL DEFINITIONS]
[PROTECTED CONSTRAINTS]
[STATE]
[RELEVANT MEMORY]
[CURRENT TASK]
```

상태 섹션 제목은 `[STATE]`다. 과거 버전을 현재 상태라고 표시하지 않기 위해서다. 빈 Protected, State, Flexible 섹션은 프롬프트에 넣지 않는다. Flexible Memory는 선택 순서의 뒤를 낮은 우선순위로 보고, 예산 초과 시 뒤에서부터 제거한다.

---

## D-04 Final Token Validation

Formatting 이후 실제 Token 수를 다시 계산한다.

```text
Final Context <= B_total
→ 정상 반환
```

초과하면:

```text
가장 낮은 priority의
Flexible Memory 제거
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

담당 파일:

```text
agent/agent.py
agent/interaction.py
agent/evaluation.py
```

Agent는 Memory 내부 구현에 직접 접근하지 않는다.

Agent가 받는 것은 최종 Context이다. 툴 결과는 `Interaction(source="tool")`로 바꾸지만, 저장소에 넣지는 않는다.

---

## AgentAction

```python
@dataclass
class AgentAction:
    tool_name: str | None
    action: str | None
    arguments: dict

    text_response: str | None
```

`action`은 ToolCall의 action과 같다. 텍스트만 반환할 때는 `tool_name`과 `action`이 모두 None이다.

---

## Agent Interface

V1 Agent는 저장소, 프롬프트, Tool Executor, planner, goal_checker를 가진 객체다. LLM은 `planner`로 나중에 연결한다. planner가 없으면 `plan`은 `NotImplementedError`를 낸다.

시계를 넘기지 않으면 `2026-10-05T12:00:00+09:00`을 쓴다.

```python
class Agent:
    def plan(self, context: str) -> AgentAction:
        ...

    def run_task(
        self,
        task: str,
        session_id: str,
        context_budget: int
    ) -> AgentRunResult:
        ...

    def act(
        self,
        context: str,
        *,
        context_budget: int,
        session_id: str
    ) -> ActResult:
        ...
```

`run_task`는 `fixed_context_tokens`로 고정 비용을 계산하고 `select_memory`와 `build_context`를 호출한 뒤 `act`로 이어진다. `act`는 완성된 Context만 받아 `plan`과 Tool Executor를 최대 `max_steps`번 반복한다. 각 툴 결과는 다음 Context 끝에 `[TOOL RESULT]`로 붙인다. 그 줄에는 발급된 경우 `approval_id=`도 포함한다.

`plan`에 넘기기 전에 프롬프트 토큰 수를 `context_budget`과 비교한다. 넘으면 그 `plan`과 이후 툴 호출은 하지 않고 `stopped_reason="context_budget"`으로 멈춘다. `tool_name`과 `action`이 모두 None이면 텍스트 응답으로 끝내고 `stopped_reason="completed"`다. 둘 중 하나만 있거나 툴이 실패하면 `tool_failure`다. 반복 한도에 닿으면 `max_steps`다.

`goal_completed`는 `stopped_reason`이 `completed`이고 생성 시 넣은 `goal_checker`가 참일 때만 True다. checker가 없거나, 예산 초과·툴 실패·반복 한도로 멈추면 False다. 툴을 호출했다는 사실만으로 작업 성공이 되지 않는다.

기억 선택이 아직 연결되지 않은 데모는 `act`만 호출할 수 있다.

---

## ActResult

```python
@dataclass
class ActResult:
    response: str | None
    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]
    stopped_reason: str
    input_tokens: int
    output_tokens: int
    interactions: tuple[Interaction, ...]
```

`input_tokens`는 `plan`에 실제로 넘긴 프롬프트 중 가장 큰 값이다. 예산을 넘어 한 번도 계획하지 않았으면 0이다.

---

## AgentRunResult

```python
@dataclass
class TokenUsage:
    fixed_input_tokens: int
    input_tokens: int | None
    output_tokens: int

@dataclass
class AgentRunResult:
    status: PipelineStatus
    response: str | None

    tool_calls: tuple[ToolCall, ...]
    tool_results: tuple[ToolResult, ...]

    selection_result: SelectionResult | None
    context_result: ContextBuildResult | None

    goal_completed: bool
    usage: TokenUsage
    stopped_reason: str
    interactions: tuple[Interaction, ...]
```

`usage.input_tokens`는 모델 호출 전에 파이프라인이 끝나면 None이다. `usage_dict`가 평가기의 `usage` 키로 바꾼다.

`run_task`의 입력은 위의 `Agent.run_task`와 같다. 저장소, 시스템 프롬프트, 툴 정의, planner, goal_checker는 Agent 생성 시 받는다.

모델 호출 전에 예산으로 중단되면 `stopped_reason`은 `invalid_budget` 또는 `insufficient_context`다. 이때 `goal_completed`는 False이고 툴을 호출하지 않는다.

---

## 실행 실패

Memory Pipeline이:

```text
INVALID_BUDGET_CONFIGURATION
```

또는:

```text
INSUFFICIENT_CONTEXT_BUDGET
```

을 반환하면 LLM Agent와 Tool Executor를 호출하지 않는다.

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

`attachment`는 평가기가 파일 상태를 메일 인자에서 읽을 수 있게 둔 선택 인자다. 예: `attachment="report_final.pdf"`. 시나리오의 기대 호출은 `recipient`와 `attachment`만 가질 수 있으므로 제목과 본문은 생략할 수 있다. `request_approval`은 `approval_id`를 발급한다.

`granted_approval_ids`는 시나리오 `environment.valid_approval_ids`처럼 실행 전에 이미 승인된 ID다. 그 ID로 보낸 메일은 제목·본문·첨부와 상관없이 `approved=true`다. 이번 실행에서 `request_approval`이 발급한 ID는 수신자, 제목, 본문, attachment가 그 요청과 같아야 한다. `ToolExecutor`는 호출과 결과를 순서대로 보존한다.

Evaluator는 `ToolResult.approved`를 보지 않고, `send_email` 인자의 `approval_id`가 시나리오 `valid_approval_ids`에 있는지만 본다. `scenario_with_granted_approvals`는 성공한 `request_approval`이 발급한 ID만 그 목록의 사본에 더한다. 시나리오 원본은 바꾸지 않는다. 실패한 요청이나 다른 ID는 넣지 않는다.

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

```python
@dataclass
class Scenario:
    scenario_id: str
    session_id: str

    interactions: list[Interaction]

    final_query: str

    target_memory_ids: list[str]

    required_state_ids: list[str]

    applicable_constraint_ids: list[str]

    expected_tool_calls: list[dict]
    forbidden_tool_calls: list[dict]

    temporal_intent: TemporalIntent

    target_time: datetime | None

    context_budget: int
```

필요한 경우 추가 실험 변수:

```text
history_length
horizon
noise_ratio
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

    task_success: bool

    retrieved_memory_ids: list[str]
    selected_memory_ids: list[str]
    target_memory_ids: list[str]

    retrieval_hit: bool

    applicable_protected_recall: float

    current_state_accuracy: float
    temporal_version_correct: bool | None

    constraint_violation: bool
    stale_state_used: bool

    state_omission_count: int
    state_error_count: int

    input_tokens: int
    output_tokens: int

    planning_latency_ms: float | None
    total_latency_ms: float | None

    total_llm_cost: float | None

    pipeline_status: PipelineStatus
```

---

# 23. Evaluation Interface

```python
def evaluate(
    scenario: Scenario,
    run_result: AgentRunResult
) -> EvaluationResult:
    ...
```

Evaluator는 다음 세 단계를 구분한다.

```text
1. Retrieval Failure

Target Memory가 retrieved_memory_ids에 없음


2. Selection Failure

Target Memory가 retrieval에는 있었지만
selected_memory_ids에 없음


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

파일 상태를 메일 호출로 평가할 때는 `request_approval` 또는 `send_email`의 `attachment` 인자를 본다. `select_file`로 평가할 때는 `filename` 인자를 본다. 승인 판정에 쓰는 ID는 20.1의 `scenario_with_granted_approvals`로 실행 중 발급된 값만 채운다.

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

모든 실행은 재현 가능한 분석을 위해 다음 정보를 기록한다.

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
             Budget Check
                   │
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
→ 최종 Prompt를 실제 Tokenizer로 검증한다.

Agent
→ 완성된 Context만 보고 행동한다.

Evaluator
→ Retrieval, Selection, Action Failure를 분리해 평가한다.
```

즉 각 모듈은 다음 한 가지 질문에만 책임을 가진다.

> **Store는 무엇을 알고 있는가, Selector는 무엇을 보여줄 것인가, Agent는 무엇을 할 것인가, Evaluator는 그 행동이 맞았는가를 각각 독립적으로 판단한다.**