from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import subprocess
from collections import defaultdict
from dataclasses import asdict, replace
from pathlib import Path

from agent.agent import Agent, AgentAction
from benchmark.criticality_llm_first_action import (
    action_from,
    expected_match,
    make_counter_and_llm,
    prepare,
    tool_executor,
)
from benchmark.experiment_runtime import multilingual_embedder
from benchmark.runner import (
    _load_runner_config,
    _require_executable_config,
)
from benchmark.task_criticality_stress import load_and_validate
from memory.criticality_policy import make_criticality_policy
from memory.selector import PipelineStatus
from pipeline.experimental_safety_gate import CriticalityGatedAdapter
from pipeline.pipeline_adapter import PipelineAdapter


ARMS = ("clean_on", "fault_on", "fault_off", "fault_off_retry")
BUDGET = 2048
MAX_STEPS = 8
REJECTION_LIMIT = 2
TOOL_FAILURE_RETRIES = 1


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_jsonl(path: Path, obj: dict) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(obj, ensure_ascii=False) + "\n"
        )
        handle.flush()
        os.fsync(handle.fileno())


def read_jsonl(path: Path, key_fn) -> dict:
    result = {}

    if not path.exists():
        return result

    for number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        if not raw.strip():
            continue

        value = json.loads(raw)
        key = key_fn(value)

        if key in result:
            raise ValueError(
                f"duplicate record at line {number}: {key}"
            )

        result[key] = value

    return result


def choose_positive(
    dataset: dict,
    labels: dict,
    max_cases: int | None,
) -> list[dict]:
    """File/Email과 S2/S3_VALID를 균형 있게 선택."""
    groups = defaultdict(list)

    for case in dataset["scenarios"]:
        label = labels[case["scenario_id"]]

        if label["expected_tool_call"] is None:
            continue

        if label["variant"] not in ("S2", "S3_VALID"):
            raise ValueError("unexpected positive variant")

        key = (
            label["task_family"],
            label["variant"],
        )
        groups[key].append(case)

    if len(groups) != 4:
        raise ValueError("expected four positive groups")

    result = []

    for position in range(
        max(map(len, groups.values()))
    ):
        for key in sorted(groups):
            if position < len(groups[key]):
                result.append(groups[key][position])

                if (
                    max_cases is not None
                    and len(result) >= max_cases
                ):
                    return result

    return result


def corrupt_action(
    action: AgentAction,
    family: str,
    scenario_id: str,
) -> AgentAction:
    """올바른 제안의 인자 한 개만 변경.

    정답 라벨은 읽지 않으며, 기존 Action 객체도 변경하지 않는다.
    """
    tag = digest(
        scenario_id.encode("utf-8")
    )[:12]

    arguments = dict(action.arguments)

    if family == "file_lookup":
        if (
            action.tool_name,
            action.action,
        ) != ("file", "find_file"):
            raise ValueError("cannot corrupt non-file action")

        arguments["filename"] = (
            f"unverified_{tag}.pdf"
        )

    elif family == "approved_email":
        if (
            action.tool_name,
            action.action,
        ) != ("email", "send_email"):
            raise ValueError("cannot corrupt non-email action")

        arguments["approval_id"] = (
            f"approved_unverified_{tag}"
        )

    else:
        raise ValueError(f"unknown task family: {family}")

    return replace(
        action,
        arguments=arguments,
    )


class FirstThenGemini:
    """첫 행동은 공유된 Gemini 제안, 이후는 실제 Gemini."""

    def __init__(self, initial: AgentAction, live):
        self.initial = initial
        self.live = live
        self.calls = 0
        self.proposals = []

    def plan(self, context: str) -> AgentAction:
        if self.calls == 0:
            result = self.initial
        else:
            result = self.live.plan(context)

        self.calls += 1
        self.proposals.append(asdict(result))
        return result

    def telemetry(self):
        # 첫 제안의 비용/사용량은 별도 기록.
        # 여기에는 후속 Gemini 호출만 포함한다.
        return self.live.telemetry()


def tool_effect(
    call,
    result,
    gold: dict,
    family: str,
) -> bool:
    action = AgentAction(
        call.tool_name,
        call.action,
        dict(call.arguments),
        None,
    )

    if (
        not expected_match(action, gold)
        or not result.success
    ):
        return False

    if family == "file_lookup":
        return result.output.get("found") is True

    return (
        result.output.get("authorized_to_send")
        is True
    )


def initial_proposal(
    case,
    label,
    *,
    budget,
    counter,
    llm,
    system,
    tools,
    top_k,
    weights,
):
    """실제 Gemini의 원본 첫 행동을 한 번 생성하고 저장."""
    inputs, context = prepare(
        case,
        system_prompt=system,
        tool_definitions=tools,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=top_k,
        weights=weights,
        budget_override=budget,
    )

    planner = llm.planner()
    action = planner.plan(context.context)
    usage = planner.telemetry().to_dict()

    observed = usage.get("prompt_tokens_per_call")

    if (
        not isinstance(observed, list)
        or len(observed) != 1
        or type(observed[0]) is not int
    ):
        raise RuntimeError(
            "first proposal has incomplete prompt usage"
        )

    if observed[0] > budget:
        raise RuntimeError(
            "first proposal actual prompt overflow"
        )

    correct = (
        expected_match(
            action,
            label["expected_tool_call"],
        ) is True
    )

    return {
        "scenario_id": case["scenario_id"],
        "initial_action": asdict(action),
        "original_gold_match": correct,
        "context_sha256": digest(
            context.context.encode("utf-8")
        ),
        "selected_memory_ids": list(
            context.selected_memory_ids
        ),
        "counted_tokens": context.total_input_tokens,
        "actual_tokens": observed[0],
        "effective_budget": inputs["total_budget"],
        "llm_usage": usage,
    }


def evaluate(
    case,
    label,
    proposal,
    *,
    arm,
    budget,
    counter,
    llm,
    system,
    tools,
    top_k,
    weights,
    side_effect_idempotency: bool = False,
):
    """하나의 Arm을 끝까지 실행하고 Recovery를 평가."""
    original = action_from(
        proposal["initial_action"]
    )
    injected = arm != "clean_on"

    initial = (
        corrupt_action(
            original,
            label["task_family"],
            case["scenario_id"],
        )
        if injected
        else original
    )

    inputs, expected = prepare(
        case,
        system_prompt=system,
        tool_definitions=tools,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=top_k,
        weights=weights,
        budget_override=budget,
    )

    if (
        digest(
            expected.context.encode("utf-8")
        ) != proposal["context_sha256"]
        or list(expected.selected_memory_ids)
        != proposal["selected_memory_ids"]
        or expected.total_input_tokens
        != proposal["counted_tokens"]
    ):
        raise RuntimeError(
            "first context changed after proposal generation"
        )

    executor = tool_executor(label)
    gold = label["expected_tool_call"]
    family = label["task_family"]

    def correct_effect(calls, results) -> bool:
        return any(
            tool_effect(c, r, gold, family)
            for c, r in zip(calls, results, strict=True)
        )

    def goal(response, calls, results) -> bool:
        return bool(response) and correct_effect(
            calls, results
        )

    planners = []

    def factory():
        planner = FirstThenGemini(
            initial,
            llm.planner(),
        )
        planners.append(planner)

        return Agent(
            planner=planner,
            executor=executor,
        )

    shared = {
        "system_prompt": system,
        "tool_definitions": tools,
        "agent_factory": factory,
        "token_counter": counter,
        "max_steps": MAX_STEPS,
        "goal_checker": goal,
    }

    if arm in ("fault_off", "fault_off_retry"):
        policy = make_criticality_policy(
            embedder=multilingual_embedder,
            top_k=top_k,
            utility_weights=weights,
        )

        adapter = PipelineAdapter(
            policy=policy,
            tool_failure_retries=(
                TOOL_FAILURE_RETRIES
                if arm == "fault_off_retry" else 0
            ),
            **shared,
        )
        outcome = adapter(**inputs)
        events = []

    else:
        adapter = CriticalityGatedAdapter(
            embedder=multilingual_embedder,
            top_k=top_k,
            utility_weights=weights,
            max_action_rejections=REJECTION_LIMIT,
            enable_side_effect_idempotency=side_effect_idempotency,
            **shared,
        )

        gated = adapter.run_with_audit(**inputs)
        outcome = gated.pipeline
        events = [
            asdict(event)
            for event in gated.gate_events
        ]

    executed = [
        step for step in outcome.steps
        if step.action_result is not None
    ]

    if (
        len(planners) != 1
        or not executed
        or planners[0].calls != len(executed)
    ):
        raise RuntimeError(
            "planner / executed-step count mismatch"
        )

    first_step = outcome.steps[0]

    if (
        first_step.context_result.context is None
        or digest(
            first_step.context_result.context.encode("utf-8")
        ) != proposal["context_sha256"]
    ):
        raise RuntimeError(
            "first replay context drift"
        )

    # Fault ON은 실제 Tool 실행 전에 반드시 차단돼야 함.
    if injected and arm == "fault_on":
        if (
            not events
            or events[0]["outcome"] != "blocked"
            or first_step.action_result.tool_calls
        ):
            raise RuntimeError(
                "injected fault was not blocked before execution"
            )

    # Fault OFF는 실제로 잘못된 첫 Tool을 실행해야 함.
    if injected and arm in ("fault_off", "fault_off_retry"):
        if not first_step.action_result.tool_calls:
            raise RuntimeError(
                "GATE OFF did not execute injected action"
            )

    usage = outcome.llm_usage or {}
    observed = usage.get("prompt_tokens_per_call")

    if (
        not isinstance(observed, list)
        or len(observed) != len(executed) - 1
    ):
        raise RuntimeError(
            "follow-up Gemini prompt usage incomplete"
        )

    prompt_audit = []

    for index, step in enumerate(executed):
        ctx = step.context_result
        counted = ctx.total_input_tokens

        actual = (
            proposal["actual_tokens"]
            if index == 0
            else observed[index - 1]
        )

        if type(actual) is not int or actual < 0:
            raise RuntimeError(
                "invalid actual token usage"
            )

        if (
            ctx.status != PipelineStatus.OK
            or counted > budget
            or actual > budget
        ):
            raise RuntimeError(
                "executed-step prompt overflow: "
                f"{case['scenario_id']} step={step.step}"
            )

        prompt_audit.append({
            "step": step.step,
            "counted": counted,
            "actual": actual,
            "delta": actual - counted,
            "source": (
                "shared_first_Gemini_proposal"
                if index == 0
                else "live_followup_Gemini"
            ),
        })

    # 예산으로 실행 자체가 중단된 Step은
    # 실제 LLM 입력 토큰 통계에 넣지 않는다.
    budget_stopped = any(
        step.action_result is None
        and step.context_result.status != PipelineStatus.OK
        for step in outcome.steps
    )

    if any(
        step.action_result is None
        and step.context_result.status == PipelineStatus.OK
        for step in outcome.steps
    ):
        raise RuntimeError(
            "missing action on OK context"
        )

    # 첫 행동 이후 실제 Tool이 정답 효과를 낸 경우.
    later_effect = any(
        tool_effect(call, result, gold, family)
        for step in outcome.steps[1:]
        if step.action_result is not None
        for call, result in zip(
            step.action_result.tool_calls,
            step.action_result.tool_results,
            strict=True,
        )
    )

    effect = correct_effect(
        outcome.tool_calls,
        outcome.tool_results,
    )
    
    first_fault_executed = bool(injected and first_step.action_result is not None and first_step.action_result.tool_calls)
    
    first_results = (
        first_step.action_result.tool_results
        if first_step.action_result is not None else ()
    )
    
    first_fault_tool_failed = bool(injected and first_results and first_results[0].success is False)

    tool_failure_count = sum(
        result.success is False
        for result in outcome.tool_results
    )
    
    post_fault_correct_effect = bool(injected and later_effect)
    
    post_fault_goal = bool(injected and later_effect and outcome.goal_completed)

    side_effect_duplicate_blocks = sum(
        event["reason"] == "duplicate_side_effect"
        for event in events
    )
    
    successful_email_sends = sum(
        call.tool_name == "email" and call.action == "send_email" and result.success
        for call, result in zip(
            outcome.tool_calls,
            outcome.tool_results,
            strict=True,
        )
    )
    
    duplicate_successful_email_sends = max(
        0,
        successful_email_sends - 1,
    )

    return {
        "scenario_id": case["scenario_id"],
        "base_case_id": case["metadata"]["base_case_id"],
        "family": family,
        "variant": label["variant"],
        "arm": arm,

        "original_gold_match": bool(
            proposal["original_gold_match"]
        ),
        "synthetic_fault_injected": injected,
        "fault_description": (
            "first_tool_argument_corrupted"
            if injected else "none"
        ),

        "first_gate_blocked": bool(
            events and events[0]["outcome"] == "blocked"
        ),

        "correct_tool_effect": effect,
        "correct_effect_after_first": later_effect,
        "goal_completed": outcome.goal_completed,

        "recovered_effect": bool(
            injected
            and arm == "fault_on"
            and later_effect
        ),
        "recovered_goal": bool(
            injected
            and arm == "fault_on"
            and later_effect
            and outcome.goal_completed
        ),

        "stopped_reason": outcome.stopped_reason,
        "pipeline_status": outcome.status.value,
        "budget_stopped": budget_stopped,
        "steps": len(outcome.steps),

        "gate_events": events,
        "proposed_actions": planners[0].proposals,
        "tool_calls": [
            asdict(call)
            for call in outcome.tool_calls
        ],
        "tool_results": [
            asdict(result)
            for result in outcome.tool_results
        ],

        "prompt_audit": prompt_audit,
        "followup_llm_usage": usage,
        "response": outcome.response,
        "first_fault_executed": first_fault_executed,
        "first_fault_tool_failed": first_fault_tool_failed,
        "tool_failure_count": tool_failure_count,
        "post_fault_correct_effect": post_fault_correct_effect,
        "post_fault_goal": post_fault_goal,
        "tool_failure_retry_policy": (
            TOOL_FAILURE_RETRIES
            if arm == "fault_off_retry" else 0
        ),
        "side_effect_idempotency_enabled": bool(side_effect_idempotency and arm in ("clean_on", "fault_on")),
        "side_effect_duplicate_blocks": side_effect_duplicate_blocks,
        "successful_email_sends": successful_email_sends,
        "duplicate_successful_email_sends": duplicate_successful_email_sends,
    }


def cluster_bootstrap(
    rows: list[dict],
    iterations: int = 2000,
    seed: int = 42,
):
    """기본 과제 단위 Bootstrap 95% CI."""
    if not rows:
        return None

    groups = defaultdict(list)

    for row in rows:
        groups[row["base_case_id"]].append(row)

    keys = sorted(groups)
    rng = random.Random(seed)
    values = []

    for _ in range(iterations):
        sampled_keys = [
            rng.choice(keys)
            for _ in keys
        ]

        sampled = [
            row
            for key in sampled_keys
            for row in groups[key]
        ]

        values.append(
            sum(row["recovered_goal"] for row in sampled)
            / len(sampled)
        )

    values.sort()

    return [
        values[int(0.025 * (len(values) - 1))],
        values[int(0.975 * (len(values) - 1))],
    ]


def summarize(scenarios, proposals, rows):
    eligible = [
        sid for sid, proposal in proposals.items()
        if proposal["original_gold_match"]
    ]

    groups = defaultdict(list)

    for row in rows:
        groups[
            (row["arm"], row["family"], row["variant"])
        ].append(row)

        groups[
            (row["arm"], "ALL", "ALL")
        ].append(row)
        
        expected_blocks = sum(
            event["reason"] == "duplicate_side_effect"
            for event in row["gate_events"]
        )

        if row["side_effect_duplicate_blocks"] != expected_blocks:
            raise ValueError(
                f"Duplicate-block audit mismatch: "
                f"{row['scenario_id']} {row['arm']}"
            )

    breakdown = []

    for (arm, family, variant), group in sorted(
        groups.items()
    ):
        breakdown.append({
            "arm": arm,
            "family": family,
            "variant": variant,
            "n": len(group),
            "correct_tool_effect": sum(
                row["correct_tool_effect"]
                for row in group
            ),
            "goal_completed": sum(
                row["goal_completed"]
                for row in group
            ),
            "first_gate_blocked": sum(
                row["first_gate_blocked"]
                for row in group
            ),
            "recovered_effect": sum(
                row["recovered_effect"]
                for row in group
            ),
            "recovered_goal": sum(
                row["recovered_goal"]
                for row in group
            ),
            "budget_stopped": sum(
                row["budget_stopped"]
                for row in group
            ),
            "side_effect_duplicate_blocks": sum(
                row.get("side_effect_duplicate_blocks", 0)
                for row in group
            ),

            "duplicate_successful_email_sends": sum(
                row.get("duplicate_successful_email_sends", 0)
                for row in group
            ),
        })

    injected_on = [
        row for row in rows
        if row["arm"] == "fault_on"
    ]

    natural = [
        row for row in rows
        if row["arm"] == "clean_on"
    ]

    natural_first_blocks = [
        row for row in natural
        if row["first_gate_blocked"]
    ]

    # 공유 첫 제안은 과제당 한 번만 비용에 합산.
    usages = [
        proposal["llm_usage"]
        for proposal in proposals.values()
    ] + [
        row["followup_llm_usage"]
        for row in rows
    ]

    costs = [
        usage.get("estimated_cost_usd")
        for usage in usages
    ]

    cost_complete = all(
        isinstance(value, (int, float))
        for value in costs
    )
    
    def compare_fault_recovery(rows):
        on = {
            row["scenario_id"]: row
            for row in rows
            if row["arm"] == "fault_on"
        }
        
        off_retry = {
            row["scenario_id"]: row
            for row in rows
            if row["arm"] == "fault_off_retry"
        }
        
        if not off_retry:
            return None
        
        if set(on) != set(off_retry):
            raise ValueError("Fault ON and OFF Retry scenarios are not paired")
        
        n = len(on)
        
        if n == 0:
            return None
        
        on_effect = sum(
            bool(row["post_fault_correct_effect"])
            for row in on.values()
        )
        
        off_effect = sum(
            bool(row["post_fault_correct_effect"])
            for row in off_retry.values()
        )
        
        on_goal = sum(
            bool(row["post_fault_goal"])
            for row in on.values()
        )
        
        off_goal = sum(
            bool(row["post_fault_goal"])
            for row in off_retry.values()
        )
        
        on_only = sum(
            on[sid]["post_fault_goal"] and not off_retry[sid]["post_fault_goal"]
            for sid in on
        )
        
        off_only = sum(
            off_retry[sid]["post_fault_goal"] and not on[sid]["post_fault_goal"]
            for sid in on
        )
        
        both = sum(
            on[sid]["post_fault_goal"] and off_retry[sid]["post_fault_goal"]
            for sid in on
        )
        
        return {
            "n_paired": n,
            "fault_on": {
                "effect": on_effect,
                "goal": on_goal,
                "first_fault_executed": sum(
                    row["first_fault_executed"]
                    for row in on.values()
                ),
            },
            "fault_off_retry": {
                "effect": off_effect,
                "goal": off_goal,
                "first_fault_executed": sum(
                    row["first_fault_executed"]
                    for row in off_retry.values()
                ),
                "first_fault_tool_failed": sum(
                    row["first_fault_tool_failed"]
                    for row in off_retry.values()
                ),
                "tool_failure_count": sum(
                    row["tool_failure_count"]
                    for row in off_retry.values()
                ),
            },
            "goal_delta_on_minus_off_retry": (on_goal - off_goal) / n,
            "paired_goal_outcomes": {
                "both_success": both,
                "gate_on_only": on_only,
                "off_retry_only": off_only,
                "both_failed": n - both - on_only - off_only,
            },
        }

    return {
        "n_candidate_scenarios": len(scenarios),
        "n_original_correct_first_proposals": len(eligible),
        "n_ineligible_first_proposals": (
            len(scenarios) - len(eligible)
        ),
        "n_completed_arm_runs": len(rows),

        "usage": {
            "api_calls": sum(
                usage.get("api_calls", 0)
                for usage in usages
            ),
            "cost_complete": cost_complete,
            "estimated_cost_usd": (
                sum(costs)
                if cost_complete else None
            ),
            "note": (
                "Shared first proposal counted once, "
                "not once per replay arm."
            ),
        },

        "controlled_recovery": {
            "denominator": len(injected_on),
            "first_fault_blocked": sum(
                row["first_gate_blocked"]
                for row in injected_on
            ),
            "recovered_tool_effect": sum(
                row["recovered_effect"]
                for row in injected_on
            ),
            "recovered_final_goal": sum(
                row["recovered_goal"]
                for row in injected_on
            ),
            "recovered_final_goal_cluster_bootstrap_95ci":
                cluster_bootstrap(injected_on),
        },

        "spontaneous_first_block_recovery": {
            "denominator": len(natural_first_blocks),
            "recovered_goal": sum(
                row["goal_completed"]
                for row in natural_first_blocks
            ),
            "note": (
                "N/A when the denominator is zero"
            ),
        },

        "arm_breakdown": breakdown,
        "fault_on_vs_off_retry": compare_fault_recovery(rows),
    }


def main():
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument("--dataset", required=True)
    parser.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--fixed-budget",
        type=int,
        default=BUDGET,
    )
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--resume",
        action="store_true",
    )
    parser.add_argument(
        "--side-effect-idempotency",
        action="store_true",
        help="Enable run-local side-effect deduplication for gated arms",
    )

    args = parser.parse_args()

    if (
        args.fixed_budget < 1
        or (
            args.max_cases is not None
            and args.max_cases < 1
        )
    ):
        parser.error(
            "budget and max-cases must be positive"
        )

    config = _load_runner_config(args.config)
    _require_executable_config(config)

    if config["freeze"]["version"] not in ("1.0.5", "1.0.7"):
        raise ValueError(
            "Recovery experiment requires freeze 1.0.5 or 1.0.7"
        )
        
    if config["scope"] != "ablation":
        raise ValueError("Recovery experiment requires ablation config")

    counter, llm = make_counter_and_llm(config)

    dataset, oracle, dataset_manifest = load_and_validate(
        args.dataset,
        counter,
    )

    if dataset["parameters"].get("split") != "holdout":
        raise ValueError(
            "independent holdout required"
        )

    scenarios = choose_positive(
        dataset,
        oracle["labels"],
        args.max_cases,
    )

    if not scenarios:
        raise ValueError(
            "no positive recovery challenges"
        )

    system = Path(
        config["model"]["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tools = Path(
        config["model"]["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    top_k = config["runtime"]["candidate_top_k"]

    weights = tuple(
        config["runtime"]["utility_weights"]
    )

    output = Path(args.output)
    output.mkdir(
        parents=True,
        exist_ok=True,
    )

    manifest_path = output / "experiment_manifest.json"
    proposal_path = output / "first_proposals.jsonl"
    runs_path = output / "runs.jsonl"

    tracked_files = (
        "benchmark/criticality_recovery.py",
        "benchmark/criticality_llm_first_action.py",
        "benchmark/task_criticality_holdout.py",
        "agent/agent.py",
        "pipeline/orchestrator.py",
        "pipeline/pipeline_adapter.py",
        "pipeline/experimental_safety_gate.py",
        "memory/criticality_policy.py",
        "memory/task_criticality.py",
        "pipeline/side_effect_idempotency.py",
    )

    manifest = {
        "kind": "recovery_fault_injection_live_replanning_v2_1",
        "first_action_source": (
            "Gemini generated, shared across arms"
        ),
        "fault_source": (
            "Deterministic synthetic argument corruption"
        ),
        "followup_source": (
            "Fresh Gemini-generated actions"
        ),
        "base_freeze": config["freeze"],
        "git_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "labels_sha256": dataset_manifest["labels_sha256"],
        "config_sha256": digest(
            Path(args.config).read_bytes()
        ),
        "system_sha256": digest(
            system.encode("utf-8")
        ),
        "tools_sha256": digest(
            tools.encode("utf-8")
        ),
        "source_sha256": {
            path: digest(Path(path).read_bytes())
            for path in tracked_files
        },
        "scenario_ids": [
            case["scenario_id"]
            for case in scenarios
        ],
        "fixed_budget": args.fixed_budget,
        "max_steps": MAX_STEPS,
        "max_action_rejections": REJECTION_LIMIT,
        "arms": list(ARMS),
        "tool_failure_retry_policy": {
            "fault_off_retry": TOOL_FAILURE_RETRIES,
            "clean_on": 0,
            "fault_on": 0,
            "fault_off": 0,
        },
        "tool_failure_retry_semantics": "Maximum additional planning attempts after consecutive failed ToolExecutor calls",
        "side_effect_idempotency": {
            "enabled": args.side_effect_idempotency,
            "enabled_arms": (
                ["clean_on", "fault_on"]
                if args.side_effect_idempotency
                else []
            ),
            "scope": "single_pipeline_run",
            "send_email_policy": "once_per_task_run",
        },
    }

    if manifest_path.exists():
        existing = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        if not args.resume or existing != manifest:
            raise RuntimeError(
                "experiment changed; "
                "use --resume or a new output"
            )
    else:
        if proposal_path.exists() or runs_path.exists():
            raise RuntimeError(
                "untracked partial files"
            )

        write_json(manifest_path, manifest)

    proposals = read_jsonl(
        proposal_path,
        lambda row: row["scenario_id"],
    )

    rows = read_jsonl(
        runs_path,
        lambda row: (
            row["scenario_id"],
            row["arm"],
        ),
    )

    scenario_ids = set(manifest["scenario_ids"])

    if (
        set(proposals) - scenario_ids
        or any(
            sid not in scenario_ids or arm not in ARMS
            for sid, arm in rows
        )
    ):
        raise ValueError(
            "unknown proposal/run in output"
        )

    for index, case in enumerate(scenarios, 1):
        sid = case["scenario_id"]
        label = oracle["labels"][sid]

        if sid not in proposals:
            proposal = initial_proposal(
                case,
                label,
                budget=args.fixed_budget,
                counter=counter,
                llm=llm,
                system=system,
                tools=tools,
                top_k=top_k,
                weights=weights,
            )

            append_jsonl(proposal_path, proposal)
            proposals[sid] = proposal

        proposal = proposals[sid]

        # 최초 제안이 정답일 때만 오류 주입.
        # 실패한 최초 제안을 성공 과제로 바꾸지 않는다.
        if proposal["original_gold_match"]:
            arms = (
                "clean_on",
                "fault_on",
                "fault_off",
                "fault_off_retry",
            )

            if index % 2 == 0:
                arms = (
                    "clean_on",
                    "fault_off_retry",
                    "fault_off",
                    "fault_on",
                )
        else:
            arms = ("clean_on",)

        for arm in arms:
            key = (sid, arm)

            if key in rows:
                continue

            result = evaluate(
                case,
                label,
                proposal,
                arm=arm,
                budget=args.fixed_budget,
                counter=counter,
                llm=llm,
                system=system,
                tools=tools,
                top_k=top_k,
                weights=weights,
                side_effect_idempotency=args.side_effect_idempotency,
            )

            append_jsonl(runs_path, result)
            rows[key] = result

            print(
                f"[{index}/{len(scenarios)}] "
                f"{sid} {arm} "
                f"goal={result['goal_completed']} "
                f"effect={result['correct_tool_effect']}",
                flush=True,
            )

    expected = {
        (case["scenario_id"], "clean_on")
        for case in scenarios
    }

    expected |= {
        (case["scenario_id"], arm)
        for case in scenarios
        if proposals[
            case["scenario_id"]
        ]["original_gold_match"]
        for arm in ("fault_on", "fault_off", "fault_off_retry")
    }

    if set(rows) != expected:
        missing = expected - set(rows)
        extra = set(rows) - expected
        
        raise RuntimeError(
            f"incomplete recovery arms: missing={sorted(missing)}, extra={sorted(extra)}"
        )

    summary = summarize(
        scenarios,
        proposals,
        list(rows.values()),
    )

    write_json(
        output / "summary.json",
        summary,
    )

    print(
        "[PASS]",
        output / "summary.json",
    )


if __name__ == "__main__":
    main()
