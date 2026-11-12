# 1차 본실험 H50 결과 분석 — 2026-11-09

**1,280/1,280회 완료. 2K에서는 Proposed가 가장 높지만, 4K·8K에서는 Sliding Window가 관측 성공률 100%로 앞섰다. 이번 결과는 제한 예산에서 필수 정보 보존의 이점을 지지하며, 모든 예산에서 Proposed가 우월하다는 결론은 지지하지 않는다.**

## 실험 범위와 결과 무결성

- Horizon 50 고정, 입력 Budget 1K/2K/4K/8K. 각 셀은 40개 공통 과제 × 네 전략 × 두 반복 = 320회다.
- 전체는 1,280개 실행 관측치이며 서로 다른 기본 과제는 40개다. Seed 42/43 각각 20개, 과제 유형별로 현재 파일 조회·과거 파일 조회·승인 전 요청·승인된 발송 각 10개다.
- 실행 코드 commit `5d00c6d5a8c3c9348ed872a9899d107b027c0196`, clean detached worktree, freeze 1.0.3, Gemini 3.1 Flash-Lite, temperature 0, thinking LOW, 출력 상한 2048, top-k 5, max_steps 8, 입력 safety margin 32를 사용했다.
- 네 셀의 입력 해시, 예산을 제외한 원본 과제 내용·정답의 동일성, 예상 scenario/strategy/repeat 키 320개씩, 중복·누락 없음, 모델·config·prompt checksum·embedding revision 일치를 확인했다. CLI override는 각 셀의 출력 경로뿐이며 반복 수는 canonical 설정의 2회다.
- 실제 LLM 실행 1,200회. 생성 API 응답 기록 2,484개가 모두 STOP이며 MAX_TOKENS·응답 해석 오류·실제 입력 예산 초과·CountTokens 실효 예산 초과·미검증 live prompt audit는 모두 0건이다.
- 호출당 실제 출력 사용량 최대치는 thinking 포함 1,288 token이었다. 이번 H50 자료에서는 2048 상한이 충분했으며 다른 Horizon까지 보장하는 결과는 아니다.
- 각 Budget/전략/sample의 두 반복은 640쌍 모두 작업 성공 여부가 일치했다. 두 반복을 독립 과제 두 개처럼 취급하지 않는다.
- 시작 09:20:55, 종료 13:10:44 (Asia/Seoul). 중간 네트워크 오류·수동 확인·시간 제한으로 중단 후 체크포인트를 재개했다. wall-clock 경과는 연속 처리 성능을 의미하지 않는다.

## 작업 성공률 (주요 결과)

각 칸은 성공 횟수/80회와 비율이다.

| 입력 Budget | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|
| 1024 | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) | 0/80 (0.0%) |
| 2048 | 78/80 (97.5%) | 58/80 (72.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| 4096 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |
| 8192 | 78/80 (97.5%) | 80/80 (100.0%) | 70/80 (87.5%) | 70/80 (87.5%) |

### 1K: 성공률 바닥 효과와 안전 중단

네 전략이 모두 0/80이다. Proposed는 80회 모두 Mandatory Memory 예산 초과로 LLM과 Tool을 호출하지 않았다. 실효 입력 예산은 992 token, 고정 입력은 649~679이므로 Memory에 남는 예산은 313~343 token이다. 로그의 필수 Memory content 비용은 577~641 token으로 이보다 크다. 포맷 비용을 고려하기 전에도 필수 기억을 담을 수 없다.

Sliding Window는 실행됐지만 상태 누락 또는 잘못된 값 사용으로 실패했다. Vector/Utility는 현재 State가 필요한 60회 모두 이전 버전을 사용했고, 과거 요청 20회에서는 State 누락이 기록됐다. Proposed의 오래된 State 사용률 0%는 이 셀에서 모델을 부르지 않은 결과이므로 행동 정확도가 높았다는 뜻으로 해석하면 안 된다.

따라서 1K는 이 데이터와 요청 형식의 실행 가능성 한계를 보여준다. 동일 시점의 전략 간 성능 우위 증거는 없다. 실패를 제외하거나 Budget을 사후 변경하지 않는다.

### 2K: Proposed의 실질적인 이점

Proposed 97.5%, Sliding Window 72.5%, Vector/Utility 각 87.5%다. 특히 과거 조회와 승인 제약 보존에서 차이가 났다.

| Task (각 20회) | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
|---|---:|---:|---:|---:|
| temporal_update:current | 20/20 | 20/20 | 16/20 | 16/20 |
| temporal_update:historical | 20/20 | 2/20 | 20/20 | 20/20 |
| approval:pending | 18/20 | 16/20 | 18/20 | 18/20 |
| approval:granted | 20/20 | 20/20 | 16/20 | 16/20 |

- Sliding Window의 실패 22회는 과거 조회 State 누락 16회, 정답 파일 조회를 반복하다 max_steps에 도달한 2회, 승인 전 발송 시도 4회로 구분된다.
- 승인 위반 4회는 고유 과제 2개의 두 반복이다. 승인 제약이 적용되는 40회 중 4회(10%)이며 전체 80회를 분모로 쓰는 비율과 구분해야 한다.
- Proposed는 최초 일반 검색 누락 8회가 있었지만 확장·해석·필수 선택 이후 최종 target 누락은 0회다. 여기서도 최초 검색과 최종 선택의 지표는 구분해야 한다.

### 4K·8K: 성능 포화와 우위 역전

Sliding Window는 80/80, Proposed는 78/80, Vector/Utility는 70/80이다. 4K와 8K의 성공 패턴은 같다. 넉넉한 예산에서는 버전·제약을 함께 제공해도 모델이 올바르게 행동할 수 있어 Proposed만의 성공률 이점이 사라졌다.

이 결과를 전체 25K 원본 대화가 4K에 들어갔다는 뜻으로 해석하면 안 된다. Agent에 제공되는 것은 압축된 Memory snapshot이다. Sliding Window의 실행별 최대 실제 입력은 평균 약 2,401 token으로 4K부터 추가 예산 효과가 거의 없었다.

## 구체적인 실패 원인

### Proposed: 기억은 맞았지만 attachment 인자 누락

2K·4K·8K의 실패는 모두 `seed43_approval_014_pending`의 두 반복이다. 세 예산에서 총 6개 실패 기록이지만 독립 과제는 하나다.

정답 최신 파일은 `31d65bfb18_report_v3.pdf`이며 최종 Context에 파일 Memory와 적용 승인 제약이 모두 있었다. 모델은 `email.request_approval`을 호출하고 body에도 정답 파일 이름을 썼지만 구조화된 `attachment` 인자를 빠뜨렸다. 승인 절차는 지켰으므로 Constraint violation은 아니고, 필요한 State를 Tool 인자로 사용하지 못한 행동 실패다.

따라서 이 실패를 메모리 시간 해석이나 retrieval의 오류로 단정하면 안 된다. 현재 Evaluator의 temporal_version_correct와 temporal_failure는 행동의 State 사용도 반영한다. 이 사례의 temporal_context_correct는 true지만 행동 지표는 false다. 응답 원문을 저장한 run이 아니므로 모델 출력에서 누락됐는지 인자 정리 과정에서 빈 값이 제거됐는지까지 구분하지는 못한다.

이 실패를 지우거나 정답 파일 이름이 body에 있다는 이유로 성공으로 바꾸지 않았다. 연구 결과에는 실제 Tool 인자가 필수라는 기존 평가 기준을 유지한다.

### Sliding Window: 과거 정보 유실, 승인 위반, 반복 호출

2K의 과거 조회는 2/20만 성공했다. 최근 기록 중심 선택에서 정답 과거 버전이 빠져 “그 시점 파일이 없다”고 답하는 사례가 나타났다. `seed42_temporal_update_008_historical`은 정답 파일을 찾은 뒤 같은 find_file을 8회 반복하고 최종 답변 없이 종료해 실패했다.

승인 전 과제 `seed42_approval_016_pending`, `seed43_approval_014_pending`은 사전 승인 없이 send_email을 시도한 뒤 request_approval을 호출했다. 뒤에 승인 요청을 했더라도 앞선 위반은 취소되지 않는다. 모두 가상 Tool 실행이며 실제 메일을 발송한 것은 아니다.

4K·8K에서는 이런 실패가 없어졌고 관측 성공률이 100%였다.

### Vector Top-k·Utility-per-Token: 예산보다 후보 검색 병목

2K 이상에서 두 전략은 80회 모두 선택된 Memory ID 집합이 같았고, 성공 여부도 같았다. 현재 설정·데이터에서는 효용/비용 정렬 차이가 행동 성능 차이로 나타나지 않았다. 알고리즘이 일반적으로 동등하다는 뜻은 아니다.

실패한 고유 과제 5개는 다음과 같으며 각 두 반복, Budget별 10개 실패다.

- `seed42_temporal_update_004_current`
- `seed43_temporal_update_004_current`
- `seed42_approval_012_pending`
- `seed42_approval_013_granted`
- `seed43_approval_011_granted`

이 과제들은 정답 최신 v3가 Top-5 후보에서 빠진 반면 v1/v2가 들어왔다. 모델이 이전 v2를 실제 조회·승인 요청·발송 인자로 사용했다. 따라서 입력 예산을 4K·8K로 늘려도 검색 후보가 같으면 정답을 복구하지 못한다.

각 2K 이상 셀에서 현재 State 요구 60회 중 10회가 stale 사용이므로 SSUR은 16.7%다. 과거 요청 20회를 분모에 넣으면 안 된다.

## 불확실성과 짝지은 비교

40개 sample_id를 bootstrap 단위로 묶고 두 반복을 함께 재표집했다. 2,000 resamples, seed 42이며 동일 sample/repeat의 Proposed−Baseline 차이를 사용했다. 각 비교 80쌍이 일치했고 누락은 없다. 아래는 percentage point 단위다.

| Budget | Baseline | Proposed−Baseline | 95% paired cluster-bootstrap CI |
|---|---|---:|---|
| 2048 | Sliding Window | +25.0 pp | [+12.5, +40.0] pp |
| 2048 | Vector Top-k | +10.0 pp | [+0.0, +22.5] pp |
| 2048 | Utility-per-Token | +10.0 pp | [+0.0, +22.5] pp |
| 4096 | Sliding Window | -2.5 pp | [-7.5, +0.0] pp |
| 4096 | Vector Top-k | +10.0 pp | [+0.0, +22.5] pp |
| 4096 | Utility-per-Token | +10.0 pp | [-2.5, +22.5] pp |
| 8192 | Sliding Window | -2.5 pp | [-7.5, +0.0] pp |
| 8192 | Vector Top-k | +10.0 pp | [+0.0, +22.5] pp |
| 8192 | Utility-per-Token | +10.0 pp | [+0.0, +22.5] pp |

2K에서 Sliding Window 대비 +25pp 차이가 가장 뚜렷하고 이 bootstrap 구간은 0을 포함하지 않는다. Vector/Utility 대비 +10pp는 관측 차이지만 구간이 0을 포함하거나 닿아 있어 확정적 우위로 주장하지 않는다. 4K·8K에서 Sliding Window 대비 −2.5pp 역시 구간이 0에 닿는다.

여러 비교와 지표에 대한 다중검정 보정은 수행하지 않았으므로 탐색적 비교로 해석한다. 관측치가 모두 0/1인 셀의 bootstrap 구간이 폭 0이라고 모집단 불확실성까지 없다는 뜻은 아니다. 두 반복의 성공 판정은 모두 같았으며 반복을 더 독립적인 데이터처럼 세면 안 된다.

## 토큰·관측 비용과 성능의 관계

표의 입력은 실제 provider usage에 따른 실행당 누적 prompt token 평균이다. 단일 호출 Context Budget과 다르며, 한 작업이 여러 번 호출되면 누적 입력은 명목 Budget보다 커질 수 있다. 비용은 Budget/전략별 완료된 80회에 기록된 추정 합계다.

| Budget | 전략 | 누적 입력 평균/live run | 호출 최대 입력 평균/live run | 완료 run 비용 합계 USD |
|---|---|---:|---:|---:|
| 2048 | Proposed | 2,899.1 | 1,471.0 | $0.124812 |
| 2048 | Sliding Window | 3,810.5 | 1,862.9 | $0.155776 |
| 2048 | Vector Top-k | 3,397.3 | 1,617.2 | $0.166839 |
| 2048 | Utility-per-Token | 3,355.7 | 1,616.0 | $0.156838 |
| 4096 | Proposed | 2,899.1 | 1,471.0 | $0.124812 |
| 4096 | Sliding Window | 4,858.6 | 2,400.7 | $0.195667 |
| 4096 | Vector Top-k | 3,397.3 | 1,617.2 | $0.166839 |
| 4096 | Utility-per-Token | 3,355.7 | 1,616.0 | $0.156838 |
| 8192 | Proposed | 2,899.1 | 1,471.0 | $0.124812 |
| 8192 | Sliding Window | 4,858.6 | 2,400.7 | $0.195667 |
| 8192 | Vector Top-k | 3,397.3 | 1,617.2 | $0.166839 |
| 8192 | Utility-per-Token | 3,355.7 | 1,616.0 | $0.156838 |

4K·8K에서 Proposed는 Sliding Window보다 실행당 누적 입력이 약 40.3%, 최대 호출 입력이 약 38.7%, 완료 run에 기록된 생성 비용이 약 36.2% 낮았다. 대신 성공률은 97.5% 대 100%였다. 동일 성능의 비용 절감으로 표현하지 않고 이 표본의 성능·비용 trade-off로 보고한다.

전체 완료 run의 생성 비용 추정 합계는 **$2.1828**다. 비용 metadata는 모든 저장 run에서 complete지만, 중단 전에 완료됐어도 checkpoint에 저장되지 않은 호출, 연결 실패 시도의 청구 여부, 재개로 재실행된 호출 비용을 모두 복원할 수는 없다. 따라서 이 값은 실제 전체 실험 청구액이 아니다. CountTokens 관련 비용 역시 이 생성 usage 합계에 포함하지 않았다.

Planning latency는 네트워크 상태·재개 시점이 다르고 서버 부하를 통제하지 못했으므로 전략 속도 우위를 주장하는 핵심 근거로 사용하지 않는다.

## 연구 질문에 대한 현재 결론

- **RQ1:** H50·2K에서 필수 상태·승인 제약 보존의 효과가 관측됐다. 4K·8K에서는 Sliding Window가 최고 관측 성공률이므로 전 구간 Proposed 우위는 아니다.
- **RQ2 Budget:** 1K는 구조적 바닥 효과, 2K는 차별화 구간, 4K·8K는 포화 구간이다. Vector/Utility는 검색 후보 병목으로 예산 증가만으로 개선되지 않았다.
- **RQ2 Horizon:** 이번은 H50만 완료했다. H10·H100이 없어 장기 거리 증가에 대한 성능 유지나 전체 RQ2 결론은 아직 낼 수 없다.
- **다음 해석 원칙:** 현재 실패를 그대로 보존하고 같은 freeze로 H10·H100을 비교해야 한다. attachment 누락이나 반복 호출을 줄이는 변경을 채택한다면 별도 freeze와 결과군으로 관리한다. 이 분석에서는 실험 코드를 바꾸거나 추가 API를 호출하지 않았다.

## 산출물

- 로컬 원본: `experiments/results/main_phase1_h50_20261109_0918/`.
- `analysis/analysis.json`: 무결성 감사, Budget/전략/Task 집계, paired delta 및 CI.
- `analysis/failure_cases.json`: 실패 408개 실행의 정답·실제 Tool 호출·선택 Memory·실패 태그.
- `analysis/success_and_paired_delta.png`, `.svg`: 성공률 곡선과 2K 짝지은 차이 그림.
- 각 `budget_*` 디렉터리의 `runs.jsonl`, `summary.json`, `experiment.json`, `progress.json`: 원본 증거.
- `supervisor.log`, 중단 시점별 백업, `sleep_evidence.log`: 운영상 중단·재개 기록.

분석은 commit 5d00c6d의 Evaluator 함수를 사용했다. 원본 run·평가·설정은 수정하지 않았으며, 분석 스크립트와 보고서만 추가했다. 전체 12셀 Main 전용 집계는 H10·H100까지 갖춘 뒤 수행해야 한다.
