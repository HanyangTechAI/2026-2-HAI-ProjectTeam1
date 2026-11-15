"""Matplotlib figures for diagnostic audits and smoke execution archives.

The untracked raw audit directories are absent from this checkout. Literal
tables below reproduce the published reports; available coverage JSON is read
directly. These diagnostics are never pooled with the Main experiments.
"""

from __future__ import annotations

import json

import matplotlib.pyplot as plt
import numpy as np

from .common import vector_heatmap


AUDITS = "docs/experiments/audits/"
BLUE, ORANGE, GREEN, PURPLE = "#0072B2", "#D55E00", "#009E73", "#CC79A7"
STRATEGIES = ["Proposed", "Sliding Window", "Vector Top-k", "Utility-per-Token"]
COLORS = [BLUE, ORANGE, GREEN, PURPLE]


def _source(filename):
    return AUDITS + filename


def _save(ctx, fig, chart_id, title, sources, note, data):
    for ax in fig.axes:
        # Heatmaps and colorbars manage their own grids and spines.
        if not getattr(ax, "_archive_heatmap", False) and ax.get_label() != "<colorbar>":
            ctx.style(ax)
    ctx.save(fig, chart_id, title, "audits", sources, note, data)


def _bars(ax, labels, values, color=BLUE, denominator=None, ylim=None):
    x = np.arange(len(labels))
    colors = color if isinstance(color, list) else [color] * len(labels)
    bars = ax.bar(x, values, color=colors, width=0.64)
    ax.set_xticks(x, labels)
    if ylim:
        ax.set_ylim(*ylim)
    for bar, value in zip(bars, values):
        text = f"{value:g}" if denominator is None else f"{value:g}/{denominator}"
        ax.annotate(text, (bar.get_x() + bar.get_width() / 2, value),
                    xytext=(0, 4), textcoords="offset points", ha="center", fontsize=9)
    return bars


def _narrative_retrieval(ctx):
    source = _source("narrative-bias-audit-2026-11-10.md")
    modes = ["1: repeated", "2: natural", "3: mixed"]
    records = [
        dict(mode=1, pool="baseline", state_recall_pct=87.5, general_recall_pct=90.0,
             target_rank=4.225, target_similarity=.6000, marker_mean=7.2, n_scenarios=40),
        dict(mode=1, pool="proposed_general", state_recall_pct=90, general_recall_pct=92.5,
             target_rank=3.575, target_similarity=.6000, marker_mean=7.2, n_scenarios=40),
        dict(mode=2, pool="baseline", state_recall_pct=80, general_recall_pct=82.5,
             target_rank=4.5, target_similarity=.5988, marker_mean=0, n_scenarios=40),
        dict(mode=2, pool="proposed_general", state_recall_pct=80, general_recall_pct=82.5,
             target_rank=3.75, target_similarity=.5988, marker_mean=0, n_scenarios=40),
        dict(mode=3, pool="baseline", state_recall_pct=82.5, general_recall_pct=85,
             target_rank=4.2, target_similarity=.6119, marker_mean=3.475, n_scenarios=40),
        dict(mode=3, pool="proposed_general", state_recall_pct=87.5, general_recall_pct=90,
             target_rank=3.55, target_similarity=.6119, marker_mean=3.475, n_scenarios=40),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 9))
    for ax, key, title, ylabel in zip(axes.flat,
            ["state_recall_pct", "general_recall_pct", "target_rank", "target_similarity"],
            ["State Recall@5", "General Recall@5", "Mean target rank", "Mean target similarity"],
            ["Recall (%)", "Recall (%)", "Rank (lower is better)", "Cosine similarity"]):
        for pool, label, color, marker in [("baseline", "Baseline pool", GREEN, "s"),
                ("proposed_general", "Proposed general pool", BLUE, "o")]:
            rows = [r for r in records if r["pool"] == pool]
            ax.plot(range(3), [r[key] for r in rows], marker=marker,
                    color=color, label=label, linewidth=2, markerfacecolor="none",
                    markersize=9 if pool == "baseline" else 5)
        ax.set_xticks(range(3), modes)
        ax.set_title(title)
        ax.set_ylabel(ylabel)
        if key.endswith("pct"):
            ax.set_ylim(0, 105)
        elif key == "target_rank":
            ax.set_ylim(0, 5.2)
        else:
            ax.set_ylim(.55, .65)
    axes[0, 0].legend(loc="lower left", fontsize=9)
    _save(ctx, fig, "audit_01_narrative_retrieval", "Narrative modes: offline retrieval sensitivity",
          [source], "Published report table; 40 paired tasks per mode; Proposed general pool is retrieval only, before selection. No Agent/LLM generation or confidence intervals.", records)

    paired = [
        ("baseline", "1 -> 2", 17, .9417, -7.5, .275, 2, 5),
        ("baseline", "1 -> 3", 15, .9667, -5, -.025, 1, 3),
        ("baseline", "2 -> 3", 13, .9750, 2.5, -.300, 2, 1),
        ("proposed_general", "1 -> 2", 11, .9500, -10, .175, 1, 5),
        ("proposed_general", "1 -> 3", 9, .9750, -2.5, -.025, 1, 2),
        ("proposed_general", "2 -> 3", 9, .9750, 7.5, -.200, 3, 0),
    ]
    records = [dict(pool=p, comparison=c, changed_top5=n, n_scenarios=40, jaccard=j,
                    state_recall_delta_pp=d, rank_delta=r, improved=i, worsened=w)
               for p, c, n, j, d, r, i, w in paired]
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    x = np.arange(3)
    for i, (pool, label, color) in enumerate([("baseline", "Baseline pool", GREEN),
                ("proposed_general", "Proposed general pool", BLUE)]):
        rows = [r for r in records if r["pool"] == pool]
        xx = x + (i - .5) * .34
        axes[0].bar(xx, [r["changed_top5"] for r in rows], .32, label=label, color=color)
        axes[1].bar(xx, [r["state_recall_delta_pp"] for r in rows], .32, label=label, color=color)
    for ax in axes:
        ax.set_xticks(x, ["Mode 1 -> 2", "Mode 1 -> 3", "Mode 2 -> 3"])
    axes[0].set_ylim(0, 40)
    axes[0].set_ylabel("Tasks with changed Top-5 set (out of 40)")
    axes[0].legend(fontsize=9)
    axes[1].axhline(0, color="#555555", linewidth=.8)
    axes[1].set_ylim(-15, 15)
    axes[1].set_ylabel("State Recall@5 change (percentage points)")
    _save(ctx, fig, "audit_02_narrative_paired_changes", "Narrative modes: paired retrieval changes",
          [source], "Published report table; each contrast reuses the same 40 tasks. Differences are descriptive; mode comparisons are not additional independent samples.", records)


def _factorial(ctx):
    source = _source("narrative-factorial-audit-2026-11-11.md")
    records = []
    fig, axes = plt.subplots(1, 3, figsize=(15, 5.5), gridspec_kw={"width_ratios": [1, 1, 1.8]})
    for ax, pool, label, vals in zip(axes[:2], ["baseline", "proposed_general"],
            ["Baseline pool", "Proposed general pool"],
            [[[87.5, 87.5], [80, 80]], [[90, 90], [80, 80]]]):
        vector_heatmap(ax, vals, vmin=0, vmax=100, cmap="Blues", equal=True)
        ax.set_xticks([0, 1], ["N1 repeated", "N2 natural"])
        ax.set_yticks([0, 1], ["T1 repeated", "T2 natural"])
        ax.set_xlabel("Noise padding")
        ax.set_ylabel("Target padding")
        ax.set_title(label + "\nState Recall@5")
        for ti, row in enumerate(vals):
            for ni, v in enumerate(row):
                ax.text(ni, ti, f"{v:g}%", ha="center", va="center", color="white", fontsize=14)
                records.append(dict(kind="condition", pool=pool, target=ti+1, noise=ni+1,
                                    recall_pct=v, n_scenarios=40))
    effects = [("baseline", "Target", -7.5, -20, 5), ("proposed_general", "Target", -10, -22.5, 0),
               ("baseline", "Noise", 0, 0, 0), ("proposed_general", "Noise", 0, 0, 0),
               ("baseline", "Interaction", 0, 0, 0), ("proposed_general", "Interaction", 0, 0, 0)]
    for j, (pool, effect, mean, low, high) in enumerate(effects):
        color = GREEN if pool == "baseline" else BLUE
        axes[2].errorbar(mean, j, xerr=[[mean-low], [high-mean]], fmt="o", color=color,
                         capsize=4, markersize=6)
        records.append(dict(kind="effect", pool=pool, effect=effect, effect_pp=mean,
                            ci95_low_pp=low, ci95_high_pp=high, n_scenarios=40))
    axes[2].set_yticks(range(6), [f"{effect}: {'Baseline' if pool=='baseline' else 'Proposed pool'}"
                               for pool, effect, *_ in effects])
    axes[2].invert_yaxis()
    axes[2].axvline(0, color="#555555", linewidth=.8)
    axes[2].set_xlim(-27, 10)
    axes[2].set_xlabel("Recall effect (percentage points)")
    axes[2].set_title("Paired effects and 95% CI")
    _save(ctx, fig, "audit_03_narrative_factorial", "Target/noise padding: 2 x 2 offline retrieval audit",
          [source], "Published report tables; 40 paired tasks; paired bootstrap 4,000 resamples. Target effects match at both noise settings; noise effects match at both target settings. No Agent execution.", records)


def _context(ctx):
    source = _source("narrative-final-context-audit-2026-11-11.md")
    steps = ["Initial\nretrieval", "Chain\nexpansion", "Temporal\nresolution", "Required-state\nresolution", "Selector\noutput", "Final\ncontext"]
    records = []
    fig, axes = plt.subplots(1, 2, figsize=(13.5, 5.8))
    for mode, values, color, marker in [(1, [36, 40, 40, 40, 40, 40], BLUE, "o"),
                                        (2, [32, 40, 40, 40, 40, 40], ORANGE, "s")]:
        axes[0].plot(range(6), [v/40*100 for v in values], marker=marker, color=color,
                     label=f"Mode {mode}", linewidth=2, markerfacecolor="none" if mode == 2 else color)
        records.extend(dict(kind="stage", mode=mode, stage=step.replace("\n", " "),
                            complete=n, denominator=40, complete_pct=n/40*100)
                       for step, n in zip(steps, values))
    axes[0].set_xticks(range(6), steps, rotation=20, ha="right")
    axes[0].set_ylabel("Tasks with complete required state (%)")
    axes[0].set_ylim(0, 108)
    axes[0].legend(loc="lower right")
    axes[0].set_title("Recovery through the Proposed pipeline")
    means, lows, highs = [1356.05, 1358.30], [1334, 1330], [1390, 1394]
    axes[1].bar([0, 1], means, color=[BLUE, ORANGE], width=.6)
    axes[1].errorbar([0, 1], means, yerr=[np.array(means)-lows, np.array(highs)-means],
                    fmt="none", color="#222222", capsize=5)
    axes[1].axhline(2016, color="#555555", linestyle="--", label="Effective budget: 2,016")
    axes[1].set_xticks([0, 1], ["Mode 1", "Mode 2"])
    axes[1].set_ylim(0, 2250)
    axes[1].set_ylabel("Request-aware CountTokens")
    axes[1].set_title("Mean final-context tokens and observed range")
    axes[1].legend(loc="upper right", fontsize=9)
    for mode, mean, lo, hi in zip([1, 2], means, lows, highs):
        records.append(dict(kind="tokens", mode=mode, mean_tokens=mean, min_tokens=lo,
                            max_tokens=hi, effective_budget=2016, n_scenarios=40))
    _save(ctx, fig, "audit_04_final_context_recovery", "Final-context audit: initial misses recovered",
          [source], "Published report tables; 40 paired tasks per mode, no LLM generation. Token whiskers show observed min-max, not confidence intervals. Final context overflow: 0/80 rows.", records)


def _llm(ctx):
    source = _source("narrative-llm-execution-audit-2026-11-11.md")
    records = [
        dict(mode=1, strategy="Proposed", successes=39, n=40, success_pct=97.5, low=92.5, high=100,
             stale=0, stale_n=30, constraint_violations=0, constraint_n=20,
             input_tokens=1469.98, output_tokens=558.58, latency_ms=4554, cost_usd=.0015626),
        dict(mode=1, strategy="Vector Top-k", successes=35, n=40, success_pct=87.5, low=75, high=97.5,
             stale=5, stale_n=30, constraint_violations=0, constraint_n=20,
             input_tokens=1616.15, output_tokens=824.10, latency_ms=5207, cost_usd=.0020855),
        dict(mode=2, strategy="Proposed", successes=40, n=40, success_pct=100, low=100, high=100,
             stale=0, stale_n=30, constraint_violations=0, constraint_n=20,
             input_tokens=1473.85, output_tokens=567.05, latency_ms=4214, cost_usd=.0015769),
        dict(mode=2, strategy="Vector Top-k", successes=32, n=40, success_pct=80, low=65, high=92.5,
             stale=8, stale_n=30, constraint_violations=0, constraint_n=20,
             input_tokens=1569.10, output_tokens=825.55, latency_ms=4866, cost_usd=.0020544),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8))
    for i, strategy, color in [(0, "Proposed", BLUE), (1, "Vector Top-k", GREEN)]:
        rows = [r for r in records if r["strategy"] == strategy]
        x = np.arange(2) + (i-.5)*.34
        vals = [r["success_pct"] for r in rows]
        axes[0].bar(x, vals, .32, color=color, label=strategy,
                    yerr=[[r["success_pct"]-r["low"] for r in rows],
                          [r["high"]-r["success_pct"] for r in rows]], capsize=4)
        for xx, r in zip(x, rows):
            axes[0].text(xx, 8, f"{r['successes']}/40", color="white", ha="center", fontsize=10)
        axes[1].bar(x, [r["stale"]/30*100 for r in rows], .32, color=color, label=strategy)
        for xx, r in zip(x, rows):
            axes[1].annotate(f"{r['stale']}/30", (xx, r["stale"]/30*100), xytext=(0, 4),
                             textcoords="offset points", ha="center", fontsize=10)
    for ax in axes:
        ax.set_xticks([0, 1], ["Mode 1: repeated", "Mode 2: natural"])
        ax.set_ylim(0, 112)
    axes[0].set_ylabel("Action-level task success (%)")
    axes[0].set_title("Task success with bootstrap 95% CI")
    axes[0].legend(loc="upper center", ncol=2, fontsize=9)
    axes[1].set_ylabel("Stale-state use among applicable tasks (%)")
    axes[1].set_title("Stale-state use (denominator: 30)")
    _save(ctx, fig, "audit_05_narrative_llm_behavior", "Narrative modes: actual Agent/Tool behavior",
          [source], "Published report table; same 40 paired tasks, one execution per strategy/mode. Task bootstrap: 2,000 resamples. Constraint violations: 0/20 in all four conditions; not pooled with Main.", records)

    fig, axes = plt.subplots(2, 2, figsize=(12.5, 9))
    for ax, key, label, scale in zip(axes.flat,
            ["input_tokens", "output_tokens", "latency_ms", "cost_usd"],
            ["Mean input tokens / run", "Mean output tokens / run", "Mean planning latency (seconds)",
             "Mean recorded cost (USD / 1,000 runs)"], [1, 1, .001, 1000]):
        for i, strategy, color in [(0, "Proposed", BLUE), (1, "Vector Top-k", GREEN)]:
            rows = [r for r in records if r["strategy"] == strategy]
            ax.bar(np.arange(2)+(i-.5)*.34, [r[key]*scale for r in rows], .32, color=color, label=strategy)
        ax.set_xticks([0, 1], ["Mode 1", "Mode 2"])
        ax.set_ylabel(label)
        ax.set_ylim(bottom=0)
    axes[0, 0].legend(fontsize=9)
    axes[0, 0].set_ylim(0, 2000)
    _save(ctx, fig, "audit_06_narrative_llm_resources", "Narrative modes: measured Agent resources",
          [source], "Published report means; 40 executions per condition. Cost is provider-usage-based estimated cost, scaled to 1,000 runs for readability; not a billing total. No resource confidence intervals reported.", records)


def _attachment(ctx):
    source = _source("attachment-recheck-2026-11-11.md")
    labels = ["Target\nretrieved", "Target\nselected", "Context\nstate correct", "Required state\nin action", "Correct\nattachment", "Task\nsuccess"]
    data = []
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), gridspec_kw={"width_ratios": [1.8, 1]})
    for i, mode, values, color in [(0, 1, [5, 5, 5, 0, 0, 0], BLUE),
                                   (1, 2, [5]*6, ORANGE)]:
        x = np.arange(6) + (i-.5)*.34
        axes[0].bar(x, values, .32, label=f"Mode {mode}", color=color)
        data.extend(dict(kind="behavior", mode=mode, metric=label.replace("\n", " "),
                         count=value, n_repeats=5, independent_tasks=1)
                    for label, value in zip(labels, values))
    axes[0].set_xticks(range(6), labels, rotation=20, ha="right")
    axes[0].set_ylim(0, 6.2)
    axes[0].set_ylabel("Observed repeats (out of 5)")
    axes[0].set_title("Single approval task: context-to-action transfer")
    axes[0].legend(loc="upper right", ncol=2)
    x = np.arange(2)
    axes[1].bar(x-.17, [1540, 1564], .32, color=BLUE, label="Input tokens")
    axes[1].bar(x+.17, [864, 898], .32, color=GREEN, label="Output tokens")
    axes[1].set_xticks(x, ["Mode 1", "Mode 2"])
    axes[1].set_ylabel("Mean tokens / run")
    axes[1].set_ylim(0, 1950)
    axes[1].set_title("Provider usage")
    axes[1].legend(fontsize=9)
    data.extend([dict(kind="resources", mode=1, input_tokens=1540, output_tokens=864,
                      latency_ms=5090, cost_usd=.0020230, n_repeats=5, independent_tasks=1),
                 dict(kind="resources", mode=2, input_tokens=1564, output_tokens=898,
                      latency_ms=4973, cost_usd=.0020815, n_repeats=5, independent_tasks=1)])
    _save(ctx, fig, "audit_07_attachment_recheck", "Attachment recheck: one task repeated five times per mode",
          [source], "Published report tables; ONE fixed approval task, five repeats per mode. Repeats are not five independent tasks or a general mode-effect estimate. No actual email was sent.", data)


def _action_time(ctx):
    source = _source("action-time-preflight-2026-11-11.md")
    levels = [0, 25, 50, 75, 100]
    full, nopack, different = [0, 1.7, 15, 51.7, 100], [0, 1.7, 11.7, 40, 100], [0, 0, 2, 10, 0]
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8))
    axes[0].plot(levels, full, "o-", label="Full", color=BLUE)
    axes[0].plot(levels, nopack, "s--", label="No budget-aware packing", color=ORANGE)
    axes[0].set_ylabel("Mean flexible-memory recovery (%)")
    axes[0].set_ylim(0, 107)
    axes[0].legend(fontsize=9)
    axes[1].bar(levels, different, width=13, color=PURPLE)
    for x, n in zip(levels, different):
        axes[1].annotate(f"{n}/40", (x, n), xytext=(0, 4), textcoords="offset points", ha="center")
    axes[1].set_ylim(0, 40)
    axes[1].set_ylabel("Paired tasks with different selected sets")
    for ax in axes:
        ax.set_xticks(levels, [f"{v}%" for v in levels])
        ax.set_xlabel("Normalized flexible-budget level")
    data = [dict(budget_level_pct=l, full_flexible_recovery_pct=f,
                 no_packing_flexible_recovery_pct=n, different_sets=d, n_paired_tasks=40,
                 valid_contexts=80, mandatory_misses=0, overflows=0)
            for l, f, n, d in zip(levels, full, nopack, different)]
    _save(ctx, fig, "audit_08_action_time_budget", "Action-time preflight: flexible-memory selection by budget",
          [source], "Published report table; 40 paired tasks at each normalized level; 400 context evaluations, all valid. No critical-memory labels or LLM actions; recovery is not correctness or task success.", data)


def _coverage(ctx):
    base = "docs/results/constraint_coverage_20261113/"
    balanced_base = "docs/results/constraint_coverage_balanced_v2_20261113/"
    paths = [base+"summary.json", base+"tracked_main_summary.json", balanced_base+"summary.json"]
    sources = [_source("constraint-coverage-audit-2026-11-13.md"),
               _source("balanced-generator-2026-11-13.md"), *paths]
    raw = [json.loads((ctx.root / path).read_text(encoding="utf-8")) for path in paths]
    labels = ["Actual legacy\nAblation input", "Tracked legacy\nmain.zip", "New\nbalanced_v2"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    x = np.arange(3)
    scenario_pct = [r["applicable_scenario_ratio"]*100 for r in raw]
    constraint_pct = [r["applicable_fraction_of_stored_constraints"]*100 for r in raw]
    axes[0].bar(x-.17, scenario_pct, .32, color=BLUE, label="Scenarios with applicable rules")
    axes[0].bar(x+.17, constraint_pct, .32, color=GREEN, label="Applicable / stored constraints")
    for i, r in enumerate(raw):
        axes[0].text(i-.17, 4, f"{r['scenarios_with_applicable_constraints']}/40", ha="center", color="white", fontsize=9)
        axes[0].text(i+.17, 4, f"{r['applicable_constraint_memory_count']}/120", ha="center", color="white", fontsize=9, rotation=90)
    axes[0].set_xticks(x, labels)
    axes[0].set_ylabel("Declared applicability (%)")
    axes[0].set_ylim(0, 128)
    axes[0].legend(loc="upper left", fontsize=8)
    rule_counts = [0, 0, raw[2]["rules_restricting_visible_candidates_count"]]
    grant_facts = [r["explicit_grant_fact_count"] for r in raw]
    axes[1].bar(x-.17, rule_counts, .32, color=PURPLE, label="File rules restricting visible candidates")
    axes[1].bar(x+.17, grant_facts, .32, color=ORANGE, label="Separate approval-grant facts")
    axes[1].set_xticks(x, labels)
    axes[1].set_ylabel("Count per 40-task cell")
    axes[1].set_ylim(0, 128)
    axes[1].legend(loc="upper left", fontsize=8)
    data = [dict(dataset=label.replace("\n", " "), n_scenarios=r["n_independent_scenarios"],
                 applicable_scenarios=r["scenarios_with_applicable_constraints"],
                 stored_constraints=r["stored_constraint_memory_count"],
                 applicable_constraints=r["applicable_constraint_memory_count"],
                 applicable_scenario_pct=sp, applicable_constraint_pct=cp,
                 candidate_restricting_file_rules=rr, separate_grant_facts=gf)
            for label, r, sp, cp, rr, gf in zip(labels, raw, scenario_pct, constraint_pct, rule_counts, grant_facts)]
    _save(ctx, fig, "audit_09_constraint_coverage", "Constraint coverage: legacy inputs and balanced generator",
          sources, "Coverage JSON plus published reports; 40 task identities per dataset. Legacy prohibited files do not restrict actual visible candidates. Declared applicability and behavioral necessity differ; no new LLM performance results.", data)

    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8))
    labels = ["Full Proposed", "No constraint\npreservation"]
    axes[0].bar(np.arange(2)-.17, [97.5, 100], .32, color=BLUE, label="Task success: /80 runs")
    axes[0].bar(np.arange(2)+.17, [100, 90], .32, color=GREEN, label="Applicable constraint recall: /40")
    axes[0].set_xticks([0, 1], labels)
    axes[0].set_ylim(0, 125)
    axes[0].set_ylabel("Observed percentage")
    axes[0].legend(loc="upper left", fontsize=8)
    controls = raw[0]["synthetic_evaluator_controls"]["by_subtype"]
    # Approved tasks isolate the additional unauthorized action check: expected
    # calls remain present in both artificial tool traces.
    granted = controls["approval/granted"]
    cv_values = [granted["original_gt_oracle_plus_unapproved_send_success"],
                 granted["rule_gt_removed_oracle_plus_unapproved_send_success"]]
    _bars(axes[1], ["Original\nevaluator rules", "Rule GT removed\n(diagnostic only)"], cv_values,
          color=[ORANGE, PURPLE], denominator=10, ylim=(0, 12.5))
    axes[1].set_ylabel("Synthetic traces marked successful (out of 10)")
    axes[1].set_title("Correct send + extra unauthorized send\nApproval-granted synthetic controls")
    data = [dict(kind="llm_ablation_report", preset="full", successes=78, n_runs=80,
                 constraint_recalled=40, constraint_n=40),
            dict(kind="llm_ablation_report", preset="no_constraint_preservation", successes=80, n_runs=80,
                 constraint_recalled=36, constraint_n=40),
            dict(kind="synthetic_evaluator_control", rules="original", successes=cv_values[0], n_traces=10),
            dict(kind="synthetic_evaluator_control", rules="rule_gt_removed", successes=cv_values[1], n_traces=10)]
    _save(ctx, fig, "audit_10_constraint_retention_controls", "Constraint retention and separate synthetic evaluator controls",
          sources[:1]+[paths[0]], "Left: existing LLM Ablation runs, 40 tasks x 2 repeats; preservation disabled does not delete constraints. Right: artificial evaluator traces only, no LLM. The two panels have different scopes and denominators.", data)

    validation_path = balanced_base + "validation.json"
    validation = json.loads((ctx.root / validation_path).read_text(encoding="utf-8"))
    allowed = validation["allowed_candidate_counts_by_subtype"]
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    subtypes = list(allowed)
    candidate_data = []
    bottom = np.zeros(4)
    for candidate, color in zip(["1", "2", "3", "4"], [BLUE, GREEN, ORANGE, PURPLE]):
        values = [allowed[st].get(candidate, 0) for st in subtypes]
        axes[0].bar(np.arange(4), values, bottom=bottom, color=color, label=f"Candidate {candidate}")
        for st, value in zip(subtypes, values):
            candidate_data.append(dict(kind="correct_candidate_distribution", subtype=st,
                                       candidate=int(candidate), count=value, subtype_n=10))
        bottom += values
    axes[0].set_xticks(range(4), ["Approval\ngranted", "Approval\npending", "Temporal\ncurrent", "Temporal\nhistorical"])
    axes[0].set_ylabel("Task count by correct candidate position")
    axes[0].set_ylim(0, 13)
    axes[0].legend(ncol=2, fontsize=8, loc="upper left")
    axes[0].set_title("Balanced answer positions")
    checks = [validation["oracle_checks_passed"], validation["offline_rule_planner_integration"]["successes"], 259, 1]
    # Separate scales keep the 1 skipped test visible without implying a rate.
    axes[1].axis("off")
    rows = [["Evaluator oracle traces", f"{checks[0]}/480 passed"],
            ["Rule-planner integration", f"{checks[1]}/40 succeeded"],
            ["Local validation tests", "259 passed / 1 skipped"],
            ["Unique task identities", str(validation["n_unique_sample_ids"])],
            ["Budget/horizon cells", str(validation["n_cells"])] ]
    table = axes[1].table(cellText=rows, colLabels=["Offline verification", "Recorded result"],
                          loc="center", cellLoc="left", colWidths=[.63, .37])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 2.1)
    candidate_data.extend(dict(kind="verification", check=key, result=value) for key, value in rows)
    _save(ctx, fig, "audit_11_balanced_generator_validation", "Balanced generator: answer positions and offline validation",
          [_source("balanced-generator-2026-11-13.md"), validation_path],
          "Offline provisional tokenizer, constant test embedding, integration top-k 20. 480 appearances reuse 40 task identities across 12 cells. These checks do not measure LLM or experimental top-k 5 performance.", candidate_data)


def _readiness(ctx):
    source = _source("smoke-4k8k-readiness-2026-11-08.md")
    fig, axes = plt.subplots(1, 2, figsize=(12, 5.8))
    x = np.arange(2)
    axes[0].bar(x-.17, [2473, 2473], .32, color=BLUE, label="Max counted prompt")
    axes[0].bar(x+.17, [2474, 2474], .32, color=GREEN, label="Max actual prompt")
    axes[0].plot(x, [4096-32, 8192-32], "D--", color=ORANGE, label="Effective budget")
    axes[0].set_xticks(x, ["4K", "8K"])
    axes[0].set_ylim(0, 9500)
    axes[0].set_ylabel("Input tokens")
    axes[0].legend(fontsize=8)
    axes[0].set_title("Counted vs actual prompt")
    axes[1].axis("off")
    rows = [["Completed runs", "32/32", "32/32"], ["Actual LLM execution", "32/32", "32/32"],
            ["Verified prompt calls", "61/61", "61/61"], ["Budget overflow", "0", "0"],
            ["Incomplete cost records", "0", "0"], ["Readiness", "PASS", "PASS"]]
    table = axes[1].table(cellText=rows, colLabels=["Operational check", "4K", "8K"],
                          loc="center", cellLoc="center", colWidths=[.62, .19, .19])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.9)
    data = [dict(budget=b, effective_budget=b-32, runs_completed=32, runs_planned=32,
                 actual_llm_runs=32, prompt_calls_verified=61, prompt_calls=61,
                 counted_prompt_max=2473, actual_prompt_max=2474, overflow_count=0,
                 incomplete_cost_count=0, readiness="PASS") for b in [4096, 8192]]
    _save(ctx, fig, "audit_12_smoke_readiness", "4K/8K readiness gate: prompt instrumentation",
          [source], "Published report table; eight fixed tasks x four strategies per budget. Operational readiness only; no comparative performance inference. Safety margin: 32 tokens.", data)


def _smoke(ctx):
    source = _source("pipeline-smoke-2026-11-12.md")
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.8))
    _bars(axes[0], ["Proposed", "Sliding\nWindow", "Vector\nTop-k", "Utility-per-\nToken"],
          [5, 4, 5, 5], color=COLORS, denominator=5, ylim=(0, 6.4))
    axes[0].set_ylabel("Tasks succeeded (out of 5)")
    axes[0].set_title("Latest completed smoke snapshot")
    axes[1].axis("off")
    rows = [["Completed / pipeline ok", "20/20"], ["Expected tool call observed", "19/20"],
            ["Budget overflow", "0/20"], ["Constraint violations", "0/8 applicable evaluations"],
            ["Repeated snapshots", "3; same five inputs"], ["Interrupted retry", "0/20; excluded"]]
    table = axes[1].table(cellText=rows, colLabels=["Check", "Result"], loc="center", cellLoc="left",
                          colWidths=[.57, .43])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.95)
    data = [dict(strategy=s, successes=n, tasks=5, completed_runs=5, snapshot="2026-11-12 21:09")
            for s, n in zip(STRATEGIES, [5, 4, 5, 5])]
    data.extend([dict(check="expected_tool_call", count=19, denominator=20),
                 dict(check="constraint_violation", count=0, denominator=8),
                 dict(check="overflow", count=0, denominator=20),
                 dict(check="interrupted_retry_completed", count=0, denominator=20)])
    _save(ctx, fig, "audit_13_pipeline_smoke", "Pipeline smoke: five fixed inputs and execution checks",
          [source], "Published report tables; latest of three snapshots using the same five tasks. A pipeline diagnostic, not a strategy performance estimate; repeated snapshots are not independent tasks.", data)

    early_source = _source("early-runner-and-smoke-archive-2026-11-15.md")
    snapshots = ["11/08\n22:03", "11/08\n22:07", "11/11\n00:19", "11/11\n19:03", "11/11\n23:08",
                 "11/12\n17:04", "11/12\n18:24", "11/12\n21:09"]
    counts = [[5]*8, [4]*8, [4, 4, 5, 5, 5, 5, 5, 5], [5]*8]
    # Rows represent snapshots rather than connecting observations across code
    # revisions with a trend line.
    fig, ax = plt.subplots(figsize=(12, 6))
    array = np.array(counts)
    vector_heatmap(ax, array, vmin=0, vmax=5, cmap="Blues")
    ax.set_xticks(range(8), snapshots)
    ax.set_yticks(range(4), STRATEGIES)
    ax.set_xlabel("Execution snapshot (same five tasks; different commits)")
    data = []
    for si, strategy in enumerate(STRATEGIES):
        for ti, snapshot in enumerate(snapshots):
            n = counts[si][ti]
            ax.text(ti, si, f"{n}/5", ha="center", va="center", color="white", fontsize=12)
            data.append(dict(strategy=strategy, snapshot=snapshot.replace("\n", " "), successes=n,
                             tasks=5, commit_state="dirty" if ti < 2 else "clean"))
    _save(ctx, fig, "audit_14_smoke_execution_archive", "Repeated smoke snapshots: observation archive",
          [early_source, source], "Published report tables; same five tasks in every snapshot, with different code commits. Nov 8 snapshots are dirty. Counts are separate records; no pooled success rate or temporal/causal trend is estimated.", data)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5.8))
    labels = ["Proposed", "Sliding\nWindow", "Vector\nTop-k", "Utility-per-\nToken"]
    _bars(axes[0], labels, [18, 12, 12, 12], color=COLORS, denominator=27, ylim=(0, 32))
    axes[0].set_ylabel("Observed fixture successes (out of 27)")
    axes[0].set_title("Early runner_integration fixture archive")
    axes[1].axis("off")
    rows = [["quant_pilot_sanity", "P 1/1; SW 1/1; V 0/1; U 0/1"],
            ["WARNING: Proposed", "Success and selection failure both 1/1"],
            ["pipeline_adapter_check", "SW / V / U each 1/1; P not run"],
            ["runner_smoke", "Sliding Window 0/27"],
            ["Provenance", "Missing freeze/commit in early fixtures"]]
    table = axes[1].table(cellText=rows, colLabels=["Separate fixture check", "Archived observation"],
                          loc="center", cellLoc="left", colWidths=[.44, .56])
    table.auto_set_font_size(False)
    table.set_fontsize(8)
    table.scale(1, 2)
    data = [dict(archive="runner_integration", strategy=s, successes=n, n_fixtures=27,
                 valid_research_performance=False) for s, n in zip(STRATEGIES, [18, 12, 12, 12])]
    data.extend([dict(archive="quant_pilot_sanity", strategy=s, successes=n, n_fixtures=1,
                      selection_failure=1 if s == "Proposed" else "not transcribed",
                      valid_research_performance=False)
                 for s, n in zip(STRATEGIES, [1, 1, 0, 0])])
    data.extend([dict(archive="pipeline_adapter_check", strategy=s, successes=1, n_fixtures=1,
                      valid_research_performance=False) for s in STRATEGIES[1:]])
    data.append(dict(archive="runner_smoke", strategy="Sliding Window", successes=0,
                     n_fixtures=27, valid_research_performance=False))
    _save(ctx, fig, "audit_15_early_runner_fixtures", "Early runner fixtures: development diagnostics only",
          [early_source], "Published archive table; provisional counter and incomplete provenance. quant_pilot_sanity contains contradictory success/selection diagnostics. These observations must not support strategy comparisons or be pooled with later experiments.", data)


def build(ctx):
    """Generate every quantitative audit family without rerunning experiments."""
    _narrative_retrieval(ctx)
    _factorial(ctx)
    _context(ctx)
    _llm(ctx)
    _attachment(ctx)
    _action_time(ctx)
    _coverage(ctx)
    _readiness(ctx)
    _smoke(ctx)
