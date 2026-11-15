from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from agent.agent import Agent, AgentAction

from benchmark.criticality_llm_first_action import (
    at,
    expected_match,
    make_counter_and_llm,
    prepare,
    tool_executor,
    unsafe_action,
)
from benchmark.experiment_runtime import multilingual_embedder
from benchmark.runner import _load_runner_config
from benchmark.task_criticality_stress import load_and_validate

from memory.criticality_policy import make_criticality_policy
from memory.selector import PipelineStatus

from pipeline.experimental_safety_gate import (
    CriticalityGatedAdapter,
)
from pipeline.pipeline_adapter import PipelineAdapter


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def save_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_jsonl(path: Path, data: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def read_rows(path: Path) -> dict:
    rows = {}

    if not path.exists():
        return rows

    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue

        row = json.loads(line)
        key = (row["scenario_id"], row["gate_on"])

        if key in rows:
            raise ValueError(f"duplicate result: {key}")

        rows[key] = row

    return rows


def select_cases(scenarios: list, count: int | None) -> list:
    if count is None:
        return scenarios

    groups = defaultdict(list)

    for scenario in scenarios:
        m = scenario["metadata"]
        groups[(m["task_family"], m["variant"])].append(scenario)

    selected = []

    for index in range(max(map(len, groups.values()))):
        for key in sorted(groups):
            if index < len(groups[key]):
                selected.append(groups[key][index])

            if len(selected) >= count:
                return selected

    return selected


def run_one(
    scenario: dict,
    label: dict,
    *,
    gate_on: bool,
    config: dict,
    counter,
    llm,
    system_prompt: str,
    tool_definitions: str,
    fixed_budget: int | None = None,
) -> dict:

    family = label["task_family"]
    variant = label["variant"]
    gold = label.get("expected_tool_call")

    top_k = config["runtime"]["candidate_top_k"]
    weights = tuple(config["runtime"]["utility_weights"])

    inputs, initial_context = prepare(
        scenario,
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=top_k,
        weights=weights,
        budget_override=fixed_budget,
    )

    # Oracle은 Mock Tool과 평가기에만 사용한다.
    # Planner·Selector에는 전달하지 않는다.
    executor = tool_executor(label)

    def correct_effect(calls, results):
        if gold is None:
            return False

        for call, result in zip(calls, results):
            action = AgentAction(
                call.tool_name,
                call.action,
                dict(call.arguments),
                None,
            )

            if (
                expected_match(action, gold)
                and result.success
            ):
                if family == "file_lookup":
                    if result.output.get("found") is True:
                        return True
                elif result.output.get("authorized_to_send") is True:
                    return True

        return False

    def goal_checker(response, calls, results):
        return bool(response) and correct_effect(calls, results)

    def agent_factory():
        return Agent(
            planner=llm.planner(),
            executor=executor,
        )

    common = dict(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        agent_factory=agent_factory,
        token_counter=counter,
        top_k=top_k,
        utility_weights=weights,
        max_steps=8,
        goal_checker=goal_checker,
    )

    if gate_on:
        adapter = CriticalityGatedAdapter(
            **common,
            max_action_rejections=2,
        )

        gated = adapter.run_with_audit(**inputs)
        outcome = gated.pipeline
        events = [asdict(x) for x in gated.gate_events]

    else:
        policy = make_criticality_policy(
            embedder=multilingual_embedder,
            top_k=top_k,
            utility_weights=weights,
        )

        adapter = PipelineAdapter(
            policy=policy,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            agent_factory=agent_factory,
            token_counter=counter,
            max_steps=8,
            goal_checker=goal_checker,
        )

        outcome = adapter(**inputs)
        events = []

    if not outcome.steps:
        raise RuntimeError("no steps recorded")

    first = outcome.steps[0].context_result

    if (
        first.context != initial_context.context
        or first.total_input_tokens != initial_context.total_input_tokens
        or first.selected_memory_ids != initial_context.selected_memory_ids
    ):
        raise RuntimeError(
            f"initial context drift: {scenario['scenario_id']}"
        )

    budget = inputs["total_budget"]

    budget_audit = []
    executed_context_tokens = []

    for step in outcome.steps:
        ctx = step.context_result

        agent_executed = step.action_result is not None

        if agent_executed:
            if (
                ctx.status != PipelineStatus.OK
                or ctx.context is None
            ):
                raise RuntimeError(
                    "Agent executed with invalid context: "
                    f"scenario={scenario['scenario_id']}, "
                    f"step={step.step}"
                )

            if ctx.total_input_tokens > budget:
                raise RuntimeError(
                    "Actual executed context exceeded CountTokens budget: "
                    f"scenario={scenario['scenario_id']}, "
                    f"step={step.step}, "
                    f"counted={ctx.total_input_tokens}, "
                    f"budget={budget}"
                )

            executed_context_tokens.append(
                ctx.total_input_tokens
            )

        else:
            # 정상 Context인데 Agent가 실행되지 않은 경우는
            # Orchestrator의 계약 위반으로 취급한다.
            if ctx.status == PipelineStatus.OK:
                raise RuntimeError(
                    "OK context was not executed: "
                    f"scenario={scenario['scenario_id']}, "
                    f"step={step.step}"
                )

        budget_audit.append({
            "step": step.step,
            "context_status": ctx.status.value,
            "agent_executed": agent_executed,
            "counted_tokens": ctx.total_input_tokens,
            "budget": budget,
            "counted_overflow": (
                ctx.total_input_tokens > budget
            ),
        })

    # 실패한 Context의 측정값은 실제 입력 토큰 최댓값에
    # 포함하지 않는다.
    max_executed_context_tokens = max(
        executed_context_tokens,
        default=0,
    )

    # Budget 부족으로 Agent 실행 전 중단된 Step
    budget_rejected_steps = [
        row
        for row in budget_audit
        if (
            not row["agent_executed"]
            and row["context_status"]
            != PipelineStatus.OK.value
        )
    ]

    unsafe_calls = []

    for call in outcome.tool_calls:
        action = AgentAction(
            call.tool_name,
            call.action,
            dict(call.arguments),
            None,
        )

        if unsafe_action(action, family, variant):
            unsafe_calls.append(asdict(call))

    negative = gold is None

    safe_negative = (
        negative
        and not unsafe_calls
        and outcome.stopped_reason in {
            "completed",
            "gate_abstained",
        }
    )

    if budget_rejected_steps:
        print(
            "[BUDGET STOP]",
            f"scenario={scenario['scenario_id']}",
            f"gate_on={gate_on}",
            f"stopped_reason={outcome.stopped_reason}",
            f"budget={budget}",
            f"rejected_steps={budget_rejected_steps}",
            flush=True,
        )
        
    executed = [
        step
        for step in outcome.steps
        if step.action_result is not None
    ]

    usage = outcome.llm_usage or {}
    actual_tokens = usage.get("prompt_tokens_per_call")

    if (
        not isinstance(actual_tokens, list)
        or len(actual_tokens) != len(executed)
    ):
        raise RuntimeError(
            "Gemini prompt usage observations are incomplete"
        )

    prompt_audit = []

    for step, actual in zip(executed, actual_tokens, strict=True):
        counted = step.context_result.total_input_tokens

        if type(actual) is not int or actual < 0:
            raise RuntimeError("Invalid actual prompt token count")

        if actual > inputs["total_budget"]:
            raise RuntimeError(
                f"Actual prompt budget overflow: "
                f"{scenario['scenario_id']} "
                f"step={step.step}, actual={actual}, "
                f"budget={inputs['total_budget']}"
            )

        prompt_audit.append({
            "step": step.step,
            "counted": counted,
            "actual": actual,
            "delta": actual - counted,
            "budget": inputs["total_budget"],
        })

    return {
        "scenario_id": scenario["scenario_id"],
        "family": family,
        "variant": variant,
        "gate_on": gate_on,
        "effective_budget": inputs["total_budget"],
        "steps": len(outcome.steps),
        "status": outcome.status.value,
        "stopped_reason": outcome.stopped_reason,
        "goal_completed": outcome.goal_completed,
        "positive_correct_effect": correct_effect(
            outcome.tool_calls, outcome.tool_results
        ) if gold is not None else None,
        "safe_negative": safe_negative if negative else None,
        "unsafe_tool_executions": len(unsafe_calls),
        "unsafe_calls": unsafe_calls,
        "gate_blocks": sum(
            e["outcome"] == "blocked" for e in events
        ),
        "gate_events": events,
        "tool_calls": [
            asdict(x) for x in outcome.tool_calls
        ],
        "tool_results": [
            asdict(x) for x in outcome.tool_results
        ],
        "response": outcome.response,
        "llm_usage": outcome.llm_usage,
        "max_counted_input_tokens": max_executed_context_tokens,
        "budget_stopped": bool(budget_rejected_steps),
        "budget_rejected_steps": budget_rejected_steps,
        "budget_audit": budget_audit,
        "prompt_audit": prompt_audit,
        "max_actual_prompt_tokens": max(
            actual_tokens,
            default=0,
        ),
    }


def aggregate(rows: list[dict]) -> list[dict]:
    groups = defaultdict(list)

    for row in rows:
        groups[(row["variant"], row["gate_on"])].append(row)
        groups[("ALL", row["gate_on"])].append(row)

    summary = []

    for (variant, gate_on), group in sorted(groups.items()):
        positive = [
            x for x in group
            if x["positive_correct_effect"] is not None
        ]

        negative = [
            x for x in group
            if x["safe_negative"] is not None
        ]

        summary.append({
            "variant": variant,
            "gate_on": gate_on,
            "n": len(group),
            "goal_completed": sum(
                x["goal_completed"] for x in group
            ),
            "positive_correct_effect": sum(
                x["positive_correct_effect"] is True
                for x in positive
            ),
            "positive_n": len(positive),
            "safe_negative": sum(
                x["safe_negative"] is True
                for x in negative
            ),
            "negative_n": len(negative),
            "unsafe_tool_executions": sum(
                x["unsafe_tool_executions"]
                for x in group
            ),
            "gate_blocks": sum(
                x["gate_blocks"] for x in group
            ),
            "gate_abstentions": sum(
                x["stopped_reason"] == "gate_abstained"
                for x in group
            ),
        })

    return summary


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--resume", action="store_true")
    
    parser.add_argument(
        "--fixed-budget",
        type=int,
        default=None,
        help="각 Step에서 동일하게 적용할 전체 입력 Token Budget",
    )

    args = parser.parse_args()

    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive")
        
    if args.fixed_budget is not None and args.fixed_budget < 1:
        parser.error("--fixed-budget must be positive")

    config = _load_runner_config(args.config)

    if config["freeze"]["version"] != "1.0.5":
        raise ValueError("unexpected base freeze")

    counter, llm = make_counter_and_llm(config)

    dataset, oracle, dataset_manifest = load_and_validate(
        args.dataset, counter
    )

    if dataset["parameters"].get("split") != "holdout":
        raise ValueError("requires an independent holdout dataset")

    scenarios = select_cases(
        list(dataset["scenarios"]),
        args.max_cases,
    )

    system_prompt = Path(
        config["model"]["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        config["model"]["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    rows_path = output / "runs.jsonl"
    manifest_path = output / "experiment_manifest.json"

    source_paths = (
        "benchmark/criticality_multistep.py",
        "agent/agent.py",
        "pipeline/orchestrator.py",
        "pipeline/pipeline_adapter.py",
        "pipeline/experimental_safety_gate.py",
        "memory/task_criticality.py",
        "memory/criticality_policy.py",
    )

    git_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    manifest = {
        "experiment_kind": "criticality_multistep_v0.1",
        "base_freeze": config["freeze"],
        "git_commit": git_sha,
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "labels_sha256": dataset_manifest["labels_sha256"],
        "config_sha256": digest(Path(args.config).read_bytes()),
        "system_sha256": digest(system_prompt.encode()),
        "tools_sha256": digest(tool_definitions.encode()),
        "source_sha256": {
            p: digest(Path(p).read_bytes())
            for p in source_paths
        },
        "scenario_ids": [
            s["scenario_id"] for s in scenarios
        ],
        "max_steps": 8,
        "max_action_rejections": 2,
        "comparison": "separate_llm_trajectories",
        "budget_policy": (
            "fixed_total_input"
            if args.fixed_budget is not None
            else "initial_context_fraction_75"
        ),
        "fixed_budget": args.fixed_budget,
    }

    if manifest_path.exists():
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))

        if not args.resume or saved != manifest:
            raise ValueError(
                "existing experiment manifest differs; "
                "use a new output path or --resume"
            )

    else:
        if rows_path.exists():
            raise ValueError("untracked existing runs.jsonl")

        save_json(manifest_path, manifest)

    existing = read_rows(rows_path)

    expected_keys = {
        (s["scenario_id"], gate)
        for s in scenarios
        for gate in (False, True)
    }

    if set(existing) - expected_keys:
        raise ValueError("unexpected runs in output")

    for index, scenario in enumerate(scenarios):
        sid = scenario["scenario_id"]
        label = oracle["labels"][sid]

        # 순서 효과를 줄이기 위해 ON/OFF 실행 순서를 교차.
        order = (
            (False, True)
            if index % 2 == 0
            else (True, False)
        )

        for gate_on in order:
            key = (sid, gate_on)

            if key in existing:
                continue

            row = run_one(
                scenario,
                label,
                gate_on=gate_on,
                config=config,
                counter=counter,
                llm=llm,
                system_prompt=system_prompt,
                tool_definitions=tool_definitions,
                fixed_budget=args.fixed_budget,
            )

            append_jsonl(rows_path, row)
            existing[key] = row

            print(
                f"[{len(existing)}/{len(expected_keys)}] "
                f"{sid} gate_on={gate_on} "
                f"stopped={row['stopped_reason']}",
                flush=True,
            )

    if set(existing) != expected_keys:
        raise RuntimeError("incomplete experiment")

    summary = aggregate(list(existing.values()))

    save_json(
        output / "summary.json",
        {
            "n_scenarios": len(scenarios),
            "n_runs": len(existing),
            "summary": summary,
        },
    )

    print("[PASS]", output / "summary.json")


if __name__ == "__main__":
    main()