from __future__ import annotations

import argparse
import csv
import copy
import hashlib
import json
import math
import subprocess

from collections import defaultdict
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION
from context.builder import build_context, fixed_context_tokens
from context.tokenizer import GeminiTokenCounter
from memory.ablation import get_ablation_config
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, select_memory
from memory.store import SnapshotMemoryStore

LEVELS = (0.0, 0.25, 0.5, 0.75, 1.0)
PRESETS = ("full", "no_budget_aware_packing")
REFERENCE_BUDGET = 1_000_000
SAFETY_MARGIN = 32


def _time(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("scenario.current_time must be an ISO datetime")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _snapshot(scenario: dict[str, Any]) -> SnapshotMemoryStore:
    memories = tuple(
        memory
        for raw in scenario["memory_snapshot"]
        if (memory := MemoryItem.from_dict(raw)).session_id == scenario["session_id"] and memory.turn_id <= scenario["current_turn"]
    )
    return SnapshotMemoryStore(memories)


def _select(
    scenario,
    store,
    clock,
    system_prompt,
    tool_definitions,
    embedder,
    counter,
    budget,
    preset,
    top_k,
    weights,
):
    fixed = fixed_context_tokens(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=scenario["query"],
        token_counter=counter,
    )

    selection = select_memory(
        query=scenario["query"],
        session_id=scenario["session_id"],
        memory_store=store,
        total_budget=budget,
        fixed_tokens=fixed,
        current_turn=scenario["current_turn"],
        current_time=clock,
        embedder=embedder,
        token_counter=counter,
        top_k=top_k,
        utility_weights=weights,
        ablation=get_ablation_config(preset),
    )

    context = build_context(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=scenario["query"],
        selection=selection,
        total_budget=budget,
        token_counter=counter,
    )

    return selection, context


def calibrate(
    scenario,
    system_prompt,
    tool_definitions,
    embedder,
    counter,
    top_k=5,
    weights=(0.6, 0.3, 0.1),
):
    """첫 행동 단계의 Mandatory Floor와 All-Flexible Ceiling 계산."""
    store = _snapshot(scenario)
    clock = _time(scenario["current_time"])
    reference = {}

    for preset in PRESETS:
        selection, context = _select(
            scenario,
            store,
            clock,
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            REFERENCE_BUDGET,
            preset,
            top_k,
            weights,
        )

        if (
            selection.status != PipelineStatus.OK
            or context.status != PipelineStatus.OK
        ):
            raise RuntimeError(
                f"reference failed: {scenario['scenario_id']} {preset}"
            )

        if len(context.selected_memory_ids) != len(
            set(context.selected_memory_ids)
        ):
            raise RuntimeError("duplicate selected memory IDs")

        # Mandatory만 포함한 Context를 생성한다.
        mandatory_selection = replace(selection, flexible=())

        mandatory_context = build_context(
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            query=scenario["query"],
            selection=mandatory_selection,
            total_budget=REFERENCE_BUDGET,
            token_counter=counter,
        )

        if mandatory_context.status != PipelineStatus.OK:
            raise RuntimeError("mandatory reference failed")

        fixed = fixed_context_tokens(
            system_prompt,
            tool_definitions,
            scenario["query"],
            counter,
        )

        mandatory_cost = sum(
            m.token_count for m in selection.protected + selection.states
        )
        flexible_cost = sum(
            m.token_count for m in selection.flexible
        )

        # 실제 Request CountTokens와 Selector의 비용 추정을 모두 고려한다.
        reference[preset] = {
            "floor": max(
                mandatory_context.total_input_tokens,
                fixed + mandatory_cost,
            ),
            "ceiling": max(
                context.total_input_tokens,
                fixed + mandatory_cost + flexible_cost,
            ),
            "mandatory_ids": set(mandatory_context.selected_memory_ids),
            "flexible_ids": (
                set(context.selected_memory_ids)
                - set(mandatory_context.selected_memory_ids)
            ),
        }

    full, baseline = (reference[p] for p in PRESETS)

    if full["mandatory_ids"] != baseline["mandatory_ids"]:
        raise RuntimeError(
            "mandatory sets differ between packing policies"
        )

    if full["flexible_ids"] != baseline["flexible_ids"]:
        raise RuntimeError(
            "reference flexible candidate sets differ"
        )

    floor = max(full["floor"], baseline["floor"])
    ceiling = max(full["ceiling"], baseline["ceiling"])

    if ceiling < floor:
        raise RuntimeError(
            "all-flexible ceiling is less than mandatory floor"
        )

    # 정답 Flexible ID가 명시된 데이터셋에서만 Recall을 평가한다.
    labels = scenario.get("metadata", {}).get(
        "critical_flexible_memory_ids"
    )

    if labels is not None:
        if not isinstance(labels, list) or any(
            not isinstance(v, str) for v in labels
        ):
            raise ValueError(
                "critical_flexible_memory_ids must be an array of IDs"
            )

        if not set(labels).issubset(full["flexible_ids"]):
            raise ValueError(
                "critical labels are not in reference flexible pool"
            )

    return {
        "scenario_id": scenario["scenario_id"],
        "floor": floor,
        "ceiling": ceiling,
        "reference_flexible_ids": sorted(full["flexible_ids"]),
        "reference_mandatory_ids": sorted(full["mandatory_ids"]),
        "critical_ids": labels,
        "store": store,
        "clock": clock,
    }


def evaluate_level(
    scenario,
    calibration,
    level,
    system_prompt,
    tool_definitions,
    embedder,
    counter,
    top_k=5,
    weights=(0.6, 0.3, 0.1),
    safety_margin=SAFETY_MARGIN,
):
    """동일한 절대 예산으로 두 Packing 정책을 비교한다."""
    floor = calibration["floor"]
    ceiling = calibration["ceiling"]

    effective_budget = floor + math.floor(
        level * (ceiling - floor)
    )
    nominal_budget = effective_budget + safety_margin

    candidate_ids = set(
        calibration["reference_flexible_ids"]
    )
    critical = calibration["critical_ids"]

    records = []

    for preset in PRESETS:
        selection, context = _select(
            scenario,
            calibration["store"],
            calibration["clock"],
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            effective_budget,
            preset,
            top_k,
            weights,
        )

        selected_ids = set(context.selected_memory_ids)
        flexible_ids = selected_ids & candidate_ids

        mandatory_ok = (
            context.status == PipelineStatus.OK
            and set(
                calibration["reference_mandatory_ids"]
            ).issubset(selected_ids)
        )

        within_budget = (
            context.status == PipelineStatus.OK
            and context.total_input_tokens <= effective_budget
        )

        critical_recall = (
            None if not critical
            else len(flexible_ids & set(critical)) / len(critical)
        )

        records.append({
            "scenario_id": scenario["scenario_id"],
            "level": level,
            "preset": preset,
            "nominal_budget": nominal_budget,
            "effective_budget": effective_budget,
            "floor": floor,
            "ceiling": ceiling,
            "selection_status": selection.status.value,
            "context_status": context.status.value,
            "context_tokens": (
                context.total_input_tokens
                if context.status == PipelineStatus.OK
                else None
            ),
            "selected_ids": sorted(selected_ids),
            "selected_flexible_ids": sorted(flexible_ids),
            "dropped_flexible_ids": list(
                context.dropped_flexible_ids
            ),
            "reference_flexible_count": len(candidate_ids),
            "mandatory_ok": mandatory_ok,
            "within_budget": within_budget,
            "critical_flexible_recall": critical_recall,
            "critical_flexible_labeled": critical is not None,
        })

    return records


def summarize(records):
    """동일 과제·예산 수준의 Paired Selection을 집계한다."""
    pairs = defaultdict(dict)

    for r in records:
        key = (r["scenario_id"], r["level"])

        if r["preset"] in pairs[key]:
            raise ValueError(
                f"duplicate evaluation: {key} {r['preset']}"
            )

        pairs[key][r["preset"]] = r

    out = []

    for level in LEVELS:
        relevant = [
            v for (sid, lev), v in pairs.items()
            if lev == level
        ]

        if any(set(v) != set(PRESETS) for v in relevant):
            raise ValueError("missing paired result")

        both_ok = [
            v for v in relevant
            if all(
                v[p]["context_status"] == "ok"
                for p in PRESETS
            )
        ]

        different = sum(
            set(v["full"]["selected_flexible_ids"])
            != set(
                v["no_budget_aware_packing"]["selected_flexible_ids"]
            )
            for v in both_ok
        )

        full_retention = [
            len(v["full"]["selected_flexible_ids"])
            / v["full"]["reference_flexible_count"]
            for v in both_ok
            if v["full"]["reference_flexible_count"]
        ]

        no_retention = [
            len(
                v["no_budget_aware_packing"]["selected_flexible_ids"]
            )
            / v["no_budget_aware_packing"]["reference_flexible_count"]
            for v in both_ok
            if v["no_budget_aware_packing"]["reference_flexible_count"]
        ]

        out.append({
            "level": level,
            "paired_cases": len(relevant),
            "both_ok": len(both_ok),
            "selection_set_diff": different,
            "full_ok": sum(
                v["full"]["context_status"] == "ok"
                for v in relevant
            ),
            "no_packing_ok": sum(
                v["no_budget_aware_packing"]["context_status"] == "ok"
                for v in relevant
            ),
            "full_flexible_retention_mean": (
                sum(full_retention) / len(full_retention)
                if full_retention else None
            ),
            "no_packing_flexible_retention_mean": (
                sum(no_retention) / len(no_retention)
                if no_retention else None
            ),
            "critical_labeled_cases": sum(
                v["full"]["critical_flexible_labeled"]
                for v in relevant
            ),
        })

    return out


def _csv(path, rows):
    if not rows:
        raise ValueError("nothing to write")

    columns = list(rows[0])

    with path.open(
        "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=columns
        )
        writer.writeheader()

        for row in rows:
            writer.writerow({
                k: (
                    json.dumps(v, ensure_ascii=False)
                    if isinstance(v, list) else v
                )
                for k, v in row.items()
            })


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )
    parser.add_argument("--scenarios", required=True)
    parser.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )
    parser.add_argument(
        "--output",
        default="experiments/results/action_time_preflight_v104",
    )
    args = parser.parse_args()

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

    if config["scope"] != "ablation":
        raise ValueError("expected ablation config")

    src = Path(args.scenarios)
    data = json.loads(src.read_text(encoding="utf-8"))

    scenarios = (
        data["scenarios"]
        if isinstance(data, dict) else data
    )
    scenarios = [
        s for s in scenarios
        if s.get("benchmark_enabled", True)
        and s.get("validation_scope", "integration")
        == "integration"
    ]

    if (
        len(scenarios) != 40
        or len({s["scenario_id"] for s in scenarios}) != 40
    ):
        raise ValueError(
            "expected exactly 40 unique integration scenarios"
        )

    if {s["context_budget"] for s in scenarios} != {2048}:
        raise ValueError(
            "expected B2048 source scenarios"
        )

    if any(
        s.get("token_count_mode")
        != "gemini:gemini-3.1-flash-lite"
        for s in scenarios
    ):
        raise ValueError(
            "scenario token_count_mode mismatch"
        )

    # 이전 2K Ablation의 과제 내용과 동일한지 검증한다.
    normalized = copy.deepcopy(scenarios)

    for s in normalized:
        s["context_budget"] = 0
        s["metadata"]["context_token_budget"] = 0

    normalized.sort(
        key=lambda x: x["scenario_id"]
    )

    dataset_hash = hashlib.sha256(
        json.dumps(
            normalized,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    expected_hash = (
        "98e6fee91b5b124b8f06c09b1c7afe08b95a82"
        "a898b6cd449f0efbd8d721aae5"
    )

    if dataset_hash != expected_hash:
        raise ValueError(
            "scenario contents differ from the frozen "
            "2K ablation dataset"
        )

    model = config["model"]
    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
    )

    # 생성형 LLM은 호출하지 않는다.
    # CountTokens의 요청 구조만 기존 Runner와 맞춘다.
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
        generation_config=llm.planner().generation_config(),
    )

    system_prompt = Path(
        model["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    weights = tuple(
        config["runtime"]["utility_weights"]
    )
    top_k = config["runtime"]["candidate_top_k"]

    calibration_rows = []
    records = []

    for i, scenario in enumerate(scenarios, 1):
        cal = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            multilingual_embedder,
            counter,
            top_k,
            weights,
        )

        calibration_rows.append({
            k: v for k, v in cal.items()
            if k not in ("store", "clock")
        })

        for level in LEVELS:
            records.extend(
                evaluate_level(
                    scenario,
                    cal,
                    level,
                    system_prompt,
                    tool_definitions,
                    multilingual_embedder,
                    counter,
                    top_k,
                    weights,
                )
            )

        print(
            f"[{i}/40] {scenario['scenario_id']} "
            f"floor={cal['floor']} ceiling={cal['ceiling']}",
            flush=True,
        )

    summaries = summarize(records)

    if len(records) != 400:
        raise AssertionError(
            f"expected 400 contexts, got {len(records)}"
        )

    if any(
        r["context_status"] == "ok"
        and not r["within_budget"]
        for r in records
    ):
        raise AssertionError(
            "measured Context exceeds effective budget"
        )

    output = Path(args.output)

    # 기존 결과를 자동으로 덮어쓰지 않는다.
    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    (output / "calibration.json").write_text(
        json.dumps(
            calibration_rows,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    (output / "selection_details.jsonl").write_text(
        "".join(
            json.dumps(
                r, ensure_ascii=False
            ) + "\n"
            for r in records
        ),
        encoding="utf-8",
    )

    _csv(
        output / "selection_details.csv",
        records,
    )

    _csv(
        output / "paired_summary.csv",
        summaries,
    )

    git = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )

    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )

    audit = {
        "input": str(src),
        "input_sha256": hashlib.sha256(
            src.read_bytes()
        ).hexdigest(),
        "normalized_scenarios_sha256": dataset_hash,
        "git_commit": (
            git.stdout.strip()
            if git.returncode == 0 else None
        ),
        "git_dirty": (
            bool(dirty.stdout.strip())
            if dirty.returncode == 0 else None
        ),
        "freeze": config["freeze"],
        "tokenizer": counter.metadata(),
        "embedding": embedding_runtime_metadata(),
        "first_action_step": 1,
        "llm_generation_calls": 0,
        "safety_margin": SAFETY_MARGIN,
        "n_scenarios": len(scenarios),
        "n_context_evaluations": len(records),
        "all_built_contexts_within_budget": all(
            r["within_budget"]
            for r in records
            if r["context_status"] == "ok"
        ),
        "summary": summaries,
    }

    (output / "validation.json").write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    for s in summaries:
        print(
            f"level={s['level']:.0%} "
            f"both_ok={s['both_ok']}/40 "
            f"flexible_set_diff={s['selection_set_diff']}"
        )

    print("[PASS] Saved:", output)


if __name__ == "__main__":
    main()