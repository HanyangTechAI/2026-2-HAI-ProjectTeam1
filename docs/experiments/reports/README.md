# 실험 보고서

파일명 끝의 날짜는 보고서 기준일입니다. 프로토콜과 원본 실행 경로는 각 보고서에서 확인할 수 있습니다.

## 초기 파일럿 아카이브 · 2026-11-03

- [초기 오프라인·Gemini Pilot 실행 아카이브](early-pilot-runs-2026-11-03.md) — 초기 계측 한계와 집계 모순을 명시하며 Main 분석과 분리한다.

## Token Budget 사전 점검 · 2026-11-08

- [1K·2K Token Budget Preflight](token-budget-preflight-2026-11-08.md) — safety margin 0·32 조건의 128회 실행을 정리한다.

## Main 결과 · 2026-11-09

- [전체 Main 분석](main-final-analysis-2026-11-09.md)
- [Phase 1 · H50 분석](main-phase1-h50-analysis-2026-11-09.md)
- [Phase 2 · H10 분석](main-phase2-h10-analysis-2026-11-09.md)
- [출력 한도 2048 검증](output-limit-2048-validation-2026-11-09.md)

## 후속 실험 · 2026-11-11

- [2K/H100 Ablation Test 결과](ablation-2k-h100-2026-11-11.md)
- [2K/4K Ablation 분석](ablation-2k-h100-analysis-2026-11-11.md)
- [Temporal 제거 재실행](no-temporal-v104-2k-h100-2026-11-11.md)
- [Budget Pressure 분석](budget-pressure-analysis-2026-11-11.md)
- [Long Horizon 분석](long-horizon-analysis-2026-11-11.md)
- [H100 State Update 분석](state-update-h100-analysis-2026-11-11.md)

## Critical Flexible 후속 실험 · 2026-11-12

- [Critical Flexible 라벨 Preflight](critical-flexible-preflight-2026-11-12.md)
- [Critical Flexible 단일 Step Agent Pilot](critical-flexible-agent-pilot-2026-11-12.md)
- [Critical Flexible 75% 단일 Step Holdout](critical-flexible-holdout-2026-11-12.md)
- [Flexible Packing Alpha 개발 실험](packing-alpha-development-2026-11-12.md)

## Task Criticality 선택 Preflight · 2026-11-12

- [Task Criticality 75% 선택 Preflight](task-criticality-75-selection-preflight-2026-11-12.md) — 동일한 Critical Flexible 개발 데이터에서 별도 정책의 Context 선택을 비교한다.
- [Task Criticality S2/S3/S5 스트레스 Preflight](task-criticality-s235-stress-preflight-2026-11-12.md) — 중요도 역전, 무효·누락 근거, 상충 slot을 선택 단계에서 점검한다.

## Task Criticality v0.2 후속 Preflight · 2026-11-13

- [S2/S3/S5 v0.2 Preflight](task-criticality-s235-v02-preflight-2026-11-13.md) — 같은 stress data에서 무효 Evidence 제외와 상충 감지를 재검토한다.

## Task Criticality Safety Gate · 2026-11-15

- [S2/S3/S5 Safety Gate v0.3 오프라인 평가](task-criticality-safety-gate-2026-11-15.md) — 고정 scripted action 160개에서 gate OFF/ON을 비교하고, Freeze 1.0.5 approval-fix 재실행과 차단 원인 차이를 기록한다. LLM generation은 없다.

## Task Criticality 실제 LLM Safety Gate · 2026-11-15

- [Safety Gate v0.3 Gemini 첫 행동 paired replay](task-criticality-safety-gate-llm-v03-2026-11-15.md) — 160개 실제 LLM 제안에 대해 Gate OFF/ON을 paired replay했다. 위험 호출 60개 차단, 정답 행동 80개 허용 결과와 8개 smoke, 모의 실행 및 holdout이 아닌 한계를 기록한다.
