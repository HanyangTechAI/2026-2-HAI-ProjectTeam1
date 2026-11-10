# Horizon Narrative Retrieval Bias Audit (2026-11-10)

## 1. 목적과 결론

이 audit은 Horizon 사이를 채우는 반복 문장과 자연어 문장 구성이 semantic retrieval의
Top-5 후보에 영향을 주는지 확인한다. 동일한 40개 기본 과제를 세 생성 방식으로 다시
만들어 offline retrieval 결과를 paired 비교했다.

핵심 결론은 **채움 문체를 바꾸면 Top-5 후보와 State Recall이 실제로 달라진다**는 것이다.
기존 반복 방식인 Mode 1에서 로컬 자연어 Mode 2로 바꾸자 State Recall@5가 baseline에서
`87.5% → 80.0%`, proposed 후보군에서 `90.0% → 80.0%`로 낮아졌다. 혼합 Mode 3은
각각 `82.5%`, `87.5%`로 일부 회복했다. 따라서 기존 반복 문장이 retrieval과 무관하다고
가정할 수 없으며, 생성 방식을 바꾼 데이터는 기존 Main 결과의 반복 실행으로 합치면 안 된다.

다만 Mode 1이 항상 낙관적 편향을 만든다고 인과적으로 확정할 수는 없다. Mode 3의 평균
target similarity가 가장 높았고, task 유형별 방향도 동일하지 않았다. 이번 결과는 한 셀의
40개 합성 과제에 대한 민감도 점검이며 Agent 행동이나 TSR 실험이 아니다.

## 2. 실험 범위

| 항목 | 값 |
|---|---|
| 비교 모드 | Mode 1 기존 반복 / Mode 2 로컬 자연어 / Mode 3 50:50 혼합 |
| 기본 과제 | 세 모드에 공통인 paired scenario 40개 |
| 조건 | Budget 2,048 / History 25K / Horizon 100 / Noise 0.5 |
| 추가 조건 | 저장 Constraint 3 / State update 2 |
| Token counter | `gemini:gemini-3.1-flash-lite` |
| Retrieval | Top-k 5 |
| Embedder | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Embedder revision | `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| Safety margin | 32 tokens |
| Proposed selection | 비활성화; Context Builder 이전 retrieval만 비교 |

`baseline` pool은 현재 turn까지 사용 가능한 전체 Memory를 대상으로 한다. `proposed` pool은
별도 Protected 경로에서 처리할 활성 protected constraint를 일반 검색 후보에서 제외한다.
따라서 여기서 `proposed`는 전체 Proposed Selector나 Agent 실행 결과가 아니라
**Proposed의 일반 retrieval 후보군**을 뜻한다.

## 3. 데이터 무결성과 재현성

무결성 검사는 통과했다.

- 세 모드의 scenario ID 40개가 모두 일치했다.
- query, 현재 turn/time, 정답 ID, 제약, 기대·금지 Tool call과 환경은 동일했다.
- Memory 개수와 ID·type·version·시간 구조는 유지됐다.
- Mode 2와 Mode 3은 각각 Mode 1 대비 Memory content 80개가 변경됐다.
- 세 데이터셋의 조건과 Token counter가 일치했다.

원본 manifest와 선택 cell의 SHA-256은
[`audit.json`](../experiments/results/narrative_bias_h100_b2048/audit.json)에 보존돼 있다.
세 데이터셋은 `benchmark/generated/narrative_mode1`, `narrative_mode2`,
`narrative_mode3`에 저장됐다.

## 4. 전체 결과

| Mode | Pool | State Recall@5 | General Recall@5 | 평균 target rank | 평균 similarity | 반복 marker 평균 |
|---:|---|---:|---:|---:|---:|---:|
| 1 | baseline | 87.5% | 90.0% | 4.225 | 0.6000 | 7.200 |
| 1 | proposed | 90.0% | 92.5% | 3.575 | 0.6000 | 7.200 |
| 2 | baseline | 80.0% | 82.5% | 4.500 | 0.5988 | 0.000 |
| 2 | proposed | 80.0% | 82.5% | 3.750 | 0.5988 | 0.000 |
| 3 | baseline | 82.5% | 85.0% | 4.200 | 0.6119 | 3.475 |
| 3 | proposed | 87.5% | 90.0% | 3.550 | 0.6119 | 3.475 |

Mode 1과 Mode 2의 평균 similarity는 거의 같지만 Recall과 rank가 달라졌다. 전체 평균
similarity 하나만으로 Top-5 포함 여부의 안정성을 판단할 수 없다는 뜻이다. 평균 target
token 수는 Mode 1/2/3에서 각각 약 `611.1/611.6/612.5`로 비슷했고, target 대 다른
Memory token 비율도 약 `5.78`로 유지됐다.

## 5. Paired Top-5 변화

아래 delta는 오른쪽 모드에서 왼쪽 모드를 뺀 값이다. Rank delta가 양수면 순위가
나빠진 것이다.

| Pool | 비교 | Top-5 변경 과제 | 평균 Jaccard | State Recall delta | Rank delta | 개선 / 악화 |
|---|---|---:|---:|---:|---:|---:|
| baseline | 1 → 2 | 17/40 | 0.9417 | −7.5%p | +0.275 | 2 / 5 |
| baseline | 1 → 3 | 15/40 | 0.9667 | −5.0%p | −0.025 | 1 / 3 |
| baseline | 2 → 3 | 13/40 | 0.9750 | +2.5%p | −0.300 | 2 / 1 |
| proposed | 1 → 2 | 11/40 | 0.9500 | −10.0%p | +0.175 | 1 / 5 |
| proposed | 1 → 3 | 9/40 | 0.9750 | −2.5%p | −0.025 | 1 / 2 |
| proposed | 2 → 3 | 9/40 | 0.9750 | +7.5%p | −0.200 | 3 / 0 |

평균 Jaccard는 `0.9417~0.9750`으로 높지만, Mode 1→2에서 baseline 17개와 proposed
11개의 Top-5가 바뀌었다. 작은 후보 집합 변화도 cutoff 경계의 정답 State 포함 여부를
바꿀 수 있으므로 “대부분 유사하다”는 이유로 생성 방식의 영향을 무시할 수 없다.

## 6. Task 유형별 관측

- `temporal_update/historical`은 모든 모드·pool에서 State Recall@5가 100%였다.
- Mode 2에서 `approval/pending`과 `temporal_update/current`의 State Recall@5가 두
  pool 모두 70%로 낮았다.
- Mode 3은 proposed 후보군의 `approval/pending`을 90%,
  `temporal_update/current`를 80%로 회복했지만 Mode 1을 모든 항목에서 재현하지는 않았다.
- `approval/granted`는 세 모드 모두 State Recall@5 80%로 같았다.

과제 유형당 표본은 10개뿐이므로 이 차이를 모집단 효과나 일반적인 자연어 품질 효과로
확정하지 않는다.

## 7. 해석과 연구 결정

1. 현재 Main 결과는 Mode 1 데이터에 대한 결과로 그대로 보존한다.
2. Mode 2~5 데이터의 실행 결과를 Main의 추가 repeat로 합치지 않는다.
3. 자연스러운 Horizon에 대한 후속 행동 실험은 모드별 새 결과군에서 네 전략을 모두
   다시 실행해야 한다.
4. 후속 우선순위는 Mode 2·3의 Agent/TSR 실행, 그다음 Mode 4·5 데이터 생성과 같은
   audit의 반복이다.
5. 생성 모드별 보고에서는 retrieval, selection, Context Builder, Agent 행동 효과를
   분리한다. 이번 audit은 첫 번째 단계만 측정했다.

## 8. 한계

- Budget 2K·Horizon 100의 한 셀만 분석했다.
- paired scenario는 40개이고 신뢰구간이나 다중 비교 보정을 계산하지 않았다.
- Mode 4·5의 LLM 자연어는 포함하지 않았다.
- `selection_fixed_tokens`를 지정하지 않아 Proposed selection과 예산 조립을 실행하지 않았다.
- Agent, Tool, LLM을 실행하지 않았으므로 Task Success Rate·Constraint Violation·비용을
  이 결과에서 추론할 수 없다.
- 모드 변경은 반복성뿐 아니라 문구와 의미 분포도 함께 바꾼다. 관측 차이를 반복 marker
  하나의 인과 효과로 분해하지 못한다.

## 9. 산출물

- 정규 audit: [`audit.json`](../experiments/results/narrative_bias_h100_b2048/audit.json)
- 전체 retrieval 행: `experiments/results/narrative_bias_h100_b2048/retrieval_details.csv`
- paired 비교 행: `experiments/results/narrative_bias_h100_b2048/pairwise_details.csv`
- 집계: `summary.csv`, `pairwise_summary.csv`
- 실행 코드: [`benchmark/narrative_bias_audit.py`](../benchmark/narrative_bias_audit.py)

## 10. 후속 요인 분리 분석

2026-11-11에는 required State padding과 synthetic noise padding을 독립적으로 교체한
2×2 audit를 수행했다. Mode 1→2 Recall 차이는 target padding 교체에서 재현됐지만
noise padding 교체에서는 관측되지 않았다. 설계, bootstrap 구간과 제한 사항은
[Narrative Padding 2×2 Factorial Audit](narrative_factorial_audit_20261111.md)을 따른다.

이후 같은 Mode 1/2 입력을 Proposed의 실제 단계와 Context Builder에 통과시켰다. 초기
검색 miss는 Mode 1 4건, Mode 2 8건이었지만 40개 모두 최종 Context에서 Required State를
포함했다. 자세한 trace는 [Final Context Audit](narrative_final_context_audit_20261111.md)에
기록했다.

이후 같은 paired 과제에 Gemini Agent와 Tool 환경을 적용한 TSR 평가는 별도
[Mode 1/2 LLM 실행 보고서](narrative_llm_execution_audit_20261111.md)에 기록했다.
offline Recall@5와 Action-Level TSR은 서로 다른 평가 단계와 지표다.

