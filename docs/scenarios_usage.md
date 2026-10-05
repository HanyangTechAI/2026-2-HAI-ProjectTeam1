# scenarios.json 사용법

`benchmark/scenarios.json`에는 39개의 독립적인 입력 시나리오가 있다.
최상위 `scenarios` 배열에서 하나씩 읽는다. 실행 결과나 성능 측정치는 포함하지 않는다.

| 사례 | 검증 대상 |
|---|---|
| S01~S08 | 현재/미지정/과거/상대 시간, SUPERSEDED 선택, 유효 구간 경계 |
| S09~S10 | 이력 시작 전 및 버전 공백에서 정보 부족 반환 |
| S11~S12 | 검색 후보에서 빠진 최신/과거 버전의 chain 확장 |
| S13~S15 | Protected Retrieval, 승인 완료, State/행동 시점 분리 |
| S16~S20 | 제약 변경·과거 조회·구체성 우선·예외 범위·최신 규칙 |
| S21~S22 | 중복 ACTIVE 오류 및 Session 분리 |
| S23~S29 | 큰 단일 Memory, 필수 합계 초과, 고정 비용 초과, 예산 경계 |
| S30~S32 | 포맷 초과, 반복 tokenize, Flexible 제거 후 실패 |
| S33~S36 | Calendar/File/Task 제약과 여러 State를 결합하는 이메일 행동 |
| S37~S39 | Noise 속 단일 사실, 무관 제약 제외, 정보 없는 질문에서 기권 |

## 입력과 정답

- `history`: Memory 추출 테스트에 사용할 핵심 Interaction 목록. 중간 turn을 모두 채운 긴 대화는 아니다. `current_turn`과의 차이는 테스트용 turn 거리이다.
- `memory_snapshot`: 선택기/Store 테스트용 저장 시점 snapshot. `MemoryItem.from_dict`로 각 항목을 검증할 수 있다. 버전 관계가 이미 반영되어 있어 일반 `insert`로 다시 버전을 생성하지 않는다. Store snapshot 복원 adapter가 필요하다.
- `query_context`: 고정된 시간 해석의 정답. 실제 Query Analyzer 출력과 비교하며, Agent에 정답을 주입하지 않는다.
- `retrieval_fixture`: 단위 테스트에서 검색 결과와 점수를 고정하는 입력. 통합 Benchmark에서는 실제 Retrieval을 실행한다. 점수는 MemoryItem 밖에서 관리한다.
- `required_states`, `constraint_rules`, `expected_tool_calls`, `expected_answers`: 기존 Evaluator의 행동/응답 정답이다. `.test` 주소는 가상 수신자이며 실제 발송하지 않는다.
- `assertions`: 최종 선택 ID, 제외 ID, 버전 확장, 중단, 재tokenize 등을 검증하는 실행기용 정답. 기존 Evaluator는 이 필드를 검사하지 않는다.
- `context_fixture`: Context Builder 단위 테스트용 토큰 측정 반환값과 제거 순서. 실제 Tokenizer 구현을 대신하는 성능 데이터가 아니다.

`token_count_mode=synthetic_fixture`의 토큰 비용은 예산 경계 검증용 고정 숫자이다.
실제 LLM 실험에서는 모든 전략에 동일한 Tokenizer를 사용하여 Memory·고정 입력·최종 Context를 다시 측정한다.
`experiment_axes`는 확장 실험 조건이다. 긴 History 생성, Noise 비율 계산, seed별 sample 생성은 별도 구현이 필요하다.
메타데이터에는 측정하지 않은 History token 수나 Noise token 비율을 넣지 않았다.

## 정책과 평가 범위

EC-01/EC-06에 남아 있는 과거 부분 선택 설명과 최신 설계가 다르다.
이 데이터는 최신 `architecture.md`, `interfaces.md` 및 EC-12에 따라 필수 Memory가 예산에 들어가지 않으면
`insufficient_context_budget`을 반환하고 Agent를 중단하는 정책을 따른다.
상태 문자열은 `PipelineStatus`의 JSON 값인 소문자를 사용한다.

S15(EC-09)는 후순위로 `benchmark_enabled=false`이다. S21은 Store 오류 단위 테스트로,
현재 PipelineStatus에 Store 오류가 없으므로 상태값을 새로 만들지 않고 별도 assertion으로 검증한다.
같은 구체성·같은 시점의 제약 충돌처럼 정책이 아직 정해지지 않은 조건에는 정답을 임의로 지정하지 않았다.
S20은 같은 범위의 규칙이 갱신된 경우를 검증하며, 구체성 계산 자체의 구현 정책을 정의하지 않는다.

`validation_scope`가 `selection`, `budget`, `context_builder`, `store_consistency`인 사례는
해당 모듈의 검증용이다. 모든 항목을 그대로 TSR 비교 실험에 넣지는 않는다.
실패 상태를 올바르게 반환하는 것은 정책 검사에서는 통과지만, 기존 Evaluator의 Task Success는 false이다.
따라서 정책 통과율과 Task Success Rate를 분리해서 기록한다.

Tool pattern은 정확한 인자에 대한 부분 일치이다. Calendar 시간 범위나 권한의 모든 가능한 위반을
자동 판정하는 범용 규칙 엔진은 아니며, 각 사례의 명시적 기대 인자와 금지 행동을 검사한다.
행동 순서는 기존 Evaluator에서 검사하지 않으므로 다단계 승인은 승인 전/후를 별도 snapshot으로 평가한다.

## 검증

```powershell
python -m unittest discover -s tests -p "test_scenarios.py"
```

이 검증은 EC/TV 커버리지, Memory schema, 버전 참조, Evaluator 정답 호환성,
오답·무승인 행동의 실패 판정, 포맷 제거 fixture의 예산 일관성을 확인한다.
정답 snapshot을 이용한 계약 검사이며 실제 Agent의 성공률이나 모듈 구현 완료를 의미하지 않는다.
