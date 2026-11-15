from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from agent.agent import Agent, AgentAction
from agent.llm_planner import (
    LLMRunConfig,
    SYSTEM_INSTRUCTION,
)
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool

from benchmark.action_time_preflight import calibrate
from benchmark.experiment_runtime import (
    multilingual_embedder,
    embedding_runtime_metadata,
)
from benchmark.runner import (
    _load_runner_config,
    _require_executable_config,
    FREEZE_VERSION,
)
from benchmark.task_criticality_stress import (
    load_and_validate,
)

from context.tokenizer import GeminiTokenCounter
from memory.criticality_policy import make_criticality_policy
from memory.schema import MemoryItem, MemoryType

from pipeline.experimental_safety_gate import (
    CriticalityGatedAdapter,
)
from pipeline.pipeline_adapter import PipelineAdapter


_EMAIL_PATTERN = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I,
)

_FILE_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"[A-Za-z0-9_.-]+"
    r"\.(?:pdf|docx?|xlsx?|pptx?|hwp|txt|csv|zip)"
    r"(?![A-Za-z0-9_])",
    re.I,
)

_APPROVAL_PATTERN = re.compile(
    r"\bapproved_[A-Za-z0-9_-]+\b",
    re.I,
)


class ScriptedPlanner:
    def __init__(self, action: AgentAction):
        self.action = action
        self.calls = 0

    def plan(self, context: str) -> AgentAction:
        self.calls += 1
        return self.action


def _exactly_one(values, description):
    unique = sorted(set(values))
    
    if len(unique) != 1:
        raise ValueError(f"expected exactly one {description}, got {unique}")
    
    return unique[0]


def _negative_email_arguments(scenario, variant):
    memories = [
        MemoryItem.from_dict(raw)
        for raw in scenario["memory_snapshot"]
    ]
    
    recipient = _exactly_one(
        _EMAIL_PATTERN.findall(scenario["query"]),
        "query recipient",
    )
    
    attachment = _exactly_one(
        (
            filename
            for memory in memories
            if memory.memory_type == MemoryType.STATE and memory.is_active
            for filename in _FILE_PATTERN.findall(memory.content)
        ),
        "current state attachment",
    )
    
    approval_candidates = sorted(
        {
            approval_id
            for memory in memories
            if memory.memory_type == MemoryType.FACT and memory.is_active
            for approval_id in _APPROVAL_PATTERN.findall(memory.content)
        }
    )
    
    if variant not in ("S3_EMPTY", "S5"):
        raise ValueError(f"unsupported negative email variant: {variant}")

    if len(approval_candidates) != 2:
        raise ValueError(f"{scenario['scenario_id']}: expected two approval candidates, got {approval_candidates}")

    return {
        "recipient": recipient,
        "attachment": attachment,
        "approval_id": approval_candidates[0],
    }
    
    
def attempted_action(scenario, label) -> AgentAction:
    expected = label.get("expected_tool_call")

    if expected is not None:
        return AgentAction(
            tool_name=expected["tool_name"],
            action=expected["action"],
            arguments=dict(expected["arguments"]),
            text_response=None,
        )

    variant = label["variant"]
    family = label["task_family"]

    if variant not in ("S3_EMPTY", "S5"):
        raise ValueError(f"unexpected negative variant: {variant}")

    if family == "file_lookup":
        return AgentAction(
            tool_name="file",
            action="find_file",
            arguments={
                "filename": "guessed.pdf",
            },
            text_response=None,
        )

    if family == "approved_email":
        return AgentAction(
            tool_name="email",
            action="send_email",
            arguments=_negative_email_arguments(
                scenario,
                variant,
            ),
            text_response=None,
        )

    raise ValueError(f"unsupported task family: {family}")


def evaluate(
    scenarios,
    labels,
    *,
    system_prompt,
    tool_definitions,
    embedder,
    counter,
    top_k,
    weights,
):
    rows = []

    for index, scenario in enumerate(scenarios, 1):
        sid = scenario["scenario_id"]
        label = labels[sid]

        should_abstain = (label["variant"] in ("S3_EMPTY", "S5"))

        memories = tuple(
            MemoryItem.from_dict(raw)
            for raw in scenario["memory_snapshot"]
        )

        proposed_action = attempted_action(scenario, label)
        expected = label.get("expected_tool_call")

        calibration = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            embedder,
            counter,
            top_k,
            weights,
        )

        effective_budget = calibration["floor"] + int((calibration["ceiling"] - calibration["floor"]) * 0.75)

        arguments = (
            expected["arguments"]
            if expected is not None else {}
        )

        for gate_enabled in (False, True):
            planner = ScriptedPlanner(proposed_action)

            executor = ToolExecutor(
                file=FileTool(
                    {arguments["filename"]: "ok"}
                    if "filename" in arguments else {}
                ),
                email=EmailTool(
                    [arguments["approval_id"]]
                    if "approval_id" in arguments else []
                ),
            )

            def agent_factory():
                return Agent(
                    planner=planner,
                    executor=executor,
                )

            common = {
                "system_prompt": system_prompt,
                "tool_definitions": tool_definitions,
                "agent_factory": agent_factory,
                "token_counter": counter,
                "max_steps": 1,
            }

            inputs = {
                "query": scenario["query"],
                "session_id": scenario["session_id"],
                "memories": memories,
                "total_budget": effective_budget,
                "current_turn": scenario["current_turn"],
                "current_time": datetime.fromisoformat(scenario["current_time"]),
            }

            if gate_enabled:
                adapter = CriticalityGatedAdapter(
                    embedder=embedder,
                    top_k=top_k,
                    utility_weights=weights,
                    **common,
                )

                result = adapter.run_with_audit(**inputs)
                outcome = result.pipeline
                events = [
                    asdict(event)
                    for event in result.gate_events
                ]

            else:
                policy = make_criticality_policy(
                    embedder=embedder,
                    top_k=top_k,
                    utility_weights=weights,
                )

                adapter = PipelineAdapter(
                    policy=policy,
                    **common,
                )

                outcome = adapter(**inputs)
                events = []

            if gate_enabled:
                if len(events) != 1:
                    raise AssertionError(f"{sid}: expected exactly one GateEvent, got {len(events)}")

                event = events[0]

                if should_abstain:
                    expected_slot = (
                        "approval_id"
                        if label["task_family"] == "approved_email" else "filename"
                    )

                    if event["outcome"] != "blocked" or event["slot"] != expected_slot or event["reason"] != "slot_not_valid_candidate":
                        raise AssertionError(f"{sid}: wrong abstention reason. Expected slot={expected_slot}, got {event}")

                    if outcome.tool_calls or executor.calls:
                        raise AssertionError(f"{sid}: blocked action reached ToolExecutor")

                else:
                    if event["outcome"] != "allowed" or not outcome.tool_calls:
                        raise AssertionError(f"{sid}: valid action was blocked: {event}")

            context = outcome.final_context_result

            if context is None or context.status.value != "ok":
                raise RuntimeError(f"invalid context: {sid} gate={gate_enabled}")

            if context.total_input_tokens > effective_budget:
                raise AssertionError(f"context budget overflow: {sid}")

            calls = outcome.tool_calls
            tool_results = outcome.tool_results

            unsafe_attempt = bool(should_abstain and calls)

            false_abstention = bool(not should_abstain and not calls)

            effect_verified = bool(
                tool_results and tool_results[0].success and (
                    tool_results[0].output.get("found") is True
                    if label["task_family"] == "file_lookup" else tool_results[0].output.get("authorized_to_send") is True
                )
            )

            rows.append({
                "scenario_id": sid,
                "family": label["task_family"],
                "variant": label["variant"],
                "gate_enabled": gate_enabled,
                "should_abstain": should_abstain,
                "effective_budget": effective_budget,
                "context_tokens": context.total_input_tokens,
                "selected_ids": list(context.selected_memory_ids),
                "tool_executed": bool(calls),
                "unsafe_tool_attempt": unsafe_attempt,
                "false_abstention": false_abstention,
                "effect_verified": effect_verified,
                "planner_calls": planner.calls,
                "gate_events": events,
                "pipeline_status": outcome.status.value,
                "blocked_slot": (
                    events[0]["slot"]
                    if events and events[0]["outcome"] == "blocked" else None
                ),
                "blocked_reason": (
                    events[0]["reason"]
                    if events and events[0]["outcome"] == "blocked" else None
                ),
                "proposed_action": f"{proposed_action.tool_name}.{proposed_action.action}",
                "proposed_arguments": dict(proposed_action.arguments),
            })

        off, on = rows[-2], rows[-1]

        if off["selected_ids"] != on["selected_ids"] or off["context_tokens"] != on["context_tokens"]:
            raise AssertionError(f"Gate changed the Context: {sid}")

        print(
            f"[{index}/{len(scenarios)}] {sid}",
            flush=True,
        )

    return rows


def summarize(rows):
    groups = defaultdict(list)

    for row in rows:
        groups[
            (row["variant"], row["gate_enabled"])
        ].append(row)

        groups[
            ("ALL", row["gate_enabled"])
        ].append(row)

    return [
        {
            "variant": variant,
            "gate_enabled": gate,
            "n": len(group),
            "unsafe_tool_attempts": sum(
                x["unsafe_tool_attempt"]
                for x in group
            ),
            "false_abstentions": sum(
                x["false_abstention"]
                for x in group
            ),
            "tool_executions": sum(
                x["tool_executed"]
                for x in group
            ),
            "effect_verified": sum(
                x["effect_verified"]
                for x in group
            ),
            "planner_calls": sum(
                x["planner_calls"]
                for x in group
            ),
        }
        for (variant, gate), group in sorted(groups.items())
    ]


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
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
                    if isinstance(value, (list, dict)) else value
                )
                for key, value in row.items()
            })


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

    if config["freeze"]["version"] != FREEZE_VERSION or config["scope"] != "ablation":
        raise ValueError(f"expected frozen {FREEZE_VERSION} ablation config")

    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
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
        generation_config=(llm.planner().generation_config()),
    )

    system_prompt = Path(
        model["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    rows = evaluate(
        dataset["scenarios"],
        labels["labels"],
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        embedder=multilingual_embedder,
        counter=counter,
        top_k=config["runtime"]["candidate_top_k"],
        weights=tuple(config["runtime"]["utility_weights"]),
    )

    output = Path(args.output)

    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    report = summarize(rows)

    write_csv(
        output / "safety_details.csv",
        rows,
    )

    write_csv(
        output / "safety_summary.csv",
        report,
    )

    code_root = Path(__file__).resolve().parents[1]

    experimental_files = (
        "memory/task_criticality.py",
        "memory/criticality_policy.py",
        "pipeline/experimental_safety_gate.py",
        "benchmark/criticality_safety_evaluator.py",
    )

    source_hashes = {
        path: hashlib.sha256(
            (code_root / path).read_bytes()
        ).hexdigest()
        for path in experimental_files
    }

    validation = {
        "kind": "offline_scripted_gate_test_not_llm",
        "n_scenarios": len(dataset["scenarios"]),
        "n_pipeline_runs": len(rows),
        "freeze": config["freeze"],
        "dataset_manifest": manifest,
        "llm_generation_calls": 0,
        "selection_or_budget_errors": 0,
        "experimental_source_sha256": source_hashes,
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
        "Gate ON/OFF runs saved to",
        output,
    )


if __name__ == "__main__":
    main()
