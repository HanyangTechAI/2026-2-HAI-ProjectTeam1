# Constraint-Preserving Budget-Aware Memory

**Long-Horizon LLM Agent를 위한 제약 보존 및 Token Budget 기반 메모리 관리**

2026-2 HAI Project · Team 1

장기간 동작하는 LLM Agent가 제한된 Context 안에서도 반드시 지켜야 하는 제약과 현재 Task에 필요한 상태를 유지하도록 하는 Memory Architecture를 연구한다. Calendar, Email, File, Task 등의 가상 Tool을 사용하는 자체 Testbed를 구축하고, 메모리 선택이 실제 Agent의 행동과 작업 성공률에 미치는 영향을 평가한다.

> 현재는 연구 설계와 V1 파이프라인 검증 단계이다. 인메모리 Memory Store와 State Versioning, 세 Baseline, Context 조립·최종 Budget Validation, 결정적 규칙 Planner와 Agent, 다단계 Orchestrator, PipelineAdapter 기반 Benchmark Runner와 Evaluator가 구현되어 있다. 공백 기준 임시 Token Counter와 Gemini CountTokens 연동도 제공한다. Proposed Query Analyzer·검색·시간 해석·선택 알고리즘, 시나리오 생성기와 실제 LLM Planner 연동은 아직 미구현 또는 미연결 상태이다.

## 배경과 목표

긴 Interaction History 전체를 Context에 넣으면 Token 비용이 증가하고, 제한된 예산에서는 모든 정보를 유지하기 어렵다. 단순 검색이나 최근 기록 중심의 선택은 과거에 주어진 행동 제약을 누락하거나 이미 변경된 상태를 반환할 수 있다.

본 프로젝트의 핵심 질문은 **“모든 과거 정보를 기억할 수 없는 Agent는 무엇을 반드시 기억해야 올바른 행동을 유지할 수 있는가?”**이다.

- 동일한 Context Token Budget에서 높은 Task Success Rate 유지
- 중요한 제약과 Task가 요구한 시점의 상태를 보존하여 Constraint Violation 및 오래된 State 사용 감소
- Full Context 대비 Token 사용량과 비용의 관계 분석
- 실제 Tool 호출 결과를 통한 행동 수준의 메모리 평가

성능 개선 여부는 비교 실험으로 검증하며, 아직 실험 결과는 없다.

## 제안 메모리 구조

| 구성 요소 | 보존할 정보 | 관리 방식 |
| --- | --- | --- |
| **Protected Memory** | 외부 발송 전 승인, 수행 금지 작업, 예산·권한 등 행동 제약 | 일반 Memory Ranking보다 우선하여 보존 |
| **Required State** | Task가 요구하는 시점에 유효한 파일, 일정, 작업 상태 등 | Version-Aware State Resolution으로 현재·과거 버전 선택 |
| **Flexible Memory** | 과거 경험, 선호, 참고 정보 등 | 남은 예산에서 관련성·중요도·최신성·Token Cost를 고려하여 선택 |

예를 들어 보고서가 `report_v1.pdf`에서 `report_final.pdf`로 변경되면, 이전 파일은 `SUPERSEDED`, 최신 파일은 `ACTIVE`로 관리한다.

```mermaid
flowchart TD
    A[User / Tool Interaction] --> B[Memory Analyzer]
    B --> C[Protected Memory]
    B --> D[Required State]
    B --> E[Flexible Memory]
    C --> F[Budget-Aware Selector]
    D --> F
    E --> F
    F --> G[Context Builder]
    G --> H[LLM Agent]
    H --> I[Tool / Action]
    I --> A
```

### Budget-Aware Selection

전체 입력 Context Budget을 `B`, 공통 지시문·현재 요청·Tool 정의 등의 고정 비용을 `C_fixed`, Protected Memory와 Required State의 비용을 `C_P`라고 하면 Flexible Memory에 사용할 수 있는 예산은 다음과 같다.

```text
B_flex = B - C_fixed - C_P

maximize  Σ U_i × x_i
subject to Σ C_i × x_i ≤ B_flex,  x_i ∈ {0, 1}
```

필수 정보가 예산을 넘으면 `INSUFFICIENT_CONTEXT_BUDGET`으로 중단하고 LLM과 Tool을 호출하지 않는다.

`U_i`는 Memory 항목의 효용, `C_i`는 Token 비용, `x_i`는 선택 여부이다. 필수 정보를 먼저 배치하고 남은 예산에서 Flexible Memory의 효용을 최적화한다. 필수 정보 자체가 예산을 초과하는 조건은 별도로 기록하여 보존 가능 범위와 한계를 평가한다.

`context/builder.py`는 선택 결과를 실제 Prompt로 조립한 뒤 전체 입력을 다시 측정한다. Formatting overhead로 예산을 넘으면 우선순위가 가장 낮은 Flexible Memory부터 제거하고 재조립·재측정을 반복한다. 모든 Flexible Memory를 제거해도 초과하면 `INSUFFICIENT_CONTEXT_BUDGET`을 반환한다. `context/tokenizer.py`는 공용 `TokenCounter`와 반환값 검증을 담당한다.

## 연구 질문

1. **RQ1:** 동일한 Context Token Budget에서 Proposed가 기존 기억 방식보다 실제 Tool 행동을 포함한 작업 성공률(Action-Level TSR)을 높이는가?
2. **RQ2:** Budget이 감소하거나 중요한 정보와 Task 사이의 거리(Horizon)가 증가할 때 Proposed가 제약 위반과 잘못된 State 사용을 더 잘 억제하는가?

기준 문서는 [Research Questions](docs/research_questions.md)이다. 현재 Task에 필요한 State는 항상 최신 버전을 뜻하지 않는다. Historical Query에서는 요청 시점에 유효했던 과거 버전이 정답이다.

## 실험 계획

동일한 Agent와 Task 조건에서 Memory Policy를 변경하여 비교한다.

| 비교 방식 | Context 구성 |
| --- | --- |
| Full Context | 전체 Interaction History 제공 |
| Sliding Window | 최근 History 중심으로 제공 |
| Vector Top-k Memory | 벡터 유사도에 따라 검색한 Memory 제공 |
| Utility-per-Token Memory | Token 비용 대비 효용을 기준으로 Memory 선택 |
| Proposed | 제약과 Required State를 우선 보존하고 남은 예산을 효용 기반으로 할당 |

동일 예산 비교는 각 방식의 입력이 예산 내에 들어가도록 수행한다. 전체 History가 예산을 초과하는 Full Context 실행은 별도의 비용·성능 참고 기준으로 보고한다.

### 자체 Long-Horizon Agent Testbed

가상 Calendar, Email, File, Task Tool을 제공하고 다음 조건을 조절한다.

| 실험 | 조절 조건 | 확인할 내용 |
| --- | --- | --- |
| Token Budget Scaling | 1K / 2K / 4K / 8K 등 | 예산 감소에 따른 성능과 오류 변화 |
| State Change | 동일 정보의 변경 횟수 | 최신 상태 활용 능력 |
| Constraint Stress Test | 행동 제약의 수 | 제약 보존과 위반 여부 |
| Memory Noise | 관련 없는 과거 정보의 비율 | 정보 간섭에 대한 안정성 |
| Long-Horizon Test | History Length, 중요 정보와 최종 Task 사이의 거리 | 장기 기억 성능 |
| Ablation Study | Protected Memory, State Versioning, Budget Optimization 각각 제거 | 구성 요소별 기여도 |

History Length와 Token Budget의 교차 실험을 통해 두 조건의 복합 효과도 분석한다.

### 평가 지표

- **작업 성과:** Action-Level Task Success Rate
- **행동 신뢰성:** Constraint Violation Rate, Required-State Accuracy, 오래된 State 사용률
- **메모리 선택 품질:** Memory Retrieval Accuracy
- **효율:** Input Token Usage, Total LLM Cost, Planning Latency

작업 성공은 적용되는 제약 준수와 Task가 요구하는 시점·버전의 State 사용을 포함하여 평가한다. 반복 실행으로 결과의 불확실성을 확인하고 성능·비용·신뢰성의 관계를 분석한다.

## 데모 시나리오

아래는 데모에 구현된 예시이며, 연구 실험의 측정 결과는 아니다.

| 시점 | Interaction |
| --- | --- |
| Turn 3 | “외부 이메일은 보내기 전에 반드시 확인받아.” |
| Turn 15 | “보고서는 `report_v1.pdf`를 사용해.” |
| Turn 60 | “보고서를 `report_final.pdf`로 변경했어.” |
| Turn 120 | “지난 내용대로 보고서를 보내줘.” |

중간에 관련 없는 대화가 누적되어도 올바른 Agent는 최신 파일을 선택하고 외부 발송 전 승인을 요청해야 한다. Context Budget 2K 등의 조건에서 동일한 Task를 서로 다른 Memory Policy로 실행하고, 선택된 Memory·Token 사용량·Agent Action·제약 위반 여부를 비교하는 Dashboard를 구축할 계획이다.

## 저장소 구조

아래는 현재 디렉터리 구성과 각 영역의 역할이다.

```text
.
├── memory/              # Memory 스키마, 저장소, 선택 알고리즘
├── context/             # Token Counter, Prompt 조립, 최종 Budget Validation
├── agent/               # Agent 실행 및 가상 Tool
├── pipeline/            # 선택·Context·Agent 반복 실행과 adapter
├── benchmark/           # Baseline 실행, 시나리오 생성 자리와 행동 평가
├── experiments/
│   ├── configs/         # 실험 설정
│   └── results/         # 실험 결과
├── prototype/           # 데모 실행과 예시 출력
├── tests/               # Memory, State, 시나리오 테스트
├── docs/                # 연구 질문 및 설계 문서
├── requirements.txt     # Python 의존성 목록
└── README.md
```

결정적 Planner와 mock Tool 데모는 저장소 루트에서 다음으로 실행한다.

```text
python prototype/run_demo.py
```

데모는 결정적 fixture로 파일 선택, 승인 요청, 발송 및 예산 중단 정책을 재현한다. 시나리오 `memory_snapshot`과 합성 토큰 수를 사용하는 별도 데모 경로이며 실제 LLM 성능 측정은 아니다. `python prototype/run_demo.py S01`처럼 시나리오 ID를 지정하거나 `--all`, `--list`를 쓸 수 있다.

실행 파이프라인에서는 `memory/baselines.py`의 Sliding Window, Vector Top-k, Utility-per-Token을 공통 `SelectionResult`로 변환한다. `pipeline/orchestrator.py`는 선택, Context 재조립, Agent 행동, Tool 결과 feedback을 최대 단계 수 안에서 반복하며 예산 실패 시 Agent를 호출하지 않는다. `benchmark/runner.py`는 `PipelineAdapter`로 활성 integration 시나리오를 전략·반복별 실행하고 `runs.jsonl`과 `summary.json`을 생성한다. 단계별 선택·Context·행동, 최종 결과와 누적 입력 토큰도 실행 레코드에 보존한다.

`memory/store.py`는 프로세스 내부 인메모리 저장소를 제공한다. 동일 세션·`memory_key`의 새 ACTIVE 항목을 삽입하면 기존 ACTIVE 항목을 비활성화하고 유효 종료 시각과 양방향 대체 참조를 연결한다. ID·버전 참조·시간 순서·ACTIVE 유일성도 검증하지만, 파일이나 DB로 영속화하지는 않는다.

## 설치와 의존성

Python 3.10 이상이 필요하며 현재 개발 환경에서는 Python 3.12로 검증한다. 가상 환경 사용을 권장한다.

```text
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
```

프로젝트의 표준 설치 의존성으로 Gemini CountTokens 연동용 `google-genai>=2.0.0`을 유지한다. `pip install -r requirements.txt`는 이 패키지를 설치한다. 다만 기본 공백 기준 counter와 fake client 기반 단위 테스트는 Gemini API를 호출하지 않으므로 API key가 필요하지 않다. `unittest`는 표준 라이브러리이므로 별도 테스트 패키지가 필요하지 않다.

`google-genai` 외의 provider 의존성은 구현을 주입하는 쪽에서 결정한다.

| 기능 | 현재 계약 | 추가 의존성 |
| --- | --- | --- |
| Sliding Window | 내장 구현을 바로 사용 | 없음 |
| Vector Top-k | `embedder(text) -> Sequence[float]` 주입 | 선택한 embedding SDK 또는 로컬 모델 |
| Utility-per-Token | Vector Top-k와 같은 embedder 사용 | 선택한 embedding SDK 또는 로컬 모델 |
| Token 측정 | 기본 `provisional_token_count`, `GeminiTokenCounter` 또는 사용자 `TokenCounter` | `google-genai`는 표준 설치, 실제 Gemini API 호출 시 API key 필요 |
| Agent | 기본 결정적 규칙 `Planner` | 실제 LLM 연결 시 provider SDK |
| 저장소 | 프로세스 내부 `MemoryStore` | DB 영속화를 구현할 때 DB driver |

Gemini token 측정은 다음처럼 설정한다.

```text
$env:GEMINI_API_KEY="..."

python -m benchmark.runner ... \
  --gemini-token-model <model-name> \
  --gemini-token-cache-size 4096
```

`GeminiTokenCounter`는 동일 문자열의 CountTokens 결과를 메모리에 캐시한다. `--token-counter module:function`과 `--gemini-token-model`은 동시에 사용할 수 없다. 둘 다 생략하면 `provisional_token_count`를 사용하며, 이 값은 실제 모델 Token Usage로 보고하지 않는다. 실행 결과의 `token_count_mode`에는 `provisional`, `custom`, 또는 `gemini:<model>`이 기록된다.

Embedding과 실제 LLM provider는 아직 확정되지 않았다. 연구 실행 전에는 선택한 provider와 정확한 버전을 고정하고, 같은 embedder와 TokenCounter를 비교 전략 전체에 사용해야 한다. 개발 PC에 우연히 설치된 패키지를 프로젝트 의존성으로 간주하지 않는다.

현재 `unittest` 기반 테스트 전체는 다음 명령으로 실행한다.

```text
python -m unittest discover -s tests -v
```

`tests/test_memory.py`와 `tests/test_state.py`는 Store와 State Versioning을, `tests/test_memory_context_integration.py`는 Store와 Context Builder 경계를 검증한다. `tests/test_baseline_context_agent_integration.py`와 `tests/test_pipeline.py`는 Baseline부터 다단계 Agent 실행까지를 검증한다. `tests/test_pipeline_adapter.py`는 실행별 Agent 격리, Runner 저장·재평가와 중간 실행 후 예산 실패 기록을 검증하며, `tests/test_tokenizer.py`는 Gemini counter의 입력 검증·API 응답 처리·캐시 동작을 fake client로 검증한다.

## 기술 스택 후보

아래 항목은 설치된 의존성이 아니라 향후 실험·서비스 구현 후보이다. 실제 채택 전까지 `requirements.txt`의 필수 패키지로 간주하지 않는다.

| 영역 | 후보 |
| --- | --- |
| 언어 및 분석 | Python, Pandas, NumPy, Matplotlib |
| 모델 및 Agent | LLM API 또는 Open-Source LLM, LangGraph 또는 Custom Agent Framework |
| Memory 저장·검색 | Embedding Model, FAISS 또는 Chroma, SQLite 또는 PostgreSQL |
| API 및 Dashboard | FastAPI, Streamlit |

## 7주 개발 계획

| 주차 | 목표 |
| --- | --- |
| 1주차 | 선행연구, 연구 질문, Memory 구조 및 실험 설계 |
| 2주차 | Tool-Using Agent Testbed 및 Baseline 구현 |
| 3주차 | Constraint Extraction 및 State Versioning |
| 4주차 | Budget-Aware Memory Manager 통합 |
| 5주차 | 필수 RQ1/RQ2 Budget × Horizon 실험 |
| 6주차 | 실패 분석, 여유가 있으면 Ablation·stress, Dashboard Demo |
| 7주차 | 최종 재현 실험, 발표 자료 및 Demo 준비 |

## 관련 문서

- [프로젝트 제안서](docs/Constraint-Preserving%20Budget-Aware%20Memory.docx)
- [연구 질문 및 평가 방향](docs/research_questions.md)
