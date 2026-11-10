# Horizon Narrative Final Context Audit (2026-11-11)

## 1. 목적과 결론

앞선 Mode 1/2 audit은 embedding retrieval의 초기 Top-5를 비교했다. 이번 audit은 같은
paired 입력을 실제 Proposed 단계에 통과시켜 초기 검색, version chain 확장, temporal
resolution, Required State 복구, 예산 선택과 최종 Context 조립을 단계별로 확인했다.

Mode 2는 초기 Required State hit가 Mode 1보다 적었다(32/40 대 36/40). 하지만 초기
검색에 같은 version chain의 진입 기억이 포함된 40개 모두에서 chain expansion 이후
정답 State가 복구됐다. 두 모드 모두 최종 선택·Context의 Required State와 해당되는
protected constraint가 완전했고, 잘못된 버전 포함과 Context Budget 초과는 0건이었다.

따라서 이 데이터에서 관측된 초기 retrieval 차이는 최종 Context 누락으로 이어지지
않았다. 이 audit은 **최종 Context 구성까지의 단일 단계 검증**이며 Agent나 LLM 행동을
실행하지 않아 TSR이나 실제 행동의 성공을 측정하지 않는다.

## 2. 실험 범위와 방법

| 항목 | 값 |
|---|---|
| 입력 | Mode 1 기존 반복 / Mode 2 로컬 자연어의 paired scenario 40개 |
| 셀 | Budget 2,048 / History 25K / Horizon 100 / Noise 0.5 |
| 추가 조건 | 저장 Constraint 3 / State update 2 |
| 실행 단위 | mode별 40개, 총 80개 single-step audit row |
| Selector | Main Freeze v1.0.3 Proposed, top-k 5, utility weights 0.6/0.3/0.1 |
| Budget | 명목 2,048, 공통 Safety Margin 32, 실효 2,016 tokens |
| Token counter | Gemini 3.1 Flash-Lite, request-aware `generateContentRequest` CountTokens |
| Embedder | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Embedder revision | `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| 실행 범위 | Query Analysis부터 Context Builder까지, LLM 호출 전 종료 |

두 모드의 Scenario ID, query, 정답·제약·환경과 Memory ID 및 비내용 metadata를 대조했다.
content와 token count의 변화는 required State와 synthetic noise Memory에만 허용했다.
Selector의 실제 초기 retrieval과 별도 단계 trace가 같은지, Required State와 protected
constraint trace가 실제 선택 결과와 같은지도 확인했다. 최종 Context의 각 Memory 본문,
실효 Budget 준수도 검사했다.

## 3. 단계별 결과

| 단계 | Mode 1 필수 State 완전 | Mode 2 필수 State 완전 |
|---|---:|---:|
| Initial retrieval | 36/40 (90%) | 32/40 (80%) |
| Version chain expansion | 40/40 (100%) | 40/40 (100%) |
| Temporal resolution | 40/40 (100%) | 40/40 (100%) |
| Required State resolution | 40/40 (100%) | 40/40 (100%) |
| Selector output | 40/40 (100%) | 40/40 (100%) |
| Final Context | 40/40 (100%) | 40/40 (100%) |
| Final Context built | 40/40 (100%) | 40/40 (100%) |

- Initial State miss: Mode 1은 4/40, Mode 2는 8/40이었다.
- 초기 miss는 Mode 1의 4/4, Mode 2의 8/8 모두 최종 Context에서 복구됐다.
- 두 모드 모두 40/40에서 동일 version chain으로 진입할 기억을 초기 검색에서 확보했다.
- 잘못된 버전이 최종 Context에 들어간 경우는 양쪽 모두 0건이었다.
- 적용 가능한 protected constraint가 있는 승인 시나리오 20/20에서 최종 constraint가
  포함됐다. temporal update 20개에는 적용 constraint가 지정되지 않았다.
- Flexible Memory 제거는 양쪽 모두 0건이었다.

## 4. Token 및 Context 결과

| 값 | Mode 1 | Mode 2 |
|---|---:|---:|
| 평균 고정 입력 | 666.275 | 666.275 |
| 평균 선택 Memory token | 670.175 | 672.325 |
| 평균 최종 Context token | 1,356.05 | 1,358.30 |
| 최종 Context 범위 | 1,334–1,390 | 1,330–1,394 |
| 평균 남은 실효 예산 | 659.95 | 657.70 |
| Context overflow | 0 | 0 |

두 모드의 평균 최종 Context 차이는 2.25 tokens였고 최대 관측값 1,394는 실효 상한
2,016보다 낮았다. 이 값은 audit 입력의 CountTokens 결과이며 provider 응답에서 관측한
prompt usage가 아니다.

## 5. 해석

Mode 2에서 초기 검색 누락은 두 배였지만, 선택기 전체 경로에서는 누락으로 남지 않았다.
이 paired 사례에서는 version chain expansion과 temporal/required-state 단계가 초기
Top-k 차이를 보완했고, 이후 selector와 builder가 올바른 시점의 State를 예산 안에
포함했다.

이 결과는 Mode 1과 2의 초기 retrieval 차이가 중요하지 않다는 일반 결론을 뒷받침하지
않는다. 효과는 입력 데이터, 후보에 포함된 version chain 진입점, Budget과 구현에
의존한다. 또한 final Context에 기억이 포함된 사실만으로 모델이 그 기억을 올바르게
해석하거나 Tool 인자를 성공적으로 채울 것이라고 추론할 수 없다.

## 6. 한계

- 단일 Budget/Horizon cell과 40개 paired scenario만 분석했다.
- 반복 실행과 bootstrap CI를 계산하지 않았다.
- Proposed만 단계별 분석했으며 Baseline들과의 단계별 비교는 수행하지 않았다.
- LLM Planner, Agent loop, Tool 실행, Task Success, constraint violation 행동률 및
  provider prompt usage는 측정하지 않았다.
- Mode 3~5는 포함하지 않았다.
- 0건의 overflow는 이 80개 audit row에 한정된다.

## 7. 재현 정보와 산출물

- 입력 manifest/cell SHA-256 및 freeze·prompt checksum: `run_spec.json`
- 단계별 결과: `details.csv`
- Mode 간 paired 비교: `paired.csv`
- 합계와 Gemini counter metadata: `summary.json`
- 코드: [`benchmark/proposed_context_audit.py`](../benchmark/proposed_context_audit.py)
- 결과 디렉터리: `experiments/results/narrative_final_context_h100_b2048/`

Token counter metadata에는 request-aware CountTokens API 호출 266회, persistent cache hit
189회, retry 0회가 기록돼 있다. 이 카운트는 Token audit API 사용량이며 Agent의 생성
호출 수나 과금 총액으로 해석하지 않는다.

