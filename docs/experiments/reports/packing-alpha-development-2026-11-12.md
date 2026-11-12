# Flexible Packing Alpha 개발 실험 — 2026-11-12

## 요약

`utility / max(token_count, 1) ** alpha` 점수에서 길이 패널티를 바꿔 조건 C의 첫 행동을 비교했다. Seed 42–61로 만든 개발 데이터의 조건 C·75% 예산 40개 scenario를 alpha별 한 번씩 실행했다. 첫 Tool/action, 필수 인자와 작업별 Tool 효과가 모두 맞은 수는 `alpha=0` 40/40, `alpha=0.2` 31/40, `alpha=1` 0/40이었다.

이 결과는 중요 기억이 긴 조건 C에서 길이 패널티가 있는 두 설정이 핵심 기억을 덜 보존한 상황과 일치한다. 동일한 개발 자료로 alpha를 살펴본 단일 step 실험이므로, `alpha=0`을 일반적으로 우수한 정책으로 확정하거나 기본 Selector를 변경하는 근거로 사용하지 않는다.

## 설계와 검증

| 항목 | 설정 |
|---|---|
| 기준 | Architecture / Parameter Freeze 1.0.4, 실행 commit `e71738c` (clean) |
| 개발 데이터 | Seed 42–61, `file_lookup` 20개와 `approved_email` 20개 기본 과제, 조건 C |
| 예산 | 시나리오별 mandatory floor–all-flexible ceiling의 75%, safety margin 32 tokens |
| 선택 점수 | `utility / max(token_count, 1) ** alpha`; alpha `0`, `0.2`, `1` |
| 모델 실행 | Gemini 3.1 Flash-Lite, 과제당 1회, `max_steps=1`; 총 120 runs / 40 paired scenario |
| 지표 | 첫 Tool/action과 필수 인자, 작업별 Tool 효과가 모두 맞은 `first_action_correct` |
| 검증 | Prompt budget audit 누락 0, 실제 예산 초과 0, 첫 Tool 호출 누락 0 |

원본 개발 dataset SHA-256은 `74f8e7245f33b209037ac0e07b6af2f8717f880619c5736c86a6f7dd16f14130`, 준비된 조건 C·75% 입력 SHA-256은 `e166abf0a52f0de12a3b464d0a1fa19e197ebea7dff993ae5e58b0310dd49702`다.

LLM 실행 전 selection-only preflight는 같은 160개 개발 scenario에서 예산 수준별 선택을 점검했다. 75% 예산의 800개 Context 평가에서 초과와 Mandatory 누락은 없었다. 조건 C의 핵심 Flexible 선택 수는 `alpha=0` 40/40, `alpha=0.2` 31/40, `alpha=0.5` 0/40, `alpha=1` 0/40이었다. 추가로 0–100% 전체 예산 sweep은 4,000개 Context 평가를 완료했다.

## 결과

| 과제 유형 | Alpha | 첫 행동 정답 | 핵심 Flexible이 Context에 포함 |
|---|---:|---:|---:|
| 파일 검색 | 0 | 20/20 | 20/20 |
| 파일 검색 | 0.2 | 18/20 | 18/20 |
| 파일 검색 | 1 | 0/20 | 0/20 |
| 승인 메일 | 0 | 20/20 | 20/20 |
| 승인 메일 | 0.2 | 13/20 | 13/20 |
| 승인 메일 | 1 | 0/20 | 0/20 |
| **전체** | **0** | **40/40 (100%)** | **40/40 (100%)** |
| **전체** | **0.2** | **31/40 (77.5%)** | **31/40 (77.5%)** |
| **전체** | **1** | **0/40 (0%)** | **0/40 (0%)** |

짝 비교에서 `alpha=0`은 `alpha=0.2`보다 9개 scenario에서 정답이고 나머지 31개에서는 같았다. `alpha=0.2`는 `alpha=1`보다 31개에서 정답이고 나머지 9개에서는 같았다. 각 scenario는 한 번씩만 실행됐으므로 반복 간 모델 변동을 측정하거나 신뢰구간을 추정하지 않았다.

모든 arm은 40개 실행을 완료했다. `max_steps=1`이므로 최종 Task Success Rate는 지표에 포함하지 않는다. 이 수치는 완료율이 아니라 첫 행동 진단이다.

## 해석과 한계

- 세 arm은 모두 같은 개발 데이터와 동일한 조건 C·75% 예산을 사용했다. 독립 holdout 평가가 아니다.
- 결과는 길이·잡음 조합을 고정한 합성 시나리오 40개에 한정된다. 다른 조건, 예산, Task 유형으로 일반화할 수 없다.
- Preflight는 LLM 실행 전 후보 선택만 점검하며, Agent 결과를 예측하지 않는다. Agent 평가는 별도 120회 단일 step 실행이다.
- `alpha=0`은 토큰 길이 패널티를 제거하고 효용 점수만으로 순위를 정한다. 예산 초과는 방지하지만 모든 자료에서 더 나은 선택을 한다는 뜻은 아니다.
- 실험은 Selector를 바꾸지 않는 overlay로 실행했다. `memory/selector.py`와 기본 Proposed 동작은 그대로다.
- 이전의 독립 seed·템플릿 holdout은 `full` 대 `no_budget_aware_packing`을 비교한 별도 실험이다. 여기서 선택한 `alpha=0`을 검증한 holdout이 아니며 두 결과를 같은 비교로 취급하지 않는다.

## 산출물과 재현 안내

로컬 산출물은 `experiments/results/` 아래의 `packing_alpha_dev_75_v1/`, `packing_alpha_dev_full_v1/`, `packing_alpha_llm_0/`, `packing_alpha_llm_02/`, `packing_alpha_llm_1/`, `packing_alpha_llm_comparison/` 디렉터리에 있다. 비교 검증 파일은 총 120 runs, paired scenario 40개, 예산 초과 0건을 기록한다. 이 경로는 저장소의 ignore 정책을 따르며, 이 보고서에는 요약과 검증 범위를 보존한다.

사전평가·Agent 실행 명령과 실험용 `pack_task_criticality()`의 범위는 [실험용 Packing·Task Criticality 가이드](../../guides/experimental-packing.md)를 참고한다. 파라미터 선택에는 개발 데이터만 사용하고 holdout은 별도로 보존한다.
