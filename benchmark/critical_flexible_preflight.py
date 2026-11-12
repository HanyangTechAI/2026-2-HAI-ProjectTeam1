from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess

from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Sequence

from benchmark.action_time_preflight import (
    LEVELS,
    PRESETS,
    REFERENCE_BUDGET,
    SAFETY_MARGIN,
    _select,
    calibrate,
)

from benchmark.critical_flexible_generator import (
    CONDITIONS,
    DATASET_VERSION,
    TASK_FAMILIES,
    validate_scenario,
)

from context.builder import build_context, fixed_context_tokens
from context.tokenizer import GeminiTokenCounter, token_count_mode

from memory.query_analyzer import analyze_query
from memory.selector import (
    PipelineStatus,
    compute_utility,
    retrieve_candidates,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _status(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _candidate_scores(
    scenario, cal, embedder, counter, top_k, weights,
):
    """Retrieval 후보의 실제 Utility 및 Token Cost를 진단한다."""
    query_context = analyze_query(
        scenario["query"],
        scenario["current_turn"],
        cal["clock"],
    )

    candidates = retrieve_candidates(
        query_context=query_context,
        memory_store=cal["store"],
        session_id=scenario["session_id"],
        embedder=embedder,
        top_k=top_k,
        exclude_active_protected=True,
    )

    scores = {}

    for candidate in candidates:
        memory = candidate.memory
        cost = counter(memory.content)

        utility = compute_utility(
            candidate,
            query_context,
            weights=weights,
        )

        scores[memory.memory_id] = {
            "tokens": cost,
            "semantic_score": candidate.semantic_score,
            "utility_score": utility,
            "utility_per_token": utility / max(1, cost),
        }

    return scores


def _minimum_critical_budget(
    scenario,
    selection,
    critical_id,
    system_prompt,
    tool_definitions,
    counter,
):
    """Mandatory + Critical 하나만 넣을 때 필요한 최소 실효 예산.

    Selector의 토큰 비용과 실제 Request CountTokens 중 큰 값을 사용한다.
    이 반사실적 Context는 예산 수용 가능성 판정에만 사용한다.
    """
    critical = next(
        (
            memory
            for memory in selection.flexible
            if memory.memory_id == critical_id
        ),
        None,
    )

    if critical is None:
        return None

    mandatory = {
        memory.memory_id: memory
        for memory in (
            *selection.protected,
            *selection.states,
        )
    }

    fixed = fixed_context_tokens(
        system_prompt,
        tool_definitions,
        scenario["query"],
        counter,
    )

    critical_only = replace(
        selection,
        flexible=(critical,),
    )

    context = build_context(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=scenario["query"],
        selection=critical_only,
        total_budget=REFERENCE_BUDGET,
        token_counter=counter,
    )

    if context.status != PipelineStatus.OK:
        raise RuntimeError(
            "mandatory + critical reference context failed"
        )

    selector_cost = (
        fixed
        + sum(m.token_count for m in mandatory.values())
        + critical.token_count
    )

    return max(
        selector_cost,
        context.total_input_tokens,
    )


def preflight_scenarios(
    scenarios: Sequence[dict[str, Any]],
    *,
    system_prompt: str,
    tool_definitions: str,
    embedder: Callable,
    counter: Callable,
    top_k: int = 5,
    weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    safety_margin: int = SAFETY_MARGIN,
):
    """Scenario × 5 Budget × 2 Strategy의 선택 결과를 계산한다."""
    if not scenarios:
        raise ValueError("no scenarios")

    if type(safety_margin) is not int or safety_margin < 0:
        raise ValueError("invalid safety margin")

    identifiers = [s["scenario_id"] for s in scenarios]

    if len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate scenario_id")

    if any(
        s.get("token_count_mode") != token_count_mode(counter)
        for s in scenarios
    ):
        raise ValueError("scenario/token counter mismatch")

    calibration_rows = []
    records = []

    for index, scenario in enumerate(scenarios, 1):
        validate_scenario(scenario, counter)

        meta = scenario["metadata"]

        if meta.get("dataset_version") != DATASET_VERSION:
            raise ValueError("dataset version mismatch")

        if meta.get("stress_condition") not in CONDITIONS:
            raise ValueError("unknown stress condition")

        if meta.get("task_family") not in TASK_FAMILIES:
            raise ValueError("unknown task family")

        critical_ids = meta["critical_flexible_memory_ids"]

        if len(critical_ids) != 1:
            raise ValueError(
                "exactly one Critical Flexible is required"
            )

        critical_id = critical_ids[0]

        # 첫 행동의 Mandatory Floor와 All-Flexible Ceiling
        cal = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            top_k,
            weights,
        )

        candidate_scores = _candidate_scores(
            scenario,
            cal,
            embedder,
            counter,
            top_k,
            weights,
        )

        # 충분한 예산에서 Full의 후보를 확보한다.
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
            raise RuntimeError("reference selection failed")

        minimum = _minimum_critical_budget(
            scenario,
            reference,
            critical_id,
            system_prompt,
            tool_definitions,
            counter,
        )

        # 이번 v1 데이터는 검색 대상이 최대 5개이므로
        # Critical Retrieval Hit가 반드시 성립해야 한다.
        if critical_id not in candidate_scores or minimum is None:
            raise ValueError(
                "critical absent from reference retrieval: "
                + scenario["scenario_id"]
            )

        if minimum > cal["ceiling"]:
            raise RuntimeError(
                "critical requirement exceeds all-flexible ceiling"
            )

        calibration_rows.append({
            "scenario_id": scenario["scenario_id"],
            "base_case_id": meta["base_case_id"],
            "task_family": meta["task_family"],
            "condition": meta["stress_condition"],
            "critical_length": meta["critical_length"],
            "noise_length": meta["noise_length"],
            "floor": cal["floor"],
            "ceiling": cal["ceiling"],
            "critical_min_effective_budget": minimum,
            "critical_id": critical_id,
            "reference_mandatory_ids": (
                cal["reference_mandatory_ids"]
            ),
            "reference_flexible_ids": (
                cal["reference_flexible_ids"]
            ),
            "candidate_scores": candidate_scores,
        })

        reference_flexible = set(
            cal["reference_flexible_ids"]
        )
        mandatory = set(
            cal["reference_mandatory_ids"]
        )

        for level in LEVELS:
            effective = (
                cal["floor"]
                + int(
                    (cal["ceiling"] - cal["floor"]) * level
                )
            )

            nominal = effective + safety_margin

            # Critical 하나를 넣을 수 있는 예산인가?
            feasible = effective >= minimum

            for preset in PRESETS:
                selection, context = _select(
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

                ok = (
                    selection.status == PipelineStatus.OK
                    and context.status == PipelineStatus.OK
                )

                selected = (
                    set(context.selected_memory_ids)
                    if ok else set()
                )

                retrieved = (
                    critical_id
                    in selection.retrieved_memory_ids
                )
                selector_hit = (
                    critical_id
                    in selection.selected_memory_ids
                )
                retained = critical_id in selected

                # 정답 누락 원인을 순서대로 구분한다.
                if retained:
                    reason = "retained"
                elif not retrieved:
                    reason = "retrieval_miss"
                elif not feasible:
                    reason = "budget_infeasible"
                elif not ok:
                    reason = "pipeline_budget_failure"
                elif not selector_hit:
                    reason = "selector_packing_omission"
                else:
                    reason = "context_builder_trimmed"

                if ok and context.total_input_tokens > effective:
                    raise AssertionError(
                        "context exceeds effective budget"
                    )

                if ok and not mandatory.issubset(selected):
                    raise AssertionError(
                        "mandatory memory omitted"
                    )

                records.append({
                    "scenario_id": scenario["scenario_id"],
                    "base_case_id": meta["base_case_id"],
                    "task_family": meta["task_family"],
                    "condition": meta["stress_condition"],
                    "level": level,
                    "preset": preset,

                    "nominal_budget": nominal,
                    "effective_budget": effective,
                    "floor": cal["floor"],
                    "ceiling": cal["ceiling"],

                    "critical_min_effective_budget": minimum,
                    "critical_id": critical_id,
                    "critical_feasible": feasible,
                    "critical_retrieved": retrieved,
                    "critical_in_selector": selector_hit,
                    "critical_in_context": retained,
                    "critical_omission_reason": reason,

                    "selection_status": _status(
                        selection.status
                    ),
                    "context_status": _status(
                        context.status
                    ),
                    "context_tokens": (
                        context.total_input_tokens
                        if ok else None
                    ),

                    "budget_overflow": bool(
                        ok and
                        context.total_input_tokens > effective
                    ),
                    "mandatory_retained": bool(
                        ok and mandatory.issubset(selected)
                    ),

                    "selected_flexible_ids": sorted(
                        selected & reference_flexible
                    ),
                    "selected_memory_ids": sorted(selected),
                    "selector_selected_ids": list(
                        selection.selected_memory_ids
                    ),
                    "builder_dropped_ids": list(
                        context.dropped_flexible_ids
                    ),

                    "critical_tokens": (
                        candidate_scores[critical_id]["tokens"]
                    ),
                    "critical_utility": (
                        candidate_scores[critical_id]["utility_score"]
                    ),
                    "critical_utility_per_token": (
                        candidate_scores[critical_id]["utility_per_token"]
                    ),
                })

        print(
            f"[{index}/{len(scenarios)}] "
            f"{scenario['scenario_id']} "
            f"floor={cal['floor']} "
            f"ceiling={cal['ceiling']}",
            flush=True,
        )

    summaries = summarize_pairs(records)

    return calibration_rows, records, summaries


def summarize_pairs(records: Sequence[dict]) -> list[dict]:
    """조건별 Paired 결과를 계산한다. 반복을 독립 표본으로 취급하지 않는다."""
    groups = defaultdict(dict)

    for row in records:
        key = (
            row["scenario_id"],
            row["level"],
            row["condition"],
        )

        if row["preset"] in groups[key]:
            raise ValueError(
                f"duplicate paired record: {key}"
            )

        groups[key][row["preset"]] = row

    if any(
        set(pair) != set(PRESETS)
        for pair in groups.values()
    ):
        raise ValueError(
            "unmatched Full / No Packing pair"
        )

    summaries = []

    for condition in (*CONDITIONS.keys(), "ALL"):
        for level in LEVELS:
            pairs = [
                pair
                for (sid, lev, cond), pair in groups.items()
                if lev == level
                and (condition == "ALL" or cond == condition)
            ]

            if not pairs:
                continue

            full = [p["full"] for p in pairs]
            baseline = [
                p["no_budget_aware_packing"]
                for p in pairs
            ]

            feasible = [
                p for p in pairs
                if p["full"]["critical_feasible"]
            ]

            both_ok = [
                p for p in pairs
                if all(
                    p[k]["context_status"] == "ok"
                    for k in PRESETS
                )
            ]

            summaries.append({
                "condition": condition,
                "level": level,
                "pairs": len(pairs),

                "unique_base_cases": len({
                    p["full"]["base_case_id"]
                    for p in pairs
                }),

                "both_ok": len(both_ok),
                "critical_feasible": len(feasible),

                "retrieval_hit": sum(
                    p["full"]["critical_retrieved"]
                    for p in pairs
                ),

                "flexible_set_diff": sum(
                    p["full"]["selected_flexible_ids"]
                    != p["no_budget_aware_packing"]["selected_flexible_ids"]
                    for p in both_ok
                ),

                "full_critical_recall": (
                    sum(
                        r["critical_in_context"]
                        for r in full
                    ) / len(full)
                ),

                "no_packing_critical_recall": (
                    sum(
                        r["critical_in_context"]
                        for r in baseline
                    ) / len(baseline)
                ),

                # 예산상 정답 수용 가능 과제만 별도 계산
                "full_feasible_recall": (
                    sum(
                        p["full"]["critical_in_context"]
                        for p in feasible
                    ) / len(feasible)
                    if feasible else None
                ),

                "no_packing_feasible_recall": (
                    sum(
                        p["no_budget_aware_packing"]
                        ["critical_in_context"]
                        for p in feasible
                    ) / len(feasible)
                    if feasible else None
                ),

                "full_packing_omissions": sum(
                    r["critical_omission_reason"] in (
                        "selector_packing_omission",
                        "context_builder_trimmed",
                    )
                    for r in full
                ),

                "no_packing_omissions": sum(
                    r["critical_omission_reason"] in (
                        "selector_packing_omission",
                        "context_builder_trimmed",
                    )
                    for r in baseline
                ),

                "full_pipeline_failures": sum(
                    r["context_status"] != "ok"
                    for r in full
                ),

                "no_packing_pipeline_failures": sum(
                    r["context_status"] != "ok"
                    for r in baseline
                ),

                "overflow_count": sum(
                    r["budget_overflow"]
                    for r in (*full, *baseline)
                ),
            })

    return summaries


def _save_csv(path: Path, records: Sequence[dict]) -> None:
    if not records:
        raise ValueError("cannot save empty CSV")

    columns = list(records[0])

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=columns,
        )
        writer.writeheader()

        for record in records:
            writer.writerow({
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in record.items()
            })


def _git_provenance() -> dict:
    def git(*args):
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip()

    status = git("status", "--porcelain")

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": (
            bool(status) if status is not None else None
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

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
        default=(
            "experiments/results/"
            "critical_flexible_preflight_v104"
        ),
    )

    args = parser.parse_args()

    from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION

    from benchmark.runner import (
        _load_runner_config,
        _require_executable_config,
    )

    from benchmark.experiment_runtime import (
        multilingual_embedder,
        embedding_runtime_metadata,
    )

    config = _load_runner_config(args.config)
    _require_executable_config(config)

    if (
        config["scope"] != "ablation"
        or config["freeze"]["version"] != "1.0.4"
    ):
        raise ValueError(
            "expected Ablation Freeze v1.0.4"
        )

    source = Path(args.scenarios)

    data = json.loads(
        source.read_text(encoding="utf-8")
    )

    if data.get("dataset_version") != DATASET_VERSION:
        raise ValueError("dataset version mismatch")

    scenarios = data["scenarios"]

    if (
        len(scenarios) != 160
        or len({
            s["scenario_id"] for s in scenarios
        }) != 160
    ):
        raise ValueError(
            "expected 160 unique scenarios"
        )

    condition_counts = defaultdict(int)
    base_ids = set()

    for scenario in scenarios:
        meta = scenario["metadata"]

        condition_counts[
            meta["stress_condition"]
        ] += 1

        base_ids.add(
            meta["base_case_id"]
        )

    if (
        set(condition_counts) != set(CONDITIONS)
        or any(
            condition_counts[k] != 40
            for k in CONDITIONS
        )
        or len(base_ids) != 40
    ):
        raise ValueError(
            "expected 40 base cases and "
            "40 cases per condition"
        )

    # Generator가 생성한 Manifest와 원본 바이트 비교
    manifest_path = source.with_name(
        source.stem + "_manifest.json"
    )

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"missing manifest: {manifest_path}"
        )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if _sha256(source) != manifest.get("dataset_sha256"):
        raise ValueError(
            "dataset checksum mismatch"
        )

    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
    )

    if data["token_count_mode"] != token_count_mode(counter):
        raise ValueError(
            "dataset/tokenizer mismatch"
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

    # 기존 Runner와 동일한 Request CountTokens 형식
    counter.configure_request(
        system_instruction=llm.system_instruction,
        generation_config=llm.planner().generation_config(),
    )

    system_prompt = Path(
        model["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    calibrations, records, summaries = preflight_scenarios(
        scenarios,
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=config["runtime"]["candidate_top_k"],
        weights=tuple(
            config["runtime"]["utility_weights"]
        ),
        safety_margin=config["budget_policy"][
            "request_token_safety_margin"
        ],
    )

    assert len(calibrations) == 160
    assert len(records) == 1600

    output = Path(args.output)

    # 기존 결과 덮어쓰기 방지
    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    (output / "calibration.json").write_text(
        json.dumps(
            calibrations,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    (output / "selection_details.jsonl").write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
            ) + "\n"
            for row in records
        ),
        encoding="utf-8",
    )

    _save_csv(
        output / "selection_details.csv",
        records,
    )

    _save_csv(
        output / "paired_summary.csv",
        summaries,
    )

    validation = {
        "dataset_file": str(source),
        "dataset_sha256": _sha256(source),
        "manifest_sha256": _sha256(manifest_path),
        "freeze": config["freeze"],

        "scenarios": len(calibrations),
        "context_evaluations": len(records),
        "paired_cases": len(records) // 2,

        "llm_generation_calls": 0,
        "safety_margin": config["budget_policy"][
            "request_token_safety_margin"
        ],

        "tokenizer": counter.metadata(),
        "embedding": embedding_runtime_metadata(),

        "overflow_count": sum(
            r["budget_overflow"]
            for r in records
        ),

        "mandatory_failures": sum(
            r["context_status"] == "ok"
            and not r["mandatory_retained"]
            for r in records
        ),

        "summary": summaries,
        **_git_provenance(),
    }

    (output / "validation.json").write_text(
        json.dumps(
            validation,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    for row in summaries:
        if row["condition"] != "ALL":
            continue

        print(
            f"level={row['level']:.0%} "
            f"critical_recall: "
            f"full={row['full_critical_recall']:.1%} "
            f"no_packing={row['no_packing_critical_recall']:.1%} "
            f"feasible={row['critical_feasible']}/{row['pairs']} "
            f"selection_diff={row['flexible_set_diff']}"
        )

    print("[PASS] Saved:", output)


if __name__ == "__main__":
    main()
