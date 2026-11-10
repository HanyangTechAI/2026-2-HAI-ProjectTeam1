"""Main 결과의 예산별 성공률을 그린다.

저장소 루트에서 실행: python experiments/plot_main_results.py
필요한 패키지: python -m pip install matplotlib
"""

import json
from pathlib import Path

import matplotlib

# 창을 띄우지 않고 파일로 저장한다. 서버에서도 같은 방식으로 실행 가능하다.
matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/results/main_20261109/summary.json"
OUTPUT = ROOT / "experiments/results/main_visualization"

# 모든 패널에서 전략별 색과 기호를 동일하게 유지한다.
STYLES = {
    "proposed": ("Proposed", "#0072B2", "o", "-", 6),
    "sliding_window": ("Sliding Window", "#D55E00", "^", "--", 6),
    "vector_top_k": ("Vector Top-k", "#009E73", "s", "-.", 9),
    "utility_per_token": ("Utility-per-Token", "#CC79A7", "D", ":", 4),
}


def main():
    # 1. JSON을 읽는다. 각 행은 전략 × Horizon × Budget 한 조건이다.
    rows = json.loads(SOURCE.read_text(encoding="utf-8"))
    horizons = sorted({row["horizon_turns"] for row in rows})
    budgets = sorted({row["context_token_budget"] for row in rows})

    # 2. Horizon별 패널을 만들고, 비교하기 쉽게 y축 범위를 공유한다.
    fig, axes = plt.subplots(1, len(horizons), figsize=(13, 4.8), sharey=True)

    for ax, horizon in zip(axes, horizons):
        for strategy, (label, color, marker, line, size) in STYLES.items():
            selected = sorted(
                (row for row in rows
                 if row["strategy"] == strategy and row["horizon_turns"] == horizon),
                key=lambda row: row["context_token_budget"],
            )

            # 3. 평균과 CI를 0~1 비율에서 백분율로 바꾼다.
            x = [row["context_token_budget"] for row in selected]
            metrics = [row["metrics"]["task_success"] for row in selected]
            y = [metric["mean"] * 100 for metric in metrics]

            # yerr에는 CI 끝점이 아니라 평균에서 각 끝점까지의 거리를 준다.
            errors = [
                [(m["mean"] - m["ci95"][0]) * 100 for m in metrics],
                [(m["ci95"][1] - m["mean"]) * 100 for m in metrics],
            ]
            ax.errorbar(
                x, y, yerr=errors, label=label, color=color,
                marker=marker, markersize=size, markerfacecolor="none",
                linestyle=line, linewidth=1.8, elinewidth=0.9, capsize=3,
            )

        ax.set_title(f"Horizon = {horizon}")
        ax.set_xlabel("Context token budget")
        ax.set_xscale("log", base=2)
        ax.set_xticks(budgets, [f"{budget // 1024}K" for budget in budgets])
        ax.set_ylim(-5, 105)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.grid(axis="y", alpha=0.25)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0].set_ylabel("Task success (%)")
    fig.suptitle("Task success by context budget and horizon", y=0.99)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, 0.92),
               ncol=4, frameon=False)
    fig.text(
        0.5, 0.02,
        "Error bars: sample-cluster bootstrap 95% CI; 40 tasks x 2 repeats per point.\n"
        "Vector Top-k and Utility-per-Token have identical success rates.",
        ha="center", fontsize=9,
    )
    fig.tight_layout(rect=(0, 0.09, 1, 0.82))

    # 4. 발표용 PNG와 확대해도 선명한 SVG를 함께 저장한다.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "svg"):
        path = OUTPUT / f"task_success.{extension}"
        fig.savefig(path, dpi=300, bbox_inches="tight")
        print(path)
    plt.close(fig)


if __name__ == "__main__":
    main()
