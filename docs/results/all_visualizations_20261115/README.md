# 전체 실험 matplotlib 시각화 · 2026-11-15

총 **54개 SVG**. [미리보기 갤러리](index.html)에서 그림을 고르고 SVG를 열면 됩니다.

- `svg/`: 확대해도 선명한 SVG. 글자와 도형을 편집할 수 있습니다.
- `previews/`: 빠르게 확인하는 PNG 미리보기.
- `data/`: 각 그림에 사용한 수치 CSV. 빈 값은 미측정/해당 없음이며 0으로 바꾸지 않았습니다.
- `manifest.json`: 그림별 출처·주의점·원본 SHA-256 및 보고서 포함 여부.

## 해석과 수정

원본 JSON/CSV가 있는 경우 직접 읽고, 로컬 원본 로그가 없는 실험은 보고서 표의 수치를 코드에 명시했습니다. 보고서 기반 그림의 주석과 출처를 함께 확인하세요. 오프라인 선택·scripted gate·smoke 결과는 LLM 성능과 구분했습니다.

이 갤러리의 Safety Gate 그림은 고정 scripted action 오프라인 평가까지만 포함한다. 2026-11-15 stress LLM replay와 독립 Holdout replay 및 각 smoke는 이 54개 그림에 포함하지 않았다. 수치와 원본 경로는 [stress LLM 보고서](../../experiments/reports/task-criticality-safety-gate-llm-v03-2026-11-15.md), [독립 Holdout 보고서](../../experiments/reports/task-criticality-safety-gate-holdout-v1-2026-11-15.md)에서 확인한다.

신뢰구간은 저장된 값 또는 보고서에 명시된 값만 표시했습니다. Main의 각 점은 40개 기본 과제 × 2회이며, 반복·Budget·Horizon을 독립 과제로 세면 안 됩니다. Main 차수별 보고서는 최종 Main의 부분집합입니다. 실패 태그는 겹칠 수 있어 합계가 실패 실행 수와 일치하지 않을 수 있습니다.

Main의 answer exact match·F1은 전체 집계가 null이어서 그리지 않았습니다. 입력 token 집계의 `input_tokens`는 실행별 최대 단일 Context 크기입니다. 누적 provider 입력량과 구분해 별도 그림으로 표시했습니다.

그림의 영문 표기는 폰트 호환성과 논문/발표 편집을 위한 것입니다. 수정 요청 시 그림 ID를 알려주면 됩니다. SVG의 글자는 텍스트로 유지되며 DejaVu Sans를 사용합니다.

## 다시 생성

저장소 루트의 PowerShell에서:

```powershell
.\.venv\Scripts\python.exe -m pip install -r experiments/visualization/requirements.txt
.\.venv\Scripts\python.exe experiments/plot_all_results.py
```

코드는 `experiments/visualization/`에 있습니다. 출력 경로는 `--output`으로 지정할 수 있습니다. `--groups main preliminary followups criticality audits` 중 일부만 지정해 골라 생성할 수도 있습니다.

전체 ZIP의 `reproduce/`에는 생성 코드와 사용한 집계·보고서가 같은 상대 경로로 들어 있습니다. 해당 폴더에서 Python 환경을 준비하면 원본 저장소 없이도 다시 생성할 수 있습니다.

## Main 본실험

| 그림 ID | 내용 | 파일 |
|---|---|---|
| `main_01_success` | Main: task success by budget and horizon | [SVG](svg/main_01_success.svg) · [CSV](data/main_01_success.csv) |
| `main_02_behavior_reliability` | Main: behavior reliability and safety | [SVG](svg/main_02_behavior_reliability.svg) · [CSV](data/main_02_behavior_reliability.csv) |
| `main_03_context_and_action` | Main: memory context versus action correctness | [SVG](svg/main_03_context_and_action.svg) · [CSV](data/main_03_context_and_action.csv) |
| `main_04_retrieval_diagnostics` | Main: supporting retrieval diagnostics | [SVG](svg/main_04_retrieval_diagnostics.svg) · [CSV](data/main_04_retrieval_diagnostics.csv) |
| `main_05_selection_diagnostics` | Main: selection and state failure diagnostics | [SVG](svg/main_05_selection_diagnostics.svg) · [CSV](data/main_05_selection_diagnostics.csv) |
| `main_06_tokens` | Main: observed token usage per stored run | [SVG](svg/main_06_tokens.svg) · [CSV](data/main_06_tokens.csv) |
| `main_07_cost_and_latency` | Main: observed generation cost and planning latency | [SVG](svg/main_07_cost_and_latency.svg) · [CSV](data/main_07_cost_and_latency.csv) |
| `main_08_paired_effects` | Main: paired success differences with saved 95% CI | [SVG](svg/main_08_paired_effects.svg) · [CSV](data/main_08_paired_effects.csv) |
| `main_09_failure_tags` | Main: failure tags across all 12 cells | [SVG](svg/main_09_failure_tags.svg) · [CSV](data/main_09_failure_tags.csv) |
| `main_10_task_subgroups` | Main: task-family outcomes at the 2K boundary | [SVG](svg/main_10_task_subgroups.svg) · [CSV](data/main_10_task_subgroups.csv) |
| `main_11_performance_cost` | Main: observed success and generation-cost trade-off | [SVG](svg/main_11_performance_cost.svg) · [CSV](data/main_11_performance_cost.csv) |
| `main_12_input_accounting` | Main: cumulative provider usage versus peak counted input | [SVG](svg/main_12_input_accounting.svg) · [CSV](data/main_12_input_accounting.csv) |

## 초기 파일럿·실행 검증

| 그림 ID | 내용 | 파일 |
|---|---|---|
| `preliminary_01_early_pilots` | Early pilots: recorded outcomes with known measurement limitations | [SVG](svg/preliminary_01_early_pilots.svg) · [CSV](data/preliminary_01_early_pilots.csv) |
| `preliminary_02_budget_margin` | Token-budget preflight: safety margin 0 versus 32 | [SVG](svg/preliminary_02_budget_margin.svg) · [CSV](data/preliminary_02_budget_margin.csv) |
| `preliminary_03_output_limit` | Output-limit validation: 512 versus 2048 tokens | [SVG](svg/preliminary_03_output_limit.svg) · [CSV](data/preliminary_03_output_limit.csv) |
| `preliminary_04_output_limit_budget` | Output-limit 2048 validation: selected-input budget breakdown | [SVG](svg/preliminary_04_output_limit_budget.svg) · [CSV](data/preliminary_04_output_limit_budget.csv) |

## Ablation·예산·장기 기억

| 그림 ID | 내용 | 파일 |
|---|---|---|
| `followup_01_ablation_quality` | Ablation: quality and memory preservation | [SVG](svg/followup_01_ablation_quality.svg) · [CSV](data/followup_01_ablation_quality.csv) |
| `followup_02_ablation_retrieval_failures` | Ablation: retrieval and failure diagnostics | [SVG](svg/followup_02_ablation_retrieval_failures.svg) · [CSV](data/followup_02_ablation_retrieval_failures.csv) |
| `followup_03_ablation_resources` | Ablation: tokens, estimated cost, and recorded latency | [SVG](svg/followup_03_ablation_resources.svg) · [CSV](data/followup_03_ablation_resources.csv) |
| `followup_04_temporal_versions_deltas` | Temporal ablation: corrected condition and paired effects | [SVG](svg/followup_04_temporal_versions_deltas.svg) · [CSV](data/followup_04_temporal_versions_deltas.csv) |
| `followup_05_budget_pressure_outcomes` | Budget pressure: memory retention and task success | [SVG](svg/followup_05_budget_pressure_outcomes.svg) · [CSV](data/followup_05_budget_pressure_outcomes.csv) |
| `followup_06_budget_pressure_stages` | Budget pressure: first, intermediate, and final contexts | [SVG](svg/followup_06_budget_pressure_stages.svg) · [CSV](data/followup_06_budget_pressure_stages.csv) |
| `followup_07_budget_pressure_boundary` | Budget pressure: the additional failure at rho=0 | [SVG](svg/followup_07_budget_pressure_boundary.svg) · [CSV](data/followup_07_budget_pressure_boundary.csv) |
| `followup_08_horizon_state_success` | Long horizon and state updates: six unique cells | [SVG](svg/followup_08_horizon_state_success.svg) · [CSV](data/followup_08_horizon_state_success.csv) |
| `followup_09_state_retrieval_recovery` | State updates: initial retrieval and context recovery | [SVG](svg/followup_09_state_retrieval_recovery.svg) · [CSV](data/followup_09_state_retrieval_recovery.csv) |
| `followup_10_state_safety` | State updates at H1000: stale states and approval violations | [SVG](svg/followup_10_state_safety.svg) · [CSV](data/followup_10_state_safety.csv) |
| `followup_11_sliding_subgroups` | Sliding Window: which task types fail as versions increase | [SVG](svg/followup_11_sliding_subgroups.svg) · [CSV](data/followup_11_sliding_subgroups.csv) |
| `followup_12_horizon_input_diagnostics` | Long horizon inputs: measured history and State-memory lengths | [SVG](svg/followup_12_horizon_input_diagnostics.svg) · [CSV](data/followup_12_horizon_input_diagnostics.csv) |

## Criticality·Packing·Safety Gate

| 그림 ID | 내용 | 파일 |
|---|---|---|
| `criticality_01_flexible_budget_sweep` | Critical Flexible preflight: selection across budget levels | [SVG](svg/criticality_01_flexible_budget_sweep.svg) · [CSV](data/criticality_01_flexible_budget_sweep.csv) |
| `criticality_02_pilot_first_step` | Critical Flexible pilot: first-step diagnostics | [SVG](svg/criticality_02_pilot_first_step.svg) · [CSV](data/criticality_02_pilot_first_step.csv) |
| `criticality_03_pilot_resources` | Critical Flexible pilot: input size and reported cost | [SVG](svg/criticality_03_pilot_resources.svg) · [CSV](data/criticality_03_pilot_resources.csv) |
| `criticality_04_holdout_first_action` | Critical Flexible holdout: first-action correctness | [SVG](svg/criticality_04_holdout_first_action.svg) · [CSV](data/criticality_04_holdout_first_action.csv) |
| `criticality_05_holdout_paired_outcomes` | Critical Flexible holdout: paired first-action outcomes | [SVG](svg/criticality_05_holdout_paired_outcomes.svg) · [CSV](data/criticality_05_holdout_paired_outcomes.csv) |
| `criticality_06_packing_alpha_development` | Packing alpha: long critical memory with short distractors | [SVG](svg/criticality_06_packing_alpha_development.svg) · [CSV](data/criticality_06_packing_alpha_development.csv) |
| `criticality_07_selection_policies` | Task Criticality: critical-memory inclusion at 75% budget | [SVG](svg/criticality_07_selection_policies.svg) · [CSV](data/criticality_07_selection_policies.csv) |
| `criticality_08_stress_slot_signals` | S2/S3/S5 stress preflight: selection and slot-detection signals | [SVG](svg/criticality_08_stress_slot_signals.svg) · [CSV](data/criticality_08_stress_slot_signals.csv) |
| `criticality_09_stress_memory_exposure` | S2/S3/S5 stress preflight: context exposure before and after v0.2 | [SVG](svg/criticality_09_stress_memory_exposure.svg) · [CSV](data/criticality_09_stress_memory_exposure.csv) |
| `criticality_10_safety_gate_versions` | Safety Gate v0.3: fixed scripted actions with gate OFF / ON | [SVG](svg/criticality_10_safety_gate_versions.svg) · [CSV](data/criticality_10_safety_gate_versions.csv) |
| `criticality_11_safety_gate_variant_actions` | Safety Gate ON: allowed and blocked scripted actions by variant | [SVG](svg/criticality_11_safety_gate_variant_actions.svg) · [CSV](data/criticality_11_safety_gate_variant_actions.csv) |

## 감사·오프라인 점검

| 그림 ID | 내용 | 파일 |
|---|---|---|
| `audit_01_narrative_retrieval` | Narrative modes: offline retrieval sensitivity | [SVG](svg/audit_01_narrative_retrieval.svg) · [CSV](data/audit_01_narrative_retrieval.csv) |
| `audit_02_narrative_paired_changes` | Narrative modes: paired retrieval changes | [SVG](svg/audit_02_narrative_paired_changes.svg) · [CSV](data/audit_02_narrative_paired_changes.csv) |
| `audit_03_narrative_factorial` | Target/noise padding: 2 x 2 offline retrieval audit | [SVG](svg/audit_03_narrative_factorial.svg) · [CSV](data/audit_03_narrative_factorial.csv) |
| `audit_04_final_context_recovery` | Final-context audit: initial misses recovered | [SVG](svg/audit_04_final_context_recovery.svg) · [CSV](data/audit_04_final_context_recovery.csv) |
| `audit_05_narrative_llm_behavior` | Narrative modes: actual Agent/Tool behavior | [SVG](svg/audit_05_narrative_llm_behavior.svg) · [CSV](data/audit_05_narrative_llm_behavior.csv) |
| `audit_06_narrative_llm_resources` | Narrative modes: measured Agent resources | [SVG](svg/audit_06_narrative_llm_resources.svg) · [CSV](data/audit_06_narrative_llm_resources.csv) |
| `audit_07_attachment_recheck` | Attachment recheck: one task repeated five times per mode | [SVG](svg/audit_07_attachment_recheck.svg) · [CSV](data/audit_07_attachment_recheck.csv) |
| `audit_08_action_time_budget` | Action-time preflight: flexible-memory selection by budget | [SVG](svg/audit_08_action_time_budget.svg) · [CSV](data/audit_08_action_time_budget.csv) |
| `audit_09_constraint_coverage` | Constraint coverage: legacy inputs and balanced generator | [SVG](svg/audit_09_constraint_coverage.svg) · [CSV](data/audit_09_constraint_coverage.csv) |
| `audit_10_constraint_retention_controls` | Constraint retention and separate synthetic evaluator controls | [SVG](svg/audit_10_constraint_retention_controls.svg) · [CSV](data/audit_10_constraint_retention_controls.csv) |
| `audit_11_balanced_generator_validation` | Balanced generator: answer positions and offline validation | [SVG](svg/audit_11_balanced_generator_validation.svg) · [CSV](data/audit_11_balanced_generator_validation.csv) |
| `audit_12_smoke_readiness` | 4K/8K readiness gate: prompt instrumentation | [SVG](svg/audit_12_smoke_readiness.svg) · [CSV](data/audit_12_smoke_readiness.csv) |
| `audit_13_pipeline_smoke` | Pipeline smoke: five fixed inputs and execution checks | [SVG](svg/audit_13_pipeline_smoke.svg) · [CSV](data/audit_13_pipeline_smoke.csv) |
| `audit_14_smoke_execution_archive` | Repeated smoke snapshots: observation archive | [SVG](svg/audit_14_smoke_execution_archive.svg) · [CSV](data/audit_14_smoke_execution_archive.csv) |
| `audit_15_early_runner_fixtures` | Early runner fixtures: development diagnostics only | [SVG](svg/audit_15_early_runner_fixtures.svg) · [CSV](data/audit_15_early_runner_fixtures.csv) |

## 보고서 포함 여부

| 원본 보고서 | 그림 ID |
|---|---|
| ablation-2k-h100-2026-11-11.md | `followup_01_ablation_quality`, `followup_02_ablation_retrieval_failures`, `followup_03_ablation_resources`, `followup_04_temporal_versions_deltas` |
| ablation-2k-h100-analysis-2026-11-11.md | `followup_01_ablation_quality`, `followup_02_ablation_retrieval_failures`, `followup_03_ablation_resources`, `followup_04_temporal_versions_deltas` |
| budget-pressure-analysis-2026-11-11.md | `followup_05_budget_pressure_outcomes`, `followup_06_budget_pressure_stages`, `followup_07_budget_pressure_boundary` |
| critical-flexible-agent-pilot-2026-11-12.md | `criticality_02_pilot_first_step`, `criticality_03_pilot_resources` |
| critical-flexible-holdout-2026-11-12.md | `criticality_04_holdout_first_action`, `criticality_05_holdout_paired_outcomes` |
| critical-flexible-preflight-2026-11-12.md | `criticality_01_flexible_budget_sweep` |
| early-pilot-runs-2026-11-03.md | `preliminary_01_early_pilots` |
| long-horizon-analysis-2026-11-11.md | `followup_08_horizon_state_success`, `followup_09_state_retrieval_recovery`, `followup_10_state_safety`, `followup_12_horizon_input_diagnostics` |
| main-final-analysis-2026-11-09.md | `main_01_success`, `main_02_behavior_reliability`, `main_03_context_and_action`, `main_04_retrieval_diagnostics`, `main_05_selection_diagnostics`, `main_06_tokens`, `main_07_cost_and_latency`, `main_08_paired_effects`, `main_09_failure_tags`, `main_10_task_subgroups`, `main_11_performance_cost`, `main_12_input_accounting` |
| main-phase1-h50-analysis-2026-11-09.md | `main_10_task_subgroups`, `main_12_input_accounting` |
| main-phase2-h10-analysis-2026-11-09.md | `main_10_task_subgroups`, `main_12_input_accounting` |
| no-temporal-v104-2k-h100-2026-11-11.md | `followup_04_temporal_versions_deltas` |
| output-limit-2048-validation-2026-11-09.md | `preliminary_03_output_limit`, `preliminary_04_output_limit_budget` |
| packing-alpha-development-2026-11-12.md | `criticality_06_packing_alpha_development` |
| state-update-h100-analysis-2026-11-11.md | `followup_08_horizon_state_success`, `followup_09_state_retrieval_recovery`, `followup_10_state_safety`, `followup_11_sliding_subgroups`, `followup_12_horizon_input_diagnostics` |
| task-criticality-75-selection-preflight-2026-11-12.md | `criticality_07_selection_policies` |
| task-criticality-s235-stress-preflight-2026-11-12.md | `criticality_08_stress_slot_signals`, `criticality_09_stress_memory_exposure` |
| task-criticality-s235-v02-preflight-2026-11-13.md | `criticality_08_stress_slot_signals`, `criticality_09_stress_memory_exposure` |
| task-criticality-safety-gate-2026-11-15.md | `criticality_10_safety_gate_versions`, `criticality_11_safety_gate_variant_actions` |
| token-budget-preflight-2026-11-08.md | `preliminary_02_budget_margin` |
| action-time-preflight-2026-11-11.md | `audit_08_action_time_budget` |
| attachment-recheck-2026-11-11.md | `audit_07_attachment_recheck` |
| balanced-generator-2026-11-13.md | `audit_09_constraint_coverage`, `audit_11_balanced_generator_validation` |
| constraint-coverage-audit-2026-11-13.md | `audit_09_constraint_coverage`, `audit_10_constraint_retention_controls` |
| early-runner-and-smoke-archive-2026-11-15.md | `audit_14_smoke_execution_archive`, `audit_15_early_runner_fixtures` |
| narrative-bias-audit-2026-11-10.md | `audit_01_narrative_retrieval`, `audit_02_narrative_paired_changes` |
| narrative-factorial-audit-2026-11-11.md | `audit_03_narrative_factorial` |
| narrative-final-context-audit-2026-11-11.md | `audit_04_final_context_recovery` |
| narrative-llm-execution-audit-2026-11-11.md | `audit_05_narrative_llm_behavior`, `audit_06_narrative_llm_resources` |
| pipeline-smoke-2026-11-12.md | `audit_13_pipeline_smoke`, `audit_14_smoke_execution_archive` |
| smoke-4k8k-readiness-2026-11-08.md | `audit_12_smoke_readiness` |
