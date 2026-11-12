# Critical Flexible 라벨 Preflight 보고서 — 2026-11-12

## 요약

2026-11-11 Action-Time Budget Preflight에서 확인하지 못했던 **중요 Flexible Memory의 정답 포함 여부**를 사전 라벨링해 후속 preflight를 수행했다. Freeze 1.0.4의 160개 시나리오를 mandatory floor부터 all-flexible ceiling까지 다섯 수준으로 평가한 결과, 75% 예산의 조건 C에서만 `full`이 정답 Critical Memory를 40/40회 제외했다. 같은 입력의 `no_budget_aware_packing`은 40/40회를 보존했다. 모든 Context 평가가 정상 종료했고 예산 초과와 Mandatory 누락은 없었다.

이 결과는 예산이 허용하는데도 Critical Flexible 기억이 선택에서 탈락하는 구체적인 조건을 찾은 것이다. LLM generation과 Tool 행동은 실행하지 않았으므로 Task 성공률의 차이는 측정하지 않았다.

## 설계

| 항목 | 설정 |
|---|---|
| freeze / commit | 1.0.4 / `62e5d970c72872adcb65f45565db564f973a6dfb` (clean) |
| 데이터 | 40개 기본 과제 × 조건 A–D = 160 scenarios |
| 과제 유형 | `file_lookup` 80개, `approved_email` 80개 |
| 조건 | A: 핵심 짧음·잡음 짧음, B: 짧음·김, C: 김·짧음, D: 김·김 |
| 기억 구성 | scenario당 중요도 0.9인 Critical Flexible 1개와 중요도 0.3인 distractor 3개. `approved_email`에는 Required State와 Protected Constraint도 포함 |
| 길이 목표 | Short 24–40 tokens, Long 130–170 tokens; Gemini CountTokens로 실측 |
| 비교 | `full` 대 `no_budget_aware_packing` |
| 예산 sweep | 시나리오별 mandatory floor–all-flexible ceiling의 0%, 25%, 50%, 75%, 100%; safety margin 32 tokens |
| 범위 | 160 × 5 × 2 = 1,600 Context 평가, 800 paired cases; LLM generation 0회 |

0%와 100% 수준은 각각 추가 Flexible 예산이 없는 기준과 전체 후보를 수용하는 기준이다. 아래의 선택 수는 각 수준의 160개 시나리오를 분모로 한다. `critical feasible`은 Mandatory 기억을 포함한 예산에 정답 Critical Memory까지 담을 수 있는 경우다.
이 데이터의 시나리오별 floor는 645–745 tokens, ceiling은 778–1,320 tokens였다. 따라서 각 퍼센트는 동일한 절대 Budget이 아니라 시나리오별 구간 안에서 정규화한 수준이다.

## 결과

| Flexible 예산 | Critical feasible | Full에서 Critical 선택 | Packing 비활성에서 Critical 선택 | 선택 집합 차이 |
|---:|---:|---:|---:|---:|
| 0% | 0/160 | 0/160 | 0/160 | 0/160 |
| 25% | 40/160 | 40/160 | 40/160 | 0/160 |
| 50% | 120/160 | 120/160 | 120/160 | 0/160 |
| 75% | 160/160 | **120/160** | **160/160** | **40/160** |
| 100% | 160/160 | 160/160 | 160/160 | 0/160 |

75%의 차이 40건은 전부 **조건 C: 긴 Critical + 짧은 distractor**에서 발생했다.

| 조건 C · 75% | Critical feasible | Full 선택 | Packing 비활성 선택 | Full의 packing 제외 |
|---|---:|---:|---:|---:|
| `file_lookup` 20쌍 | 20/20 | 0/20 | 20/20 | 20/20 |
| `approved_email` 20쌍 | 20/20 | 0/20 | 20/20 | 20/20 |

조건 A, B, D에서는 75% 수준에서 두 설정 모두 40/40회의 Critical Memory를 선택했다. 조건 C도 100%에서는 양쪽 모두 40/40회를 선택했다. 따라서 이 결과는 모든 예산에서 발생하는 검색 실패가 아니라, **Critical이 긴 반면 짧은 distractor들이 예산 효율이 높은 특정 중간 예산 경계**에 한정된다. Calibration 점수에서도 조건 C 예시의 Critical utility/token은 약 0.0041, 짧은 distractor는 약 0.0142–0.0150으로 관측됐다.

모든 1,600개 평가에서 Pipeline 상태는 정상이며 `overflow_count=0`, `mandatory_failures=0`이었다. 선택 집합 차이 40건은 예산 초과로 생긴 것이 아니라 유효 예산 안에서 어떤 Flexible을 택했는지의 차이다.

## 해석과 한계

이 후속 데이터는 이전 Action-Time audit의 핵심 한계였던 “중요 Flexible 기억의 정답 라벨 부재”를 보완한다. 이번 조건에서 Full의 token-aware packing은 짧은 기억을 우선해 길고 중요한 기억을 제외할 수 있으며, 이는 단순 Flexible 전체 회수율만으로는 드러나지 않았던 실패다.

결과는 합성 시나리오 40개와 고정된 Freeze 1.0.4, 특정 모델·임베딩 및 정해진 길이 범위에 한정된다. 반복 조건과 장기 온라인 Memory 생성은 평가하지 않았다. `no_budget_aware_packing`의 관측 우위를 일반적인 정책 우위로 확대할 수 없고, 실제 Agent의 작업 성공이나 제약 위반에 미치는 영향도 이 preflight만으로 판단할 수 없다. 이 한계를 살피기 위한 단일 step Agent probe와 별도 seed·템플릿 holdout 결과는 [후속 Pilot 보고서](critical-flexible-agent-pilot-2026-11-12.md)와 [75% Holdout 보고서](critical-flexible-holdout-2026-11-12.md)에 기록했다.

## 재현과 산출물

데이터 생성과 Context preflight는 다음 모듈을 사용한다. Gemini CountTokens 호출이 발생하므로 같은 원본 결과를 보존하려면 재실행 시 새 데이터·출력 경로를 사용한다.

```powershell
python -m benchmark.critical_flexible_generator --counter gemini --seed-start 42 --cases-per-family 20 --output benchmark/generated/critical_flexible_v1/repro_dataset.json
python -m benchmark.critical_flexible_preflight --scenarios benchmark/generated/critical_flexible_v1/repro_dataset.json --config experiments/configs/ablation.json --output experiments/results/critical_flexible_preflight_v104_repro
```

원본 결과 디렉터리는 `experiments/results/critical_flexible_preflight_v104/`이다. `validation.json`의 dataset SHA-256은 `74f8e7245f33b209037ac0e07b6af2f8717f880619c5736c86a6f7dd16f14130`, manifest SHA-256은 `bfe0ce9fef0d86e2328b4e6e670c659a82eb31304668a9423effd2a6105847c7`이다. CountTokens는 `gemini-3.1-flash-lite`를 사용했으며 1,368회 API 호출, retry 0회였다. Embedder resolved revision은 `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`다.

원본 디렉터리에는 `validation.json`, scenario별 `calibration.json`, `paired_summary.csv`, `selection_details.csv`, `selection_details.jsonl`이 있다. 결과 디렉터리는 현재 `.gitignore` 정책을 따른다.
