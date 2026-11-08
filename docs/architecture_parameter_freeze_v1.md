# Architecture / Parameter Freeze v1.0.2

- 상태: **FROZEN**
- 기준일: 2026-11-07 (Asia/Seoul)
- 적용 범위: V1 synthetic benchmark의 생성, 실행, 평가
- 기계 판독 기준: `experiments/configs/*.json`의 `freeze.version = "1.0.2"`
- 기준 구현 commit: `fd779e5b14321cc0852da1f60c5ea5070a046cc7`

## Patch note - v1.0.1

v1.0.1은 연구 아키텍처 또는 주요 실험 파라미터의 변경이 아니라,
Pilot에서 발견된 approval semantics 오류를 수정한 patch freeze이다.

기존 구현은 `email.request_approval`이 반환한 receipt/pending approval ID를
실제 발송 허가(grant)로 오인할 수 있었다.

v1.0.1에서는 다음 의미를 명시적으로 구분한다.

- receipt: 승인 요청 접수 상태이며 발송 권한이 없음
- grant: 실제 승인 완료 상태이며 발송 가능

이 수정은 Agent action correctness에 영향을 주므로 기존 v1.0 결과와
동일 결과군으로 합치지 않는다.

Embedding model, retrieval algorithm, utility weights, token budget,
LLM model 및 실험 grid는 v1.0과 동일하다.

이 문서는 본실험 전에 변경할 수 있는 요소와 고정된 요소를 분리한다. 아래 항목을
바꾸는 실행은 V1.0 결과와 같은 실험으로 합치지 않는다. 필요한 경우 freeze version을
올리고, 입력을 다시 생성·측정하며, 결과 디렉터리를 분리한다.

## Patch note - V1.0.2

V1.0.2는 Context Budget의 token counting 범위를 실제 Gemini request 입력과
일치시키는 accounting patch이다.

- MemoryItem 개별 Token Cost는 기존과 동일하게 content-only CountTokens를 사용한다.
- fixed_context_tokens와 최종 Context Budget Validation은
  contents + Planner SYSTEM_INSTRUCTION을 CountTokens한다.
- LLM model, Embedder, retrieval algorithm, K, utility weights, dataset grid,
  prompts 및 Planner behavior는 변경하지 않는다.
- 따라서 알고리즘의 Memory 선택 원칙은 동일하며, Context Budget B의
  측정 범위만 실제 Agent input semantics와 일치시킨다.

## 1. Frozen architecture

```text
Interaction / Tool result
        ↓
Memory snapshot + version history
        ├──────────────→ Protected retrieval
        ↓                         ↓
General semantic retrieval   Active/applicable constraint
        ↓                         ↓
Version-chain expansion          │
        ↓                         │
Temporal resolution              │
        ↓                         │
Required state ──────────────────┤
                                  ↓
              Mandatory-first budget selector
                                  ↓
                     Flexible utility packing
                                  ↓
                Context build + final retokenization
                                  ↓
                       Planner → Mock tools
                                  ↓
                       Deterministic evaluator
```

핵심 결정은 다음과 같다.

1. Store, Selector, Context Builder, Agent, Tool, Evaluator의 책임을 분리한다.
2. Proposed만 version-chain expansion, temporal resolution, protected constraint 보존,
   required-state mandatory selection을 수행한다.
3. Sliding Window, Vector Top-k, Utility-per-Token은 동일 snapshot과 총 입력 예산을
   사용하지만 Proposed 전용 보존/해석 단계를 사용하지 않는다.
4. 총 입력 예산은 조립된 context의 상한이다. 고정 입력이 예산보다 크면
   `invalid_budget_configuration`, mandatory memory가 남은 예산보다 크면
   `insufficient_context_budget`으로 중단한다. 필수 기억을 일부 버리고 실행하지 않는다.
5. Context Builder는 formatting 이후 다시 측정하고, 초과 시 flexible memory의
   최저 우선순위 항목만 제거한다.
6. 각 strategy/repeat는 새 Agent와 Tool 환경을 사용한다. scenario, memory snapshot,
   query, 현재 turn/time은 공유하고 평가 정답은 실행 경로에 전달하지 않는다.
7. Token cache는 실행별로 격리한다. 다른 strategy가 먼저 계산한 값이 이후 실행의
   latency에 영향을 주지 않게 한다.

V1 interface 목록과 필드 계약은 [interfaces.md](interfaces.md)의 “Interface Freeze 기준”을
따른다. 내부 구현 최적화는 허용하지만 공개 입력/출력, status 의미, strategy 의미를
바꾸면 freeze 변경이다.

## 2. Frozen common parameters

| 구분 | V1.0 값 |
|---|---|
| LLM provider/model | Google / `gemini-3.1-flash-lite` |
| Temperature | `0` |
| Max output tokens | `512` |
| Thinking level | `LOW` |
| LLM retry | 최대 `2`, base `0.5s`, exponential backoff |
| LLM timeout | `60000ms` |
| System prompt | `experiments/prompts/system_v1.txt` |
| Tool definitions | `experiments/prompts/tools_v1.txt` |
| Algorithm specification | `docs/algorithm.md` |
| Planner system instruction | `agent.llm_planner:SYSTEM_INSTRUCTION` |
| Planner action schema | `agent.llm_planner:ACTION_SCHEMA` |
| Embedder callable | `benchmark.experiment_runtime:multilingual_embedder` |
| Embedding model | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Candidate Top-k | `5` |
| Max agent steps | `8` |
| Utility weights | relevance `0.6`, importance `0.3`, recency `0.1` |
| Token counter | Gemini CountTokens / `gemini-3.1-flash-lite` |
| Token cache | memory `4096`, SQLite `.cache/gemini_token_cache.sqlite3` |
| Token retry | 최대 `3`, base `0.5s`, exponential backoff |
| Budget scope | `total_input` |
| Mandatory overflow | `stop_and_log` |
| Bootstrap | `2000` iterations, seed `42` |
| Embedding normalization | `normalize_embeddings=True` |
| Similarity metric | cosine similarity |

Algorithm specification, Prompt 및 Planner behavior checksum도 freeze에 포함한다.

| 파일 | SHA-256 |
|---|---|
| `docs/algorithm.md` | `f029a473fa91a292e6c6d4ee693546e42787e1f6a41d550c71781668156b05ab` |
| `system_v1.txt` | `9ad2e2d481997048ba770648ef1d07050a098a77674f5187c9dd645f75b6fac5` |
| `tools_v1.txt` | `cc701a57b9aa951060ccfd28d69e74955e37af1e709ca932e04b96d725e61c3` |
| `agent.llm_planner:SYSTEM_INSTRUCTION` | `aff1c564d3d2c2d9aa28e1af91cf722c00588db88bad54d276bb4c0cdab6307f` |
| `agent.llm_planner:ACTION_SCHEMA` | `634277e47b8b38427623d5d9116289d90d04868a43f4753ed7ab779a750370b9` |

`SYSTEM_INSTRUCTION`은 UTF-8 문자열 바이트를 직접 해시한다. `ACTION_SCHEMA`는
`json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`로
정규화한 UTF-8 JSON 바이트를 해시한다. 실행 시 두 값은 `experiment.json`의
`planner_rules_hash`와 `action_schema_hash`에 각각 기록한다. `docs/algorithm.md`는 파일의
원본 바이트를 해시하며 실행 시 `experiment.json`의 `algorithm_hash`에 기록한다.
파일 기반 freeze 대상인 `system_v1.txt`, `tools_v1.txt`, `docs/algorithm.md`는
`.gitattributes`에서 `text eol=lf`로 고정한다. 따라서 Runner의 원본 바이트 SHA-256
검증은 Windows와 Linux checkout에서 같은 LF 바이트를 기준으로 한다.

Runner는 version, 공통 모델/runtime 값, algorithm specification, prompt 및 Planner
behavior checksum을 검증한다.
Config를 쓰지 않은 ad-hoc 실행 또는 명시적 CLI override는 가능하지만, freeze 본실험
결과로 자동 간주하지 않으며 `experiment.json`에 effective value와 override를 기록한다.

## 3. Frozen experiment scopes

### Main — executable, 필수

- strategies: Proposed, Sliding Window, Vector Top-k, Utility-per-Token
- budgets: `1024, 2048, 4096, 8192`
- horizon: `10, 50, 100` turns
- history: `25000` tokens
- noise ratio: `0.5`
- constraints: `3`
- state updates: `2`
- task types: `temporal_update`, `approval`
- samples/cell: `20`, seeds: `42, 43`, repeats: `2`
- retrieval metric k: `5`
- planned agent runs: `3840`

### Ablation — executable, 선택

Full Proposed와 다음 단일 제거 변형을 비교한다.

- `no_constraint_preservation`
- `no_temporal` — temporal resolution 제거에 따라 mandatory state도 비활성화
- `no_state_mandatory`
- `no_budget_aware_packing`

Budget은 `2048, 4096`, horizon은 `100`, planned agent runs는 `800`이다.

### Stress — executable, 선택

Budget `2048`, history `25000`, horizon `100`을 고정하고 noise
`0.1/0.5/0.9`, constraint `1/3/5`, state update `0/2/5`를 교차한다.
Planned agent runs는 `2160`이다.

### Smoke — executable, 검증용

Budget `2048`, history `15000`, horizon `50`, 5 samples, seed `42`, repeat `1`이다.
본실험 효과 크기 보고에는 사용하지 않는다.

### Pilot — generation only

100개의 distinct base sample을 생성·검증하는 입력 freeze다. Agent benchmark 실행에는
사용하지 않는다.

### Long-context reference — deferred

`131072` budget의 별도 참고 조건이지만 long-context adapter가 없으므로 V1.0에서는
실행하지 않는다. 동일-budget ranking에도 포함하지 않는다.

## 4. Evaluation freeze

주 지표는 Action-Level Task Success Rate다. 함께 보고할 지표는 Constraint Violation,
Required-State Accuracy, Stale-State Usage, Temporal Version Accuracy, general retrieval
지표, selection failure, token usage, latency다.

- 검색 실패, 선택 실패, 행동 실패를 분리한다.
- Protected target은 general retrieval 분모에서 제외하고 별도로 평가한다.
- Sliding Window에는 general retrieval 지표를 적용하지 않는다.
- 과거 시점 요청에서 superseded state 사용은 오류가 아니다. 요청 시점에 유효한 버전인지
  평가한다.
- `retrieval_k`는 평가 cutoff이고 `candidate_top_k`는 후보 검색 폭이다. 둘은 독립 값이다.
- sample/repeat 단위 paired delta와 sample-cluster bootstrap 95% CI를 사용한다.

### 4.1 Provider Failure Isolation

LLM provider 전송·인증·rate limit·timeout·서버 오류는 Memory Policy 또는 모델의
행동 실패와 분리한다. Provider 장애를 임의의 text action이나 Task failure row로
변환하면 전략별 실행 순서와 장애 시점에 따라 성능 지표가 오염되므로 다음 계약을
고정한다.

- 408, 429, 500, 502, 503, 504와 connection·timeout·unavailable·rate-limit 계열
  오류만 transient failure로 분류한다. `max_retries=2`, base `0.5s`의 exponential
  backoff를 적용하므로 최초 요청을 포함한 최대 시도 횟수는 3회다.
- transient retry가 모두 소진되거나 non-retryable 오류가 발생하면 Planner는
  `LLMProviderError`를 발생시킨다. 오류에는 model, 실제 attempts, 원 예외 type과
  가능한 경우 status code를 보존하고 원 예외를 exception cause로 연결한다.
- Provider 오류를 `AgentAction`이나 “모델 호출 실패” text response로 바꾸지 않는다.
  해당 strategy/sample/repeat의 결과 row와 deterministic evaluation은 생성하지 않으며,
  batch는 즉시 중단한다.
- 중단 전에 newline 단위로 확정된 `runs.jsonl` row는 보존한다. `progress.json`은
  `interrupted`와 완료 row 수를 기록하고, 같은 resume identity로 `--resume`하면 이미
  완료된 key를 재실행하지 않은 채 실패한 실행부터 계속한다.
- HTTP 요청 자체는 성공했지만 structured response를 행동으로 파싱할 수 없는 경우는
  Provider failure가 아니다. 이 경우 “모델 응답을 행동으로 읽지 못했습니다.”라는
  정상 Agent 응답으로 남기고 evaluator가 모델 행동 실패로 판정한다.
- `progress.json`이 `completed`가 아니거나 예정된 run key가 모두 확정되지 않은 batch는
  본실험 집계에 사용하지 않는다. Provider 장애로 누락된 실행을 Task failure 0점으로
  대체하거나 완료된 일부 row만으로 전략 성능을 비교하지 않는다.

이 격리 정책은 Memory 선택 알고리즘, prompt, model parameter 또는 metric 정의를
바꾸지 않는다. 다만 Provider failure를 연구 결과와 분리하는 실행·checkpoint 계약이므로
모든 strategy와 repeat에 동일하게 적용한다.

## 5. Explicitly deferred

다음은 V1.0 결과에 섞지 않는다.

- Long-context adapter 및 동일-budget full-context 비교
- Hybrid recent-context + retrieved-memory 전략
- 실제 LLM Memory Analyzer를 통한 online memory write-back
- 영속 vector DB / production DB 교체
- 공개 benchmark 확장
- 생성 모델, embedding model, prompt 또는 tokenizer 교체
- BM25 retrieval
- Entity-based retrieval
- Semantic + BM25 hybrid retrieval
- Semantic + BM25 + Entity multi-signal retrieval

## 6. Execution readiness gates

Architecture/parameter가 frozen인 것과 본실험 입력이 준비된 것은 구분한다.

- 현재 저장된 `benchmark/generated/main.zip`은 `provisional_token_count`로 생성된 개발용
  입력이다. V1.0 본실험 전에 freeze된 Gemini counter로 main dataset을 다시 생성·측정해야
  한다. Metadata의 `token_count_mode`만 바꾸어 재사용하지 않는다.
- `GEMINI_API_KEY`와 embedding model weight를 실행 환경에 준비한다.
- 실제 사용한 Python/package 버전과 embedding model revision을 결과와 함께 기록한다.
  `requirements.txt`의 lower bound만으로 동일 weight revision까지 보장하지 않는다.
- 전체 test와 smoke가 통과하고 `experiment.json`의 model, tokenizer, K, utility weights,
  prompt, override 목록이 이 문서와 일치하는지 확인한 뒤 main을 실행한다.

## 7. Change control

Freeze 항목 변경 절차는 다음과 같다.

1. 변경 이유와 영향받는 RQ/metric을 기록한다.
2. `freeze.version`을 올린다.
3. prompt/model/tokenizer가 바뀌면 dataset token count를 다시 측정하고 생성물을 재생성한다.
4. 모든 strategy에 같은 변경을 적용한다.
5. 기존 결과와 다른 output directory를 사용한다.
6. 전체 테스트와 smoke run을 통과한 뒤 새 freeze를 승인한다.

버그 수정이 결과를 바꿀 수 있으면 parameter 값이 같아도 새 patch freeze로 취급한다.
로깅·문서·성능 최적화처럼 결과 의미를 바꾸지 않는 수정만 V1.0 안에서 허용한다.
