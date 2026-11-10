# Narrative Padding 2×2 Factorial Retrieval Audit (2026-11-11)

## 1. 목적과 결론

앞선 Mode 1~3 audit에서는 target Memory와 synthetic noise Memory의 문체가 함께
바뀌었기 때문에 어떤 부분이 Recall 변화에 기여했는지 분리할 수 없었다. 이번 audit은
Mode 1과 Mode 2의 required State padding 및 synthetic noise padding을 독립적으로
교체한 2×2 factorial 설계로 두 요인의 효과와 상호작용을 점검했다.

이 조건에서 관측된 Recall 변화는 **required State padding 교체에만 연결**됐다.
Mode 1 target을 Mode 2 target으로 바꾸면 State Recall@5가 baseline 후보군에서
`87.5% → 80.0%`(−7.5%p), Proposed 일반 후보군에서 `90.0% → 80.0%`(−10.0%p)로
변했다. Noise padding만 교체했을 때 Recall, target rank와 Top-5 ID는 하나도 바뀌지
않았고 Target×Noise interaction도 0이었다.

그러나 baseline의 target 효과 95% bootstrap 구간은 `[−20.0, +5.0]%p`, Proposed
후보군은 `[−22.5, 0.0]%p`이므로 통계적으로 확정적인 일반 효과로 표현하지 않는다.
결론은 “현재 단일 셀·40개 과제에서는 target 서술 변화가 관측 차이를 설명했고 noise
서술 효과는 관측되지 않았다”로 제한한다.

## 2. 실험 설계

| 항목 | 값 |
|---|---|
| 원본 데이터 | Mode 1 기존 반복 / Mode 2 로컬 자연어 |
| 기본 과제 | paired scenario 40개 |
| 조건 | Budget 2,048 / History 25K / Horizon 100 / Noise 0.5 |
| 추가 조건 | 저장 Constraint 3 / State update 2 |
| Token counter | `gemini:gemini-3.1-flash-lite` |
| Retrieval | Top-k 5 |
| Embedder | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Embedder revision | `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` |
| 불확실성 | paired bootstrap 4,000회, seed 2026 |
| 범위 | offline retrieval only |

두 요인은 다음과 같다.

- `T`: 과제의 유일한 required State Memory padding
- `N`: synthetic noise Memory padding
- `1`: Mode 1의 기존 반복 문장
- `2`: Mode 2의 결정적 로컬 자연어

| 조건 | Target padding | Noise padding |
|---|---|---|
| A (`T1_N1`) | Mode 1 | Mode 1 |
| B (`T2_N1`) | Mode 2 | Mode 1 |
| C (`T1_N2`) | Mode 1 | Mode 2 |
| D (`T2_N2`) | Mode 2 | Mode 2 |

Mode 1 Memory snapshot을 복사한 뒤 각 조건에 지정된 target/noise의 `content`와
`token_count`만 Mode 2 값으로 교체했다. query, 정답 ID, 제약, version/temporal 구조,
환경과 나머지 Memory는 고정했다.

## 3. 무결성과 균형

- Mode 1·2의 grid parameter, Token counter와 scenario ID가 일치했다.
- 각 scenario에는 required State가 정확히 하나 있고 synthetic noise와 ID가 겹치지 않았다.
- target/noise 이외의 Memory 내용 또는 구조가 달라지면 audit가 중단되도록 검증했다.
- 정답 파일명이 교체한 State content 안에 유지됨을 확인했다.
- 평균 target token은 Mode 1 `611.1`, Mode 2 `611.6`이었다.
- 평균 noise token은 Mode 1 `639.5`, Mode 2 `639.875`였다.
- 네 조건의 평균 실제 noise ratio는 `0.49970~0.50003`이었다.

원본 manifest·cell 경로와 SHA-256은
[`audit.json`](../experiments/results/narrative_factorial_h100_b2048/audit.json)에 기록돼 있다.

## 4. 전체 Recall@5

| Pool | A: T1/N1 | B: T2/N1 | C: T1/N2 | D: T2/N2 |
|---|---:|---:|---:|---:|
| baseline | 87.5% | 80.0% | 87.5% | 80.0% |
| Proposed 일반 후보군 | 90.0% | 80.0% | 90.0% | 80.0% |

`baseline`은 현재 turn까지 사용 가능한 전체 Memory 후보군이다. `Proposed 일반 후보군`은
별도 Protected 경로에서 처리할 활성 protected constraint를 일반 후보에서 제외한
후보군이다. 전체 Proposed Selector, Context Builder 또는 Agent 실행 결과가 아니다.

## 5. Factorial 효과

효과는 오른쪽 조건에서 왼쪽 조건을 뺀 paired Recall 차이다.

| Pool | Target 효과, N1 고정 | Target 효과, N2 고정 | Noise 효과, T1 고정 | Noise 효과, T2 고정 | Interaction |
|---|---:|---:|---:|---:|---:|
| baseline | −7.5%p `[-20.0,+5.0]` | −7.5%p `[-20.0,+5.0]` | 0.0%p `[0,0]` | 0.0%p `[0,0]` | 0.0%p `[0,0]` |
| Proposed 후보군 | −10.0%p `[-22.5,0.0]` | −10.0%p `[-22.5,0.0]` | 0.0%p `[0,0]` | 0.0%p `[0,0]` | 0.0%p `[0,0]` |

Target 교체 시 baseline에서는 2개 과제가 개선되고 5개가 악화됐으며, Proposed
후보군에서는 1개가 개선되고 5개가 악화됐다. Noise 교체는 두 target 조건과 두 pool
모두에서 개선·악화가 각각 0개였다.

Noise 교체의 0 효과는 단순히 평균 Recall이 상쇄된 결과가 아니다. A↔C와 B↔D에서
40개 과제 모두 target hit, target rank 및 Top-5 ID가 동일했다. 다만 similarity margin은
미세하게 바뀌었으므로 모든 검색 점수가 완전히 동일했다는 뜻은 아니다.

## 6. Task 유형별 관측

- `approval/pending`: target 교체 효과가 두 pool에서 −20%p, CI `[−50,0]`이었다.
- `temporal_update/current`: baseline −10%p, CI `[−40,+20]`; Proposed 후보군
  −20%p, CI `[−50,0]`이었다.
- `approval/granted`: 평균 효과 0이지만 개선 1개와 악화 1개가 상쇄됐고 CI는
  `[−30,+30]`이었다.
- `temporal_update/historical`: 네 조건 모두 Recall@5 100%였다.
- 모든 task 유형에서 관측된 noise 효과와 interaction은 0이었다.

각 유형의 표본은 10개뿐이므로 subgroup 차이는 탐색적으로만 해석한다.

## 7. 앞선 audit과의 관계

앞선 [Mode 1~3 retrieval audit](narrative_bias_audit_20261110.md)은 Mode 1→2에서
baseline −7.5%p, Proposed 후보군 −10%p를 관측했다. 이번 2×2 결과는 동일한 전체
차이가 target padding만 Mode 2로 교체한 B 조건에서도 재현되고, noise만 교체한 C
조건에서는 나타나지 않음을 보여준다.

따라서 이전 차이를 “자연어 noise가 더 강하게 경쟁해서 생긴 결과”로 설명하는 증거는
현재 셀에서는 없다. 오히려 required State 자체의 서술·padding 변화가 embedding과
cutoff 포함 여부에 영향을 준 것으로 해석하는 편이 관측값에 맞는다. 다만 이 factorial은
기존 두 데이터셋을 재조합한 분석이므로 새로운 독립 표본을 추가한 것이 아니다.

## 8. 연구 결정

1. 기존 Main Mode 1 결과와 이전 audit 결과는 수정하지 않고 보존한다.
2. Mode 2~5 결과를 Main의 repeat로 합치지 않는다.
3. 후속 생성기에서는 target State의 핵심 사실과 padding을 분리해 기록하고, 핵심 문장만
   사용한 retrieval 또는 padding 길이·문체를 교차한 추가 audit를 고려한다.
4. Noise 효과의 일반성을 검증하려면 Noise 비율, Budget, Horizon, Top-k와 과제 수를
   확장해야 한다.
5. 행동 성능을 주장하려면 각 조건에서 전체 Selector→Context→Agent 경로를 별도 실행한다.

## 9. 한계

- Budget 2K·Horizon 100의 한 셀과 40개 paired scenario만 사용했다.
- Mode 1과 Mode 2만 사용했으며 혼합 Mode 3과 LLM Mode 4·5는 포함하지 않았다.
- bootstrap은 기본 과제 단위 paired percentile 구간이며 다중 비교 보정은 하지 않았다.
- 이진 Hit@5는 cutoff 변화만 포착한다. 점수·순위의 작은 변화가 행동에 미치는 영향은
  측정하지 않았다.
- hybrid condition은 저장된 Mode 1·2 Memory를 재조합한 것이며 새 Agent 실행이 아니다.
- Agent, Tool, Context Budget 조립과 TSR·CVR·비용을 이 결과에서 추론할 수 없다.

## 10. 산출물

- 정규 audit: [`audit.json`](../experiments/results/narrative_factorial_h100_b2048/audit.json)
- 조건별 retrieval: `experiments/results/narrative_factorial_h100_b2048/factorial_details.csv`
- sample별 효과: `factorial_effects.csv`
- 전체·task별 집계와 CI: `factorial_summary.csv`
- 실행 코드: [`benchmark/narrative_factorial_audit.py`](../benchmark/narrative_factorial_audit.py)

