# 결과 아카이브

이 폴더에는 분석 보고서가 사용하는 소형 집계 파일과 그래프가 있습니다. 전체 원본 실행 데이터는 각 실험 보고서에 적힌 `experiments/results/` 경로에 보존됩니다.

- [전체 실험 matplotlib 시각화 · 2026-11-15](all_visualizations_20261115/README.md) — 31개 보고서의 실험을 SVG 54개와 수치 CSV로 정리했다. [미리보기 갤러리](all_visualizations_20261115/index.html) · [전체 ZIP](all_visualizations_20261115/all_experiment_svg_20261115.zip).

- [Main 12셀 결과 아카이브 · 2026-11-09](main_20261109/README.md)
- [2K/H100 Ablation 집계 · 2026-11-11](ablation_2k_h100_20261111/)
- [Constraint Coverage 점검 · 2026-11-13](constraint_coverage_20261113/) — 시나리오별 제약 적용 비율과 로컬 평가기 진단.
- [균형 생성기 Coverage · 2026-11-13](constraint_coverage_balanced_v2_20261113/) — 새 입력과 오프라인 검증이며 LLM 성능 결과가 아니다.
- [Task Criticality Safety Gate 결과 · 2026-11-15](../experiments/reports/task-criticality-safety-gate-2026-11-15.md) — scripted action 160개 비교; 원본 CSV·validation은 `experiments/results/task_criticality_s235_gate_v03/` 및 `..._approval_fix/`에 있다.
- [초기 Runner·Smoke 실행 점검 · 2026-11-15](../experiments/audits/early-runner-and-smoke-archive-2026-11-15.md) — 과거 runner fixture 결과와 동일한 5개 입력의 2K smoke 재실행을 요약한다.

각 실험의 JSON 및 그림 파일은 실행·분석 코드에서 경로로 참조하므로 현재 하위 폴더 이름을 유지합니다.
