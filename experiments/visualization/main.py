"""Main 12-cell experiment, reading the preserved canonical aggregates."""

import json

import matplotlib.pyplot as plt
import numpy as np

from .common import COLORS, LABELS, STRATEGIES, vector_heatmap

SUMMARY = "docs/results/main_20261109/summary.json"
DELTAS = "docs/results/main_20261109/deltas.json"
FINAL = "docs/experiments/reports/main-final-analysis-2026-11-09.md"
PHASE1 = "docs/experiments/reports/main-phase1-h50-analysis-2026-11-09.md"
PHASE2 = "docs/experiments/reports/main-phase2-h10-analysis-2026-11-09.md"
BUDGETS = [1024, 2048, 4096, 8192]
HORIZONS = [10, 50, 100]
MARKERS = ["o", "^", "s", "D"]
LINES = ["-", "--", "-.", ":"]
BASE_NOTE = ("Canonical saved aggregates; 40 sample clusters x 2 repeats per strategy/cell. "
             "Bars: saved 95% sample-cluster bootstrap CI; no multiplicity adjustment. "
             "1K Proposed stops before model execution. Overlapping curves retain distinct markers.")


def _draw(ctx, ax, rows, metric, factor=100, percent=True):
    data = []
    for i, (key, label) in enumerate(zip(STRATEGIES, LABELS)):
        selected = sorted([r for r in rows if r["strategy"] == key], key=lambda r: r["context_token_budget"])
        # Even a completely unmeasured series must remain blank (n=0) in CSV.
        for r in selected:
            value = r["metrics"][metric]
            ci = value["ci95"]
            data.append(dict(horizon=r["horizon_turns"], budget=r["context_token_budget"],
                             strategy=key, metric=metric, mean=value["mean"],
                             ci95_low=ci[0] if ci else None, ci95_high=ci[1] if ci else None,
                             n=value["n"], n_samples=value.get("n_samples"), display_factor=factor))
        present = [r for r in selected if r["metrics"][metric]["mean"] is not None]
        if not present:
            continue
        x = [r["context_token_budget"] for r in present]
        m = [r["metrics"][metric] for r in present]
        y = np.array([v["mean"] for v in m]) * factor
        errors = np.array([[max(0, v["mean"] - v["ci95"][0]) * factor for v in m],
                           [max(0, v["ci95"][1] - v["mean"]) * factor for v in m]])
        ax.errorbar(x, y, yerr=errors, label=label, color=COLORS[i], marker=MARKERS[i],
                    linestyle=LINES[i], markersize=[6, 6, 9, 4][i], markerfacecolor="none",
                    linewidth=1.6, capsize=2.5, elinewidth=.7)
    ctx.style(ax)
    ax.set_xscale("log", base=2)
    ax.set_xticks(BUDGETS, ["1K", "2K", "4K", "8K"])
    ax.set_xlabel("Context budget (tokens)")
    if percent:
        ax.set_ylim(-3, 105)
        ax.set_yticks([0, 25, 50, 75, 100])
    else:
        ax.set_ylim(bottom=0)
    return data


def metric_grid(ctx, rows, chart_id, title, specs, note=""):
    fig, axes = plt.subplots(len(specs), 3, figsize=(15.5, 3.6 * len(specs)), squeeze=False)
    data = []
    for j, (metric, ylabel, factor, percent) in enumerate(specs):
        for c, h in enumerate(HORIZONS):
            ax = axes[j, c]
            data += _draw(ctx, ax, [r for r in rows if r["horizon_turns"] == h], metric, factor, percent)
            ax.set_title(f"H{h} | {ylabel}")
            if c == 0:
                ax.set_ylabel(ylabel)
    axes[0, 1].legend(loc="lower center", bbox_to_anchor=(.5, 1.15), ncol=4, frameon=False)
    ctx.save(fig, chart_id, title, "main", [SUMMARY, FINAL], BASE_NOTE + " " + note, data)


def build(ctx):
    rows = json.loads((ctx.root / SUMMARY).read_text(encoding="utf-8"))
    metric_grid(ctx, rows, "main_01_success", "Main: task success by budget and horizon",
                [("task_success", "Task success (%)", 100, True)])
    metric_grid(ctx, rows, "main_02_behavior_reliability", "Main: behavior reliability and safety",
                [("required_state_accuracy", "Required state accuracy (%)", 100, True),
                 ("constraint_violation_rate", "Constraint violations (%)", 100, True),
                 ("stale_state_usage_rate", "Stale state usage (%)", 100, True)],
                "RSA denominator 80; applicable CVR denominator 40; current-state SSUR denominator 60 per cell. "
                "Lower is better for violations/stale usage. A zero violation rate after a preflight stop is not task success.")
    metric_grid(ctx, rows, "main_03_context_and_action", "Main: memory context versus action correctness",
                [("temporal_context_correct", "Temporal context correct (%)", 100, True),
                 ("temporal_version_correct", "Action temporal version correct (%)", 100, True),
                 ("applicable_protected_recall", "Applicable protected recall (%)", 100, True)],
                "Correct memory context does not guarantee correct structured tool arguments.")
    metric_grid(ctx, rows, "main_04_retrieval_diagnostics", "Main: supporting retrieval diagnostics",
                [("recall_at_k", "Recall at k (%)", 100, True),
                 ("precision_at_k", "Precision at k (%)", 100, True),
                 ("mrr", "Mean reciprocal rank (%)", 100, True),
                 ("general_retrieval_miss", "General retrieval miss (%)", 100, True)],
                "Missing Sliding retrieval metrics are not measured, not zero. Retrieval metrics are diagnostics, not task success.")
    metric_grid(ctx, rows, "main_05_selection_diagnostics", "Main: selection and state failure diagnostics",
                [("selection_failure", "Selection failure (%)", 100, True),
                 ("budget_selection_failure", "Budget selection failure (%)", 100, True),
                 ("state_omission_rate", "State omission (%)", 100, True),
                 ("state_error_rate", "State error (%)", 100, True)],
                "Denominators and absent metrics are retained in CSV. Failure metrics can overlap.")
    metric_grid(ctx, rows, "main_06_tokens", "Main: observed token usage per stored run",
                [("input_tokens", "Peak counted input tokens / run", 1, False),
                 ("output_tokens", "Output tokens / run", 1, False)],
                "Input is the per-run maximum counted context, not cumulative provider usage. "
                "Proposed 1K input is unmeasured after preflight stop. H100/2K output-token means use 78 Sliding and 79 Utility runs.")
    metric_grid(ctx, rows, "main_07_cost_and_latency", "Main: observed generation cost and planning latency",
                [("total_llm_cost", "Estimated generation USD / run", 1, False),
                 ("planning_latency_ms", "Planning latency (seconds / run)", .001, False)],
                "H100/2K cost averages use 78 Sliding and 79 Utility runs; others use 80. "
                "Costs omit unknown attempts and are not a total bill. Latency is descriptive: network/time were uncontrolled.")
    paired(ctx)
    failure_tags(ctx, rows)
    subgroups(ctx)
    tradeoff(ctx, rows)
    token_accounting(ctx, rows)


def paired(ctx):
    rows = json.loads((ctx.root / DELTAS).read_text(encoding="utf-8"))
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.2), sharey=True)
    data = []
    for ax, h in zip(axes, HORIZONS):
        for i, strategy in enumerate(STRATEGIES[1:], start=1):
            group = sorted([r for r in rows if r["horizon_turns"] == h and r["baseline"] == strategy],
                           key=lambda r: r["context_token_budget"])
            m = [r["delta_success"] for r in group]
            ax.errorbar(np.arange(4) + (i-2)*.12, [v["mean"] * 100 for v in m],
                        yerr=[[max(0, v["mean"]-v["ci95"][0])*100 for v in m],
                              [max(0, v["ci95"][1]-v["mean"])*100 for v in m]],
                        fmt=MARKERS[i], color=COLORS[i], label=LABELS[i], capsize=4,
                        markersize=7, markerfacecolor="none")
            for r in group:
                v = r["delta_success"]
                data.append(dict(horizon=h, budget=r["context_token_budget"], baseline=strategy,
                                 delta_pp=v["mean"]*100, ci95_low_pp=v["ci95"][0]*100,
                                 ci95_high_pp=v["ci95"][1]*100, n_pairs=r["n_pairs"], n_samples=v["n_samples"]))
        ax.axhline(0, color="#555555", linewidth=1)
        ax.set_xticks(range(4), ["1K", "2K", "4K", "8K"])
        ax.set_title(f"H{h}")
        ax.set_xlabel("Context budget")
        ax.set_ylim(-15, 45)
        ctx.style(ax)
    axes[0].set_ylabel("Proposed minus baseline task success (pp)")
    axes[1].legend(loc="upper center", bbox_to_anchor=(.5, 1.2), ncol=3, frameon=False)
    ctx.save(fig, "main_08_paired_effects", "Main: paired success differences with saved 95% CI", "main",
             [DELTAS, FINAL], "Canonical final aggregation; 40 sample clusters / 80 paired runs per point. "
             "Intervals are exploratory and unadjusted for multiplicity; endpoint zero does not establish superiority or equivalence.", data)


def failure_tags(ctx, rows):
    tags = sorted({k for r in rows for k in r["failure_counts"]})
    fig, axes = plt.subplots(1, 3, figsize=(18, 6.4), sharey=True)
    data = []
    for ax, h in zip(axes, HORIZONS):
        selected = [next(r for r in rows if r["horizon_turns"] == h and r["context_token_budget"] == b
                         and r["strategy"] == s) for b in BUDGETS for s in STRATEGIES]
        values = np.array([[r["failure_counts"].get(t, 0) for r in selected] for t in tags])
        vector_heatmap(ax, values, vmin=0, vmax=80, cmap="YlOrRd")
        for (j, i), v in np.ndenumerate(values):
            if v:
                ax.text(i, j, str(v), ha="center", va="center", fontsize=8,
                        color="white" if v >= 45 else "#222222")
            r = selected[i]
            data.append(dict(horizon=h, budget=r["context_token_budget"], strategy=r["strategy"],
                             failure_tag=tags[j], count=int(v), n_runs=r["n_runs"]))
        ax.set_xticks(range(16), [f"{b//1024}K\n{s}" for b in BUDGETS for s in ["P", "SW", "V", "U"]], fontsize=8)
        ax.set_yticks(range(len(tags)), [t.replace("_", " ") for t in tags])
        ax.set_title(f"H{h} | tag counts / 80 runs")
        ax.set_xlabel("Budget / strategy")
    ctx.save(fig, "main_09_failure_tags", "Main: failure tags across all 12 cells", "main", [SUMMARY, FINAL],
             "Tags overlap: never sum to infer failed runs. Blank cells are zero. P=Proposed, SW=Sliding, V=Vector, U=Utility. "
             "1K preflight stops are retained. Color scale: 0 to 80 tagged runs.", data)


def subgroups(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.4), sharey=True)
    categories = ["Current file", "Historical file", "Approval pending", "Approval granted"]
    values = {
        10: [[20, 20, 18, 20], [20, 12, 14, 20], [16, 20, 18, 16], [16, 20, 18, 16]],
        50: [[20, 20, 18, 20], [20, 2, 16, 20], [16, 20, 18, 16], [16, 20, 18, 16]],
    }
    data = []
    x = np.arange(4)
    for ax, (h, matrix) in zip(axes, values.items()):
        for i, counts in enumerate(matrix):
            bars = ax.bar(x + (i-1.5)*.19, np.array(counts)*5, .19, label=LABELS[i], color=COLORS[i])
            ax.bar_label(bars, labels=[str(c)+"/20" for c in counts], fontsize=7, rotation=90, padding=3)
            data += [dict(horizon=h, budget=2048, subgroup=g, strategy=STRATEGIES[i], successes=n,
                          n=20, success_percent=n*5) for g, n in zip(categories, counts)]
        ax.set_title(f"H{h} / 2K")
        ax.set_xticks(x, ["Current\nfile", "Historical\nfile", "Approval\npending", "Approval\ngranted"])
        ax.set_ylim(0, 128)
        ax.set_yticks([0, 25, 50, 75, 100])
        ctx.style(ax)
    axes[0].set_ylabel("Task success (%)")
    axes[1].legend(loc="upper center", bbox_to_anchor=(-.1, 1.18), ncol=4, frameon=False)
    ctx.save(fig, "main_10_task_subgroups", "Main: task-family outcomes at the 2K boundary", "main",
             [PHASE1, PHASE2, FINAL], "Published phase tables; 10 shared base tasks x 2 repeats per family/strategy. "
             "These are subsets of final Main, not extra independent runs. No family-level intervals were supplied.", data)


def tradeoff(ctx, rows):
    fig, axes = plt.subplots(1, 3, figsize=(15.5, 5.2), sharey=True)
    data = []
    for ax, h in zip(axes, HORIZONS):
        for i, key in enumerate(STRATEGIES):
            for budget in BUDGETS[1:]:
                r = next(r for r in rows if r["horizon_turns"] == h and r["strategy"] == key and r["context_token_budget"] == budget)
                cost = r["metrics"]["total_llm_cost"]
                success = r["metrics"]["task_success"]["mean"]*100
                # Small consistent offsets in annotation only; exact points never jittered.
                ax.scatter(cost["mean"], success, marker=MARKERS[i], s={2048:65,4096:110,8192:165}[budget],
                           facecolors="none", edgecolors=COLORS[i], linewidths=1.5,
                           label=LABELS[i] if budget == 2048 else None)
                if budget == 2048:
                    ax.annotate("2K", (cost["mean"], success), xytext=(5, -13 if i == 3 else 6),
                                textcoords="offset points", color=COLORS[i], fontsize=8)
                data.append(dict(horizon=h, strategy=key, budget=budget, success_percent=success,
                                 estimated_usd_per_observed_run=cost["mean"], cost_n=cost["n"], task_n=r["n_runs"]))
        ax.set_title(f"H{h}")
        ax.set_xlabel("Estimated generation cost (USD / observed run)")
        ax.set_ylim(65, 104)
        ax.ticklabel_format(axis="x", style="sci", scilimits=(-3, -3))
        ctx.style(ax)
    axes[0].set_ylabel("Task success (%)")
    axes[1].legend(loc="upper center", bbox_to_anchor=(.5, 1.2), ncol=4, frameon=False)
    ctx.save(fig, "main_11_performance_cost", "Main: observed success and generation-cost trade-off", "main", [SUMMARY, FINAL],
             "Marker area encodes 2K/4K/8K budget (small/medium/large); concentric points overlap. 1K is omitted here because Proposed did not execute. "
             "H100/2K cost means use 78 Sliding and 79 Utility runs. Stored usage is an estimate, not a total bill; intervals shown in other Main figures.", data)


def token_accounting(ctx, rows):
    cumulative = {
        10: [[2899.1]*3, [3632.9,4858.5,4858.5], [3397.3]*3, [3355.7]*3],
        50: [[2899.1]*3, [3810.5,4858.6,4858.6], [3397.3]*3, [3355.7]*3],
    }
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    data = []
    for j, h in enumerate([10, 50]):
        for i, strategy in enumerate(STRATEGIES):
            group = [next(r for r in rows if r["horizon_turns"] == h and r["strategy"] == strategy
                          and r["context_token_budget"] == b) for b in BUDGETS[1:]]
            peak = [r["metrics"]["input_tokens"]["mean"] for r in group]
            for c, (values, label) in enumerate([(cumulative[h][i], "Cumulative provider prompt usage"),
                                                 (peak, "Peak counted context")]):
                axes[j,c].plot(range(3), values, color=COLORS[i], marker=MARKERS[i], linestyle=LINES[i],
                               markerfacecolor="none", markersize=[6,6,9,4][i], label=LABELS[i])
                axes[j,c].set_title(f"H{h} | {label}")
                axes[j,c].set_ylim(0, 5500)
                axes[j,c].set_xticks(range(3), ["2K", "4K", "8K"])
                axes[j,c].set_xlabel("Context budget")
                axes[j,c].set_ylabel("Mean tokens / run")
                ctx.style(axes[j,c])
            data += [dict(horizon=h, strategy=strategy, budget=b, cumulative_provider_input_mean=cum,
                          peak_counted_context_mean=p, n=80)
                     for b,cum,p in zip(BUDGETS[1:], cumulative[h][i], peak)]
    axes[0,1].legend(loc="lower center", bbox_to_anchor=(-.15,1.14), ncol=4, frameon=False)
    ctx.save(fig, "main_12_input_accounting", "Main: cumulative provider usage versus peak counted input", "main",
             [SUMMARY, PHASE1, PHASE2, FINAL],
             "Cumulative usage: rounded published phase means, no intervals reported. Peak context: saved canonical summary means. "
             "80 live runs per point. A multi-call run can have cumulative input above its single-request budget. These are subsets of final Main.", data)
