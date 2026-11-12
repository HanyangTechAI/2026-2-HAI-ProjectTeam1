
"""Selection-only Criticality-Aware vs frozen packing (development set).

Uses Gemini CountTokens and frozen embedding; does not call Gemini generation.
No scenario labels/expected tool arguments are passed to any policy.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from benchmark.action_time_preflight import REFERENCE_BUDGET, _select, calibrate
from benchmark.critical_flexible_generator import DATASET_VERSION, validate_scenario
from benchmark.critical_flexible_preflight import _minimum_critical_budget
from context.builder import build_context, fixed_context_tokens
from context.tokenizer import GeminiTokenCounter, token_count_mode
from memory.ablation import get_ablation_config
from memory.criticality_policy import make_criticality_policy
from memory.schema import MemoryItem
from memory.selector import PipelineStatus
from pipeline.orchestrator import make_proposed_policy


POLICIES = ("full", "no_packing", "criticality")
LEVEL = 0.75


def evaluate_development(
    scenarios: Sequence[dict[str, Any]],
    *,
    system_prompt: str,
    tool_definitions: str,
    embedder: Callable,
    counter: Callable,
    top_k: int = 5,
    weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    safety_margin: int = 32,
) -> tuple[list[dict], list[dict]]:
    if type(safety_margin) is not int or safety_margin < 0:
        raise ValueError("invalid safety margin")

    ids = [s["scenario_id"] for s in scenarios]

    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicate scenario IDs")

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
            ablation=get_ablation_config("no_budget_aware_packing"),
        ),
        "criticality": make_criticality_policy(
            embedder=embedder,
            top_k=top_k,
            utility_weights=weights,
            on_decision=trace.append,
        ),
    }

    rows = []

    for index, scenario in enumerate(scenarios, 1):
        if (
            scenario.get("token_count_mode")
            != token_count_mode(counter)
        ):
            raise ValueError("scenario token counter mismatch")

        validate_scenario(scenario, counter)

        cal = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            top_k,
            weights,
        )

        reference, _ = _select(
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

        if reference.status != PipelineStatus.OK:
            raise RuntimeError("invalid frozen reference")

        critical_ids = scenario["metadata"][
            "critical_flexible_memory_ids"
        ]

        if len(critical_ids) != 1:
            raise ValueError(
                "expected exactly one labeled critical fact"
            )

        critical_id = critical_ids[0]

        minimum = _minimum_critical_budget(
            scenario,
            reference,
            critical_id,
            system_prompt,
            tool_definitions,
            counter,
        )

        if minimum is None:
            raise RuntimeError(
                "critical not in frozen retrieval pool"
            )

        effective = cal["floor"] + int(
            (cal["ceiling"] - cal["floor"]) * LEVEL
        )

        fixed = fixed_context_tokens(
            system_prompt,
            tool_definitions,
            scenario["query"],
            counter,
        )

        memories = tuple(
            MemoryItem.from_dict(m)
            for m in scenario["memory_snapshot"]
        )

        meta = scenario["metadata"]

        mandatory_reference = {
            m.memory_id
            for m in (
                *reference.protected,
                *reference.states,
            )
        }

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

            if ok and context.total_input_tokens > effective:
                raise AssertionError("Context budget overflow")

            if ok and not mandatory_reference.issubset(selected):
                raise AssertionError(
                    "Protected/State omission"
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
                    "missing or duplicated criticality diagnostic"
                )

            decision = trace[0] if trace else None

            rows.append({
                "scenario_id": scenario["scenario_id"],
                "base_case_id": meta["base_case_id"],
                "task_family": meta["task_family"],
                "condition": meta["stress_condition"],
                "policy": name,
                "level": LEVEL,
                "nominal_budget": effective + safety_margin,
                "effective_budget": effective,
                "critical_feasible": effective >= minimum,
                "critical_in_context": critical_id in selected,
                "critical_retrieved": (
                    critical_id in selection.retrieved_memory_ids
                ),
                "mandatory_ok": (
                    ok and mandatory_reference.issubset(selected)
                ),
                "selector_status": selection.status.value,
                "context_status": context.status.value,
                "context_tokens": (
                    context.total_input_tokens if ok else None
                ),
                "unused_budget": (
                    context.unused_budget if ok else None
                ),
                "promoted_ids": (
                    list(decision.promoted_ids)
                    if decision else []
                ),
                "ambiguous_slots": (
                    list(decision.ambiguous_slots)
                    if decision else []
                ),
                "unresolved_slots": (
                    list(decision.unresolved_slots)
                    if decision else []
                ),
                "builder_dropped_ids": list(
                    context.dropped_flexible_ids
                ),
                "noise_in_context": len(
                    set(meta["distractor_memory_ids"]) & selected
                ),
                "false_promotions": sum(
                    mid != critical_id
                    for mid in (
                        decision.promoted_ids if decision else ()
                    )
                ),
            })

        print(
            f"[{index}/{len(scenarios)}] "
            f"{scenario['scenario_id']}",
            flush=True,
        )

    return rows, summarize(rows)


def summarize(rows: Sequence[dict]) -> list[dict]:
    groups = defaultdict(list)

    for row in rows:
        groups[("ALL", row["policy"])].append(row)
        groups[(row["condition"], row["policy"])].append(row)

    report = []

    for (condition, policy), values in sorted(groups.items()):
        feasible = [
            r for r in values
            if r["critical_feasible"]
        ]

        report.append({
            "condition": condition,
            "policy": policy,
            "n": len(values),
            "base_cases": len({
                r["base_case_id"] for r in values
            }),
            "retrieval_recall": sum(
                r["critical_retrieved"] for r in values
            ) / len(values),
            "critical_recall": sum(
                r["critical_in_context"] for r in values
            ) / len(values),
            "feasible_recall": (
                sum(
                    r["critical_in_context"] for r in feasible
                ) / len(feasible)
                if feasible else None
            ),
            "packing_omissions": sum(
                r["critical_feasible"]
                and not r["critical_in_context"]
                for r in values
            ),
            "promotions": sum(
                len(r["promoted_ids"]) for r in values
            ),
            "ambiguous": sum(
                bool(r["ambiguous_slots"]) for r in values
            ),
            "false_promotions": sum(
                r["false_promotions"] for r in values
            ),
            "mean_noise_count": sum(
                r["noise_in_context"] for r in values
            ) / len(values),
            "pipeline_failures": sum(
                r["context_status"] != "ok"
                for r in values
            ),
            "mandatory_omissions": sum(
                r["context_status"] == "ok"
                and not r["mandatory_ok"]
                for r in values
            ),
        })

    return report


def _csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        raise ValueError("empty output")

    with path.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as fp:
        writer = csv.DictWriter(
            fp,
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
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--scenarios",
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

    from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION
    from benchmark.experiment_runtime import (
        multilingual_embedder,
        embedding_runtime_metadata,
    )
    from benchmark.runner import (
        _load_runner_config,
        _require_executable_config,
    )

    cfg = _load_runner_config(args.config)
    _require_executable_config(cfg)

    if (
        cfg.get("scope") != "ablation"
        or cfg.get("freeze", {}).get("version") != "1.0.4"
    ):
        raise ValueError(
            "expected frozen v1.0.4 ablation config"
        )

    source = Path(args.scenarios)

    data = json.loads(
        source.read_text(encoding="utf-8")
    )

    if (
        data.get("generator")
        != "benchmark.critical_flexible_generator"
        or data.get("holdout")
        or data.get("dataset_version") != DATASET_VERSION
        or data.get("parameters", {}).get("seed_start") != 42
        or data.get("parameters", {}).get("cases_per_family") != 20
        or len(data.get("scenarios", [])) != 160
    ):
        raise ValueError(
            "use original 160-case development dataset, not holdout"
        )

    manifest_path = source.with_name(
        source.stem + "_manifest.json"
    )

    dataset_sha = hashlib.sha256(
        source.read_bytes()
    ).hexdigest()

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if manifest.get("dataset_sha256") != dataset_sha:
        raise ValueError(
            "dataset manifest SHA-256 mismatch"
        )

    tc = cfg["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
    )

    if data.get("token_count_mode") != token_count_mode(counter):
        raise ValueError(
            "runtime/dataset token mode mismatch"
        )

    model = cfg["model"]

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

    rows, report = evaluate_development(
        data["scenarios"],
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=cfg["runtime"]["candidate_top_k"],
        weights=tuple(
            cfg["runtime"]["utility_weights"]
        ),
        safety_margin=cfg["budget_policy"][
            "request_token_safety_margin"
        ],
    )

    output = Path(args.output)

    # 기존 실험 결과를 덮어쓰지 않는다.
    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    (output / "selection_details.jsonl").write_text(
        "".join(
            json.dumps(r, ensure_ascii=False) + "\n"
            for r in rows
        ),
        encoding="utf-8",
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
        "kind": "development_selection_only",
        "source_sha256": dataset_sha,
        "freeze": cfg["freeze"],
        "n_scenarios": len(data["scenarios"]),
        "n_contexts": len(rows),
        "n_generation_calls": 0,
        "budget_level": LEVEL,
        "policies": list(POLICIES),
        "pipeline_failures": sum(
            r["context_status"] != "ok"
            for r in rows
        ),
        "mandatory_omissions": sum(
            r["context_status"] == "ok"
            and not r["mandatory_ok"]
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

    for r in report:
        if r["condition"] == "C":
            print(
                f"C {r['policy']}: "
                f"Critical Recall={r['critical_recall']:.1%}, "
                f"omissions={r['packing_omissions']}/{r['n']}"
            )

    print(
        f"[PASS] {len(rows)} Contexts -> {output}"
    )


if __name__ == "__main__":
    main()
