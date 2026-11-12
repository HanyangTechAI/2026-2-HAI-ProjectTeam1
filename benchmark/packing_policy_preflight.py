
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Sequence

from benchmark.action_time_preflight import (
    REFERENCE_BUDGET,
    _select,
    calibrate,
)
from benchmark.critical_flexible_generator import (
    CONDITIONS,
    DATASET_VERSION,
    validate_scenario,
)
from benchmark.critical_flexible_preflight import (
    _candidate_scores,
    _minimum_critical_budget,
)
from context.builder import (
    build_context,
    fixed_context_tokens,
)
from context.tokenizer import (
    GeminiTokenCounter,
    token_count_mode,
)
from memory.experimental_packing import (
    repack_reference,
    validate_alpha,
)
from memory.selector import PipelineStatus


DEFAULT_ALPHAS = (0.0, 0.1, 0.2, 0.5, 1.0)
DEFAULT_LEVELS = (0.0, 0.25, 0.5, 0.75, 1.0)


def _validate_grid(
    alphas: Sequence[float],
    levels: Sequence[float],
) -> None:
    if not alphas or not levels:
        raise ValueError("alphas and levels must be nonempty")

    values = [validate_alpha(a) for a in alphas]

    if len(set(values)) != len(values):
        raise ValueError("duplicate alpha")

    if any(
        isinstance(x, bool)
        or not isinstance(x, (int, float))
        or x not in DEFAULT_LEVELS
        for x in levels
    ):
        raise ValueError(
            f"levels must be from {DEFAULT_LEVELS}"
        )

    if len(set(levels)) != len(levels):
        raise ValueError("duplicate budget level")


def evaluate_policies(
    scenarios: Sequence[dict[str, Any]],
    *,
    system_prompt: str,
    tool_definitions: str,
    embedder: Callable,
    counter: Callable,
    alphas: Sequence[float] = DEFAULT_ALPHAS,
    levels: Sequence[float] = DEFAULT_LEVELS,
    top_k: int = 5,
    weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    safety_margin: int = 32,
) -> tuple[list[dict], list[dict], int]:
    """개발 데이터에 대한 정책별 Context와 통계 계산."""
    _validate_grid(alphas, levels)

    if type(safety_margin) is not int or safety_margin < 0:
        raise ValueError("invalid safety margin")

    ids = [s["scenario_id"] for s in scenarios]

    if not ids or len(ids) != len(set(ids)):
        raise ValueError(
            "empty dataset or duplicate scenario_id"
        )

    rows = []
    control_checks = 0

    for number, scenario in enumerate(scenarios, 1):
        if (
            scenario.get("token_count_mode")
            != token_count_mode(counter)
        ):
            raise ValueError("token counter mismatch")

        validate_scenario(scenario, counter)

        meta = scenario["metadata"]

        if meta.get("stress_condition") not in CONDITIONS:
            raise ValueError("invalid stress condition")

        critical_ids = meta.get("critical_flexible_memory_ids")

        if (
            not isinstance(critical_ids, list)
            or len(critical_ids) != 1
        ):
            raise ValueError("one critical ID required")

        cid = critical_ids[0]

        cal = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            top_k,
            weights,
        )

        # 충분히 큰 예산에서 Frozen Selector가 검색한
        # Flexible 후보 전체를 확보한다.
        reference, reference_ctx = _select(
            scenario,
            cal["store"],
            cal["clock"],
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            REFERENCE_BUDGET,
            "full",
            top_k,
            weights,
        )

        if (
            reference.status != PipelineStatus.OK
            or reference_ctx.status != PipelineStatus.OK
        ):
            raise RuntimeError("reference selection failed")

        scores = _candidate_scores(
            scenario,
            cal,
            embedder,
            counter,
            top_k,
            weights,
        )

        flexible_ids = {
            memory.memory_id
            for memory in reference.flexible
        }

        if flexible_ids != set(
            cal["reference_flexible_ids"]
        ):
            raise RuntimeError(
                "reference flexible pool differs from calibration"
            )

        if not flexible_ids.issubset(scores):
            raise RuntimeError(
                "missing reference flexible scores"
            )

        if cid not in flexible_ids:
            raise RuntimeError(
                "critical ID absent from reference flexible pool"
            )

        # Ground Truth는 평가용 수용 가능성 판정에만 사용한다.
        # 실제 Packing 점수에는 전달하지 않는다.
        minimum = _minimum_critical_budget(
            scenario,
            reference,
            cid,
            system_prompt,
            tool_definitions,
            counter,
        )

        if minimum is None:
            raise RuntimeError(
                "critical feasibility could not be computed"
            )

        fixed = fixed_context_tokens(
            system_prompt,
            tool_definitions,
            scenario["query"],
            counter,
        )

        mandatory_ids = {
            memory.memory_id
            for memory in (
                *reference.protected,
                *reference.states,
            )
        }

        noise_ids = set(
            meta["distractor_memory_ids"]
        )

        for level in levels:
            effective = cal["floor"] + int(
                (cal["ceiling"] - cal["floor"]) * level
            )

            for alpha in alphas:
                selection = repack_reference(
                    reference,
                    scores,
                    total_budget=effective,
                    fixed_tokens=fixed,
                    current_turn=scenario["current_turn"],
                    alpha=alpha,
                )

                context = build_context(
                    system_prompt=system_prompt,
                    tool_definitions=tool_definitions,
                    query=scenario["query"],
                    selection=selection,
                    total_budget=effective,
                    token_counter=counter,
                )

                ok = (
                    selection.status == PipelineStatus.OK
                    and context.status == PipelineStatus.OK
                )

                selected = (
                    set(context.selected_memory_ids)
                    if ok else set()
                )

                if ok and (
                    context.total_input_tokens > effective
                    or not mandatory_ids.issubset(selected)
                ):
                    raise AssertionError(
                        "budget overflow or mandatory memory omission"
                    )

                # alpha=0과 alpha=1은 기존 Frozen 정책을
                # 실제 실행해 결과가 정확히 같은지 확인한다.
                if alpha in (0.0, 1.0):
                    preset = (
                        "no_budget_aware_packing"
                        if alpha == 0.0
                        else "full"
                    )

                    native_sel, native_ctx = _select(
                        scenario,
                        cal["store"],
                        cal["clock"],
                        system_prompt,
                        tool_definitions,
                        embedder,
                        counter,
                        effective,
                        preset,
                        top_k,
                        weights,
                    )

                    if (
                        selection.status != native_sel.status
                        or (
                            selection.selected_memory_ids
                            != native_sel.selected_memory_ids
                        )
                        or context.status != native_ctx.status
                        or (
                            context.selected_memory_ids
                            != native_ctx.selected_memory_ids
                        )
                        or (
                            context.total_input_tokens
                            != native_ctx.total_input_tokens
                        )
                    ):
                        raise AssertionError(
                            "frozen parity failed for "
                            f"{scenario['scenario_id']} "
                            f"alpha={alpha} level={level}"
                        )

                    control_checks += 1

                flexible_selected = (
                    selected & flexible_ids
                )

                rows.append({
                    "scenario_id": scenario["scenario_id"],
                    "base_case_id": meta["base_case_id"],
                    "task_family": meta["task_family"],
                    "condition": meta["stress_condition"],
                    "level": float(level),
                    "alpha": float(alpha),

                    "nominal_budget": effective + safety_margin,
                    "effective_budget": effective,

                    "critical_feasible": effective >= minimum,
                    "critical_in_context": cid in selected,
                    "retrieval_hit": cid in scores,

                    "selection_status": selection.status.value,
                    "context_status": context.status.value,

                    "mandatory_retained": (
                        ok and mandatory_ids.issubset(selected)
                    ),

                    "budget_overflow": (
                        ok and context.total_input_tokens > effective
                    ),

                    "context_tokens": (
                        context.total_input_tokens if ok else None
                    ),

                    "unused_budget": (
                        context.unused_budget if ok else None
                    ),

                    "n_flexible": len(flexible_selected),
                    "n_noise": len(selected & noise_ids),

                    "selected_utility": sum(
                        scores[mid]["utility_score"]
                        for mid in flexible_selected
                    ),

                    "selected_flexible_ids": sorted(
                        flexible_selected
                    ),

                    "builder_dropped_ids": list(
                        context.dropped_flexible_ids
                    ),
                })

        print(
            f"[{number}/{len(scenarios)}] "
            f"{scenario['scenario_id']}",
            flush=True,
        )

    return rows, summarize(rows), control_checks


def summarize(
    rows: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """조건·Budget·Alpha별 집계."""
    groups = defaultdict(list)

    for row in rows:
        groups[(
            "ALL",
            row["level"],
            row["alpha"],
        )].append(row)

        groups[(
            row["condition"],
            row["level"],
            row["alpha"],
        )].append(row)

    output = []

    for (condition, level, alpha), group in sorted(
        groups.items()
    ):
        n = len(group)

        feasible = [
            row for row in group
            if row["critical_feasible"]
        ]

        valid_unused = [
            row["unused_budget"]
            for row in group
            if row["unused_budget"] is not None
        ]

        output.append({
            "condition": condition,
            "level": level,
            "alpha": alpha,
            "n": n,

            "base_cases": len({
                row["base_case_id"]
                for row in group
            }),

            "critical_recall": (
                sum(
                    row["critical_in_context"]
                    for row in group
                ) / n
            ),

            "n_feasible": len(feasible),

            "feasible_recall": (
                sum(
                    row["critical_in_context"]
                    for row in feasible
                ) / len(feasible)
                if feasible else None
            ),

            "packing_omissions": sum(
                row["critical_feasible"]
                and not row["critical_in_context"]
                for row in group
            ),

            "mean_noise_count": (
                sum(
                    row["n_noise"]
                    for row in group
                ) / n
            ),

            "mean_utility": (
                sum(
                    row["selected_utility"]
                    for row in group
                ) / n
            ),

            "mean_unused_tokens": (
                sum(valid_unused) / len(valid_unused)
                if valid_unused else None
            ),

            "context_failures": sum(
                row["context_status"] != "ok"
                for row in group
            ),

            "budget_overflows": sum(
                row["budget_overflow"]
                for row in group
            ),
        })

    return output


def _write_csv(
    path: Path,
    rows: Sequence[dict],
) -> None:
    if not rows:
        raise ValueError("empty CSV")

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0]),
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, list)
                    else value
                )
                for key, value in row.items()
            })


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--scenarios",
        default=(
            "benchmark/generated/"
            "critical_flexible_v1/dataset.json"
        ),
    )

    parser.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )

    parser.add_argument(
        "--output",
        default="experiments/results/packing_alpha_dev_v1",
    )

    parser.add_argument(
        "--alphas",
        nargs="+",
        type=float,
        default=list(DEFAULT_ALPHAS),
    )

    parser.add_argument(
        "--levels",
        nargs="+",
        type=float,
        default=list(DEFAULT_LEVELS),
    )

    args = parser.parse_args()
    _validate_grid(args.alphas, args.levels)

    from agent.llm_planner import (
        LLMRunConfig,
        SYSTEM_INSTRUCTION,
    )

    from benchmark.experiment_runtime import (
        multilingual_embedder,
        embedding_runtime_metadata,
    )

    from benchmark.runner import (
        _load_runner_config,
        _require_executable_config,
    )

    config = _load_runner_config(args.config)
    _require_executable_config(config)

    if (
        config.get("scope") != "ablation"
        or config.get("freeze", {}).get("version") != "1.0.4"
    ):
        raise ValueError(
            "expected frozen v1.0.4 ablation config"
        )

    source = Path(args.scenarios)
    raw = source.read_bytes()
    data = json.loads(raw)

    # Holdout은 파라미터 선정에 사용하지 않는다.
    if (
        data.get("generator")
        != "benchmark.critical_flexible_generator"
        or data.get("holdout")
    ):
        raise ValueError(
            "never tune alpha on holdout data"
        )

    if data.get("dataset_version") != DATASET_VERSION:
        raise ValueError("dataset version mismatch")

    if (
        data.get("parameters", {}).get("seed_start") != 42
        or data.get("parameters", {}).get("cases_per_family") != 20
        or len(data.get("scenarios", [])) != 160
    ):
        raise ValueError(
            "expected original development dataset, seeds 42..61"
        )

    manifest_path = source.with_name(
        source.stem + "_manifest.json"
    )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    dataset_sha = hashlib.sha256(raw).hexdigest()

    if manifest.get("dataset_sha256") != dataset_sha:
        raise ValueError(
            "development dataset SHA-256 mismatch"
        )

    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
    )

    if data.get("token_count_mode") != token_count_mode(
        counter
    ):
        raise ValueError("Gemini tokenizer mismatch")

    model = config["model"]

    llm = LLMRunConfig(
        model=model["name"],
        temperature=model["temperature"],
        system_instruction=SYSTEM_INSTRUCTION,
        max_output_tokens=model["max_output_tokens"],
        thinking_level=model["thinking_level"],
        max_retries=model["max_retries"],
        retry_base_seconds=model["retry_base_seconds"],
        timeout_ms=model["timeout_ms"],
    )

    counter.configure_request(
        system_instruction=llm.system_instruction,
        generation_config=(
            llm.planner().generation_config()
        ),
    )

    system_prompt = Path(
        model["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    rows, report, checks = evaluate_policies(
        data["scenarios"],
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        counter=counter,
        alphas=args.alphas,
        levels=args.levels,
        top_k=config["runtime"]["candidate_top_k"],
        weights=tuple(
            config["runtime"]["utility_weights"]
        ),
        safety_margin=config["budget_policy"][
            "request_token_safety_margin"
        ],
    )

    output = Path(args.output)

    # 기존 실험 결과 덮어쓰기 방지
    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    (output / "policy_details.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )

    _write_csv(
        output / "policy_details.csv",
        rows,
    )

    _write_csv(
        output / "policy_summary.csv",
        report,
    )

    validation = {
        "kind": "development_selection_only",
        "dataset_sha256": dataset_sha,
        "manifest_sha256": hashlib.sha256(
            manifest_path.read_bytes()
        ).hexdigest(),
        "freeze": config["freeze"],
        "alphas": list(args.alphas),
        "levels": list(args.levels),
        "scenarios": len(data["scenarios"]),
        "context_evaluations": len(rows),
        "native_parity_checks": checks,
        "llm_generation_calls": 0,
        "budget_overflow": sum(
            row["budget_overflow"]
            for row in rows
        ),
        "mandatory_omission": sum(
            row["context_status"] == "ok"
            and not row["mandatory_retained"]
            for row in rows
        ),
        "tokenizer": counter.metadata(),
        "embedding": embedding_runtime_metadata(),
    }

    (output / "validation.json").write_text(
        json.dumps(
            validation,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    for row in report:
        if (
            row["condition"] == "C"
            and row["level"] == 0.75
        ):
            print(
                f"C @75% alpha={row['alpha']}: "
                f"Critical Recall={row['critical_recall']:.1%}, "
                f"packing omissions="
                f"{row['packing_omissions']}/{row['n']}"
            )

    print(
        f"[PASS] {len(rows)} Contexts; "
        f"{checks} frozen endpoint parity checks; "
        f"saved {output}"
    )


if __name__ == "__main__":
    main()
