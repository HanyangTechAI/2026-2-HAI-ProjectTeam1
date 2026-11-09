# Architecture / Parameter Freeze v1.0.3

- 상태: **FROZEN**
- 기준일: 2026-11-09 (Asia/Seoul)
- 적용 범위: V1 synthetic benchmark의 생성, 실행, 평가
- 기계 판독 기준: `experiments/configs/*.json`의 `freeze.version = "1.0.3"`
- 이전 기준 구현 commit: `fd779e5b14321cc0852da1f60c5ea5070a046cc7`; v1.0.3 변경은 각 실행의 `git_commit`, `git_dirty`, `resume_identity.code`와 함께 추적한다.

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
- fixed_context_tokens와 최종 Context Budget Validation은 실제 요청의 model, contents,
  Planner SYSTEM_INSTRUCTION과 설정된 tools를 `generateContentRequest` 형태로
  CountTokens한다.
- LLM model, Embedder, retrieval algorithm, K, utility weights, dataset grid,
  prompts 및 Planner behavior는 변경하지 않는다.
- 따라서 알고리즘의 Memory 선택 원칙은 동일하며, Context Budget B의
  측정 범위만 실제 Agent input semantics와 일치시킨다.

## Patch note - V1.0.3

V1.0.3은 공통 출력 상한을 512에서 2048 token으로 올린 실행 파라미터 patch이다.
모든 표준 experiment config와 config 없이 실행하는 Planner/Runner 기본값에 적용한다.

2026-11-09의 실제 API 진단에서 출력 상한 512의 문제 조건 5개가 모두
`finish_reason=MAX_TOKENS`로 끝나 미완성 행동 JSON을 반환했다. 같은 조건을
출력 상한 2048로 한 번씩 재실행했을 때 10개 응답이 모두 `STOP`으로 종료되고
5개 작업 모두 성공했다. 이는 예비 점검이며 전체 grid의 안정성을 보장하지 않는다.

- 모든 전략의 공통 `max_output_tokens`를 **512에서 2048로 변경**한다.
  출력 상한은 통제변인이며 별도 sweep 축이 아니다. 실제 출력 사용량은 측정값이다.
- 모델, temperature 0, thinking LOW, 입력 Budget/Horizon grid, 프롬프트,
  메모리 알고리즘, 입력의 정답 및 평가 성공 기준은 유지한다.
- 응답별 `finish_reasons`, candidate/thoughts token 수를
  `run_result.llm_usage.response_diagnostics`에 기록한다. 출력 잘림은 입력
  Context Budget 부족과 구분해 분석하며, 행동 성공과 함께 별도 보고한다.
- 512 상한의 기존 pilot/점검 결과를 2048 상한 결과에 합치거나 덮어쓰지 않는다.
  새 output 경로에서 실행하고 이전 checkpoint에 `--resume`하지 않는다.
- 기존 Gemini 입력 ZIP의 생성 freeze는 1.0.1일 수 있다. 이번 변경은 출력
  상한만 바꾸며 모델·입력 문자열·content token counter는 유지하므로 입력을
  재생성하거나 기존 manifest의 버전을 바꿀 필요가 없다. 원래 ZIP과 검증 보고서의
  provenance는 보존하고 실행 결과에는 실제 1.0.3 설정을 기록한다.

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
| Max output tokens | `2048` |
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
| Request CountTokens transport | Developer API REST / `generateContentRequest` |
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

MemoryItem 개별 비용은 content-only 입력을 `google-genai` SDK의 CountTokens로 측정한다.
반면 고정 입력과 최종 Context의 request-aware 측정은
`v1beta/models/{model}:countTokens` REST endpoint에 `generateContentRequest` wrapper를
보내며, 실제 생성 요청과 같은 model, user contents, systemInstruction 및 설정된 tools를
포함한다. 이 경로는 명시적 `api_key` 또는 `GEMINI_API_KEY`가 반드시 필요하다.
Metadata에는 `request_shape=generateContentRequest`,
`request_count_transport=developer_api_rest`, `request_aware_budgeting=true`와
system instruction SHA-256을 기록한다. REST CountTokens도 Token retry 정책과 같은
재시도 대상·횟수·backoff를 사용하고 `URLError`를 transient connection failure로 본다.

### Preflight 결과 및 Safety Margin 확정

- 대상 모델: Gemini 3.1 Flash-Lite (`gemini-3.1-flash-lite`)
- 측정 예산: `1,024 / 2,048` tokens
- 검증한 실제 Prompt 호출: `106`회
- CountTokens 대비 최대 양수 오차: `1` token
- 실제 Prompt Budget 초과: `0`건
- Prompt Audit 불완전: `0`건
- 최종 공통 Safety Margin: `32` tokens
- Budget Policy: `total_input`

**1K 실행 가능성 제한:** 현재 생성 데이터에서는 고정 입력과 Mandatory Memory의
합이 명목상 1K Budget을 초과한다. 따라서 Proposed는 8개 Preflight 시나리오 모두
`INSUFFICIENT_CONTEXT_BUDGET`으로 중단됐다. 이 중단은 필수 기억 보존 정책에 따른
결과이며 정상적인 Agent 행동 실패와 별도로 분석한다.

1K 조건은 예산 실현 가능성과 안전 중단 분석에 사용한다. 전략별 실제 행동 성능
비교는 LLM 실행 가능 구간에서 추가로 수행하며, 전체 TSR에는 예산 중단도 실패로
포함한다.

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

Freeze 승인용 live gate는 `tests/smoke_4k8k.py`로 별도 수행한다. 검증된 Gemini 3.1
Main ZIP에서 seed `42, 43`, horizon `50`의 동일한 8개 sample을 Budget `4096`과
`8192`에 짝지어 추출하고, 네 전략을 repeat `1`로 실행한다. Budget별 `32`회, 총
`64`회의 예정 run을 모두 완료해야 한다. 이 gate 역시 Main 효과 크기 결과에는 섞지 않는다.

`report` 단계는 run key 중복·누락, completed checkpoint, 실제 LLM 실행, request-aware
CountTokens, Prompt Audit 완전성, 실제·측정 Budget 초과, 공통 Safety Margin,
embedding revision 및 prompt checksum 일치를 검사한다. 필수 조건 위반 시 readiness는
`HOLD`다. 비용 회계 불완전은 별도 warning으로 남기고 해당 비용 비교를 금지한다.

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

### 4.2 Usage Accounting Completeness

Provider usage metadata의 일부가 없거나 retry 중 실패한 요청의 청구 여부를 확인할 수
없는데 관측된 값만 합산하면 token·비용을 실제보다 작게 보고할 수 있다. 따라서
Planner와 Runner는 다음 완전성 계약을 적용한다.

- `api_calls`는 실패한 retry를 포함한 모든 Provider 요청 시도 수이고,
  `successful_response_calls`는 응답 객체를 받은 호출 수다.
- `usage_metadata_calls`는 usage metadata가 존재한 응답 수이며,
  `cost_usage_complete_calls`는 prompt, candidate/response, cached input, tool-use prompt,
  thinking과 total token의 비용 계산 요건을 모두 만족한 응답 수다. 선택적 token 필드가
  명시적으로 없으면 0으로 취급하지만 음수·비정수 또는 필수 candidate/response token
  누락은 불완전으로 처리한다.
- `billing_uncertain_attempts = api_calls - successful_response_calls`이다. retry 후 정상
  응답을 받았더라도 이전 실패 시도가 과금됐는지 확정할 수 없으므로 전체 비용 추정은
  불완전하다.
- `prompt_usage_complete`는 모든 성공 응답에 유효한 prompt token이 있을 때만 true다.
  이는 CountTokens 대비 실제 prompt Budget 검증에 사용한다.
- `cost_estimate_complete`는 모든 API 시도가 성공 응답이며 모든 응답의 비용 metadata가
  완전할 때만 true다. 하나라도 불완전하면 prompt/output/total token 합계와 추정 비용을
  부분값이나 0으로 기록하지 않고 모두 `null`, `accounting_status="incomplete"`로 남긴다.
  API 호출이 전혀 없으면 완전한 0회 실행으로 간주해 output token과 비용은 0이다.
- Prompt audit은 `verified`, `unverified`, `exceeded`, `not_applicable` 상태를 기록한다.
  불완전하더라도 관측된 실제 입력 하나가 명목 Budget을 넘으면 `exceeded=true`를
  보존하며, 초과가 관측되지 않았지만 일부 usage가 없으면 초과 여부는 `null`이다.
- Main 집계에서 Agent가 실행된 row는 양의 API call 수와 완전한 prompt audit을 반드시
  가져야 한다. 조건을 만족하지 않는 row는 0이나 미초과로 간주하지 않고 집계를
  중단한다. 비용 완전성은 별도 필드로 보고하며 불완전 비용을 성능·비용 비교에 넣지 않는다.

이 규칙은 실제로 확인된 usage만 보고하고 미관측량을 0으로 대체하지 않는 보수적
회계 원칙이며, 네 strategy에 동일하게 적용한다.

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
- `tests/smoke_4k8k.py prepare`, `run`, `report`를 순서대로 수행하고 live gate의
  `smoke_gate_report.json`이 `readiness="PASS"`인지 확인한다. 중단된 동일 실행만
  `run --resume`으로 이어서 수행한다.

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
