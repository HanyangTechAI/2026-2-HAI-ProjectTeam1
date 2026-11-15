"""Matplotlib views of the archived critical-flexible and criticality reports.

Values are transcribed report aggregates, not reconstructed individual runs.
Every saved chart records the exact source report and plotted counts.  The
reports intentionally do not supply confidence intervals for these analyses.
"""

import matplotlib.pyplot as plt
import numpy as np


REPORTS = "docs/experiments/reports/"
SOURCES = {
    "flexible": REPORTS + "critical-flexible-preflight-2026-11-12.md",
    "pilot": REPORTS + "critical-flexible-agent-pilot-2026-11-12.md",
    "holdout": REPORTS + "critical-flexible-holdout-2026-11-12.md",
    "alpha": REPORTS + "packing-alpha-development-2026-11-12.md",
    "selection": REPORTS + "task-criticality-75-selection-preflight-2026-11-12.md",
    "stress": REPORTS + "task-criticality-s235-stress-preflight-2026-11-12.md",
    "v02": REPORTS + "task-criticality-s235-v02-preflight-2026-11-13.md",
    "gate": REPORTS + "task-criticality-safety-gate-2026-11-15.md",
}
COLORS = ["#0072B2", "#D55E00", "#009E73", "#CC79A7"]
AGGREGATE = "Report aggregates; individual run files unavailable locally. No confidence intervals reported."


def _grouped(ctx, ax, labels, series, denominators=None, ylabel="Scenarios (%)"):
    """series: (display name, integer counts) pairs; counts stay in CSV."""
    x = np.arange(len(labels))
    width = 0.78 / len(series)
    records = []
    for i, (name, counts) in enumerate(series):
        denoms = denominators if denominators is not None else [None] * len(labels)
        values = [100 * value / denom if denom else value
                  for value, denom in zip(counts, denoms)]
        bars = ax.bar(x + (i - (len(series) - 1) / 2) * width,
                      values, width, label=name, color=COLORS[i % len(COLORS)])
        for bar, label, value, count, denom in zip(bars, labels, values, counts, denoms):
            text = f"{count}/{denom}" if denom else str(count)
            ax.annotate(text, (bar.get_x() + bar.get_width() / 2, bar.get_height()),
                        xytext=(0, 4), textcoords="offset points", ha="center", va="bottom",
                        fontsize=8)
            record = {"group": label, "series": name, "count": count}
            if denom:
                record.update(denominator=denom, percent=value)
            records.append(record)
    ax.set_xticks(x, labels)
    ax.set_ylabel(ylabel)
    if denominators is not None:
        ax.set_ylim(0, 113)
        ax.set_yticks([0, 25, 50, 75, 100])
    else:
        ax.set_ylim(0, max([v for _, vals in series for v in vals] + [1]) * 1.20)
    ctx.style(ax)
    ax.legend(frameon=False, fontsize=9, loc="upper center", bbox_to_anchor=(0.5, 1.18),
              ncol=min(len(series), 4))
    return records


def _save(ctx, fig, chart_id, title, source_names, note, data):
    ctx.save(fig, chart_id, title, "criticality",
             [SOURCES[name] for name in source_names], note + " " + AGGREGATE, data)


def build(ctx):
    """Generate all report-backed critical-flexible and criticality figures."""
    flexible_budget(ctx)
    pilot(ctx)
    holdout(ctx)
    alpha(ctx)
    criticality_selection(ctx)
    stress(ctx)
    safety_gate(ctx)


def flexible_budget(ctx):
    levels = [0, 25, 50, 75, 100]
    series = [("Feasible critical memory", [0, 40, 120, 160, 160]),
              ("Full: critical selected", [0, 40, 120, 120, 160]),
              ("No packing: critical selected", [0, 40, 120, 160, 160])]
    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    records = []
    markers = ["o", "s", "^"]
    for i, (name, counts) in enumerate(series):
        ax.plot(levels, np.array(counts) / 160 * 100, color=COLORS[i], label=name,
                marker=markers[i], markersize=7 + i, markerfacecolor="none",
                linestyle=[":", "-", "--"][i], linewidth=1.8)
        records.extend({"flexible_budget_percent": level, "series": name,
                        "count": count, "denominator": 160, "percent": count / 1.6}
                       for level, count in zip(levels, counts))
    ax.annotate("Full: 120/160\nNo packing: 160/160", (75, 75), (54, 38),
                arrowprops={"arrowstyle": "->", "color": "#444444"}, fontsize=10)
    ax.set_xlabel("Scenario-normalized flexible budget (%)")
    ax.set_ylabel("Scenarios (%)")
    ax.set_xticks(levels)
    ax.set_ylim(-3, 106)
    ax.set_yticks([0, 25, 50, 75, 100])
    ctx.style(ax)
    ax.legend(frameon=False, loc="upper left", fontsize=9)
    _save(ctx, fig, "criticality_01_flexible_budget_sweep",
          "Critical Flexible preflight: selection across budget levels", ["flexible"],
          "Selection only; 160 scenarios from 40 base tasks; 1,600 context evaluations. "
          "Budget is normalized separately from each scenario's mandatory floor to its ceiling; no LLM generation.", records)


def pilot(ctx):
    fig, ax = plt.subplots(figsize=(12, 5.3))
    labels = ["Expected first\nTool call", "Critical target\nomission", "Required state\ncorrect (email)",
              "Constraint violation\n(email)"]
    data = _grouped(ctx, ax, labels, [("Full", [24, 8, 12, 0]),
                                   ("No packing", [32, 0, 16, 0])], [32, 32, 16, 16])
    _save(ctx, fig, "criticality_02_pilot_first_step",
          "Critical Flexible pilot: first-step diagnostics", ["pilot"],
          "Single-step LLM pilot; 32 scenarios per preset, 64 runs; 75% normalized budget. "
          "Email-only metrics have n=16. All runs ended at max_steps=1; these are not final task completion rates.", data)

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.8))
    data = []
    for ax, metric, vals, ylabel in [
        (axes[0], "Mean first-step input tokens", [893.2, 913.5], "Input tokens / run"),
        (axes[1], "Estimated generation cost", [0.000591, 0.000607], "USD / run"),
    ]:
        bars = ax.bar(["Full", "No packing"], vals, color=COLORS[:2], width=0.55)
        ax.set_title(metric, fontsize=11)
        ax.set_ylabel(ylabel)
        ax.set_ylim(0, max(vals) * 1.17)
        ctx.style(ax)
        for bar, val, name in zip(bars, vals, ["Full", "No packing"]):
            ax.text(bar.get_x() + bar.get_width() / 2, val, f"{val:,.1f}" if "tokens" in ylabel else f"${val:.6f}",
                    ha="center", va="bottom", fontsize=10)
            data.append({"metric": metric, "preset": name, "value": val, "runs": 32})
        if "USD" in ylabel:
            ax.ticklabel_format(axis="y", style="sci", scilimits=(-3, -3))
    _save(ctx, fig, "criticality_03_pilot_resources",
          "Critical Flexible pilot: input size and reported cost", ["pilot"],
          "Historical reported estimates for this one-step pilot; 32 runs per preset. "
          "Reported cost is approximate and is not a current pricing estimate or evidence of general cost superiority.", data)


def holdout(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.3), gridspec_kw={"width_ratios": [1.7, 1]})
    data = _grouped(ctx, axes[0], ["A", "B", "C", "D", "All"],
                    [("Full", [39, 40, 0, 38, 117]), ("No packing", [36, 40, 39, 38, 153])],
                    [40, 40, 40, 40, 160])
    axes[0].set_title("By length condition", pad=55)
    more = _grouped(ctx, axes[1], ["File lookup", "Approved email"],
                    [("Full", [57, 60]), ("No packing", [73, 80])], [80, 80])
    axes[1].set_title("By task family", pad=55)
    data = [{"breakdown": "length condition", **row} for row in data] + [
        {"breakdown": "task family", **row} for row in more]
    _save(ctx, fig, "criticality_04_holdout_first_action",
          "Critical Flexible holdout: first-action correctness", ["holdout"],
          "160 paired scenarios from 40 base tasks; separate seeds and templates; one LLM run per preset/scenario. "
          "A=short/short, B=short/long, C=long/short, D=long/long (critical/distractor). "
          "Correctness verifies the call, required arguments and tool effect; max_steps=1, not final task completion.", data)

    fig, ax = plt.subplots(figsize=(11, 5.0))
    groups = ["A", "B", "C", "D"]
    outcomes = [("Both correct", [36, 40, 0, 38]), ("Both incorrect", [1, 0, 1, 2]),
                ("Full only correct", [3, 0, 0, 0]), ("No packing only correct", [0, 0, 39, 0])]
    left = np.zeros(4)
    data = []
    for i, (name, values) in enumerate(outcomes):
        ax.barh(groups, values, left=left, color=COLORS[i], label=name, height=0.62)
        for j, count in enumerate(values):
            if count:
                ax.text(left[j] + count / 2, j, str(count), ha="center", va="center", fontsize=10,
                        color="white" if i in [0, 1, 2] else "#111111")
            data.append({"condition": groups[j], "paired_outcome": name, "count": count, "denominator": 40})
        left += values
    ax.invert_yaxis()
    ax.set_xlim(0, 40)
    ax.set_xlabel("Paired scenarios (40 per condition)")
    ax.set_ylabel("Length condition")
    ax.legend(frameon=False, loc="upper center", bbox_to_anchor=(0.5, 1.18), ncol=2)
    ctx.style(ax)
    _save(ctx, fig, "criticality_05_holdout_paired_outcomes",
          "Critical Flexible holdout: paired first-action outcomes", ["holdout"],
          "Totals: both correct 114, both incorrect 4, Full only 3, No packing only 39. "
          "The same 40 base tasks occur in A-D; first-action outcomes do not measure final completion.", data)


def alpha(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2))
    data = _grouped(ctx, axes[0], ["0", "0.2", "0.5", "1"],
                    [("Critical memory selected", [40, 31, 0, 0])], [40] * 4)
    axes[0].set_xlabel("Length penalty exponent alpha")
    axes[0].set_title("Selection-only development preflight", pad=34)
    more = _grouped(ctx, axes[1], ["0", "0.2", "1"],
                    [("File lookup", [20, 18, 0]), ("Approved email", [20, 13, 0])], [20] * 3,
                    ylabel="First-action correct (%)")
    axes[1].set_xlabel("Length penalty exponent alpha")
    axes[1].set_title("Single-step LLM development runs", pad=34)
    data = [{"evaluation": "selection only", **row} for row in data] + [
        {"evaluation": "LLM first action", **row} for row in more]
    _save(ctx, fig, "criticality_06_packing_alpha_development",
          "Packing alpha: long critical memory with short distractors", ["alpha"],
          "Condition C, 75% normalized budget; same development data, not an independent holdout. "
          "Score=utility / tokens**alpha. LLM: 40 scenarios per tested alpha, 120 one-step runs; alpha=0.5 was selection-only. "
          "Critical context inclusion equals first-action correctness in all reported LLM arms.", data)


def criticality_selection(ctx):
    fig, ax = plt.subplots(figsize=(11.5, 5.1))
    data = _grouped(ctx, ax, ["A", "B", "C", "D", "All"],
                    [("Full", [40, 40, 0, 40, 120]), ("No packing", [40, 40, 40, 40, 160]),
                     ("Criticality", [40, 40, 40, 40, 160])], [40, 40, 40, 40, 160],
                    ylabel="Critical memory included (%)")
    ax.set_xlabel("Length condition (critical / distractor)")
    _save(ctx, fig, "criticality_07_selection_policies",
          "Task Criticality: critical-memory inclusion at 75% budget", ["selection"],
          "480 selection-only contexts; same 160 development scenarios as the earlier Flexible preflight, not a new holdout. "
          "A=short/short, B=short/long, C=long/short, D=long/long. "
          "All policies retrieved 160/160 critical memories; no LLM generation or tool execution.", data)


def stress(ctx):
    fig, ax = plt.subplots(figsize=(13.2, 5.6))
    labels = ["S2: gold\nincluded", "S3_VALID: gold\nincluded", "S3_EMPTY: missing\nslot detected", "S5: conflicting\nslot detected"]
    data = _grouped(ctx, ax, labels,
                    [("Full", [0, 40, 0, 0]), ("No packing", [0, 24, 0, 0]),
                     ("Criticality v0.1", [40, 40, 40, 40]),
                     ("Criticality v0.2", [40, 40, 40, 40])], [40] * 4)
    _save(ctx, fig, "criticality_08_stress_slot_signals",
          "S2/S3/S5 stress preflight: selection and slot-detection signals", ["stress", "v02"],
          "Each measure has 40 scenarios. v0.1 and v0.2 reuse the same development dataset; 480 contexts per run. "
          "S2 reverses importance; S3 tests valid/missing evidence; S5 tests conflict. "
          "Selection and detection signals are different measures; neither execution blocking nor LLM correctness is measured.", data)

    fig, ax = plt.subplots(figsize=(12, 5.4))
    labels = ["S3_VALID:\ninvalid evidence", "S3_EMPTY:\ninvalid evidence", "S5:\nconflicting memories"]
    data = _grouped(ctx, ax, labels,
                    [("Full", [74, 76, 80]), ("No packing", [74, 80, 80]),
                     ("Criticality v0.1", [74, 76, 80]), ("Criticality v0.2", [0, 0, 80])],
                    ylabel="Memory items exposed in context (count)")
    _save(ctx, fig, "criticality_09_stress_memory_exposure",
          "S2/S3/S5 stress preflight: context exposure before and after v0.2", ["stress", "v02"],
          "Bars count memory items summed across 40 scenarios per variant, not scenario success rates. "
          "v0.2 removes invalid S3 evidence, while S5's two conflicting memories remain exposed in every scenario. "
          "Selection-only development data; no LLM generation or tool execution.", data)


def safety_gate(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.2), sharey=True)
    labels = ["Wrong tool\ncalls executed", "False\nblocks", "Tool calls\nexecuted", "Expected effects\nverified"]
    data = []
    for ax, freeze in zip(axes, ["1.0.4", "1.0.5 approval fix"]):
        more = _grouped(ctx, ax, labels,
                        [("Gate OFF", [80, 0, 160, 80]), ("Gate ON", [0, 0, 80, 80])],
                        ylabel="Scenario / action count")
        ax.set_ylim(0, 192)
        ax.set_title("Freeze " + freeze, pad=36)
        data.extend({"freeze": freeze, **row} for row in more)
    _save(ctx, fig, "criticality_10_safety_gate_versions",
          "Safety Gate v0.3: fixed scripted actions with gate OFF / ON", ["gate"],
          "160 scenarios and 320 pipeline runs per freeze version. Gate ON allowed all 80 valid actions and blocked all 80 invalid actions. "
          "ScriptedPlanner, max_steps=1, LLM generation=0; these counts do not estimate LLM safety or task success. "
          "Freeze 1.0.5 changed the blocking reason in 40 approval-email rows, while aggregate outcomes stayed identical.", data)

    fig, ax = plt.subplots(figsize=(11.5, 5))
    data = _grouped(ctx, ax, ["S2", "S3_VALID", "S3_EMPTY", "S5"],
                    [("Allowed valid action", [40, 40, 0, 0]),
                     ("Blocked invalid action", [0, 0, 40, 40])], [40] * 4)
    _save(ctx, fig, "criticality_11_safety_gate_variant_actions",
          "Safety Gate ON: allowed and blocked scripted actions by variant", ["gate"],
          "Identical aggregate results in Freeze 1.0.4 and 1.0.5; 40 scenarios per variant. "
          "Fixed valid S2/S3_VALID actions were allowed; deliberately ungrounded S3_EMPTY/S5 actions were blocked before ToolExecutor. "
          "No LLM generation or multi-step agent evaluation.", data)
