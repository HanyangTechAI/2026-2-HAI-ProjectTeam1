# 3. Budget Pressure Test — Flexible Memory 탈락 실험 설정

> 이 커밋은 보고서만 배포한다. 결과 JSON·JSONL·ZIP·그림과 실험 코드·설정은 별도로 보관하며, 아래 보관 경로와 재현 명령은 해당 로컬 산출물을 기준으로 한다. 핵심 수치와 해석은 본문 표에 포함했다.

상태: **40/40과제의 사전 검증 PASS, 800/800회 실행·분석 완료**. 결과와 해석은 [최종 분석](budget_pressure_analysis_20261111.md)을 따른다. 기계 판독 설정은 settings.json (별도 보관: `experiments/budget_pressure/settings.json`)에 있다. 이 파일은 전용 실험 설계 설정이며 기존 `benchmark.generator --config`나 `benchmark.runner --config`에 직접 전달하지 않는다. 보정한 과제별 실제 예산 입력과 Runner용 config는 `benchmark/generated/budget_pressure_v104/`에 생성했다.

목표는 입력 예산을 줄일 때 **필수 기억은 보존하고 Flexible memory부터 제외되는지** 확인하는 것이다. 같은 과제에서 Full Proposed와 Token-aware Packing 제거 조건을 비교한다. Temporal Resolution, Mandatory State, Constraint Preservation은 두 조건 모두 켠다.

## 고정 조건

| 항목 | 설정 |
| --- | --- |
| 기준 코드·실행 설정 | freeze 1.0.4, 기존 `ablation.json`의 모델·프롬프트·runtime 상속 |
| 모델 | Gemini 3.1 Flash-Lite, temperature 0, thinking LOW, 출력 상한 2048 token |
| 입력 | 기존 2K·H100 셀의 같은 40개 과제; seed 42/43 |
| History / Horizon | 25K token / 100 turns |
| Top-k / 최대 step | 5 / 8 |
| 비교 조건 | Full / `no_budget_aware_packing` |
| 반복 | 과제당 2회 |
| 계획 실행 수 | 5단계 × 40과제 × 2조건 × 2반복 = **800회** |

원본 query, history, memory content, 정답은 유지한다. 성공 여부로 과제를 선별하지 않는다. 기존 Full의 첫 step 로그에서는 40개 과제 모두 Flexible memory가 2~3개였고 content 비용 합계는 **28~94 token**이었다. 따라서 고정 예산 2K/4K 비교만으로는 작은 Flexible 구간의 탈락을 충분히 관찰하기 어렵다. 또한 이 기억은 주로 무관 기록이므로, 탈락이 작업 성공률 하락으로 이어져야 한다고 가정하지 않는다.

## 예산 5단계

| Flexible 수용 구간 비율 ρ | 목적 |
| ---: | --- |
| 1.00 | 필수·Flexible 후보를 모두 보존하는 대조 조건 |
| 0.75 | 약한 압력 |
| 0.50 | 중간 압력 |
| 0.25 | 강한 압력 |
| 0.00 | 필수 기억을 보존하는 최소 예산 경계 |

과제마다 필수 기억 크기가 다르므로 동일한 절대 token 예산 대신 다음 방식으로 예산을 보정한다.

```text
M_i = 과제 i의 필수 기억을 보존하는 데 필요한 effective 입력 예산
A_i = 과제 i의 필수 기억 + 모든 Flexible 후보를 보존하는 effective 입력 예산
B_i(ρ) = ceil(M_i + ρ × (A_i − M_i)) + 32
```

32 token은 기존 request safety margin이며 Runner의 effective budget은 `B_i − 32`다. M_i와 A_i에는 실제 system instruction·도구 스키마·query·섹션 형식 비용을 포함한다. Selector의 `fixed_tokens + memory content 비용`과 Context Builder의 조립된 request 비용 **둘 다** 충족하도록 큰 값을 사용한다. 단순히 Memory token 수만 더해 예산을 정하지 않는다. ρ는 전체 Budget 비율이나 정확한 기억 개수 보존 비율을 뜻하지 않는다. 기억은 항목 단위로 선택하므로 중간 단계에서 탈락 개수가 같을 수 있다.

보정은 기존 Full의 두 반복에서 저장된 **모든 step과 누적 Tool feedback**을 재생해 수행하고, 그중 최대 비용을 사용한다. 첫 step만 측정하면 뒤의 Tool feedback 때문에 Mandatory overflow가 발생해 Flexible 탈락 효과와 섞일 수 있다. 보정 시 새 텍스트 생성이나 도구 행동은 실행하지 않고, 실제 실험과 동일한 request-aware Gemini CountTokens를 사용한다. 새 정책의 행동 경로는 기존 trace와 달라질 수 있으므로 이 사전 검증이 모든 향후 step의 실행 가능성을 보장하지는 않는다.

Full의 예산을 먼저 보정해 두 전략에 같은 `B_i(ρ)`를 적용한다. `no_budget_aware_packing`에 별도 유리한 예산을 주지 않는다. 같은 과제는 모든 단계에서 같은 sample ID를 유지하며, metadata에 `flexible_capacity_ratio`, `mandatory_floor_tokens`, `all_flexible_ceiling_tokens`, 실제 `context_token_budget`을 함께 기록한다. 예산의 과제별 최소·중앙·최대값도 보고한다.

## 실행 전 통과 조건

보정된 실제 입력을 만들기 전에 각 과제·각 비교 조건에 대해 다음을 확인한다.

- 모든 단계에서 query·snapshot·검색 후보 집합이 같고, 예산만 달라진다.
- 보정에 사용한 모든 step에서 필수 Memory ID가 유지되고 Mandatory overflow가 없다.
- ρ=1.00에서는 모든 step에서 Flexible 후보가 모두 보존된다.
- ρ<1.00에서는 각 과제의 적어도 한 step에서 Flexible 후보가 실제로 탈락한다.
- ρ=0.00에서는 각 과제의 적어도 한 step에서 Flexible이 0개가 된다.
- Flexible 후보가 없거나 `A_i ≤ M_i`인 과제, 위 조건을 충족하지 못한 과제는 ID와 원인을 보고하고 준비를 중단한다. 조용히 제외하거나 실제 LLM 실행을 시작하지 않는다.

보정 결과를 `benchmark/generated/budget_pressure_v104/calibration.json`에 남긴다. 40개 과제 모두 통과했으며 보정 파일, 5개 셀 입력과 `runner_config.json`을 생성했다.

## 측정과 해석

핵심 지표는 step별 Flexible 유지율, Selector 예산 경쟁에서 제외된 개수, Context Builder의 형식 비용 때문에 추가로 제외된 개수, 필수 기억 유지율이다. 작업 성공률·승인 제약 위반·stale-state 사용·비용은 보조 지표다.

`selection_result.rejected_memory_ids`에는 시간 해석 등으로 제외된 후보도 포함될 수 있으므로, 그 길이를 Flexible 예산 탈락 개수로 사용하지 않는다. 큰 예산에서 확인한 동일 Flexible 후보 집합을 기준으로 선택된 집합과 비교하고, `context_result.dropped_flexible_ids`는 Builder의 추가 탈락으로 따로 센다. 실제 Budget 초과, 고정 입력 초과, Mandatory overflow도 Flexible 탈락과 구분한다. Context가 만들어지지 않은 실행의 유지율은 0으로 꾸미지 않고 미측정으로 보고한다.

과제별 예산이 다르므로 집계는 절대 Budget 대신 `strategy × flexible_capacity_ratio`로 한다. 같은 sample·repeat·압력 단계끼리 pair하고, 신뢰구간은 40개 기본 과제를 군집으로 두 반복을 함께 bootstrap한다(2,000회, seed 42). 원본 Main/Ablation 결과와 합치지 않고 `experiments/results/budget_pressure_v104/`에 저장한다.


## 2026-11-11 실행 시작 기록

원격 브랜치 17개와 Budget/Pressure 관련 PR·커밋을 확인했지만 다른 사람이 만든 해당 실험의 구현이나 결과는 찾지 못했다. 저장된 Full의 두 반복에서 모든 단계의 Tool feedback을 재생했고, 40개 과제 × 5단계 × 2조건의 사전 검증이 모두 통과했다. 구현을 포함한 전체 unittest 359개 실행에 성공했다(1개 skip).

| Flexible 수용 비율 | 실제 nominal Budget 최소 / 중앙 / 최대 |
| ---: | ---: |
| 1.00 | 1407 / 1492.5 / 1656 |
| 0.75 | 1397 / 1482.0 / 1640 |
| 0.50 | 1387 / 1471.5 / 1624 |
| 0.25 | 1377 / 1458.5 / 1608 |
| 0.00 | 1367 / 1441.0 / 1591 |

실행 스크립트는 `experiments/budget_pressure/run.py`다. 셀 최대 2개를 병렬로 진행하며 지연시간은 병렬 실행의 영향을 받는 참고 지표다. 각 셀은 체크포인트와 `runner.log`를 저장한다. 설정·입력·코드가 같을 때만 `run --resume`을 사용한다.

```sh
python -m experiments.budget_pressure.run run --workers 2 --resume
python -m experiments.budget_pressure.run report
```

새로 보정하려면 `prepare`를 사용하지만, 생성된 manifest가 있는 경로의 덮어쓰기는 거부한다. `report`는 800개의 고유 실행 키와 셀 완료를 확인한 뒤 Flexible 유지율·필수 기억 손실·단계별 탈락 원인을 집계한다. 2026-11-11 20:30 KST에 800/800회가 완료됐고 최종 결과는 [분석 문서](budget_pressure_analysis_20261111.md)에 기록했다.
