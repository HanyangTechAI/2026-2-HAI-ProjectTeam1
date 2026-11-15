"""Exploratory pilots and instrumentation checks, separate from final Main."""

import matplotlib.pyplot as plt
import numpy as np

from .common import COLORS, LABELS, STRATEGIES

REPORT = "docs/experiments/reports/"


def build(ctx):
    pilots(ctx)
    budget_preflight(ctx)
    output_limit(ctx)


def pilots(ctx):
    runs = ["quant_pilot_v1", "pilot_offline", "pilot_gemini", "pilot_gemini_v2"]
    successes = [[100, 75, 1, 0], [16, 12, 0, 0], [0, 0, 0, 0], [100, 75, 1, 0]]
    denoms = [100, 16, 100, 100]
    fig, axes = plt.subplots(1, 4, figsize=(16, 5.1), sharey=True)
    data = []
    for ax, name, counts, denom in zip(axes, runs, successes, denoms):
        bars = ax.bar(range(4), np.array(counts)/denom*100, color=COLORS[:4])
        ax.bar_label(bars, labels=[f"{n}/{denom}" for n in counts], padding=4, fontsize=9)
        ax.set_xticks(range(4), ["P", "SW", "V", "U"])
        ax.set_title(name.replace("_", "\n"))
        ax.set_ylim(0, 117)
        ax.set_yticks([0, 25, 50, 75, 100])
        ctx.style(ax)
        data.extend(dict(experiment=name, strategy=s, recorded_success=n, n=denom,
                         success_percent=n/denom*100) for s, n in zip(STRATEGIES, counts))
    axes[0].set_ylabel("Recorded task success (%)")
    ctx.save(fig, "preliminary_01_early_pilots", "Early pilots: recorded outcomes with known measurement limitations", "preliminary",
             [REPORT+"early-pilot-runs-2026-11-03.md", "experiments/results/pilot_offline_20261103_182136/summary.json"],
             "Report aggregates; P=Proposed, SW=Sliding, V=Vector, U=Utility. First two runs have contradictory success/selection-failure metrics; "
             "first Gemini run fails budgeting before valid execution. Gemini v2 lacks current provenance/cost accounting. Do not compare with final Main.", data)


def budget_preflight(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    data = []
    x = np.arange(4)
    for ax, budget in zip(axes, [1024, 2048]):
        values = [0, 0, 0, 0] if budget == 1024 else [8, 5, 5, 6]
        for i, margin in enumerate([0, 32]):
            bars = ax.bar(x+(i-.5)*.33, np.array(values)*12.5, .33,
                          color=COLORS[i], label=f"Safety margin {margin}")
            ax.bar_label(bars, labels=[f"{n}/8" for n in values], padding=4, fontsize=9)
            data.extend(dict(budget=budget, safety_margin=margin, strategy=s,
                             successes=n, n=8, success_percent=n*12.5) for s, n in zip(STRATEGIES, values))
        ax.set_title(f"Budget = {budget:,}")
        ax.set_xticks(x, ["Proposed", "Sliding", "Vector", "Utility"])
        ax.set_ylim(0, 117)
        ax.set_yticks([0, 25, 50, 75, 100])
        ctx.style(ax)
    axes[0].set_ylabel("Task success (%)")
    axes[1].legend(frameon=False, loc="upper center", bbox_to_anchor=(-.1, 1.17), ncol=2)
    ctx.save(fig, "preliminary_02_budget_margin", "Token-budget preflight: safety margin 0 versus 32", "preliminary",
             [REPORT+"token-budget-preflight-2026-11-08.md"],
             "Published report table; 8 fixed scenarios, one run per strategy/condition; 128 runs total. "
             "1K Proposed stops before model execution. No intervals reported; identical observed rates do not establish equivalence.", data)


def output_limit(ctx):
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2))
    x = np.arange(4)
    data = []
    for i, (limit, counts, errors) in enumerate([(512, [8, 6, 4, 6], [0, 1, 3, 1]),
                                               (2048, [8, 6, 6, 6], [0, 0, 0, 0])]):
        bars = axes[0].bar(x+(i-.5)*.33, np.array(counts)*10, .33, color=COLORS[i], label=f"Output limit {limit}")
        axes[0].bar_label(bars, labels=[f"{n}/10" for n in counts], padding=4, fontsize=9)
        bars2 = axes[1].bar(x+(i-.5)*.33, errors, .33, color=COLORS[i], label=f"Output limit {limit}")
        axes[1].bar_label(bars2, padding=4)
        data.extend(dict(output_limit=limit, strategy=s, successes=n, n=10,
                         success_percent=n*10, parse_error_runs=e) for s,n,e in zip(STRATEGIES, counts, errors))
    axes[0].set_ylabel("Task success (%)")
    axes[0].set_ylim(0, 115)
    axes[1].set_ylabel("Response-parse error runs")
    axes[1].set_ylim(0, 4)
    axes[1].set_yticks(range(5))
    axes[0].set_title("Observed task outcome")
    axes[1].set_title("Parsing diagnostic")
    for ax in axes:
        ax.set_xticks(x, ["Proposed", "Sliding", "Vector", "Utility"])
        ctx.style(ax)
    axes[1].legend(loc="upper center", bbox_to_anchor=(-.1, 1.18), ncol=2, frameon=False)
    source = REPORT+"output-limit-2048-validation-2026-11-09.md"
    ctx.save(fig, "preliminary_03_output_limit", "Output-limit validation: 512 versus 2048 tokens", "preliminary", [source],
             "Published report table: same 10 selected inputs per strategy; one execution, not the 12-cell Main design. "
             "Proposed has two 1K budget stops in the new run. Parse errors and MAX_TOKENS are distinct diagnostics; no paired CI supplied.", data)
    fig, ax = plt.subplots(figsize=(10.5, 5))
    counts = [[0, 4, 2, 2], [0, 2, 2, 2], [0, 3, 2, 1], [0, 3, 2, 1]]
    n = [2, 4, 2, 2]
    data = []
    for i, values in enumerate(counts):
        bars = ax.bar(x+(i-1.5)*.19, np.array(values)/n*100, .19, color=COLORS[i], label=LABELS[i])
        ax.bar_label(bars, labels=[f"{v}/{d}" for v,d in zip(values,n)], rotation=90, padding=3, fontsize=8)
        data.extend(dict(output_limit=2048, budget=b, strategy=STRATEGIES[i], successes=v, n=d,
                         success_percent=v/d*100) for b,v,d in zip([1024,2048,4096,8192],values,n))
    ax.set_xticks(x, ["1K", "2K", "4K", "8K"])
    ax.set_xlabel("Context budget")
    ax.set_ylabel("Task success (%)")
    ax.set_ylim(0, 123)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.legend(ncol=4, frameon=False, loc="upper center", bbox_to_anchor=(.5, 1.15))
    ctx.style(ax)
    ctx.save(fig, "preliminary_04_output_limit_budget", "Output-limit 2048 validation: selected-input budget breakdown", "preliminary", [source],
             "Published aggregate counts. Each budget includes only 2 or 4 selected inputs per strategy; mixed tasks/horizons are not fully crossed. "
             "38 live runs, 75 STOP responses; no observed parse or input-budget errors. Exploratory instrumentation check, not final Main.", data)
