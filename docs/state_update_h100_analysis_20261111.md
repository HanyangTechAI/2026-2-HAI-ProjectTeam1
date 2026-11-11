# H100 고정 State Update 확장 결과 분석 — 2026-11-11

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

**추가 요청한 두 셀, 640/640회가 완료됐다.** H100에서도 Update 5·10에서 Vector/Utility의 0%가 재현됐고 Proposed는 100% 성공했다. 따라서 앞서 본 큰 차이는 H1000에서만 발생한 장거리 현상이 아니라, 이 데이터에서 버전 수가 늘 때 나타나는 검색 후보 병목과 버전 복원 경로의 차이로 해석하는 편이 타당하다.

## 1. H100을 고정한 최종 결과

| State Update | State 버전 수 | Proposed | Sliding Window | Vector Top-k | Utility-per-Token |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 2 — 기존 기준 | 3 | 78/80 (97.5%) | 58/80 (72.5%) | 70/80 (87.5%) | 70/80 (87.5%) |
| **5 — 신규** | 6 | **80/80 (100%)** | 56/80 (70.0%) | 0/80 (0%) | 0/80 (0%) |
| **10 — 신규** | 11 | **80/80 (100%)** | 54/80 (67.5%) | 0/80 (0%) | 0/80 (0%) |

각 셀은 동일 기본 과제 40개 × 네 전략 × 두 반복이다. 입력 예산 2048, History 목표 128,000, seed 42/43, mode 1, Top-k 5, 모델·프롬프트·평가 기준을 유지했다. Update 2와 기존 H1000 결과는 재실행하지 않았다. 기존 1,280회에 신규 640회를 더해 총 **6개 고유 셀, 1,920개 실행 키**를 분석했다.

Proposed의 +2.5pp는 기존 `seed43_approval_014_pending` 하나의 두 반복에서 attachment 누락이 사라진 차이다. Update 5 또는 10 − Update 2의 paired 95% CI는 [0, +7.5]pp다. 최신 파일명과 선택된 문맥도 바뀌므로 업데이트가 많을수록 더 잘한다는 증거로 해석하지 않는다.

Sliding의 Update 5 − Update 2는 −2.5pp, CI [−15, +7.5]pp, Update 10 − Update 2는 −5pp, CI [−15, +5]pp다. 관측 성공률은 낮아졌지만 두 구간 모두 0을 포함한다. Vector/Utility는 Update 5·10 모두 −87.5pp였다. 해당 CI는 Vector [−97.5, −77.5]pp, Utility [−97.5, −75]pp다. bootstrap 재표집 순서에 따라 같은 관측 차이의 끝점은 소폭 달라질 수 있다.

## 2. 정답 기억이 후보에서 빠지는 현상이 H100에서도 재현됨

첫 step에서 정답 State 한 개의 포함 여부를 비교했다. 초기 hit는 Top-5에 있었는지, 컨텍스트 포함은 후보 확장·선택·형식 예산 검증 후 모델에 전달됐는지를 뜻한다.

| H100·Update | Proposed 초기 hit | Proposed 컨텍스트 포함 | Vector 초기 hit / 컨텍스트 | Utility 초기 hit / 컨텍스트 |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 72/80 | **80/80** | 70/80 | 70/80 |
| 5 | 2/80 | **80/80** | 2/80 | 2/80 |
| 10 | 0/80 | **80/80** | 0/80 | 0/80 |

6개·11개 버전이 저장돼도 초기 검색은 5개로 고정했다. Proposed도 초기에는 대부분 정답을 놓쳤지만 version chain 확장과 시점 해석을 통해 정답을 복원했다. Vector/Utility는 같은 복원 경로가 없어 정답 후보 누락을 회복하지 못했다. Update 5에서 정답이 처음부터 있었던 `seed42_approval_014_pending`의 두 반복도 승인 위반·반복 호출·이후 stale 사용으로 실패했다. 따라서 모든 실패를 검색 누락 한 원인만으로 설명하지는 않는다.

현재 State를 요구하는 60회에서 Vector/Utility의 stale 사용률은 Update 2에서 10/60(16.7%), Update 5에서 60/60(100%), Update 10에서 58/60(96.7%)였다. 두 Baseline의 승인 제약 위반은 각각 0/40 → 16/40 → 12/40이었다. Proposed는 세 조건 모두 stale 사용 0/60, 승인 위반 0/40이었다.

Sliding은 stale 사용 자체는 0/60이지만 과거 상태 누락과 승인 행동 실패가 있었다. 각 유형의 성공 분모는 20회다.

| H100·Update | 현재 조회 | 과거 조회 | 승인 대기 | 승인 완료 발송 |
| ---: | ---: | ---: | ---: | ---: |
| 2 | 20/20 | 2/20 | 16/20 | 20/20 |
| 5 | 20/20 | 2/20 | 14/20 | 20/20 |
| 10 | 20/20 | 0/20 | 14/20 | 20/20 |

## 3. H100과 H1000 비교: 큰 차이는 없었음

| Update | Proposed H100 / H1000 | Sliding H100 / H1000 | Vector·Utility H100 / H1000 |
| ---: | ---: | ---: | ---: |
| 2 | 97.5 / 97.5% | 72.5 / 75.0% | 87.5 / 87.5% |
| 5 | 100 / 100% | 70.0 / 67.5% | 0 / 0% |
| 10 | 100 / 100% | 67.5 / 67.5% | 0 / 0% |

Proposed·Vector·Utility는 세 Update 조건 모두 각 80쌍의 성공·실패 판정이 H100/H1000에서 동일했다. Sliding만 Update 2에서 한 기본 과제의 두 반복이 H1000에서 개선됐고(+2.5pp, CI [0, +7.5]), Update 5에서는 다른 한 과제가 악화됐다(−2.5pp, CI [−7.5, 0]). Update 10은 판정도 전부 같았다. 방향이 일정하지 않고 구간이 0에 닿아 뚜렷한 Horizon 효과로 주장하지 않는다.

실제 첫 Context 문자열도 SHA-256으로 비교했다. Proposed는 Update 5·10의 **80/80쌍이 완전히 같았다**. Vector는 모든 Update에서 80/80쌍이 같았다. 따라서 같은 첫 입력에 동일 성공 판정이 나왔다는 점을 고려해야 한다. 이 비교는 원본 이력을 1,000번 온라인 처리하거나 LLM에 128K 이력을 직접 읽힌 실험이 아니다. turn 간격은 Selector의 메타데이터에 반영되지만, 선택된 문맥이 같으면 모델이 보는 입력도 같아진다. 이번 결과로 장기간 온라인 기억 추출·갱신의 안정성을 입증하지 않는다.

## 4. 데이터·해석상의 제한

정답 State에는 약 548~641 token의 긴 패딩이 있고 다른 State는 약 81~114 token이다. 현재 설계에서는 버전 수 증가와 짧은 비정답 후보 수 증가가 함께 일어나며, 정답 쪽의 길이·표현 차이가 검색 순위를 낮췄을 가능성을 분리하지 못했다. **H100에서도 같은 현상이 재현됐다는 점은 확인했지만, 일반적인 Vector 검색이 업데이트 5회부터 0%가 된다는 결론은 아니다.** 길이를 맞춘 버전 데이터나 별도 Top-k 조건 검증 없이 보편적인 한계로 확대하지 않는다.

History는 같은 128,000 token 목표의 ±1%로 생성했다. 신규 H100·Update 5는 128,934~128,943 token, Update 10은 128,930~128,938 token이었다. 기존 H1000과 실제 token 수가 완전히 같은 것은 아니지만 query·Memory ID/content·정답은 모든 샘플에서 짝지어지는 것을 실제 생성 gate에서 검증했다. 반복 배경 mode 1과 40개 합성 기본 과제에 한정된 결과이며, 100%와 그 표본 bootstrap 구간은 모집단의 실패 확률 0을 뜻하지 않는다.

## 5. 검증과 산출물

- 신규 640회 및 결합 1,920개 고유 실행 키를 확인했다. 기존 4셀의 원본 해시는 유지됐으며 재실행으로 결과를 교체하지 않았다.
- 신규 Pipeline 상태는 640회 모두 `ok`, 종료 사유는 `completed` 636회와 `max_steps` 4회다. `ok`나 `completed`는 평가상 작업 성공과 다르다.
- 신규 Prompt audit 640/640회 verified, 실제 입력 Budget 초과와 Mandatory overflow는 0건이다. 결합 1,920회도 모두 audit verified이며 두 초과 지표는 0이다.
- 비용 회계는 신규 640회 모두 complete다. 신규 LLM API 1,328회의 추정 비용은 **$1.230047**, 기존 실험과 합친 LLM 추정 비용은 **$3.693720**이다. 생성 CountTokens 등 기타 비용을 포함한 청구 총액은 아니다.
- 동일 프롬프트 checksum과 임베딩 revision을 확인했다. 각 raw run의 query·History·Memory snapshot이 해당 입력과 일치했다.
- 신뢰구간은 기본 과제 40개를 군집으로 두 반복을 함께 bootstrap했다(2,000회, seed 42). Horizon·Update paired 비교는 같은 sample·repeat 80쌍이다. 다중 비교 보정은 하지 않은 탐색적 분석이다. 셀 두 개를 병렬 실행했으므로 지연시간을 엄격한 우위 근거로 쓰지 않는다.

재현은 `python -m experiments.long_horizon.analyze_h100`과 `python -m experiments.long_horizon.context_pairing`으로 수행한다. 원본 신규 결과는 `experiments/results/state_update_h100_v104/`에 보존했다. 소형 artifact는 6셀 집계 (별도 보관: `docs/results/state_update_h100_20261111/summary.json`), 검색·선택 진단 (별도 보관: `docs/results/state_update_h100_20261111/diagnostics.json`), Horizon 차이 (별도 보관: `docs/results/state_update_h100_20261111/horizon_deltas.json`), Update 차이 (별도 보관: `docs/results/state_update_h100_20261111/update_deltas.json`), 유형별 결과 (별도 보관: `docs/results/state_update_h100_20261111/subgroups.json`), 첫 Context 문자열 비교 (별도 보관: `docs/results/state_update_h100_20261111/first_context_pairing.json`), 실패 사례 (별도 보관: `docs/results/state_update_h100_20261111/failures.json`), 감사·해시 (별도 보관: `docs/results/state_update_h100_20261111/validation.json`)와 SVG 그래프 (별도 보관: `docs/results/state_update_h100_20261111/h100_extension_results.svg`)에 있다.
