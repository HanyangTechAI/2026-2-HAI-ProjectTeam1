# 실험 문서

- [실험 계획과 전체 실행 결과](plans/experiment-plan.md)
- [실험 프로토콜 목차](protocols/README.md)
- [실험 보고서 목차](reports/README.md)
- [검증 및 감사 자료 목차](audits/README.md)
- [집계 데이터와 그래프](../results/README.md)

가장 최근 결과는 [성공 후 Side-Effect 중복 재생 평가](reports/post-success-duplicate-replay-2026-11-19.md)다. 독립 synthetic Holdout의 승인 이메일 40쌍에서 실행별 idempotency ON은 성공 후 동일 action을 40/40 차단했고, read-only 파일 조회 39쌍에서 오차단은 없었다. Main 158회와 별도 smoke 16회는 mock Tool 결과이며 외부 exactly-once 보장을 뜻하지 않는다.

직전 [실행별 Side-Effect Idempotency와 Recovery v2.1](reports/side-effect-idempotency-recovery-2026-11-18.md)은 새 seed Holdout 79개에서 Gate ON 및 Tool 실패 후 1회 재계획 arm이 모두 goal 79/79를 완료한 결과다. raw log에서 반복 이메일 호출 1건을 차단했으며 원 집계 파일의 필드명 불일치를 함께 기록했다. 이전 [Safety Gate 오류 주입 복구 v2](reports/task-criticality-safety-gate-recovery-v2-2026-11-18.md), [v1 live replanning 평가](reports/task-criticality-safety-gate-recovery-2026-11-17.md), [멀티스텝 2K 독립 Holdout 평가](reports/task-criticality-safety-gate-multistep-v01-2026-11-16.md), 첫 행동 [독립 Holdout 평가](reports/task-criticality-safety-gate-holdout-v1-2026-11-15.md), stress set [LLM paired replay](reports/task-criticality-safety-gate-llm-v03-2026-11-15.md), 고정 ScriptedPlanner [오프라인 평가](reports/task-criticality-safety-gate-2026-11-15.md)는 각각의 실험 범위와 함께 기록했다.

초기 개발 Runner·11월 8일 및 11일 smoke 산출물은 [실행 점검 아카이브](audits/early-runner-and-smoke-archive-2026-11-15.md)에 모았다.
