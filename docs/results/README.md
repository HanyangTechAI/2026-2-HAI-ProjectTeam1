# 결과 아카이브

이 폴더에는 분석 보고서가 사용하는 소형 집계 파일과 그래프가 있습니다. 전체 원본 실행 데이터는 각 실험 보고서에 적힌 `experiments/results/` 경로에 보존됩니다.

- [Ablation 전용 SVG 7개 · 생성 2026-11-17](ablation_visualizations_20261117/README.md) — 실험일은 2026-11-11이며, 2K/4K 구성요소 제거와 Temporal 단독 제거 재실행을 정리했다. [미리보기](ablation_visualizations_20261117/index.html) · [전체 ZIP](ablation_visualizations_20261117/ablation_svg_20261117.zip).

- [전체 실험 matplotlib 시각화 · 2026-11-15](all_visualizations_20261115/README.md) — 31개 보고서의 실험을 SVG 54개와 수치 CSV로 정리했다. [미리보기 갤러리](all_visualizations_20261115/index.html) · [전체 ZIP](all_visualizations_20261115/all_experiment_svg_20261115.zip).

- [Main 12셀 결과 아카이브 · 2026-11-09](main_20261109/README.md)
- [2K/H100 Ablation 집계 · 2026-11-11](ablation_2k_h100_20261111/)
- [Constraint Coverage 점검 · 2026-11-13](constraint_coverage_20261113/) — 시나리오별 제약 적용 비율과 로컬 평가기 진단.
- [균형 생성기 Coverage · 2026-11-13](constraint_coverage_balanced_v2_20261113/) — 새 입력과 오프라인 검증이며 LLM 성능 결과가 아니다.
- [Task Criticality Safety Gate 결과 · 2026-11-15](../experiments/reports/task-criticality-safety-gate-2026-11-15.md) — scripted action 160개 비교; 원본 CSV·validation은 `experiments/results/task_criticality_s235_gate_v03/` 및 `..._approval_fix/`에 있다.
- [Task Criticality Safety Gate 실제 LLM 결과 · 2026-11-15](../experiments/reports/task-criticality-safety-gate-llm-v03-2026-11-15.md) — Gemini proposal 160개를 Gate OFF/ON으로 replay; 원본 proposal·CSV·validation은 `experiments/results/criticality_llm_v03_main/`, smoke 8개는 `experiments/results/criticality_llm_v03_smoke/`에 있다.
- [Task Criticality Safety Gate 독립 Holdout · 2026-11-15](../experiments/reports/task-criticality-safety-gate-holdout-v1-2026-11-15.md) — 별도 seed·템플릿 160개 LLM 결과; 원본은 `benchmark/generated/task_criticality_holdout_v1/`, `experiments/results/criticality_holdout_v1/`에 있고 8개 smoke도 각각 `_smoke_v1/` 경로에 있다.
- [Task Criticality Safety Gate 멀티스텝 2K Holdout · 2026-11-16](../experiments/reports/task-criticality-safety-gate-multistep-v01-2026-11-16.md) — 160개 scenario, 320개 최대 8-step 실행; 원본은 `experiments/results/criticality_multistep_2k_full_v01/`, 2K smoke는 `criticality_multistep_2k_smoke_v01/`에 있다. 75% budget의 미완료 실행과 smoke는 보고서에서 분리한다.
- [Task Criticality Safety Gate 오류 주입 복구 v2 · 2026-11-18](../experiments/reports/task-criticality-safety-gate-recovery-v2-2026-11-18.md) — 317 runs에서 Gate 차단과 Tool 실패 후 1회 재계획을 비교했다. 원본은 `experiments/results/recovery_v2_full/`, 8개 smoke는 `recovery_v2_smoke/`에 있다.
- [성공 후 Side-Effect 중복 재생 · 2026-11-19](../experiments/reports/post-success-duplicate-replay-2026-11-19.md) — Main 158 runs의 승인 이메일 40쌍에서 반복 발송 40/40 차단, 파일 조회 대조군 오차단 0/39를 기록했다. 원본은 `experiments/results/post_success_duplicate_full_v1/`, 8개 smoke는 `post_success_duplicate_smoke_v1/`에 있다.
- [Run-scoped Side-Effect Idempotency Recovery v2.1 · 2026-11-18](../experiments/reports/side-effect-idempotency-recovery-2026-11-18.md) — 새 seed Main 317 runs에서 Gate ON과 Tool 재시도는 goal 79/79로 같았고, raw run log에서 중복 이메일 제안 1건을 차단했다. `summary.json` 집계 키 불일치를 함께 기록한다. 원본은 `experiments/results/idempotency_full_v1/`, 8개 smoke는 `idempotency_smoke_v1/`에 있다.

- [Task Criticality Safety Gate 오류 주입 복구 · 2026-11-17](../experiments/reports/task-criticality-safety-gate-recovery-2026-11-17.md) — 오류 주입 후 재계획 236 runs; 원본 proposal·runs·summary·manifest는 `experiments/results/criticality_recovery_full_v1/`, smoke는 `criticality_recovery_smoke_v1/`에 있다.
- [topic_balanced_v3 비율 점검 입력 · 2026-11-17](topic_ratio_check_20261117/README.md) — 주제별 10개씩인 40개 생성 입력과 구성 요약이다. provisional token count를 이용한 생성 QA이며 LLM 성능 실험은 아니다.
- [초기 Runner·Smoke 실행 점검 · 2026-11-15](../experiments/audits/early-runner-and-smoke-archive-2026-11-15.md) — 과거 runner fixture 결과와 동일한 5개 입력의 2K smoke 재실행을 요약한다.

각 실험의 JSON 및 그림 파일은 실행·분석 코드에서 경로로 참조하므로 현재 하위 폴더 이름을 유지합니다.
