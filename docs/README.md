# 문서 안내

## 먼저 볼 문서

1. [연구 질문](research/research-questions.md)에서 문제와 평가 기준을 확인합니다.
2. 시스템 구조는 [아키텍처](architecture.md), 동작 규칙은 [알고리즘](algorithm.md), 모듈 계약은 [인터페이스](interfaces.md)를 봅니다.
3. 실행과 결과 해석은 [실험 계획](experiments/plans/experiment-plan.md)에서 시작합니다.
4. 분석 결과는 [실험 보고서 목록](experiments/reports/README.md)에서 찾습니다.

## 최근 추가 보고서

- [Critical Flexible 라벨 Preflight](experiments/reports/critical-flexible-preflight-2026-11-12.md) — 긴 핵심 기억이 중간 예산에서 탈락하는 조건을 분석합니다.
- [Critical Flexible 단일 Step Agent Pilot](experiments/reports/critical-flexible-agent-pilot-2026-11-12.md) — 첫 Tool 행동을 기록하며 `max_steps=1`의 성공률 해석 제한을 명시합니다.
- [Critical Flexible 75% 단일 Step Holdout](experiments/reports/critical-flexible-holdout-2026-11-12.md) — 160개 paired scenario의 첫 행동과 Tool 효과를 확인합니다.
- [11월 12일 Pipeline Smoke 점검](experiments/audits/pipeline-smoke-2026-11-12.md) — 5개 smoke scenario의 20회 실행 상태를 확인합니다.

## 사용 가이드

- [시나리오 fixture](guides/scenarios-usage.md)
- [실험 데이터 생성기](guides/generator-usage.md)
- [Evaluator](guides/evaluator-usage.md)

## 디렉토리 안내

| 위치 | 내용 |
|---|---|
| 문서 폴더 | 아키텍처, 알고리즘, 인터페이스, 메모리 스키마, 예외 사례, freeze 기준 |
| [`guides/`](guides/) | 시나리오·데이터 생성기·평가기 사용법 |
| [`experiments/plans/`](experiments/plans/) | 전체 실험 계획과 실행 기록 |
| [`experiments/protocols/`](experiments/protocols/) | 개별 실험 설정 및 재현 절차 |
| [`experiments/reports/`](experiments/reports/) | 날짜가 포함된 실험 결과와 분석 |
| [`experiments/audits/`](experiments/audits/) | 검증, 재점검, 실행 감사 |
| [`results/`](results/) | 보고서에서 참조하는 집계 JSON, 감사 데이터, 그래프 |
| [`research/`](research/) | 연구 질문과 프로젝트 제안서 |

`algorithm.md`는 실행 시 freeze 해시 검증에 쓰이는 고정 경로이므로 문서 폴더 바로 아래에 둡니다.


