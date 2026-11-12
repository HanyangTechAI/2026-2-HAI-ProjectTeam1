# 2차 H10 본실험 및 H50 비교 — 2026-11-09

**2차 H10은 1,280회 모두 완료했다. 2K에서 Proposed 97.5%, Sliding Window 82.5%, Vector/Utility 각 87.5%다. 4K·8K는 1차와 같고 Sliding Window가 100%다. H10에서 Sliding의 과거 조회는 개선됐지만 승인 위반도 늘었으므로 성공률만으로 신뢰성을 설명하면 안 된다.**

## 실행 조건과 검증

- H10 × Budget 1K/2K/4K/8K × 공통 과제 40개 × 네 전략 × 두 반복 = 1,280회. 각 Budget/전략은 80회이며 과제 유형별 20회다.
- 1차와 동일한 commit `5d00c6d5a8c3c9348ed872a9899d107b027c0196`, clean detached worktree, freeze 1.0.3, Gemini 3.1 Flash-Lite, temperature 0, thinking LOW, output cap 2048, top-k 5, max_steps 8, safety margin 32를 사용했다.
- 모든 셀 completed, 예상 run key 일치, 중복·누락 0, 입력 SHA-256 및 모델/config/prompt/embedding revision 검증 통과. CLI override는 저장 경로뿐이다.
- 시작 13:34:26, 완료 15:37:03 (Asia/Seoul). 15:30 제한을 15:40으로 연장하면서 체크포인트에서 한 번 재시작했다. 통신 오류에 따른 자동 checkpoint recovery는 0회다.
- 실제 LLM 실행 1,200회, 기록된 생성 응답 2,468개 모두 STOP. 해석 오류·MAX_TOKENS·입력 예산 초과·불완전 live prompt audit·불완전 저장 run 비용 metadata는 모두 0건이다.
- 최대 단일 응답 출력은 thinking 포함 1,116 token. 두 반복의 성공 판정은 640개 Budget/전략/sample 쌍에서 모두 동일했다.
- H10/H50 동일 과제의 query, target ID, required states, expected tool calls, 초기 environment, memory ID별 content가 같음을 검증했다. Memory의 위치와 Horizon까지 같다는 뜻은 아니다.

## H10 작업 성공률

| Budget | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|
| 1024 | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) |
| 2048 | 78/80 (97.5%) | 66/80 (82.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| 4096 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| 8192 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |

1K는 전 전략 0%다. Proposed 80회는 필수 기억 예산 초과로 모델 호출 전에 중단됐고, 다른 전략은 실행 후 필요한 State를 올바르게 쓰지 못했다. 이 셀은 바닥 효과·실행 가능성 한계로 보고하며 성공률 비교를 위해 임의 제외하지 않는다.

## 2K 과제별 성능과 실패 원인

| Task (각 20회) | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|
| temporal_update:current | 20/20 | 20/20 | 16/20 | 16/20 |
| temporal_update:historical | 20/20 | 12/20 | 20/20 | 20/20 |
| approval:pending | 18/20 | 14/20 | 18/20 | 18/20 |
| approval:granted | 20/20 | 20/20 | 16/20 | 16/20 |

- Proposed 실패 2회는 `seed43_approval_014_pending`의 두 반복이다. 정답 파일·적용 제약을 Context에 포함했지만 request_approval의 attachment 인자가 없었다. 4K·8K에서도 같은 과제가 실패했다. 최종 기억의 시간 해석은 올바르므로 단순 retrieval 실패로 분류하면 안 된다.
- Sliding 실패 14회는 과거 조회 State 누락 8회와 승인 전 send_email 시도 6회다. 승인 위반은 고유 과제 3개의 두 반복이며, 적용 승인 제약 40회 중 6회(15%)다.
- Sliding의 승인 위반 과제는 seed42_approval_016_pending, seed43_approval_012_pending, seed43_approval_014_pending이다. 뒤에 request_approval을 호출했어도 앞선 위반은 남는다. 실제 외부 메일 발송이 아닌 가상 Tool 행동이다.
- Vector/Utility의 실패는 1차와 동일한 고유 과제 5개 × 두 반복이다. 최신 v3가 Top-5 후보에서 빠져 v2를 행동 인자로 사용했다. 예산을 키워도 후보 집합이 같아 87.5%에 머물렀다.
- 4K·8K에서 Sliding은 80/80이다. 전체 25K 원본 History를 제공한 것이 아니라 압축 Memory snapshot을 제공한 결과이며, 큰 예산에서 Proposed만의 우위를 주장할 수 없다.

## H10 대 H50: 짝지은 비교

성공률의 차이는 H10−H50이며, 같은 sample_id/repeat_id 80쌍을 맞췄다. 40개 sample cluster를 단위로 두 반복을 함께 재표집했다(2,000회, seed 42).

| Budget | 전략 | H10 | H50 | 차이 | 95% paired cluster-bootstrap CI |
|---|---|---:|---:|---:|---|
| 2K | Proposed | 97.5% | 97.5% | +0.0 pp | [0.0, 0.0] pp |
| 2K | Sliding Window | 82.5% | 72.5% | +10.0 pp | [0.0, 22.5] pp |
| 2K | Vector Top-k | 87.5% | 87.5% | +0.0 pp | [0.0, 0.0] pp |
| 2K | Utility-per-Token | 87.5% | 87.5% | +0.0 pp | [0.0, 0.0] pp |

2K의 Sliding만 전체 성공률이 10pp 달랐다. 고유 과제 5개는 H10에서 성공하고 H50에서 실패했고, 승인 전 과제 1개는 반대로 H10에서 실패해 순차이가 4/40 과제다. 총 12개 실행의 성공 여부가 달랐으며 같은 과제의 두 반복을 별개의 개선 사례로 세지 않는다.

Sliding의 과거 조회는 H10 12/20(60%) 대 H50 2/20(10%)로 개선됐다. 반면 승인 전 과제는 H10 14/20(70%) 대 H50 16/20(80%)이고 적용 제약 위반률도 15% 대 10%로 악화됐다. 따라서 Horizon이 짧아지면 모든 행동 신뢰성 지표가 좋아진다고 주장할 수 없다.

전체 성공률 차이 +10pp의 bootstrap 구간은 [0, 22.5]pp로 0에 닿는다. 관측 경향은 있으나 확정적인 Horizon 효과라고 단정하지 않는다. 다른 전략과 1K·4K·8K의 작업 성공률은 두 Horizon에서 같다. H100은 아직 없다.

## H10에서 Proposed−Baseline 비교

| Budget | Baseline | 차이 | 95% sample-cluster bootstrap CI |
|---|---|---:|---|
| 2048 | Sliding Window | +15.0 pp | [5.0, 27.5] pp |
| 2048 | Vector Top-k | +10.0 pp | [0.0, 22.5] pp |
| 2048 | Utility-per-Token | +10.0 pp | [0.0, 22.5] pp |
| 4096 | Sliding Window | -2.5 pp | [-7.5, 0.0] pp |
| 4096 | Vector Top-k | +10.0 pp | [0.0, 22.5] pp |
| 4096 | Utility-per-Token | +10.0 pp | [-2.5, 22.5] pp |
| 8192 | Sliding Window | -2.5 pp | [-7.5, 0.0] pp |
| 8192 | Vector Top-k | +10.0 pp | [0.0, 22.5] pp |
| 8192 | Utility-per-Token | +10.0 pp | [0.0, 22.5] pp |

2K의 Proposed−Sliding은 +15pp, CI [5, 27.5]pp로 이 비교의 구간은 0을 포함하지 않는다. Vector/Utility 대비 +10pp는 구간이 0에 닿고, 4K·8K의 Sliding 대비 −2.5pp도 구간이 0에 닿는다. 여러 비교에 대한 다중검정 보정은 하지 않은 탐색적 해석이다.

## 토큰·관측 비용

| Budget | 전략 | live run당 누적 입력 평균 | 완료 80회 생성 비용 추정 USD |
|---|---|---:|---:|
| 2048 | Proposed | 2,899.1 | $0.124749 |
| 2048 | Sliding Window | 3,632.9 | $0.153830 |
| 2048 | Vector Top-k | 3,397.3 | $0.166839 |
| 2048 | Utility-per-Token | 3,355.7 | $0.156247 |
| 4096 | Proposed | 2,899.1 | $0.124749 |
| 4096 | Sliding Window | 4,858.5 | $0.194793 |
| 4096 | Vector Top-k | 3,397.3 | $0.166839 |
| 4096 | Utility-per-Token | 3,355.7 | $0.156247 |
| 8192 | Proposed | 2,899.1 | $0.124749 |
| 8192 | Sliding Window | 4,858.5 | $0.194793 |
| 8192 | Vector Top-k | 3,397.3 | $0.166839 |
| 8192 | Utility-per-Token | 3,355.7 | $0.156247 |

2차 완료 run의 기록된 생성 비용 추정 합계는 $2.175945, 1·2차 합계는 $4.358745다. 저장 run의 usage가 완전하더라도 중단 직전 미저장 호출·응답 없는 실패 시도·재개로 반복한 호출과 CountTokens 관련 비용은 이 합계에 완전히 포함되지 않는다. 실제 전체 청구액으로 보고하지 않는다.

1·2차 모두 높은 예산에서 Proposed가 Sliding보다 입력 token과 관측 완료-run 비용은 낮지만 성공률도 2.5pp 낮았다. 동일 성능의 비용 절감으로 표현하지 않는다. 네트워크·실행 시각·일시 중단이 달라 latency의 단순 순위는 주요 성능 근거로 사용하지 않는다.

## 현재까지의 연구 결론과 남은 범위

- 8/12셀, 2,560/3,840회가 완료됐다. H100 네 셀 1,280회가 남아 있으므로 전체 Main 결과가 완성된 것은 아니다.
- 두 Horizon 모두 2K에서는 Proposed의 필수 상태·제약 보존 이점이 관측되고, 4K·8K에서는 Sliding이 최고 성공률이다.
- H10/H50의 전체 성공률 차이는 주로 2K Sliding에 나타났지만 승인 제약 지표와 방향이 같지 않다. H100까지 같은 freeze로 관측한 뒤 RQ2의 전체 추세를 판단해야 한다.
- attachment 누락·반복 호출·검색 후보 한계는 실제 실패로 보존한다. 분석을 위해 평가 기준을 바꾸거나 실패 결과를 재실행해 교체하지 않았다.

## 산출물

- 2차 원본: `experiments/results/main_phase2_h10_20261109_1332/`.
- `analysis/analysis.json`: H10 무결성·성능·Task·토큰·비용 집계.
- `analysis/horizon_comparison.json`: H10/H50 입력·정답 일치 검증, 짝지은 차이와 변경된 12개 실행.
- `analysis/failure_cases.json`: 2차 실패 400개 실행의 정답·실제 행동·선택 Memory.
- [1차 H50 상세 분석](main-phase1-h50-analysis-2026-11-09.md).
