# Budget Pressure Test 분석 — 2026-11-11

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

**800/800회 완료. 입력 예산을 줄일수록 Flexible memory가 탈락했고 Full Proposed는 다섯 단계 모두 78/80회(97.5%) 성공했다.** 가장 강한 압력의 Packing 제거 조건은 76/80회(95.0%)였다. 추가 실패는 같은 과제의 두 반복에서 Tool feedback이 2 token 늘어 필수 컨텍스트 조립 예산을 초과한 사례다. 이번 결과는 예산에 따라 Flexible을 먼저 제거하는 동작을 확인하지만, 작업 성공률에 대한 Packing의 일반적인 우위를 입증하지는 않는다.

## 결과와 분모

| Flexible 수용 구간 비율 ρ | Full 최종 Flexible 유지율 | Packing 제거 최종 유지율 | Full 작업 성공 | Packing 제거 작업 성공 |
| ---: | ---: | ---: | ---: | ---: |
| 100% | 100.0% | 98.8% | 78/80 (97.5%) | 78/80 (97.5%) |
| 75% | 51.7% | 40.0% | 78/80 (97.5%) | 78/80 (97.5%) |
| 50% | 15.0% | 11.7% | 78/80 (97.5%) | 78/80 (97.5%) |
| 25% | 1.7% | 1.7% | 78/80 (97.5%) | 78/80 (97.5%) |
| 0% | 0.0% | 0.0%¹ | 78/80 (97.5%) | 76/80 (95.0%) |

최종 Flexible 유지율은 마지막 실행 단계의 유효한 Context에서 유지된 Flexible 개수 / 같은 과제의 큰 예산 Flexible 후보 개수를 과제·반복별로 계산한 평균이다. ¹0% Packing 제거의 예산 중단 2회에는 마지막 Context가 없으므로 유지율에서 제외했다(n=78). 이 2회는 성공률 분모 80에 포함한다. 나머지 유지율의 분모는 모두 80회다. 미측정을 0으로 바꾸지 않았다. ρ는 전체 예산 비율이나 정확한 기억 개수 비율이 아니라, 필수 기억 경계와 전체 후보 수용 경계 사이의 보정된 예산 비율이다.

각 조건은 같은 40개 기본 과제 × 2회 반복이다. Full과 Packing 제거의 Temporal Resolution, Mandatory State, Constraint Preservation은 모두 ON이며 Token-aware Packing만 다르다. H100, seed 42/43, 원본 query·history·memory snapshot, Top-k 5, 모델·프롬프트·출력 상한을 유지했다. 같은 과제·압력 단계에는 두 전략에 같은 nominal Budget을 주었다. 과제별 최소/중앙/최대 예산은 [설정 문서](../protocols/budget-pressure-test.md)에 있다.

## Flexible은 실제로 언제, 어디에서 탈락했나

Full은 75% 이하의 모든 단계에서 80/80회가 적어도 한 step에서 Flexible을 제외했다. 0% 단계에서는 80/80회가 적어도 한 step에서 Flexible 0개가 됐다. 유효한 Context를 만든 모든 1,638개 step에서 기준 필수 Memory ID는 유지됐다.

다만 탈락은 주로 **Tool feedback이 쌓인 후속 단계**에 발생했다. Full의 첫 step Flexible 유지율은 100%·75%·50% 단계에서 모두 100%였고, 25%에서 99.2%, 0%에서도 89.2%였다. 최초 행동 전에 Flexible이 탈락한 실행은 25%에서 2/80회, 0%에서 18/80회다. 따라서 최종 Flexible 유지율이 0%라는 수치를 첫 행동부터 Flexible이 없었다는 뜻으로 해석하면 안 된다. 모든 유효 step을 평균한 Full의 유지율은 같은 순서로 100.0%, 76.2%, 58.2%, 51.3%, 45.4%다.

Full의 Flexible 탈락 원인은 다음과 같다. 단위는 여러 step에서 누적한 Memory 제외 사건 수이며 고유 Memory 수가 아니다.

| ρ | Selector 예산 경쟁에서 제외 | Context Builder의 형식 비용으로 추가 제외 |
| ---: | ---: | ---: |
| 100% | 0 | 0 |
| 75% | 2 | 78 |
| 50% | 62 | 78 |
| 25% | 104 | 62 |
| 0% | 166 | 20 |

75%에서는 대부분의 탈락이 Context Builder에서 일어났다. 따라서 이 구간의 차이는 Selector의 순위뿐 아니라 뒤에서 Flexible의 마지막 항목부터 제거하는 Builder 동작과 함께 해석해야 한다. `rejected_memory_ids` 전체 길이를 예산 탈락 수로 대신 사용하지 않았으며, 모든 실행 단계에서 실제 검색 후보 집합이 사전 보정한 후보 집합과 같음을 확인했다.

## Packing의 기여도와 실패 원인

같은 sample·repeat의 공통 유효 step끼리 비교하면 75%에서 Full의 평균 Flexible 개수 유지율이 Packing 제거보다 **+5.8pp, 95% CI [+2.9, +9.2]pp** 높았다. 164개 공통 유효 step 중 선택 Memory 집합이 달랐던 step은 20개다. 이는 이 조건에서 더 많은 항목을 보존한 관측이며, 그 기억의 유용성이나 행동 성공률 향상과 같지 않다. 두 전략의 해당 성공률은 동일하다. 다른 단계의 유지율 차이 CI는 0을 포함하거나 0이었다. 다중 비교 보정을 하지 않은 탐색적 비교다.

Full의 실패 2회는 모든 압력 단계에서 동일한 `seed43_approval_014_pending`의 두 반복이다. 현재 보고서와 승인 제약이 있었지만 `request_approval`의 `attachment` 인자가 빠졌다. 이는 기존 Ablation에서 관측된 행동 인자 누락과 같다. 압력 단계별 기록을 독립 실패 과제 여러 개로 세지 않는다.

0% Packing 제거의 추가 실패 2회는 `seed43_approval_019_granted`의 두 반복이다.

- nominal Budget 1,484 − safety margin 32 = **effective Budget 1,452 token**.
- 첫 step의 Full과 Packing 제거는 같은 Memory 집합을 다른 순서로 표시했다. 둘 다 올바른 파일·승인 번호로 메일 발송에 성공했다.
- 모델이 작성한 본문이 달라 Tool feedback을 포함한 다음 단계의 고정 입력이 Full 857 token, Packing 제거 859 token이 됐다.
- Full의 필수 컨텍스트는 정확히 1,452 token으로 맞았지만, Packing 제거는 1,454 token으로 **2 token 초과**해 다음 LLM 호출 전에 중단됐다. 이때 남은 Flexible은 이미 없었다.
- 추가 실패의 `expected_tool_calls_present`, `approval_behavior_correct`, `required_state_accuracy`는 정상이나 최종 Pipeline이 예산 중단이어서 기존 성공 기준상 실패로 기록된다. 평가 기준을 바꾸지 않았다.

따라서 0%의 성공률 차이 **+2.5pp, 95% CI [0, +7.5]pp**를 Packing만의 확정적인 행동 성능 향상으로 주장하지 않는다. 같은 기억 집합의 순서, 모델 응답, 후속 feedback 길이와 매우 좁은 예산 경계가 함께 작용한 사례다. 사전 보정은 기존 Full 두 반복의 모든 step을 재생했지만, 새 응답이 더 긴 feedback을 만들 가능성까지 보장하지는 않았다.

100% 대조 조건에서도 Packing 제거의 2/80회는 후속 Context에서 Flexible 한 항목이 제외됐다. 이 역시 실제 행동 경로의 길이가 기준 trace와 달라져 발생한 보정 범위의 한계이며, 대조 조건이 모든 새 실행에서 완전히 무압력이라고 표현하지 않는다.

## 검증·비용·해석 범위

- 계획 800회, 저장 800회, 고유 실행 키 800개, 기본 과제 40개를 확인했다. 모든 단계의 query·history·snapshot이 동일하고 압력 metadata와 예산만 다름을 대조했다.
- 정상 종료 798회, 예산 안전 중단 2회다. 성공 Context에서 필수 기억이 누락된 step은 0개지만 Mandatory overflow 자체는 2회 있으므로 서로 구분한다.
- Prompt audit `verified` 800회, 실제 전송된 입력의 nominal Budget 초과 0건, 비용 회계 `complete` 800회다. 생성되지 못한 후속 Context의 초과와 실제 API 입력 초과를 혼동하지 않는다.
- LLM API 호출 1,638회의 추정 비용 합계는 **$1.239334**다. CountTokens·사전 보정·인프라를 포함한 청구 총액은 아니다. 압력을 높일수록 출력·비용이 단조 감소하지 않았다.
- CI는 같은 기본 과제의 두 반복을 함께 군집으로 묶어 bootstrap 2,000회(seed 42)로 구했다. 성공률 비교는 단계별 80쌍, 기억 유지율 비교는 양쪽에서 유효한 공통 step을 사용했다. 0%의 유효하지 않은 공통 step 2개는 기억 비교에서 제외했다.
- 셀 최대 2개를 병렬 실행했으므로 지연시간을 엄격한 Packing 비교 결과로 해석하지 않는다. 실행 기준 commit은 `f0abd2e`이고 작업 트리에는 미커밋 변경이 있었다. 준비 실행기·설정·보정·입력·원본 결과의 해시는 검증 artifact에 보존했다.

이번 실험은 **필수 정보를 우선 보존하고 여유가 줄면 Flexible부터 제외하는 동작**을 확인한다. 다만 Flexible 후보가 과제당 2~3개뿐이고 상당 부분 무관 기록이며, 대부분 최초 행동 이후에 탈락했다. 더 넓은 입력 분포의 중요 Flexible 정보 활용이나 Packing의 일반적인 작업 성공 우위까지 이 결과로 확장할 수는 없다.

재현 명령은 `python -m experiments.budget_pressure.run report`와 `python -m experiments.budget_pressure.analyze`다. 그래프 재생성에는 matplotlib이 필요하다. 원본은 `experiments/results/budget_pressure_v104/`에 보존했다. 소형 artifact는 조건별 집계 (별도 보관: `docs/results/budget_pressure_20261111/pressure_summary.json`), 단계별 유지율 (별도 보관: `docs/results/budget_pressure_20261111/stage_retention.json`), 짝지은 기억 비교 (별도 보관: `docs/results/budget_pressure_20261111/paired_memory_deltas.json`), 짝지은 성공률 비교 (별도 보관: `docs/results/budget_pressure_20261111/paired_success_deltas.json`), 실패 사례 (별도 보관: `docs/results/budget_pressure_20261111/failures.json`), 감사·해시 (별도 보관: `docs/results/budget_pressure_20261111/validation.json`)와 SVG 그래프 (별도 보관: `docs/results/budget_pressure_20261111/pressure_results.svg`)에 있다.
