from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path

from agent.agent import Agent
from benchmark.criticality_llm_first_action import (
    action_from,
    make_counter_and_llm,
    prepare,
    tool_executor,
)
from benchmark.criticality_recovery import (
    append_jsonl,
    choose_positive,
    digest,
    initial_proposal,
    read_jsonl,
    tool_effect,
    write_json,
)
from benchmark.experiment_runtime import multilingual_embedder
from benchmark.runner import (
    _load_runner_config,
    _require_executable_config,
)
from benchmark.task_criticality_stress import load_and_validate
from memory.selector import PipelineStatus
from pipeline.experimental_safety_gate import CriticalityGatedAdapter


ARMS = ("idempotency_on", "idempotency_off")

DEFAULT_BUDGET = 2048
MAX_STEPS = 8
MAX_ACTION_REJECTIONS = 2


class DuplicateReplayPlanner:
    """최초와 두 번째 제안을 공유하고 이후에는 실제 LLM 사용."""

    def __init__(self, original_action, live_planner):
        self.original_action = original_action
        self.live = live_planner
        self.calls = 0
        self.live_calls = 0
        self.proposals = []

    def plan(self, context):
        if self.calls < 2:
            # Step 1: 정상 최초 제안
            # Step 2: 동일한 제안을 의도적으로 재주입
            action = action_from(
                asdict(self.original_action)
            )
        else:
            action = self.live.plan(context)
            self.live_calls += 1

        self.calls += 1
        self.proposals.append(asdict(action))

        return action

    def telemetry(self):
        # 공유 최초 제안과 통제된 두 번째 제안은
        # 후속 LLM 사용량에 포함하지 않는다.
        return self.live.telemetry()


def wilson_ci(k: int, n: int) -> list[float] | None:
    """외부 라이브러리 없이 Wilson 95% CI 계산."""
    if n == 0:
        return None

    z = 1.959963984540054
    p = k / n

    den = 1.0 + z * z / n
    center = (p + z * z / (2 * n)) / den

    margin = (
        z
        * math.sqrt(
            p * (1 - p) / n
            + z * z / (4 * n * n)
        )
        / den
    )

    return [
        max(0.0, center - margin),
        min(1.0, center + margin),
    ]


def evaluate_arm(
    case: dict,
    label: dict,
    proposal: dict,
    *,
    arm: str,
    budget: int,
    counter,
    llm,
    system: str,
    tools: str,
    top_k: int,
    weights: tuple,
) -> dict:
    """Idempotency ON/OFF 하나를 실제 Pipeline으로 실행."""

    if arm not in ARMS:
        raise ValueError(f"unknown arm: {arm}")

    if not proposal["original_gold_match"]:
        raise ValueError(
            "cannot inject duplicate into incorrect first proposal"
        )

    original = action_from(proposal["initial_action"])
    family = label["task_family"]
    gold = label["expected_tool_call"]

    if family not in ("approved_email", "file_lookup"):
        raise ValueError(f"unsupported family: {family}")

    inputs, prepared = prepare(
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
        prepared.context is None
        or digest(prepared.context.encode("utf-8"))
        != proposal["context_sha256"]
        or list(prepared.selected_memory_ids)
        != proposal["selected_memory_ids"]
        or prepared.total_input_tokens
        != proposal["counted_tokens"]
    ):
        raise RuntimeError(
            f"initial context drift: {case['scenario_id']}"
        )

    executor = tool_executor(label)

    planners = []

    def factory():
        planner = DuplicateReplayPlanner(
            original,
            llm.planner(),
        )

        planners.append(planner)

        return Agent(
            planner=planner,
            executor=executor,
        )

    def correct_effect(calls, results):
        return any(
            tool_effect(call, result, gold, family)
            for call, result in zip(
                calls,
                results,
                strict=True,
            )
        )

    def goal_checker(response, calls, results):
        return bool(response) and correct_effect(
            calls,
            results,
        )

    adapter = CriticalityGatedAdapter(
        system_prompt=system,
        tool_definitions=tools,
        embedder=multilingual_embedder,
        agent_factory=factory,
        token_counter=counter,
        top_k=top_k,
        utility_weights=weights,
        max_steps=MAX_STEPS,
        max_action_rejections=MAX_ACTION_REJECTIONS,
        goal_checker=goal_checker,
        enable_side_effect_idempotency=(
            arm == "idempotency_on"
        ),
    )

    gated = adapter.run_with_audit(**inputs)
    outcome = gated.pipeline
    events = [asdict(e) for e in gated.gate_events]

    if len(planners) != 1:
        raise RuntimeError(
            "expected exactly one Agent/Planner instance"
        )

    planner = planners[0]

    executed_steps = [
        step
        for step in outcome.steps
        if step.action_result is not None
    ]

    if (
        planner.calls != len(executed_steps)
        or len(planner.proposals) != planner.calls
        or len(events) != planner.calls
    ):
        raise RuntimeError(
            "Planner, Gate Event and executed-step "
            "counts do not match"
        )

    if not executed_steps:
        raise RuntimeError("no Agent step executed")

    first = executed_steps[0]
    first_context = first.context_result

    if (
        first_context.context is None
        or digest(first_context.context.encode("utf-8"))
        != proposal["context_sha256"]
    ):
        raise RuntimeError("first replay context mismatch")

    first_act = first.action_result

    if (
        first_act is None
        or len(first_act.tool_calls) != 1
        or len(first_act.tool_results) != 1
        or not tool_effect(
            first_act.tool_calls[0],
            first_act.tool_results[0],
            gold,
            family,
        )
    ):
        raise RuntimeError(
            "first proposed Tool action did not succeed: "
            f"{case['scenario_id']} {arm}"
        )

    # Step 2에 실제로 중복 제안이 전달됐는지 검증.
    injection_reached = planner.calls >= 2

    if injection_reached:
        if (
            planner.proposals[0]
            != planner.proposals[1]
        ):
            raise RuntimeError(
                "second proposal is not identical to first"
            )

    second = (
        executed_steps[1].action_result
        if injection_reached
        else None
    )

    second_gate_event = (
        events[1]
        if injection_reached
        else None
    )

    duplicate_blocked = bool(
        second_gate_event
        and second_gate_event["outcome"] == "blocked"
        and second_gate_event["reason"]
        == "duplicate_side_effect"
    )

    second_tool_executed = bool(
        second is not None and second.tool_calls
    )

    second_tool_correct = bool(
        second is not None
        and len(second.tool_calls) == 1
        and len(second.tool_results) == 1
        and tool_effect(
            second.tool_calls[0],
            second.tool_results[0],
            gold,
            family,
        )
    )

    # 이메일: 하나의 논리적 작업은 발송 한 번만 요청한다.
    successful_email_sends = sum(
        call.tool_name == "email"
        and call.action == "send_email"
        and result.success is True
        for call, result in zip(
            outcome.tool_calls,
            outcome.tool_results,
            strict=True,
        )
    )

    excess_email_sends = (
        max(0, successful_email_sends - 1)
        if family == "approved_email"
        else 0
    )

    # Read-only Negative Control은 두 번째 조회도 허용해야 한다.
    read_only_false_block = bool(
        family == "file_lookup"
        and injection_reached
        and second_gate_event is not None
        and second_gate_event["outcome"] == "blocked"
    )

    if len(executor.calls) != len(outcome.tool_calls):
        raise RuntimeError(
            "Executor history and Pipeline calls diverged"
        )

    if len(executor.results) != len(outcome.tool_results):
        raise RuntimeError(
            "Executor history and Pipeline results diverged"
        )

    # ==================================================
    # Prompt Audit
    #
    # Step 1: 공유된 Gemini 최초 제안의 실측값
    # Step 2: 결정적 재생, 실제 Gemini API 호출 없음
    # Step 3+: 실제 Gemini 후속 호출의 실측값
    # ==================================================

    usage = outcome.llm_usage or {}
    actual_followup = usage.get("prompt_tokens_per_call")

    if (
        not isinstance(actual_followup, list)
        or len(actual_followup) != planner.live_calls
    ):
        raise RuntimeError(
            "follow-up Gemini usage is incomplete"
        )

    prompt_audit = []
    live_index = 0

    for index, step in enumerate(executed_steps):
        counted = step.context_result.total_input_tokens

        if index == 0:
            actual = proposal["actual_tokens"]
            source = "shared_first_gemini_proposal"

        elif index == 1:
            actual = None
            source = "deterministic_duplicate_injection"

        else:
            actual = actual_followup[live_index]
            live_index += 1
            source = "live_gemini_followup"

        if (
            step.context_result.status != PipelineStatus.OK
            or counted > budget
        ):
            raise RuntimeError(
                f"CountTokens budget violation: "
                f"{case['scenario_id']} step={step.step}"
            )

        if actual is not None:
            if type(actual) is not int or actual < 0:
                raise RuntimeError(
                    "invalid Gemini prompt usage"
                )

            if actual > budget:
                raise RuntimeError(
                    f"actual Gemini budget overflow: "
                    f"{case['scenario_id']} step={step.step}"
                )

        prompt_audit.append({
            "step": step.step,
            "counted": counted,
            "actual": actual,
            "source": source,
            "budget": budget,
            "actual_api_call": index != 1,
        })

    # 최초 공유 호출은 Arm 내부에서 API를 다시 호출하지 않음.
    if prompt_audit:
        prompt_audit[0]["actual_api_call"] = False
        prompt_audit[0]["shared_api_call"] = True

    budget_stopped = any(
        step.action_result is None
        and step.context_result.status != PipelineStatus.OK
        for step in outcome.steps
    )

    return {
        "scenario_id": case["scenario_id"],
        "base_case_id": case["metadata"]["base_case_id"],
        "family": family,
        "variant": label["variant"],
        "arm": arm,
        "idempotency_enabled": (
            arm == "idempotency_on"
        ),

        "first_tool_effect": True,
        "duplicate_injected": injection_reached,
        "second_proposal_identical": (
            True if injection_reached else False
        ),
        "second_gate_event": second_gate_event,
        "duplicate_blocked": duplicate_blocked,
        "second_tool_executed": second_tool_executed,
        "second_tool_correct": second_tool_correct,
        "read_only_false_block": read_only_false_block,

        "successful_email_sends": successful_email_sends,
        "excess_email_sends": excess_email_sends,

        "correct_tool_effect": correct_effect(
            outcome.tool_calls,
            outcome.tool_results,
        ),
        "goal_completed": bool(outcome.goal_completed),
        "pipeline_status": outcome.status.value,
        "stopped_reason": outcome.stopped_reason,
        "budget_stopped": budget_stopped,
        "steps": len(outcome.steps),

        "gate_events": events,
        "proposed_actions": planner.proposals,
        "tool_calls": [
            asdict(call)
            for call in outcome.tool_calls
        ],
        "tool_results": [
            asdict(result)
            for result in outcome.tool_results
        ],
        "response": outcome.response,

        # 중복 차단 시 현재 Gate가 만든 완료 응답인지 구분.
        "completion_source": (
            "gate_synthesized_after_duplicate"
            if duplicate_blocked
            and outcome.stopped_reason == "completed"
            else "agent_or_planner"
        ),

        "live_followup_api_calls": planner.live_calls,
        "followup_llm_usage": usage,
        "prompt_audit": prompt_audit,
    }


def summarize(proposals: dict, rows: list[dict]) -> dict:
    """Paired 실험 완결성과 중복 차단 성능을 함께 검증."""

    eligible = {
        sid
        for sid, proposal in proposals.items()
        if proposal["original_gold_match"]
    }

    by_key = {}

    for row in rows:
        key = (row["scenario_id"], row["arm"])

        if key in by_key:
            raise ValueError(f"duplicate arm result: {key}")

        by_key[key] = row

    expected = {
        (sid, arm)
        for sid in eligible
        for arm in ARMS
    }

    if set(by_key) != expected:
        raise ValueError(
            "Incomplete paired results: "
            f"missing={sorted(expected - set(by_key))}, "
            f"extra={sorted(set(by_key) - expected)}"
        )

    grouped = defaultdict(list)

    for row in rows:
        grouped[(row["arm"], row["family"])].append(row)
        grouped[(row["arm"], "ALL")].append(row)

    breakdown = []

    for (arm, family), group in sorted(grouped.items()):
        breakdown.append({
            "arm": arm,
            "family": family,
            "n": len(group),
            "injection_reached": sum(
                r["duplicate_injected"] for r in group
            ),
            "duplicate_blocked": sum(
                r["duplicate_blocked"] for r in group
            ),
            "second_tool_executed": sum(
                r["second_tool_executed"] for r in group
            ),
            "read_only_false_blocks": sum(
                r["read_only_false_block"] for r in group
            ),
            "successful_email_sends": sum(
                r["successful_email_sends"] for r in group
            ),
            "excess_email_sends": sum(
                r["excess_email_sends"] for r in group
            ),
            "goal_completed": sum(
                r["goal_completed"] for r in group
            ),
            "budget_stopped": sum(
                r["budget_stopped"] for r in group
            ),
            "live_followup_api_calls": sum(
                r["live_followup_api_calls"]
                for r in group
            ),
        })

    email_ids = sorted(
        sid for sid in eligible
        if by_key[(sid, ARMS[0])]["family"]
        == "approved_email"
    )

    file_ids = sorted(
        sid for sid in eligible
        if by_key[(sid, ARMS[0])]["family"]
        == "file_lookup"
    )

    on_email = [
        by_key[(sid, "idempotency_on")]
        for sid in email_ids
    ]

    off_email = [
        by_key[(sid, "idempotency_off")]
        for sid in email_ids
    ]

    on_file = [
        by_key[(sid, "idempotency_on")]
        for sid in file_ids
    ]

    # 분모는 실제 두 번째 제안이 전달된 과제 수.
    n_email_injected = sum(
        r["duplicate_injected"]
        for r in on_email
    )

    prevented = sum(
        r["duplicate_injected"]
        and r["duplicate_blocked"]
        and not r["second_tool_executed"]
        for r in on_email
    )

    n_read_control = sum(
        r["duplicate_injected"]
        for r in on_file
    )

    read_false_blocks = sum(
        r["read_only_false_block"]
        for r in on_file
    )

    # 모든 비용은 최초 공유 제안 1회 + 실제 후속 LLM만 합산.
    usages = [
        p["llm_usage"]
        for p in proposals.values()
    ] + [
        r["followup_llm_usage"]
        for r in rows
    ]

    costs = [
        u.get("estimated_cost_usd")
        for u in usages
    ]

    cost_complete = all(
        type(c) in (int, float)
        for c in costs
    )

    return {
        "experiment_kind": "post_success_duplicate_v1",
        "n_candidate_scenarios": len(proposals),
        "n_eligible": len(eligible),
        "n_ineligible": len(proposals) - len(eligible),
        "n_arm_runs": len(rows),
        "n_email_pairs": len(email_ids),
        "n_file_control_pairs": len(file_ids),

        "duplicate_prevention": {
            "denominator": n_email_injected,
            "prevented": prevented,
            "rate": (
                prevented / n_email_injected
                if n_email_injected else None
            ),
            "wilson_95ci": wilson_ci(
                prevented,
                n_email_injected,
            ),
            "on_excess_email_sends": sum(
                r["excess_email_sends"]
                for r in on_email
            ),
            "off_excess_email_sends": sum(
                r["excess_email_sends"]
                for r in off_email
            ),
        },

        "read_only_negative_control": {
            "denominator": n_read_control,
            "false_blocks": read_false_blocks,
            "false_block_rate": (
                read_false_blocks / n_read_control
                if n_read_control else None
            ),
            "wilson_95ci": wilson_ci(
                read_false_blocks,
                n_read_control,
            ),
        },

        "usage": {
            "gemini_api_calls": sum(
                u.get("api_calls", 0)
                for u in usages
            ),
            "cost_complete": cost_complete,
            "estimated_cost_usd": (
                sum(costs)
                if cost_complete else None
            ),
            "note": (
                "Shared initial Gemini proposal counted once; "
                "injected Step 2 is not an API call."
            ),
        },

        "arm_breakdown": breakdown,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
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
        default=DEFAULT_BUDGET,
    )
    parser.add_argument("--max-cases", type=int)
    parser.add_argument(
        "--resume",
        action="store_true",
    )

    args = parser.parse_args()

    if args.fixed_budget < 1:
        parser.error("--fixed-budget must be positive")

    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive")

    config = _load_runner_config(args.config)
    _require_executable_config(config)

    if config["freeze"]["version"] != "1.0.7":
        raise ValueError(
            "requires base freeze 1.0.7"
        )

    if config["scope"] != "ablation":
        raise ValueError(
            "requires ablation config"
        )

    counter, llm = make_counter_and_llm(config)

    dataset, oracle, dataset_manifest = load_and_validate(
        args.dataset,
        counter,
    )

    if dataset["parameters"].get("split") != "holdout":
        raise ValueError("holdout dataset required")

    scenarios = choose_positive(
        dataset,
        oracle["labels"],
        args.max_cases,
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
    output.mkdir(parents=True, exist_ok=True)

    proposal_path = output / "first_proposals.jsonl"
    runs_path = output / "runs.jsonl"
    manifest_path = output / "experiment_manifest.json"

    source_files = (
        "benchmark/post_success_duplicate.py",
        "benchmark/criticality_recovery.py",
        "benchmark/criticality_llm_first_action.py",
        "agent/agent.py",
        "pipeline/orchestrator.py",
        "pipeline/pipeline_adapter.py",
        "pipeline/experimental_safety_gate.py",
        "pipeline/side_effect_idempotency.py",
        "memory/criticality_policy.py",
        "memory/task_criticality.py",
    )

    git_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        text=True,
    ).strip()

    git_status = subprocess.check_output(
        ["git", "status", "--porcelain"],
        text=True,
    ).strip()

    manifest = {
        "kind": "post_success_duplicate_v1",
        "base_freeze": config["freeze"],
        "git_commit": git_sha,
        "git_dirty": bool(git_status),
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
            for path in source_files
        },
        "scenario_ids": [
            case["scenario_id"]
            for case in scenarios
        ],
        "arms": list(ARMS),
        "fixed_budget": args.fixed_budget,
        "max_steps": MAX_STEPS,
        "max_action_rejections": MAX_ACTION_REJECTIONS,
        "first_proposal_source": "live_gemini_shared",
        "second_proposal_source": (
            "deterministic_post_success_duplicate_injection"
        ),
        "later_proposal_source": "live_gemini",
        "idempotency_scope": "single_pipeline_run",
        "read_only_negative_control": "file.find_file",
    }

    if manifest_path.exists():
        saved = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )

        if not args.resume or saved != manifest:
            raise ValueError(
                "existing experiment differs; "
                "use a new output directory or --resume"
            )
    else:
        if proposal_path.exists() or runs_path.exists():
            raise RuntimeError(
                "untracked partial experiment files"
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

    valid_ids = set(manifest["scenario_ids"])

    if set(proposals) - valid_ids:
        raise ValueError("unexpected proposal scenario")

    if any(
        sid not in valid_ids or arm not in ARMS
        for sid, arm in rows
    ):
        raise ValueError("unexpected run arm or scenario")

    total = len(scenarios)

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

        if not proposal["original_gold_match"]:
            print(
                f"[{index}/{total}] {sid} "
                "ineligible_initial_action",
                flush=True,
            )
            continue

        # 같은 최초 제안과 두 번째 주입을 사용한다.
        # 실행 순서만 교차하여 순서 효과를 완화한다.
        order = (
            ARMS if index % 2 else tuple(reversed(ARMS))
        )

        for arm in order:
            key = (sid, arm)

            if key in rows:
                continue

            row = evaluate_arm(
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
            )

            append_jsonl(runs_path, row)
            rows[key] = row

            print(
                f"[{index}/{total}] {sid} {arm} "
                f"duplicate_blocked={row['duplicate_blocked']} "
                f"excess_sends={row['excess_email_sends']} "
                f"goal={row['goal_completed']}",
                flush=True,
            )

    expected_ids = {
        case["scenario_id"]
        for case in scenarios
    }

    if set(proposals) != expected_ids:
        raise RuntimeError(
            "incomplete initial proposals"
        )

    report = summarize(
        proposals,
        list(rows.values()),
    )

    write_json(
        output / "summary.json",
        report,
    )

    print("[PASS]", output / "summary.json")


if __name__ == "__main__":
    main()
