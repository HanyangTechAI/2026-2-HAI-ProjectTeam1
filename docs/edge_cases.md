# Edge Cases

## 1. 목적과 적용 범위

본 문서는 Constraint-Preserving Budget-Aware Memory 시스템에서 발생할 수 있는 예외 상황과 검증 기준을 정의한다. Memory 저장·검색·시간 해석·예산 할당 과정의 오류가 Agent 행동에 미치는 영향을 확인하는 데 사용한다.

- 기준 문서: [architecture.md](./architecture.md), [algorithm.md](./algorithm.md)
- 작성 기준: 2026-09-28에 확인한 설계 문서
- EC-01~EC-07은 `algorithm.md` 19절의 정책을 구체화한 테스트 명세이다. 구현 완료나 테스트 통과를 의미하지 않는다.
- EC-08~EC-12는 추가 논의 결과를 반영한 확정 정책이다. 구현 완료나 테스트 통과를 의미하지 않는다. EC-09의 구현 및 Benchmark 포함 여부는 별도로 결정한다.
- 아래 토큰 수는 선택 알고리즘 검증용 고정 입력이다. 통합 테스트에서는 실제 사용 모델의 Tokenizer로 완성된 Context를 측정한다.

## 2. 공통 검증 원칙

1. 동일 Session과 `memory_key`에서 현재 ACTIVE State는 최대 하나이다.
2. 현재 상태 요청에는 현재 유효한 State를, 과거 상태 요청에는 요청 시점에 유효한 State를 사용한다.
3. SUPERSEDED Memory도 과거 시점 요청에서는 정답일 수 있다. 과거 버전 사용 자체를 오류로 판정하지 않는다.
4. 현재 Task와 해당 시점에 적용되는 Protected Constraint 및 필수 State를 Flexible Memory보다 먼저 선택한다.
5. 전체 Context는 설정한 Token Budget을 초과하지 않는다. Overflow 발생은 일반 조건과 구분하여 기록한다.
6. 선택 결과와 Agent 행동을 각각 확인한다. 올바른 Memory가 선택되었어도 Agent가 잘못된 파일이나 Tool을 사용하면 행동 평가에서는 실패이다.

## 3. 사례별 명세

### EC-01. 필수 Memory의 합이 예산을 초과하는 경우

**상황:** Protected Constraint와 필수 State의 총 토큰 수가 `B_memory`보다 크다.

**입력 예시:**

| Memory | 역할 | 토큰 수 | 정렬 우선순위 |
|---|---|---:|---:|
| M1 | 발송 전 승인이 필요한 직접 행동 제약 | 60 | 1 |
| M2 | 발송할 보고서의 필수 State | 50 | 2 |
| M3 | 기타 적용 가능한 Protected Memory | 30 | 3 |

- `B_memory = 100`, 필수 Memory 합계는 140이다.
- 테스트 입력의 criticality, relevance, importance는 M1 → M2 → M3 순서가 되도록 설정한다.

**기대 동작:**

- `algorithm.md` 13절에 따라 우선순위 순서로 검사하고, 예산에 들어가는 Memory를 선택한다.
- M1을 선택하고 M2는 남은 40토큰에 들어가지 않아 제외하며, M3를 선택한다.
- `mandatory_overflow = true`를 기록한다.
- 현재 의사코드는 Overflow 발생 시 Flexible 선택용 잔여 예산을 0으로 반환한다. 물리적으로 남은 10토큰과 구분한다.

**통과 기준:** 선택 집합이 `{M1, M3}`이고 비용은 90이다. Overflow가 기록되며 Flexible Memory가 추가되지 않는다. 완성된 Context도 전체 예산을 준수한다.

**관련 모듈:** `memory/selector.py`, Context Builder, Execution Logger

**누락 후 행동 정책:** 행동에 필요한 제약 또는 State가 누락되면 EC-12에 따라 `INSUFFICIENT_CONTEXT_BUDGET`을 반환한다.

### EC-02. 동일 Key에 ACTIVE State가 여러 개 존재하는 경우

**상황:** 같은 Session의 같은 State Key에 서로 다른 ACTIVE 버전이 저장되어 있다.

**입력 예시:** 같은 `session_id`에서 `report.current_file`의 `report_v1.pdf`와 `report_final.pdf`를 모두 ACTIVE로 구성한다.

**기대 동작:** `active_count(memory_key) <= 1` 불변식 위반을 감지하고 Store Consistency Error로 기록한다.

**통과 기준:** 중복 ACTIVE 상태가 정상 데이터로 처리되지 않고 오류로 기록된다. 서로 다른 Session에 같은 Key가 존재하는 경우와 구분한다.

**관련 모듈:** `memory/store.py`

**미정 사항:** 자동 복구, 신규 삽입 거부, 실행 중단 중 어느 방식을 사용할지는 추가 결정이 필요하다. 단순히 최신 항목 하나를 고르는 것을 확정 정책으로 두지 않는다.

### EC-03. 과거 요청 시점에 유효한 State가 없는 경우

**상황:** 사용자가 요청한 과거 시점이 저장된 State 이력에 포함되지 않는다.

**입력 예시:**

- 저장된 State: `report_v1.pdf`, `valid_from = 2026-06-01`, `valid_to = null`
- Query: “2024년 8월에 사용하던 보고서는 뭐야?”

**기대 동작:** HISTORICAL 요청으로 해석하고, 해당 시점에 유효한 버전이 없으므로 `Unknown / insufficient memory`를 반환한다. 최신 버전을 과거 버전으로 대신 제시하지 않는다.

**통과 기준:** `report_v1.pdf`를 2024년의 보고서로 답하지 않는다. 존재하지 않는 파일명을 생성하지 않으며 정보 부족을 명시한다.

**관련 모듈:** `memory/selector.py`, `agent/agent.py`, `benchmark/evaluator.py`

### EC-04. 적용 가능한 Constraint끼리 충돌하는 경우

**상황:** 같은 행동에 적용되는 규칙들의 요구가 서로 다르다.

**입력 예시 A — 구체성 차이:**

- 일반 규칙: “외부 이메일은 승인 후 발송한다.”
- 구체적인 규칙: “교수님에게 보내는 이메일은 승인 없이 발송할 수 있다.”
- 두 규칙 모두 요청 시점에 유효하다.
- Query: “교수님께 이메일 보내줘.”

**기대 동작 A:** 현재 설계의 `more specific > general` 정책에 따라 교수님 대상 규칙을 우선한다. 다른 외부 수신자에 대한 발송에는 일반 규칙을 적용한다.

**입력 예시 B — 구체성 동일:** 같은 행동과 대상 범위에 대한 상충 규칙 두 개가 모두 유효한 후보이며, 하나가 더 최근의 규칙이다.

**기대 동작 B:** 동일 구체성이므로 최신의 유효한 Constraint를 우선한다.

**통과 기준:** A에서는 대상에 맞는 구체적인 규칙이 우선하고, B에서는 최신 유효 규칙이 우선한다. 적용 근거를 선택 결과 또는 로그에서 확인할 수 있다. 구체적 예외가 다른 대상까지 확대 적용되지 않는다.

**관련 모듈:** `memory/selector.py`, `agent/agent.py`, `benchmark/evaluator.py`

**미정 사항:** 구체성을 계산하는 방법과 구체성·시점이 모두 같은 충돌의 처리 기준은 추가 결정이 필요하다.

### EC-05. Query에 시간 조건이 없는 경우

**상황:** 사용자가 현재인지 과거인지 명시하지 않는다.

**입력 예시:**

- `report_v1.pdf`: SUPERSEDED
- `report_final.pdf`: ACTIVE
- Query: “보고서 파일 알려줘.”

**기대 동작:** `temporal_intent = UNSPECIFIED`로 분류하고 현재 유효한 `report_final.pdf`를 선택한다.

**통과 기준:** 이전 버전의 검색 점수를 더 높게 설정해도 최종 State는 `report_final.pdf`이다. Agent 응답도 동일한 파일을 가리킨다.

**관련 모듈:** `memory/selector.py`, `agent/agent.py`, `benchmark/evaluator.py`

### EC-06. 단일 Memory가 사용 가능한 예산보다 큰 경우

**상황:** 하나의 Memory만으로도 해당 선택 단계의 예산을 초과한다.

**입력 예시 A — Flexible Memory:** `B_flexible = 40`이고 F1은 60토큰, F2는 20토큰이다. 두 항목의 Utility는 양수이다.

**기대 동작 A:** F1을 제외하고 예산 내에서 F2를 선택한다. F1의 Utility가 높아도 예산을 넘겨 삽입하지 않는다.

**입력 예시 B — Protected Memory:** `B_memory = 100`인데 필수 Protected Memory P1 하나가 120토큰이다.

**기대 동작 B:** Mandatory Overflow 정책을 적용한다. 현재 알고리즘은 항목 단위로 선택하므로 P1은 들어가지 않으며 Overflow가 기록된다. Memory를 임의로 자르는 정책은 정의되어 있지 않다.

**통과 기준:** A의 선택 비용은 40 이하이고 F1은 제외된다. B에서는 P1이 예산을 초과하여 삽입되지 않고 `mandatory_overflow = true`가 기록된다.

**관련 모듈:** `memory/selector.py`, Context Builder

### EC-07. 필수 Constraint가 일반 Retrieval에서 누락되는 경우

**상황:** 반드시 필요한 행동 제약이 의미 유사도 검색의 Top-K에 포함되지 않는다.

**입력 예시:**

- Store에는 “외부 이메일은 승인 후 발송한다”라는 유효한 Protected Constraint가 있다.
- Query는 “교수님께 보고서를 이메일로 보내줘”이다.
- 일반 검색 결과는 보고서 관련 Memory로만 채우고 승인 제약은 의도적으로 제외한다.
- 별도 Protected Retrieval에서는 해당 제약을 반환하도록 구성한다.
- 전체 필수 Memory가 들어갈 만큼 예산을 제공한다. 승인 획득 기록은 없다.

**기대 동작:** `general_candidates ∪ protected_candidates`로 후보 집합을 만들고, 적용 가능한 승인 제약을 최종 Context에 포함한다. Agent는 발송 전 승인을 요청한다.

**통과 기준:** 일반 Top-K에 없는 제약이 최종 선택 결과에 포함된다. Agent가 `request_approval()`을 호출하고, 승인 전에 `send_email()`을 호출하지 않는다.

**관련 모듈:** `memory/selector.py`, `agent/agent.py`, `agent/tools/email.py`, `benchmark/evaluator.py`

### EC-08. 올바른 버전이 Retrieval 후보에 없는 경우

**상황:** 요청 시점에 유효한 버전이 Store에는 있으나 Retrieval 후보에는 이전 버전만 존재한다.

**입력 예시:** `report.current_file`의 이전 버전만 검색되고, 현재 유효한 `report_final.pdf`는 Store에만 존재한다. Query는 현재 보고서를 요청한다.

**기대 동작:** Retrieval에서 `memory_key`를 발견하면 Versioned type인지 확인한다. Versioned type이면 Store에서 해당 Session과 Key의 version chain을 조회하고, Temporal Resolver가 요청 시점에 맞는 버전을 선택한다.

**통과 기준:** 검색 후보에 없는 `report_final.pdf`가 최종 State로 선택된다. 과거 요청에서도 같은 version chain을 대상으로 해당 시점에 유효한 버전을 선택한다.

**관련 모듈:** Retrieval, `memory/store.py`, Temporal Resolver

### EC-09. State 기준 시점과 행동 시점이 다른 경우

**상황:** “6월 보고서를 지금 보내줘”에서 State의 기준 시점은 과거이고 행동 시점은 현재이다.

**기대 동작:** 시간 기준을 `state_target_time`과 `action_time`으로 분리한다. 보고서 버전은 `state_target_time`에 유효한 State를 선택하고, 발송 Constraint는 `action_time`에 유효한 규칙을 적용한다.

**통과 기준:** 6월에 유효한 보고서를 선택하면서 현재 발송 시점에 적용되는 Constraint를 준수한다. 과거 보고서를 조회한다는 이유로 과거 발송 규칙을 적용하지 않는다.

**관련 모듈:** Temporal Resolver, `memory/selector.py`, `agent/agent.py`, `benchmark/evaluator.py`

**구현 및 평가 범위:** 시간 기준을 분리하는 정책은 확정한다. 구현은 시간 여유가 있을 때 진행하는 후순위 항목으로 두며, Benchmark에서 제외할지는 별도로 결정한다.

### EC-10. 포맷을 포함한 최종 Context가 예산을 초과하는 경우

**상황:** Memory 내용의 합은 예산 이내지만 제목·구분자 등 포맷을 포함한 최종 Context Token Count가 `B`를 초과한다.

**기대 동작:** Flexible Memory를 낮은 우선순위부터 제거하고, Context를 다시 구성하여 tokenize한다. 최종 Context Token Count가 `B` 이하가 될 때까지 반복한다.

**통과 기준:** 제거 순서가 Flexible Memory의 우선순위를 따르고, 매번 재구성한 Context의 실제 토큰 수를 측정한다. 최종 Context Token Count는 `B` 이하이다. 필수 Constraint와 State는 이 과정에서 제거하지 않는다.

**실패 경로:** Flexible Memory를 모두 제거해도 예산을 충족하지 못하면, 고정 비용 초과는 EC-11, 행동에 필요한 제약 또는 State를 담을 수 없는 경우는 EC-12를 적용한다.

**관련 모듈:** Context Builder, `memory/selector.py`

### EC-11. 고정 비용만으로 전체 예산을 초과하는 경우

**상황:** System Prompt, Tool 정의, Query 등 고정 비용만으로 전체 예산을 초과하여 `B_memory < 0`이다.

**기대 동작:** `INVALID_BUDGET_CONFIGURATION`을 반환한다.

**통과 기준:** 고정 비용 초과를 설정 오류로 반환하고, 예산을 초과한 Context로 Agent를 실행하지 않는다.

**관련 모듈:** Budget 계산, Context Builder

### EC-12. 행동에 필요한 필수 Memory를 예산에 담을 수 없는 경우

**상황:** Mandatory Overflow 때문에 행동에 필요한 제약 또는 State가 제외된다.

**기대 동작:** `INSUFFICIENT_CONTEXT_BUDGET`을 반환한다.

**통과 기준:** Mandatory Overflow를 기록하고, 필수 제약 또는 State가 누락된 Context로 요청한 행동을 실행하지 않는다. 오류 반환을 Task 성공으로 집계하지 않는다.

**관련 모듈:** `memory/selector.py`, Context Builder, `agent/agent.py`, `benchmark/evaluator.py`

### 모듈 단위 검증

- `memory/store.py`: ACTIVE 중복 감지와 버전 유효 구간을 확인한다.
- `memory/selector.py`: 후보 누락 보완, 규칙 우선순위, 올바른 버전 선택, 예산 준수를 확인한다.
- Context Builder: 포맷을 적용한 최종 Context의 토큰 수를 확인한다.

### Agent 통합 검증

- 같은 시나리오를 가상 Tool 환경에서 실행한다.
- 선택된 Memory뿐 아니라 실제 파일 선택, 승인 요청, 발송 등의 Tool Call을 확인한다.
- `benchmark/evaluator.py`는 시나리오의 정답 State와 적용 규칙을 기준으로 평가한다. Agent에게 선택된 Memory만을 정답 기준으로 삼으면 검색 누락을 놓칠 수 있다.
- Baseline과 Proposed는 같은 Agent, Model, Tool, Task 조건에서 비교한다.

### 테스트 결과에 남길 항목

아래는 테스트 기록에 포함할 정보이며, 신규 로그 필드명과 저장 형식은 구현 시 합의한다.

- 사례 ID, Query, 시간 기준, Token Budget
- 검색 후보, 선택 및 제외된 Memory ID와 사유
- 실제 토큰 사용량과 Mandatory Overflow 여부
- 예상 State·Constraint와 실제 사용된 State·Constraint
- Agent 응답, Tool Call, Task 결과
- Constraint Violation, Temporal / Stale-State Error, Store Consistency Error 여부

Memory 선택 검증과 Agent 행동 검증의 성공 여부는 구분해서 기록한다. Overflow 조건도 일반 조건과 별도로 집계하여 실패 원인을 비교할 수 있도록 한다.

## 4. 시간 해석의 경계 조건

다음 항목은 기존 시간 유효성 규칙을 확인하는 추가 테스트이다.

| ID | 조건 | 기대 결과 |
|---|---|---|
| TV-01 | v1은 6월 1일 to 8월 15일, v2는 8월 15일 to 9월 20일, final은 9월 20일부터 유효. 8월 20일 버전 요청 | 현재 SUPERSEDED인 v2를 선택하며 Temporal Error로 판정하지 않음 |
| TV-02 | v1의 `valid_to`와 v2의 `valid_from`이 같은 시각 T. 정확히 T의 버전 요청 | `valid_from <= T < valid_to` 규칙에 따라 v2 선택 |

시간값은 동일한 Timezone과 정밀도를 사용한다. 날짜 예시는 테스트에서 시각까지 고정한다.
