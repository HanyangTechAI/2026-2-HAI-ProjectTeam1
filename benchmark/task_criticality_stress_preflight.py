from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION
from benchmark.action_time_preflight import calibrate
from benchmark.experiment_runtime import (
    multilingual_embedder,
    embedding_runtime_metadata,
)
from benchmark.runner import (
    _load_runner_config,
    _require_executable_config,
)
from benchmark.task_criticality_stress import load_and_validate
from context.builder import build_context, fixed_context_tokens
from context.tokenizer import GeminiTokenCounter
from memory.ablation import get_ablation_config
from memory.criticality_policy import make_criticality_policy
from memory.schema import MemoryItem
from memory.selector import PipelineStatus
from pipeline.orchestrator import make_proposed_policy

POLICIES = ("full", "no_packing", "criticality")
LEVEL = 0.75


def _csv(path: Path, rows: list[dict]) -> None:
    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=list(rows[0]),
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                    )
                    if isinstance(value, list)
                    else value
                )
                for key, value in row.items()
            })


def evaluate(
    scenarios,
    labels,
    *,
    system_prompt,
    tools,
    embedder,
    counter,
    top_k,
    weights,
):
    trace = []

    policies = {
        "full": make_proposed_policy(
            embedder=embedder,
            top_k=top_k,
            utility_weights=weights,
            ablation=get_ablation_config("full"),
        ),

        "no_packing": make_proposed_policy(
            embedder=embedder,
            top_k=top_k,
            utility_weights=weights,
            ablation=get_ablation_config(
                "no_budget_aware_packing"
            ),
        ),

        "criticality": make_criticality_policy(
            embedder=embedder,
            top_k=top_k,
            utility_weights=weights,
            on_decision=trace.append,
        ),
    }

    rows = []

    for i, scenario in enumerate(scenarios, 1):
        label = labels[scenario["scenario_id"]]

        calibration = calibrate(
            scenario,
            system_prompt,
            tools,
            embedder,
            counter,
            top_k,
            weights,
        )

        effective = calibration["floor"] + int(
            (
                calibration["ceiling"]
                - calibration["floor"]
            ) * LEVEL
        )

        fixed = fixed_context_tokens(
            system_prompt,
            tools,
            scenario["query"],
            counter,
        )

        memories = tuple(
            MemoryItem.from_dict(m)
            for m in scenario["memory_snapshot"]
        )

        mandatory_ids = set(
            calibration["reference_mandatory_ids"]
        )

        for name, policy in policies.items():
            trace.clear()

            selection = policy(
                query=scenario["query"],
                memories=memories,
                session_id=scenario["session_id"],
                total_budget=effective,
                fixed_tokens=fixed,
                current_turn=scenario["current_turn"],
                current_time=datetime.fromisoformat(
                    scenario["current_time"]
                ),
                token_counter=counter,
            )

            context = build_context(
                system_prompt,
                tools,
                scenario["query"],
                selection,
                effective,
                counter,
            )

            ok = (
                selection.status
                == context.status
                == PipelineStatus.OK
            )

            ids = (
                set(context.selected_memory_ids)
                if ok else set()
            )

            if ok and (
                context.total_input_tokens > effective
                or not mandatory_ids.issubset(ids)
            ):
                raise AssertionError(
                    "hard budget or mandatory preservation violated"
                )

            expected_traces = (
                1
                if selection.status == PipelineStatus.OK
                else 0
            )

            if (
                name == "criticality"
                and len(trace) != expected_traces
            ):
                raise AssertionError(
                    "unexpected criticality diagnostic count"
                )

            decision = trace[0] if trace else None

            if decision is not None:
                excluded = set(
                    decision.excluded_invalid_ids
                )

                if excluded & ids:
                    raise AssertionError(
                        "invalid evidence was exposed in final context"
                    )

            gold = set(
                label["expected_promoted_ids"]
            )

            invalid = set(
                label["invalid_evidence_ids"]
            )

            conflicts = set(
                label["competing_evidence_ids"]
            )

            promoted = (
                set(decision.promoted_ids)
                if decision else set()
            )

            rows.append({
                "scenario_id": scenario["scenario_id"],
                "base_case_id": scenario["metadata"][
                    "base_case_id"
                ],
                "family": label["task_family"],
                "variant": label["variant"],
                "policy": name,
                "effective_budget": effective,
                "status": context.status.value,

                "critical_in_context": int(
                    bool(gold)
                    and gold.issubset(ids)
                ),
                "positive_case": bool(gold),

                "promoted_gold": len(
                    promoted & gold
                ),
                "promoted_invalid": len(
                    promoted & (invalid | conflicts)
                ),

                "invalid_in_context": len(
                    ids & invalid
                ),
                "conflicting_in_context": len(
                    ids & conflicts
                ),

                "ambiguous_expected": bool(
                    label["expected_ambiguous_slots"]
                ),
                "ambiguous_detected": bool(
                    decision
                    and label["slot"]
                    in decision.ambiguous_slots
                ),
                "unresolved_detected": bool(
                    decision
                    and label["slot"]
                    in decision.unresolved_slots
                ),

                "promoted_ids": sorted(promoted),
                "selected_ids": sorted(ids),
                "builder_dropped_ids": list(
                    context.dropped_flexible_ids
                ),

                "context_tokens": (
                    context.total_input_tokens
                    if ok else None
                ),
                "mandatory_retained": bool(
                    ok and mandatory_ids.issubset(ids)
                ),
                "excluded_invalid_ids": (
                    list(decision.excluded_invalid_ids)
                    if decision else []
                ),

                "filtered_invalid_count": (
                    len(decision.excluded_invalid_ids)
                    if decision else 0
                ),

                "exclusion_reasons": (
                    list(decision.exclusion_reasons)
                    if decision else []
                ),

                "slot_statuses": (
                    list(decision.slot_statuses)
                    if decision else []
                ),
            })

        print(
            f"[{i}/{len(scenarios)}] "
            f"{scenario['scenario_id']}",
            flush=True,
        )

    return rows


def summarize(rows):
    groups = defaultdict(list)

    for row in rows:
        groups[
            (row["variant"], row["policy"])
        ].append(row)

    result = []

    for (variant, policy), group in sorted(
        groups.items()
    ):
        positives = [
            r for r in group
            if r["positive_case"]
        ]

        conflicts = [
            r for r in group
            if r["ambiguous_expected"]
        ]

        result.append({
            "variant": variant,
            "policy": policy,
            "n": len(group),

            "positive_recall": (
                sum(
                    r["critical_in_context"]
                    for r in positives
                ) / len(positives)
                if positives else None
            ),

            "promoted_gold": sum(
                r["promoted_gold"] for r in group
            ),

            "false_promotions": sum(
                r["promoted_invalid"] for r in group
            ),

            "invalid_exposures": sum(
                r["invalid_in_context"] for r in group
            ),

            "conflict_exposures": sum(
                r["conflicting_in_context"] for r in group
            ),

            "conflicts_detected": sum(
                r["ambiguous_detected"]
                for r in conflicts
            ),

            "missing_slot_detected": sum(
                r["unresolved_detected"]
                for r in group
            ),

            "context_failures": sum(
                r["status"] != "ok"
                for r in group
            ),

            "mandatory_omissions": sum(
                not r["mandatory_retained"]
                and r["status"] == "ok"
                for r in group
            ),
        })

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--dataset",
        required=True,
    )

    parser.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )

    parser.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    config = _load_runner_config(args.config)
    _require_executable_config(config)

    if (
        config.get("scope") != "ablation"
        or config.get("freeze", {}).get("version")
        != "1.0.4"
    ):
        raise ValueError(
            "expected frozen v1.0.4 ablation configuration"
        )

    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=(
            tc["persistent_cache_path"]
        ),
        max_retries=tc["max_retries"],
        retry_base_seconds=(
            tc["retry_base_seconds"]
        ),
    )

    dataset, labels, manifest = load_and_validate(
        args.dataset,
        counter,
    )

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

    rows = evaluate(
        dataset["scenarios"],
        labels["labels"],
        system_prompt=Path(
            model["system_prompt_file"]
        ).read_text(encoding="utf-8"),
        tools=Path(
            model["tool_definitions_file"]
        ).read_text(encoding="utf-8"),
        embedder=multilingual_embedder,
        counter=counter,
        top_k=config["runtime"]["candidate_top_k"],
        weights=tuple(
            config["runtime"]["utility_weights"]
        ),
    )

    report = summarize(rows)

    output = Path(args.output)

    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    _csv(
        output / "selection_details.csv",
        rows,
    )

    _csv(
        output / "policy_summary.csv",
        report,
    )

    validation = {
        "kind": "selection_only_development",
        "freeze": config["freeze"],
        "dataset_manifest": manifest,
        "contexts": len(rows),
        "llm_generation_calls": 0,
        "failures": sum(
            r["status"] != "ok"
            for r in rows
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

    print(
        "[PASS]",
        len(rows),
        "Context evaluations saved to",
        output,
    )


if __name__ == "__main__":
    main()
