"""Matplotlib figures for ablation, budget pressure, and horizon followups.

Values in the checked-in ablation JSON are read directly. Other followups have
report-only archives in this checkout, so their published table values are
transcribed explicitly below; they are never reconstructed as raw observations.
"""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np

from .common import vector_heatmap


REPORT = "docs/experiments/reports/"
ABLATION_REPORTS = [
    REPORT + "ablation-2k-h100-2026-11-11.md",
    REPORT + "ablation-2k-h100-analysis-2026-11-11.md",
]
TEMPORAL = REPORT + "no-temporal-v104-2k-h100-2026-11-11.md"
PRESSURE = REPORT + "budget-pressure-analysis-2026-11-11.md"
LONG = REPORT + "long-horizon-analysis-2026-11-11.md"
STATE = REPORT + "state-update-h100-analysis-2026-11-11.md"
SUMMARY = "docs/results/ablation_2k_h100_20261111/summary.json"
DELTAS = "docs/results/ablation_2k_h100_20261111/deltas.json"

COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00"]
VARIANTS = [
    "Full", "No constraint\npreservation", "No temporal\n+ no mandatory (v1.0.3)",
    "No mandatory\nstate", "No token-aware\npacking",
]
STRATEGIES = ["Proposed", "Sliding Window", "Vector Top-k", "Utility-per-Token"]
STYLES = [
    dict(color=COLORS[0], marker="o", linestyle="-", markersize=7),
    dict(color=COLORS[1], marker="^", linestyle="--", markersize=7),
    dict(color=COLORS[2], marker="s", linestyle="-.", markersize=10, markerfacecolor="none"),
    dict(color=COLORS[3], marker="D", linestyle=":", markersize=5),
]


def _bars(ctx, ax, values, labels, ylabel, title, ci=None, ylim=None, fmt=".1f"):
    x = np.arange(len(values))
    kwargs = {}
    if ci is not None:
        kwargs["yerr"] = np.array([
            [max(0, value - interval[0]) for value, interval in zip(values, ci)],
            [max(0, interval[1] - value) for value, interval in zip(values, ci)],
        ])
        kwargs.update(capsize=3, error_kw={"elinewidth": 1})
    ax.bar(x, values, color=COLORS[:len(values)], width=0.66, **kwargs)
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if ylim is not None:
        ax.set_ylim(*ylim)
    span = ax.get_ylim()[1] - ax.get_ylim()[0]
    for i, value in enumerate(values):
        top = ci[i][1] if ci is not None else value
        ax.text(i, top + span * 0.025, format(value, fmt), ha="center", va="bottom", fontsize=8)
    ctx.style(ax)


def _matrix(ax, matrix, rows, columns, title, vmax=100, suffix="%", cmap="Blues"):
    values = np.array(matrix, dtype=float)
    im = vector_heatmap(ax, values, vmin=0, vmax=vmax, cmap=cmap)
    ax.set_xticks(range(len(columns)), columns, fontsize=9)
    ax.set_yticks(range(len(rows)), rows, fontsize=9)
    ax.set_title(title)
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            display = f"{value:.1f}".rstrip("0").rstrip(".")
            ax.text(col, row, f"{display}{suffix}", ha="center", va="center",
                    fontsize=9, color="white" if value > vmax * 0.55 else "#17212b")
    return im


def _save(ctx, fig, chart_id, title, sources, note, data):
    ctx.save(fig, chart_id, title, "followups", sources, note, data)


def build(ctx):
    rows = json.loads((ctx.root / SUMMARY).read_text(encoding="utf-8"))
    deltas = json.loads((ctx.root / DELTAS).read_text(encoding="utf-8"))
    _ablation_quality(ctx, rows)
    _ablation_retrieval(ctx, rows)
    _ablation_resources(ctx, rows)
    _temporal_versions(ctx, deltas)
    _pressure_outcomes(ctx)
    _pressure_stages(ctx)
    _pressure_boundary(ctx)
    _horizon_success(ctx)
    _retrieval_recovery(ctx)
    _state_safety(ctx)
    _sliding_subgroups(ctx)
    _input_diagnostics(ctx)


def _ablation_quality(ctx, rows):
    metrics = [
        ("task_success", "Task success", "80 runs / 40 base tasks"),
        ("applicable_protected_recall", "Applicable protected recall", "40 applicable observations"),
        ("temporal_context_correct", "Temporal context correct", "80 runs"),
        ("stale_state_usage_rate", "Stale-state usage (lower is better)", "60 current-state runs"),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(15, 10))
    data = []
    for ax, (metric, label, denominator) in zip(axes.flat, metrics):
        values = [row["metrics"][metric]["mean"] * 100 for row in rows]
        ci = [[end * 100 for end in row["metrics"][metric]["ci95"]] for row in rows]
        _bars(ctx, ax, values, VARIANTS, "Rate (%)", label + "\n" + denominator,
              ci=ci, ylim=(0, 118) if metric != "stale_state_usage_rate" else (0, 45))
        for row, value, interval in zip(rows, values, ci):
            data.append(dict(strategy=row["strategy"], metric=metric, percent=value,
                             ci95_low=interval[0], ci95_high=interval[1],
                             n=row["metrics"][metric]["n"], budget=2048, freeze="1.0.3"))
    _save(ctx, fig, "followup_01_ablation_quality", "Ablation: quality and memory preservation",
          [SUMMARY] + ABLATION_REPORTS,
          "Freeze 1.0.3: no_temporal also disables mandatory state. Checked-in 2K aggregates; "
          "the report states all 400 paired 4K outcomes match. Error bars: reported sample-cluster bootstrap 95% CI.", data)


def _ablation_retrieval(ctx, rows):
    fig, axes = plt.subplots(1, 2, figsize=(15, 6))
    retrieval = ["recall_at_k", "precision_at_k", "mrr"]
    failures = ["general_retrieval_miss", "selection_failure", "budget_selection_failure",
                "state_omission_rate", "state_error_rate", "constraint_violation_rate"]
    data = []
    for ax, metrics, title, cmap in [
        (axes[0], retrieval, "Initial retrieval (higher is better)", "Blues"),
        (axes[1], failures, "Failure diagnostics (lower is better)", "Oranges"),
    ]:
        matrix = [[row["metrics"][metric]["mean"] * 100 for metric in metrics] for row in rows]
        short = {"recall_at_k": "Recall@k", "precision_at_k": "Precision@k", "mrr": "MRR",
                 "general_retrieval_miss": "Retrieval\nmiss", "selection_failure": "Selection\nfailure",
                 "budget_selection_failure": "Budget\nselection", "state_omission_rate": "State\nomission",
                 "state_error_rate": "State\nerror", "constraint_violation_rate": "Constraint\nviolation"}
        _matrix(ax, matrix, [label.replace("\n", " ") for label in VARIANTS],
                [short[metric] for metric in metrics], title, cmap=cmap)
        for row in rows:
            for metric in metrics:
                m = row["metrics"][metric]
                data.append(dict(strategy=row["strategy"], metric=metric, percent=m["mean"] * 100,
                                 n=m["n"], ci95_low=m["ci95"][0] * 100, ci95_high=m["ci95"][1] * 100))
    _save(ctx, fig, "followup_02_ablation_retrieval_failures", "Ablation: retrieval and failure diagnostics",
          [SUMMARY] + ABLATION_REPORTS,
          "Freeze 1.0.3, 2K/H100. Recall, precision, and MRR are scaled to 0-100. "
          "Failure labels can overlap; constraint violation uses n=40, other shown diagnostics n=80. Heatmap shows means.", data)


def _ablation_resources(ctx, rows):
    fig, axes = plt.subplots(1, 3, figsize=(17, 6))
    x = np.arange(5)
    input_values = [r["metrics"]["input_tokens"]["mean"] for r in rows]
    output_values = [r["metrics"]["output_tokens"]["mean"] for r in rows]
    axes[0].bar(x - .18, input_values, .36, label="Peak counted input", color=COLORS[0])
    axes[0].bar(x + .18, output_values, .36, label="Output", color=COLORS[1])
    axes[0].set_title("Mean peak input / output tokens per run")
    axes[0].set_ylabel("Tokens")
    axes[0].legend()
    cost = [r["metrics"]["total_llm_cost"]["mean"] * 1000 for r in rows]
    axes[1].bar(x, cost, color=COLORS)
    axes[1].set_ylabel("Estimated USD per run x 1,000")
    axes[1].set_title("Recorded LLM cost")
    latency_2k = [3378.6, 3270.8, 3990.0, 3272.0, 3316.1]
    latency_4k = [4260.1, 4337.7, 4810.6, 4240.7, 4283.4]
    axes[2].bar(x - .18, latency_2k, .36, color=COLORS[0], label="2K / Darwin arm64")
    axes[2].bar(x + .18, latency_4k, .36, color=COLORS[1], label="4K / Windows AMD64")
    axes[2].set_ylabel("Planning latency (ms)")
    axes[2].set_title("Different runtime environments")
    axes[2].legend(fontsize=8)
    resource_labels = ["Full", "No constraint\npreservation", "No temporal\n+ mandatory", "No mandatory\nstate", "No token-aware\npacking"]
    for ax in axes:
        ax.set_xticks(x, resource_labels, fontsize=8)
        ctx.style(ax)
    data = []
    for i, row in enumerate(rows):
        data.append(dict(strategy=row["strategy"], input_tokens=input_values[i], output_tokens=output_values[i],
                         estimated_usd_per_run=cost[i] / 1000, latency_2k_ms=latency_2k[i], latency_4k_ms=latency_4k[i],
                         n_per_budget=80, freeze="1.0.3"))
    _save(ctx, fig, "followup_03_ablation_resources", "Ablation: tokens, estimated cost, and recorded latency",
          [SUMMARY] + ABLATION_REPORTS,
          "Input is peak counted context, not cumulative provider usage. Token and cost means match between 2K and 4K. Latency includes OS/commit differences and is not a causal budget comparison. "
          "Costs are recorded LLM estimates, excluding CountTokens and infrastructure.", data)


def _temporal_versions(ctx, deltas):
    fig, axes = plt.subplots(1, 3, figsize=(17, 6))
    labels = ["Full\nv1.0.3", "Full\nv1.0.4", "No temporal\nv1.0.3", "No temporal\nv1.0.4"]
    success = [97.5, 97.5, 90, 90]
    ci = [[92.5, 100], [92.5, 100], [80, 97.5], [80, 97.5]]
    _bars(ctx, axes[0], success, labels, "Success (%)", "Success in historical and rerun conditions", ci, (0, 118))
    stale = [0, 0, 100 * 8 / 60, 100 * 8 / 60]
    _bars(ctx, axes[1], stale, labels, "Stale-state usage (%)", "Current-state runs (n=60)", ylim=(0, 25))
    delta_labels = ["No constraint (v1.0.3)", "No temporal + mandatory (v1.0.3)",
                    "No mandatory (v1.0.3)", "No packing (v1.0.3)", "No temporal ONLY (v1.0.4)"]
    delta_values = [d["delta_success"]["mean"] * 100 for d in deltas] + [7.5]
    delta_ci = [[e * 100 for e in d["delta_success"]["ci95"]] for d in deltas] + [[-2.5, 17.5]]
    y = np.arange(len(delta_values))
    errors = np.array([[v - low for v, (low, _) in zip(delta_values, delta_ci)],
                       [high - v for v, (_, high) in zip(delta_values, delta_ci)]])
    axes[2].errorbar(delta_values, y, xerr=errors, fmt="o", color=COLORS[0], capsize=4)
    axes[2].axvline(0, color="#666666", linestyle="--", linewidth=1)
    axes[2].set_yticks(y, delta_labels, fontsize=8)
    axes[2].invert_yaxis()
    axes[2].set_xlabel("Full minus ablation (percentage points)")
    axes[2].set_xlim(-11, 24)
    axes[2].set_title("Paired success difference, reported 95% CI")
    ctx.style(axes[2])
    data = [dict(condition=label.replace("\n", " "), success_percent=value, success_ci95_low=interval[0],
                 success_ci95_high=interval[1], success_n=80, stale_percent=stale[i], stale_n=60)
            for i, (label, value, interval) in enumerate(zip(labels, success, ci))]
    data += [dict(condition=label, paired_delta_pp=value, delta_ci95_low=interval[0], delta_ci95_high=interval[1], n_pairs=80)
             for label, value, interval in zip(delta_labels, delta_values, delta_ci)]
    _save(ctx, fig, "followup_04_temporal_versions_deltas", "Temporal ablation: corrected condition and paired effects",
          [DELTAS, SUMMARY] + ABLATION_REPORTS + [TEMPORAL],
          "v1.0.3 no_temporal disables temporal AND mandatory state; v1.0.4 disables temporal only. "
          "Full rerun reuses the same tasks. Corrected temporal comparison uses historical v1.0.3 Full; CIs include zero.", data)


def _pressure_outcomes(ctx):
    rho = [0, 25, 50, 75, 100]
    full_retention = [0, 1.7, 15, 51.7, 100]
    no_retention = [0, 1.7, 11.7, 40, 98.8]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    series = [("Full", full_retention, [97.5] * 5, COLORS[0], "o"),
              ("No packing", no_retention, [95, 97.5, 97.5, 97.5, 97.5], COLORS[1], "s")]
    data = []
    for strategy, retention, success, color, marker in series:
        axes[0].plot(rho, retention, marker=marker, color=color, label=strategy, linewidth=2)
        axes[1].plot(rho, success, marker=marker, color=color, label=strategy, linewidth=2,
                     markerfacecolor="none" if strategy == "No packing" else color, markersize=8)
        for i in range(5):
            data.append(dict(strategy=strategy, rho_percent=rho[i], final_flexible_retention_percent=retention[i],
                             retention_n=78 if strategy == "No packing" and rho[i] == 0 else 80,
                             success_percent=success[i], success_n=80))
    axes[0].set_title("Final-step Flexible retention")
    axes[0].set_ylabel("Mean retained Flexible count ratio (%)")
    axes[1].set_title("Task success (40 base tasks x 2 repeats)")
    axes[1].set_ylabel("Success (%)")
    for ax in axes:
        ax.set_xticks(rho)
        ax.set_xlabel("Calibrated Flexible allowance, rho (%)")
        ax.set_ylim(-3, 106)
        ax.legend()
        ctx.style(ax)
    _save(ctx, fig, "followup_05_budget_pressure_outcomes", "Budget pressure: memory retention and task success",
          [PRESSURE], "Report-derived values. Rho interpolates between mandatory and all-candidate boundaries; it is not a total-budget fraction. "
          "At rho=0, no-packing retention excludes 2 absent contexts (n=78); success includes all 80 runs. No CIs reported for these means.", data)


def _pressure_stages(ctx):
    rho = [0, 25, 50, 75, 100]
    first = [89.2, 99.2, 100, 100, 100]
    all_steps = [45.4, 51.3, 58.2, 76.2, 100]
    final = [0, 1.7, 15, 51.7, 100]
    selector = [166, 104, 62, 2, 0]
    builder = [20, 62, 78, 78, 0]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    for values, label, color, marker in [(first, "First step", COLORS[0], "o"),
                                         (all_steps, "All valid steps", COLORS[2], "s"),
                                         (final, "Final step", COLORS[1], "^")]:
        axes[0].plot(rho, values, marker=marker, color=color, label=label, linewidth=2)
    axes[0].set_xlabel("Calibrated Flexible allowance, rho (%)")
    axes[0].set_xticks(rho)
    axes[0].set_ylabel("Full Flexible retention (%)")
    axes[0].set_ylim(-3, 106)
    axes[0].set_title("Retention depends on execution stage")
    axes[0].legend()
    x = np.arange(5)
    axes[1].bar(x, selector, color=COLORS[0], label="Selector budget competition")
    axes[1].bar(x, builder, bottom=selector, color=COLORS[1], label="Context Builder formatting overhead")
    axes[1].set_xticks(x, [f"{value}%" for value in rho])
    axes[1].set_xlabel("Calibrated Flexible allowance, rho")
    axes[1].set_ylabel("Memory exclusion events across steps")
    axes[1].set_title("Full: where Flexible was removed")
    axes[1].legend(fontsize=8)
    for ax in axes:
        ctx.style(ax)
    data = [dict(rho_percent=rho[i], first_step_retention_percent=first[i], all_valid_steps_retention_percent=all_steps[i],
                 final_step_retention_percent=final[i], selector_exclusion_events=selector[i], builder_exclusion_events=builder[i],
                 runs_per_pressure=80) for i in range(5)]
    _save(ctx, fig, "followup_06_budget_pressure_stages", "Budget pressure: first, intermediate, and final contexts",
          [PRESSURE], "Report-derived Full condition. Exclusions count events across steps, not unique memories. "
          "Final retention does not describe the first action: at rho=0, first-step retention remains 89.2%.", data)


def _pressure_boundary(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.5))
    strategies = ["Full", "No packing"]
    required = [1452, 1454]
    fixed = [857, 859]
    mandatory = [required[i] - fixed[i] for i in range(2)]
    x = np.arange(2)
    axes[0].bar(x, fixed, color=COLORS[0], label="Fixed input incl. feedback")
    axes[0].bar(x, mandatory, bottom=fixed, color=COLORS[2], label="Remaining required context")
    axes[0].axhline(1452, color=COLORS[1], linestyle="--", label="Effective budget: 1,452")
    axes[0].set_xticks(x, strategies)
    axes[0].set_ylabel("Required next-step input (tokens)")
    axes[0].set_ylim(0, 1640)
    axes[0].set_title("Two-token difference at a narrow boundary")
    axes[0].legend(fontsize=8, loc="lower right")
    for i, value in enumerate(required):
        axes[0].text(i, value + 30, str(value), ha="center")
    axes[1].bar(x, [0, 2], color=[COLORS[0], COLORS[1]], width=.5)
    axes[1].set_xticks(x, strategies)
    axes[1].set_ylim(0, 3)
    axes[1].set_yticks([0, 1, 2, 3])
    axes[1].set_ylabel("Runs stopped before the next LLM call")
    axes[1].set_title("rho=0: one base task, two repeats")
    for ax in axes:
        ctx.style(ax)
    data = [dict(strategy=strategies[i], effective_budget_tokens=1452, fixed_input_tokens=fixed[i],
                 remaining_required_context_tokens=mandatory[i], total_required_tokens=required[i],
                 mandatory_overflow_runs=[0, 2][i], success_n=80,
                 affected_base_task="seed43_approval_019_granted") for i in range(2)]
    _save(ctx, fig, "followup_07_budget_pressure_boundary", "Budget pressure: the additional failure at rho=0",
          [PRESSURE], "Report-derived failure case: same first-step memory set, different ordering and generated feedback. "
          "Nominal budget 1,484 minus safety margin 32. This is a specific trace, not a general packing success guarantee.", data)


def _horizon_success(ctx):
    success = {
        100: [[97.5, 100, 100], [72.5, 70, 67.5], [87.5, 0, 0], [87.5, 0, 0]],
        1000: [[97.5, 100, 100], [75, 67.5, 67.5], [87.5, 0, 0], [87.5, 0, 0]],
    }
    updates = [2, 5, 10]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    data = []
    for ax, horizon in zip(axes, [100, 1000]):
        for strategy, values, style in zip(STRATEGIES, success[horizon], STYLES):
            ax.plot(updates, values, label=strategy, linewidth=2, **style)
            data += [dict(horizon=horizon, state_updates=update, state_versions=update + 1, strategy=strategy,
                          success_percent=value, n_runs=80, n_base_tasks=40, budget_tokens=2048)
                     for update, value in zip(updates, values)]
        ax.set_title(f"Horizon {horizon}")
        ax.set_xticks(updates)
        ax.set_xlabel("State updates (initial state adds one version)")
        ax.set_ylim(-5, 106)
        ax.legend(fontsize=8)
        ctx.style(ax)
    axes[0].set_ylabel("Task success (%)")
    _save(ctx, fig, "followup_08_horizon_state_success", "Long horizon and state updates: six unique cells",
          [LONG, STATE], "Report-derived values; 6 unique cells / 1,920 runs total, each point 40 tasks x 2 repeats. "
          "Vector/Utility success rates overlap. Snapshot selection with Top-k=5; this is not 1,000 online agent turns. No marginal CIs supplied for every point.", data)


def _retrieval_recovery(ctx):
    updates = [2, 5, 10]
    proposed_initial = [90, 2.5, 0]
    baseline_initial = [87.5, 2.5, 0]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5), sharey=True)
    data = []
    for ax, label, initial, final, color in [
        (axes[0], "Proposed", proposed_initial, [100] * 3, COLORS[0]),
        (axes[1], "Vector Top-k / Utility-per-Token", baseline_initial, baseline_initial, COLORS[2]),
    ]:
        ax.plot(updates, initial, "s--", color=COLORS[1], label="Initial Top-5 hit", markersize=10, markerfacecolor="none")
        ax.plot(updates, final, "o-", color=color, label="Final first-step context", linewidth=2)
        ax.set_xticks(updates)
        ax.set_xlabel("State updates")
        ax.set_title(label)
        ax.set_ylim(-5, 108)
        ax.legend(fontsize=8)
        ctx.style(ax)
        for update, hit, context in zip(updates, initial, final):
            data.append(dict(strategy=label, state_updates=update, initial_hit_percent=hit,
                             final_context_hit_percent=context, n_runs_per_horizon=80,
                             horizons_with_same_values="100;1000"))
    axes[0].set_ylabel("Runs containing the one correct State (%)")
    _save(ctx, fig, "followup_09_state_retrieval_recovery", "State updates: initial retrieval and context recovery",
          [LONG, STATE], "Report-derived rates are identical at H100 and H1000 (80 runs per strategy/update/horizon). "
          "This diagnostic tracks one correct State, not all required constraints. Correct State is substantially longer than other versions in these synthetic inputs.", data)


def _state_safety(ctx):
    updates = [2, 5, 10]
    stale_counts = [[0, 0, 0], [0, 0, 0], [10, 60, 58], [10, 60, 58]]
    approval_counts = [[0, 0, 0], [2, 8, 6], [0, 16, 12], [0, 16, 12]]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    data = []
    for metric, ax, counts, denominator, title in [
        ("stale_state_usage", axes[0], stale_counts, 60, "Stale-state usage: current-state runs (n=60)"),
        ("approval_violation", axes[1], approval_counts, 40, "Approval constraint violations (n=40)"),
    ]:
        for strategy, values, style in zip(STRATEGIES, counts, STYLES):
            percentages = [100 * value / denominator for value in values]
            ax.plot(updates, percentages, label=strategy, linewidth=2, **style)
            for update, count, rate in zip(updates, values, percentages):
                data.append(dict(horizon=1000, state_updates=update, strategy=strategy, metric=metric,
                                 events=count, applicable_n=denominator, percent=rate))
        ax.set_title(title)
        ax.set_xlabel("State updates")
        ax.set_xticks(updates)
        ax.set_ylabel("Rate (%) - lower is better")
        ax.set_ylim(-5, 107 if metric == "stale_state_usage" else 50)
        ax.legend(fontsize=8)
        ctx.style(ax)
    _save(ctx, fig, "followup_10_state_safety", "State updates at H1000: stale states and approval violations",
          [LONG, STATE], "Report-derived H1000 values. Vector and Utility overlap; their displayed rates also recur at H100. "
          "Stale usage excludes 20 historical-state requests; zero stale usage alone does not imply task success.", data)


def _sliding_subgroups(ctx):
    counts = [[20, 20, 20], [2, 2, 0], [16, 14, 14], [20, 20, 20]]
    labels = ["Current lookup", "Historical lookup", "Approval pending", "Approved sending"]
    updates = [2, 5, 10]
    fig, ax = plt.subplots(figsize=(10, 5.5))
    _matrix(ax, np.array(counts) * 5, labels, [f"Update {u}" for u in updates],
            "Sliding Window: subgroup success at H100 (20 runs per cell)")
    data = [dict(horizon=100, strategy="Sliding Window", subgroup=labels[i], state_updates=updates[j],
                 successes=counts[i][j], n_runs=20, success_percent=counts[i][j] * 5)
            for i in range(4) for j in range(3)]
    _save(ctx, fig, "followup_11_sliding_subgroups", "Sliding Window: which task types fail as versions increase",
          [STATE], "Report-derived H100 subgroup counts. Stale-state usage is zero for Sliding, but historical versions and approval behavior still produce failures. "
          "Each condition reuses the same ten base tasks per subgroup with two repeats.", data)


def _input_diagnostics(ctx):
    cells = ["H100 / U2", "H100 / U5", "H100 / U10", "H1000 / U2", "H1000 / U5", "H1000 / U10"]
    lows = [128942, 128934, 128930, 127012, 126880, 126896]
    highs = [128946, 128943, 128938, 127040, 127060, 127038]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    y = np.arange(6)
    axes[0].hlines(y, lows, highs, color=COLORS[0], linewidth=5)
    axes[0].scatter(lows, y, color=COLORS[0], s=20)
    axes[0].scatter(highs, y, color=COLORS[0], s=20)
    axes[0].axvline(128000, color="#666666", linestyle="--", label="Target: 128,000")
    axes[0].axvspan(126720, 129280, color=COLORS[0], alpha=.07, label="Target +/- 1%")
    axes[0].set_yticks(y, cells)
    axes[0].invert_yaxis()
    axes[0].set_xlim(126600, 129400)
    axes[0].set_xlabel("Measured original-history tokens")
    axes[0].set_title("History targets are shared; measured lengths vary")
    axes[0].ticklabel_format(style="plain", axis="x", useOffset=False)
    axes[0].legend(fontsize=8, loc="center left")
    axes[1].hlines([0, 1], [548, 81], [641, 114], color=[COLORS[1], COLORS[0]], linewidth=12)
    axes[1].set_yticks([0, 1], ["Correct State", "Other State versions"])
    axes[1].invert_yaxis()
    axes[1].set_xlim(0, 720)
    axes[1].set_xlabel("Tokens per State memory (reported range)")
    axes[1].set_title("Answer-memory padding is a retrieval confound")
    axes[1].text(650, 0, "548-641", va="center", ha="left", fontsize=9)
    axes[1].text(123, 1, "81-114", va="center", ha="left", fontsize=9)
    axes[1].set_ylim(1.6, -.6)
    for ax in axes:
        ctx.style(ax)
    data = [dict(item=cell, metric="history_tokens", min_tokens=low, max_tokens=high, target_tokens=128000)
            for cell, low, high in zip(cells, lows, highs)]
    data += [dict(item="Correct State", metric="memory_tokens", min_tokens=548, max_tokens=641),
             dict(item="Other State versions", metric="memory_tokens", min_tokens=81, max_tokens=114)]
    _save(ctx, fig, "followup_12_horizon_input_diagnostics", "Long horizon inputs: measured history and State-memory lengths",
          [LONG, STATE], "Report-derived min-max ranges, not confidence intervals. History is restored as a synthetic snapshot; "
          "the full 128K history is not sent to the LLM. Correct-State padding and fixed Top-k=5 limit generalization.", data)
