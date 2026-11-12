# Long Horizon / State Update 확장 결과 분석 — 2026-11-11

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

**고유 4셀, 1,280/1,280회 실행·검증을 완료했다.** 이번 입력에서는 H100에서 H1000으로 거리를 늘린 효과보다 State Update를 2에서 5·10으로 늘렸을 때의 전략 차이가 훨씬 컸다. Proposed는 97.5~100% 성공했고, Vector/Utility는 Update 5·10에서 모두 0%였다. 핵심 차이는 초기 Top-5에 거의 없는 정답 버전을 Proposed가 version chain으로 복원한 것이다. 다만 고정된 Top-k, 정답 기억에 집중된 긴 패딩, 합성 snapshot 방식이 이 차이를 키웠을 가능성을 함께 고려해야 한다.

## 1. 최종 성공률

각 값의 분모는 동일 기본 과제 40개 × 두 반복 = 80회다. H1000·Update 2의 320회 결과는 두 분석에서 같은 기준 셀로 참조하고 중복 집계하지 않았다.

| Horizon | State Update | State 버전 수 | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 2 | 3 | 78/80 **97.5%** | 58/80 72.5% | 70/80 87.5% | 70/80 87.5% |
| 1000 | 2 | 3 | 78/80 **97.5%** | 60/80 75.0% | 70/80 87.5% | 70/80 87.5% |
| 1000 | 5 | 6 | 80/80 **100.0%** | 54/80 67.5% | 0/80 0.0% | 0/80 0.0% |
| 1000 | 10 | 11 | 80/80 **100.0%** | 54/80 67.5% | 0/80 0.0% | 0/80 0.0% |

2K 입력 예산, History 128,000 token 목표, seed 42/43, 생성 mode 1, 제약 3, noise ratio 0.5, Top-k 5, 동일 모델·프롬프트·최대 8 step을 사용했다. 현재 조회·과거 조회·승인 대기·승인 완료는 셀당 각각 기본 과제 10개이며, 전략별 각 유형 실행은 20회다.

## 2. Horizon만 늘렸을 때

Update 2를 고정하면 Proposed·Vector·Utility의 **80쌍 모두 성공 판정이 동일**했다. Proposed는 H1000에서도 관측 성공률을 유지했다. Sliding의 +2.5pp는 `seed42_approval_016_pending` 하나가 두 반복에서 실패→성공한 차이며, paired 95% CI는 [0, +7.5]pp다. 일반적인 Horizon 증가 효과나 Sliding 개선으로 단정하지 않는다.

이 실험의 H는 저장된 필수 기억과 현재 과제의 turn 간격이다. Runner는 원본 History 128K 전체를 LLM에 전달하거나 1,000회의 실제 Agent 행동을 수행하지 않는다. `memory_snapshot`을 복원한 뒤 같은 selector와 Context Builder를 사용한다. 따라서 이 결과는 **합성 snapshot의 긴 turn 간격에서도 선택·행동이 유지된 관측**이며, 장기간 온라인 기억 추출·갱신·누적 오류에 대한 검증은 아니다.

Horizon 두 조건의 query·Memory ID/content·정답이 같은 것을 검증했다. 원본 History의 token 목표는 같지만 실측 길이는 완전히 같지는 않다.

| 셀 | 실제 History token 범위 |
| --- | ---: |
| H100·Update 2 | 128,942~128,946 |
| H1000·Update 2 | 127,012~127,040 |
| H1000·Update 5 | 126,880~127,060 |
| H1000·Update 10 | 126,896~127,038 |

모두 공통 128,000 목표의 ±1% 범위 안이다. 비교는 동일한 실제 token 수가 아니라 동일한 목표·허용 오차에서 이뤄졌다.

## 3. Update가 늘 때 왜 Vector/Utility가 실패했나

업데이트 2/5/10회는 초기 State를 포함해 3/6/11개 버전이다. Top-k는 5로 유지했다. 아래는 첫 step에서 **정답 State 한 개**가 검색 후보 및 컨텍스트에 있었는지를 센 결과다. Constraint나 승인 grant까지 모두 맞았다는 지표는 아니다.

| H1000 조건 | Proposed 초기 검색 hit | Proposed 컨텍스트에 정답 State | Vector 초기 hit / 컨텍스트 | Utility 초기 hit / 컨텍스트 |
| --- | ---: | ---: | ---: | ---: |
| Update 2 | 72/80 | **80/80** | 70/80 | 70/80 |
| Update 5 | 2/80 | **80/80** | 2/80 | 2/80 |
| Update 10 | 0/80 | **80/80** | 0/80 | 0/80 |

Proposed 역시 Update 5·10에서 초기 검색은 거의 실패했다. 그러나 같은 `memory_key`의 version chain을 확장하고 시점에 맞는 버전을 선택해 필수 State로 보존했다. Vector/Utility에는 이 복원 경로가 없어 초기 후보 밖의 정답을 얻지 못했다. 두 Baseline의 성공률 하락(Update 5 또는 10 − Update 2)은 **−87.5pp, 95% CI [−97.5, −77.5]pp**였다.

Update 5의 예외적인 초기 hit 2회도 성공하지 못했다. `seed42_approval_014_pending`의 두 반복에서 정답 v6는 있었지만 승인 제약이 없는 채 발송을 시도했고, 이후 승인 요청·파일 조회를 반복하면서 v5/v4 같은 stale State를 사용했다. 따라서 0% 전체를 검색 누락 하나만으로 설명하지 않는다. Update 10에서는 80/80회 모두 첫 컨텍스트에 정답 State가 없었다.

현재 State를 요구하는 실행 60회를 분모로 한 stale 사용률은 다음과 같다. 과거 요청 20회는 이 분모에서 제외한다.

| H1000·Update | Proposed | Sliding | Vector | Utility |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 0/60 | 0/60 | 10/60 (16.7%) | 10/60 (16.7%) |
| 5 | 0/60 | 0/60 | 60/60 (100%) | 60/60 (100%) |
| 10 | 0/60 | 0/60 | 58/60 (96.7%) | 58/60 (96.7%) |

Sliding의 stale 사용 0%가 높은 성공률을 뜻하지는 않는다. 과거 버전 누락이나 승인 제약·행동 인자 오류로도 실패했다. 적용 승인 과제 40회를 분모로 한 위반률은 Proposed가 모든 셀에서 0/40이었고, H1000의 Update 2/5/10에서 Sliding은 2/8/6회, Vector와 Utility는 각각 0/16/12회였다.

## 4. Proposed 97.5% → 100%를 해석하는 방법

Proposed의 Update 2 실패는 H100과 H1000 모두 `seed43_approval_014_pending` 하나의 두 반복이었다. 올바른 보고서가 컨텍스트에 있었지만 `request_approval`의 attachment가 누락됐다. Update 5·10에서는 같은 기본 과제의 올바른 attachment를 채워 성공했다.

그 결과 paired 차이는 +2.5pp지만 95% CI가 [0, +7.5]pp로 0에 닿는다. **업데이트가 많을수록 성능이 좋아진다는 뜻은 아니다.** 최신 파일 이름과 선택된 Flexible 후보 구성이 달라져 모델에 보이는 문맥도 달라졌다. Proposed의 첫 step Flexible 개수는 Update 2에서 2~3개였지만, Update 5에서 58/80회, Update 10에서 60/80회는 0개였다. 이는 후보가 구버전으로 채워졌다가 시간 해석 후 제거되는 경로의 변화다.

Sliding은 Update 2의 75%에서 Update 5·10의 67.5%로 −7.5pp였지만 CI는 두 비교 모두 [−17.5, +2.5]pp다. Update 5와 10의 전체 성공률은 같아도 성공·실패 과제는 기본 과제 6개에서 달랐다. 단순한 단조 하락이나 성능 동등성을 주장하지 않는다.

## 5. 생성 데이터의 길이 차이와 결과의 한계

Generator는 2K에서 선택 압력을 만들기 위해 **정답 Memory 쪽에 긴 패딩을 추가**한다. 이번 데이터에서 정답 State는 548~641 token(평균 약 611), 다른 State는 81~114 token(평균 약 106~109)이었다. 정답 쪽이 약 5~6배 길고 반복 배경 문장도 포함한다. Update를 늘리면 짧은 비정답 버전이 5개 또는 10개 생긴다.

이 길이·표현 차이가 embedding 순위에 영향을 줘 정답을 Top-5 밖으로 밀어냈을 가능성을 분리하지 못했다. 따라서 가장 직접적인 해석은 **이 데이터와 Top-k=5 조건에서 버전 체인 확장이 후보 검색 실패를 복구했다**는 것이다. 일반적인 모든 Vector 검색이 Update 5부터 실패한다거나, 실제 서비스에서도 0%가 된다고 일반화할 수 없다. 길이를 맞춘 버전 데이터나 별도 Top-k 확장 검증 없이 보편적 경계로 주장하지 않는다.

또한 이 실험은 세 가지 축이 제한된다. 원본 History는 반복 문장 mode 1이고, 과제는 40개 합성 기본 과제이며, 기억 추출은 실제 대화에서 수행하지 않고 snapshot으로 주어진다. 관측된 100% 및 bootstrap의 [100%, 100%] 구간은 이 표본 안의 결과이며 모집단에서 실패 가능성이 없다는 뜻이 아니다.

## 6. 검증·비용·재현

- 고유 4셀 × 320회 = **1,280개 실행 키**를 대조했다. 공통 H1000·Update 2는 한 번만 집계했다.
- 모든 저장 run의 query·History·Memory snapshot이 해당 생성 입력과 동일했다. 모든 프롬프트 checksum과 임베딩 resolved revision이 일치했다.
- Pipeline 상태는 1,280회 모두 `ok`이며, 종료 사유는 `completed` 1,268회와 `max_steps` 12회다. `ok`는 작업 성공을 뜻하지 않는다. 예산 중단이나 Mandatory overflow는 없었다.
- Prompt audit `verified` **1,280/1,280회**, 실제 입력 Budget 초과 **0건**, 최대 실제 prompt는 2,015 token이다. 안전 여유 32를 둔 effective 상한은 2,016이다.
- 비용 회계는 1,280회 모두 `complete`다. LLM API 호출 2,728회의 추정 비용 합계는 **$2.463673**이다. 입력 생성의 CountTokens 호출 3,879회와 기타 비용을 포함한 청구 총액은 아니다.
- CI는 같은 기본 과제의 두 반복을 함께 묶어 bootstrap 2,000회(seed 42)로 구했다. 셀 간 비교는 동일 sample·repeat 80쌍이다. 여러 비교에 대한 보정은 하지 않은 탐색적 분석이다.
- 실행 기준 commit은 `f0abd2e`이며 미커밋 변경이 있는 상태였다. 생성기·드라이버·계획·입력·원본 결과의 해시와 셀별 실행 환경은 artifact에 보존했다. 셀 두 개씩 병렬 실행했으므로 지연시간을 엄격한 우위 근거로 사용하지 않는다.

재현은 `python -m experiments.long_horizon.run report`와 `python -m experiments.long_horizon.analyze`로 수행한다. 분석 재생성에는 matplotlib이 필요하다. 원본 실행은 plan에 지정된 `experiments/results/long_horizon_v104/` 및 `state_update_scaling_v104/`에 있고, 소형 집계는 summary.json (별도 보관: `docs/results/long_horizon_20261111/summary.json`), 후보·선택 진단 (별도 보관: `docs/results/long_horizon_20261111/diagnostics.json`), 입력 길이 진단 (별도 보관: `docs/results/long_horizon_20261111/input_diagnostics.json`), Horizon 차이 (별도 보관: `docs/results/long_horizon_20261111/horizon_deltas.json`), Update 차이 (별도 보관: `docs/results/long_horizon_20261111/update_deltas.json`), 유형별 집계 (별도 보관: `docs/results/long_horizon_20261111/subgroups.json`), 실패 사례 (별도 보관: `docs/results/long_horizon_20261111/failures.json`), 감사·해시 (별도 보관: `docs/results/long_horizon_20261111/validation.json`), 실행 환경 (별도 보관: `docs/results/long_horizon_20261111/provenance.json`)과 SVG 그래프 (별도 보관: `docs/results/long_horizon_20261111/long_horizon_results.svg`)에 보존했다.
