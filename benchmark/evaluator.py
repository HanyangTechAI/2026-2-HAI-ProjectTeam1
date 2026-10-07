"""Deterministic evaluation of supplied ground truth and execution snapshots.

No agent, store, tokenizer or API is called here. See docs/evaluation_usage.md
for the input contract and the offline JSONL command.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import itertools
import json
import math
from pathlib import Path
import random
import statistics
import unicodedata
from typing import Any, Mapping


def _record(value: Any) -> Any:
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {key: _record(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_record(item) for item in value]
    return value


def normalize_answer(text: str) -> str:
    """Unicode/case/whitespace normalization; punctuation is a separator.

    Articles are preserved, so this is also suitable for Korean answers.
    """
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(" " if unicodedata.category(c).startswith("P") else c for c in text)
    return " ".join(text.split())


def exact_match(prediction: str, reference: str) -> float:
    return float(prediction == reference)


def normalized_exact_match(prediction: str, reference: str) -> float:
    return exact_match(normalize_answer(prediction), normalize_answer(reference))


def token_f1(prediction: str, reference: str) -> float:
    predicted = normalize_answer(prediction).split()
    expected = normalize_answer(reference).split()
    if not predicted or not expected:
        return float(predicted == expected)
    overlap = sum((Counter(predicted) & Counter(expected)).values())
    return 2 * overlap / (len(predicted) + len(expected))


def retrieval_metrics(retrieved: list[str], targets: list[str], k: int = 20) -> dict:
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    # Duplicate IDs must not improve precision, recall, or rank.
    ranked = list(dict.fromkeys(retrieved))[:k]
    target_set = set(targets)
    if not target_set:
        return dict(retrieval_hit=None, recall_at_k=None, precision_at_k=None, mrr=None)
    hits = len(set(ranked) & target_set)
    rank = next((i for i, mid in enumerate(ranked, 1) if mid in target_set), None)
    return dict(
        retrieval_hit=bool(hits), recall_at_k=hits / len(target_set),
        precision_at_k=hits / len(ranked) if ranked else 0.0,
        mrr=1 / rank if rank else 0.0,
    )


def _matches(actual: Any, pattern: Any) -> bool:
    """Recursive subset matching, with exact values (including scalar types)."""
    if isinstance(pattern, Mapping):
        return isinstance(actual, Mapping) and all(
            key in actual and _matches(actual[key], value) for key, value in pattern.items()
        )
    return type(actual) is type(pattern) and actual == pattern


def _expected_calls_present(calls: list[dict], patterns: list[dict]) -> bool:
    # Bipartite matching: one call cannot satisfy two expected calls, and broad
    # patterns cannot consume the only call that satisfies a narrower pattern.
    assigned: dict[int, int] = {}

    def assign(pattern_index: int, visited: set[int]) -> bool:
        for index, call in enumerate(calls):
            if index in visited or not _matches(call, patterns[pattern_index]):
                continue
            visited.add(index)
            if index not in assigned or assign(assigned[index], visited):
                assigned[index] = pattern_index
                return True
        return False

    return all(assign(i, set()) for i in range(len(patterns)))


@dataclass(frozen=True)
class EvaluationResult:
    """A serializable result. Metrics remain extensible without store coupling."""

    sample_id: str
    strategy: str
    repeat_id: int
    pipeline_status: str
    task_success: bool
    metrics: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.metrics, sample_id=self.sample_id, strategy=self.strategy,
                    repeat_id=self.repeat_id, pipeline_status=self.pipeline_status,
                    task_success=self.task_success)


def _selection_diagnostics(run: dict, selection: dict, context: dict,
                           targets: set[str], selected: set[str]) -> dict:
    """Use execution traces to distinguish discovered targets from budget drops.

    rejected_memory_ids proves rejection, but not its cause. Budget attribution
    needs an explicit selection trace or the Context Builder's budget-drop log.
    Missing traces remain unknown; they are never treated as an empty log.
    """
    trace = run.get("selection_trace") or {}

    def ids(source: dict, key: str) -> set[str]:
        values = source.get(key)
        if values is None:
            return set()
        if not isinstance(values, list) or not all(isinstance(mid, str) and mid for mid in values):
            raise ValueError(f"{key} must be a list of non-empty memory IDs")
        return set(values)

    before_context = ids(selection, "selected_memory_ids")
    rejected = ids(selection, "rejected_memory_ids")
    dropped = ids(context, "dropped_flexible_ids")
    budget_rejected = ids(trace, "budget_rejected_memory_ids")
    candidates = ids(trace, "candidate_memory_ids")
    if before_context & rejected:
        raise ValueError("selection selected and rejected IDs must be disjoint")
    if budget_rejected & (before_context | selected):
        raise ValueError("budget-rejected IDs cannot be selected")
    if dropped & selected:
        raise ValueError("context-dropped IDs cannot be in the final context")
    if selection.get("selected_memory_ids") is not None and not dropped <= before_context:
        raise ValueError("context-dropped IDs must have been selected before formatting")
    # An explicit pool is after temporal/applicability resolution, before budget
    # selection. Do not mix initial Top-K (possibly wrong versions) into it.
    if trace.get("candidate_memory_ids") is not None:
        if not (before_context | rejected | budget_rejected) <= candidates:
            raise ValueError("candidate_memory_ids must cover selection evidence")
    known_candidates = candidates | before_context | rejected | budget_rejected | dropped | selected
    missing = targets - selected
    selection_budget_targets = missing & budget_rejected
    context_budget_targets = missing & dropped
    budget_targets = selection_budget_targets | context_budget_targets
    complete_budget_trace = (trace.get("budget_rejected_memory_ids") is not None
                             and context.get("dropped_flexible_ids") is not None)
    budget_failure = (bool(budget_targets) if budget_targets or not missing or complete_budget_trace
                      else None) if targets else None
    # Reasons may be multiple per target; explicit budget evidence takes
    # precedence over a generic rejection record.
    reasons = {}
    for mid in sorted(missing):
        evidence = []
        if mid in selection_budget_targets:
            evidence.append("selection_budget")
        if mid in context_budget_targets:
            evidence.append("context_budget")
        if not evidence and mid in rejected:
            evidence.append("selection_rejection_unspecified")
        reasons[mid] = evidence or ["unknown"]
    return dict(
        selection_candidate_memory_ids=sorted(known_candidates),
        selection_candidate_trace_complete=trace.get("candidate_memory_ids") is not None,
        pre_context_selected_memory_ids=sorted(before_context),
        rejected_memory_ids=sorted(rejected), dropped_flexible_ids=sorted(dropped),
        budget_rejected_memory_ids=sorted(budget_rejected),
        found_but_unselected_target_ids=sorted(missing & known_candidates),
        selection_rejected_target_ids=sorted(missing & rejected),
        selection_budget_omitted_target_ids=sorted(selection_budget_targets),
        context_budget_omitted_target_ids=sorted(context_budget_targets),
        budget_omitted_target_ids=sorted(budget_targets),
        budget_selection_failure=budget_failure,
        budget_omission_trace_complete=complete_budget_trace,
        target_omission_reasons=reasons,
    )


def evaluate(scenario: Any, run_result: Any, *, strategy: str | None = None,
             k: int = 20, repeat_id: int = 0) -> EvaluationResult:
    """Evaluate mappings or dataclass snapshots without accessing a MemoryStore.

    Required states use scenario.required_states with task-specific tool argument
    values and state_scope (current or historical; defaults to current for legacy
    inputs). Historical states are excluded from stale-state usage metrics.
    Constraints use scenario.constraint_rules. ID-only ground truth
    cannot establish whether an action was compliant and is rejected.
    """
    scenario, run = _record(scenario), _record(run_result)
    sample_id = scenario.get("scenario_id", scenario.get("sample_id"))
    strategy = strategy or run.get("strategy")
    if not sample_id or not strategy:
        raise ValueError("scenario_id/sample_id and strategy are required")
    if type(repeat_id) is not int or repeat_id < 0:
        raise ValueError("repeat_id must be a non-negative integer")
    status = run.get("status", run.get("pipeline_status"))
    if status not in {"ok", "invalid_budget_configuration", "insufficient_context_budget"}:
        raise ValueError("a recognized pipeline status is required")
    selection = run.get("selection_result") or {}
    context = run.get("context_result") or {}
    # Interface contract: initial General Retrieval Top-K, before version
    # expansion/temporal resolution, excluding the separate protected path.
    retrieved = list(selection.get("retrieved_memory_ids", run.get("retrieved_memory_ids", [])))
    # Formatting can drop flexible memories; the context snapshot is authoritative.
    selected = list(context.get("selected_memory_ids", selection.get(
        "selected_memory_ids", run.get("selected_memory_ids", []))))
    if status != "ok":
        selected = []  # No LLM context was delivered on a pipeline failure.
    targets = list(scenario.get("target_memory_ids", []))
    required_ids = set(scenario.get("required_state_ids", []))
    states = scenario.get("required_states", [])
    rules = scenario.get("constraint_rules", [])
    constraint_ids = set(scenario.get("applicable_constraint_ids", []))
    if required_ids != {state["memory_id"] for state in states}:
        raise ValueError("required_state_ids must match explicit required_states")
    if constraint_ids != {rule["constraint_id"] for rule in rules}:
        raise ValueError("applicable_constraint_ids must match explicit constraint_rules")
    if len(states) != len(required_ids) or len(rules) != len(constraint_ids):
        raise ValueError("duplicate state or constraint ground truth")
    for rule in rules:
        if not rule.get("forbidden_tool_calls") and not rule.get("approval"):
            raise ValueError("each constraint needs a forbidden action or approval rule")
    calls = run.get("tool_calls", [])
    violated: set[str] = set()
    approval_checks = []
    for rule in rules:
        if any(_matches(call, pattern) for call in calls
               for pattern in rule.get("forbidden_tool_calls", [])):
            violated.add(rule["constraint_id"])
        approval = rule.get("approval")
        if approval:
            approved = set(approval.get("valid_approval_ids", []))
            sends = [call for call in calls if _matches(call, approval["execution"])]
            unauthorized = any(call.get("arguments", {}).get("approval_id") not in approved
                               for call in sends)
            requested = any(_matches(call, approval["request"]) for call in calls)
            correct = not unauthorized and (
                not approval.get("request_required", True) or requested)
            approval_checks.append(correct)
            if unauthorized:
                violated.add(rule["constraint_id"])

    correct_states = stale_states = omissions = errors = current_states = 0
    for state in states:
        scope = state.get("state_scope", "current")
        if scope not in {"current", "historical"}:
            raise ValueError("state_scope must be current or historical")
        current_states += scope == "current"
        # Only current-state requirements can misuse a superseded value.
        # For a historical request, even a newer version is a task-specific
        # state error rather than stale-state usage.
        stale_values = state.get("stale_values", []) if scope == "current" else []
        relevant = [call for call in calls if _matches(call, state["tool_call"])]
        values = [call.get("arguments", {})[state["argument"]] for call in relevant
                  if state["argument"] in call.get("arguments", {})]
        missing_argument = any(state["argument"] not in call.get("arguments", {}) for call in relevant)
        if not values or missing_argument:
            omissions += 1
        if values and not missing_argument and all(_matches(value, state["expected_value"]) for value in values):
            correct_states += 1
        if values:
            # Any wrong use fails this state, even if a later call corrects it.
            wrong = [value for value in values if not _matches(value, state["expected_value"])]
            if any(any(_matches(value, old) for old in stale_values)
                   for value in wrong):
                stale_states += 1
            if any(not any(_matches(value, old) for old in stale_values)
                   for value in wrong):
                errors += 1

    temporal_required = bool(scenario.get("temporal_version_required", False))
    if temporal_required and not required_ids:
        raise ValueError("temporal evaluation needs required_state_ids")
    wrong_version_ids = set(scenario.get("wrong_version_memory_ids", []))
    temporal_correct = (required_ids <= set(selected) and not
                        (wrong_version_ids & set(selected))) if temporal_required else None
    budget = scenario.get("context_budget", scenario.get("context_token_budget"))
    usage = run.get("usage") or {}
    input_tokens = usage.get("input_tokens", context.get("input_tokens", run.get("input_tokens")))
    fixed_tokens = usage.get("fixed_input_tokens", run.get("fixed_input_tokens"))
    output_tokens = usage.get("output_tokens", run.get("output_tokens"))
    for name, value in (("context_budget", budget), ("input_tokens", input_tokens),
                        ("fixed_input_tokens", fixed_tokens), ("output_tokens", output_tokens)):
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} must be a non-negative integer")
    if budget is None or (status == "ok" and input_tokens is None):
        raise ValueError("context_budget and actual input_tokens for successful runs are required")
    budget_exceeded = input_tokens is not None and input_tokens > budget
    invalid_fixed_budget = fixed_tokens is not None and fixed_tokens > budget
    if input_tokens is not None and fixed_tokens is not None and fixed_tokens > input_tokens:
        raise ValueError("fixed_input_tokens cannot exceed measured input_tokens")
    expected = scenario.get("expected_tool_calls", [])
    forbidden = scenario.get("forbidden_tool_calls", [])
    expected_ok = _expected_calls_present(calls, expected)
    forbidden_hit = any(_matches(call, pattern) for call in calls for pattern in forbidden)
    answer_scores = dict(answer_exact_match=None, answer_normalized_exact_match=None, answer_f1=None)
    references = scenario.get("expected_answers")
    if references is not None:
        if not isinstance(references, list) or not references or not all(isinstance(x, str) for x in references):
            raise ValueError("expected_answers must be a non-empty list of strings")
        response = run.get("response")
        prediction = response if response is not None else ""
        answer_scores = dict(
            answer_exact_match=max(exact_match(prediction, ref) for ref in references) if response is not None else 0.0,
            answer_normalized_exact_match=max(normalized_exact_match(prediction, ref) for ref in references) if response is not None else 0.0,
            answer_f1=max(token_f1(prediction, ref) for ref in references) if response is not None else 0.0,
        )
    goal_completed = run.get("goal_completed")
    if goal_completed is not None and type(goal_completed) is not bool:
        raise ValueError("goal_completed must be a boolean")
    if status == "ok" and goal_completed is None and references is None:
        raise ValueError("action tasks require an explicit goal_completed outcome")
    goal_ok = goal_completed if goal_completed is not None else bool(answer_scores["answer_normalized_exact_match"])
    success = (status == "ok" and goal_ok and expected_ok and not forbidden_hit
               and not violated and correct_states == len(states)
               and (references is None or bool(answer_scores["answer_normalized_exact_match"]))
               and all(approval_checks) and not budget_exceeded and not invalid_fixed_budget)
    failures = []
    retrieval_applicable = strategy not in {"sliding_window", "long_context", "full_context", "long_context_reference"}
    if status != "ok" or budget_exceeded or invalid_fixed_budget:
        failures.append("budget_failure")
    # Initial General Retrieval misses are diagnostic metrics, not failures:
    # version expansion/protected retrieval can still supply the final context.
    # Selection failure means final target omission, not a causal attribution
    # to the selection algorithm (retrieval or budget may be the actual cause).
    general_targets = set(targets) - constraint_ids
    general_missing = general_targets - set(retrieved)
    general_retrieval_miss = bool(general_missing) if retrieval_applicable and general_targets else None
    missing_selected = set(targets) - set(selected)
    selection_failure = bool(missing_selected) if targets else None
    if selection_failure:
        failures.append("selection_failure")
    selection_diagnostics = _selection_diagnostics(run, selection, context, set(targets), set(selected))
    if selection_diagnostics["budget_selection_failure"] is True:
        failures.append("budget_selection_failure")
    if temporal_correct is False:
        failures.append("temporal_failure")
    if violated or forbidden_hit:
        failures.append("constraint_failure")
    if approval_checks and not all(approval_checks):
        failures.append("approval_failure")
    if omissions:
        failures.append("state_omission")
    if stale_states:
        failures.append("stale_state_usage")
    if errors:
        failures.append("state_error")
    if status == "ok" and not success and set(targets) <= set(selected) and not failures:
        failures.append("reasoning_action_failure")
    metrics = dict(scenario.get("metadata", {}))
    # General Retrieval does not search the separately resolved protected constraints.
    metrics.update(retrieval_metrics(retrieved, sorted(general_targets) if retrieval_applicable else [], k))
    metrics.update(answer_scores)
    metrics.update(selection_diagnostics)
    metrics.update(
        context_token_budget=budget, fixed_input_tokens=fixed_tokens,
        available_memory_tokens=budget - fixed_tokens if fixed_tokens is not None else None,
        retrieved_memory_ids=retrieved, selected_memory_ids=selected, target_memory_ids=targets,
        retrieved_memories=run.get("retrieved_memories", []),
        retrieval_k=k, retrieval_applicable=retrieval_applicable,
        general_retrieval_miss=general_retrieval_miss,
        general_retrieval_missing_target_ids=sorted(general_missing) if retrieval_applicable else [],
        recovered_target_ids=sorted(general_missing & set(selected)) if retrieval_applicable else [],
        selection_failure=selection_failure, missing_selected_target_ids=sorted(missing_selected),
        selected_target_recall=len(set(targets) & set(selected)) / len(set(targets)) if targets else None,
        applicable_protected_recall=len(constraint_ids & set(selected)) / len(constraint_ids) if constraint_ids else None,
        constraint_violation=bool(violated), constraint_violation_count=len(violated),
        constraint_violation_rate=len(violated) / len(rules) if rules else None,
        violated_constraint_ids=sorted(violated),
        required_state_accuracy=correct_states / len(states) if states else None,
        # Deprecated output alias for consumers of the original evaluator.
        current_state_accuracy=correct_states / len(states) if states else None,
        current_state_requirement_count=current_states, stale_state_usage_count=stale_states,
        stale_state_used=bool(stale_states) if current_states else None,
        stale_state_usage_rate=stale_states / current_states if current_states else None,
        state_omission_count=omissions, state_error_count=errors,
        state_omission_rate=omissions / len(states) if states else None,
        state_error_rate=errors / len(states) if states else None,
        temporal_version_required=temporal_required, temporal_version_correct=temporal_correct,
        stale_memory_count=len(wrong_version_ids & set(retrieved)),
        stale_selected_memory_count=len(wrong_version_ids & set(selected)),
        approval_behavior_correct=all(approval_checks) if approval_checks else None,
        mandatory_memory_overflow=run.get("mandatory_memory_overflow"),
        budget_exceeded=budget_exceeded, invalid_fixed_budget=invalid_fixed_budget,
        expected_tool_calls_present=expected_ok,
        forbidden_tool_call_used=forbidden_hit, input_tokens=input_tokens, output_tokens=output_tokens,
        retrieval_tokens=usage.get("retrieval_tokens"), memory_tokens=context.get("memory_tokens", selection.get("memory_tokens")),
        planning_latency_ms=run.get("planning_latency_ms"), total_latency_ms=run.get("total_latency_ms"),
        total_llm_cost=run.get("total_llm_cost"), failure_types=failures,
    )
    return EvaluationResult(str(sample_id), str(strategy), repeat_id, status, bool(success), metrics)


SUMMARY_METRICS = (
    "task_success", "constraint_violation_rate", "required_state_accuracy",
    "general_retrieval_miss", "selection_failure", "budget_selection_failure",
    "stale_state_usage_rate", "temporal_version_correct", "recall_at_k",
    "state_omission_rate", "state_error_rate",
    "precision_at_k", "mrr", "answer_normalized_exact_match", "answer_f1",
    "applicable_protected_recall", "input_tokens", "output_tokens",
    "planning_latency_ms", "total_latency_ms", "total_llm_cost",
)


def _cluster_mean_ci(clusters: list[list[float]], rng: random.Random, iterations: int) -> dict:
    values = [v for cluster in clusters for v in cluster]
    if not values:
        return {"mean": None, "ci95": None, "n": 0, "n_samples": 0}
    mean = statistics.fmean(values)
    if len(clusters) < 2:
        return {"mean": mean, "ci95": None, "n": len(values), "n_samples": len(clusters)}
    draws = sorted(statistics.fmean(v for cluster in rng.choices(clusters, k=len(clusters))
                                   for v in cluster) for _ in range(iterations))
    return {"mean": mean, "ci95": [draws[int(.025 * (iterations - 1))], draws[int(.975 * (iterations - 1))]],
            "n": len(values), "n_samples": len(clusters)}


def aggregate_results(results: list[Any], *, group_by: tuple[str, ...] = ("strategy", "context_token_budget"),
                      bootstrap_iterations: int = 2000, seed: int = 42) -> list[dict]:
    if bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be >= 100")
    groups = defaultdict(list)
    for result in results:
        row = result.to_dict() if isinstance(result, EvaluationResult) else _record(result)
        groups[tuple(row.get(key) for key in group_by)].append(row)
    rng = random.Random(seed)
    summaries = []
    for key, rows in groups.items():
        metrics = {}
        for name in SUMMARY_METRICS:
            clusters = defaultdict(list)
            for row in rows:
                value = row.get(name)
                if isinstance(value, (int, float)) and math.isfinite(value):
                    clusters[row["sample_id"]].append(float(value))
            metrics[name] = _cluster_mean_ci(list(clusters.values()), rng, bootstrap_iterations)
        failures = Counter(failure for row in rows for failure in row.get("failure_types", []))
        summaries.append(dict(zip(group_by, key), n_runs=len(rows),
                              n_samples=len({r["sample_id"] for r in rows}), metrics=metrics,
                              failure_counts=dict(failures)))
    return summaries


def paired_success_deltas(results: list[Any], *, proposed: str = "proposed",
                          group_by: tuple[str, ...] = ("context_token_budget", "horizon_turns"),
                          bootstrap_iterations: int = 2000, seed: int = 42) -> list[dict]:
    """RQ5: pair sample/repeat IDs, bootstrap entire samples, report unmatched runs."""
    if bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be >= 100")
    groups = defaultdict(lambda: defaultdict(dict))
    for result in results:
        row = result.to_dict() if isinstance(result, EvaluationResult) else _record(result)
        cell = tuple(row.get(key) for key in group_by)
        pair = (row["sample_id"], row.get("repeat_id", 0))
        bucket = groups[cell][row["strategy"]]
        if pair in bucket:
            raise ValueError(f"duplicate sample/repeat within strategy and cell: {pair}")
        bucket[pair] = float(row["task_success"])
    rng = random.Random(seed)
    output = []
    for cell, strategies in groups.items():
        if proposed not in strategies:
            continue
        base = strategies[proposed]
        for strategy, values in strategies.items():
            if strategy == proposed:
                continue
            pairs = sorted(base.keys() & values.keys())
            clusters = defaultdict(list)
            for pair in pairs:
                clusters[pair[0]].append(base[pair] - values[pair])
            output.append(dict(zip(group_by, cell), baseline=strategy,
                               delta_success=_cluster_mean_ci(list(clusters.values()), rng, bootstrap_iterations),
                               n_pairs=len(pairs), unmatched_proposed=len(base.keys() - values.keys()),
                               unmatched_baseline=len(values.keys() - base.keys())))
    return output


def load_config(path: str | Path) -> dict:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    grid = config["grid"]
    if not grid or any(not isinstance(v, list) or not v for v in grid.values()):
        raise ValueError("grid axes must be non-empty lists")
    for key in ("strategies", "seeds"):
        if not config.get(key) or len(set(config[key])) != len(config[key]):
            raise ValueError(f"{key} must be non-empty and unique")
    if type(config["repeats"]) is not int or config["repeats"] < 1:
        raise ValueError("repeats must be a positive integer")
    if any(type(seed) is not int or seed < 0 for seed in config["seeds"]):
        raise ValueError("seeds must be non-negative integers")
    for axis in ("context_token_budget", "history_length_tokens", "horizon_turns",
                 "constraint_count", "state_update_count"):
        if axis in grid and any(type(value) is not int or value < 0 for value in grid[axis]):
            raise ValueError(f"{axis} must contain non-negative integers")
    if "context_token_budget" not in grid or any(value == 0 for value in grid["context_token_budget"]):
        raise ValueError("context_token_budget must contain positive integers")
    if any(not isinstance(value, (int, float)) or not 0 <= value <= 1
           for value in grid.get("noise_memory_ratio", [])):
        raise ValueError("noise_memory_ratio must be between 0 and 1")
    if type(config["dataset"]["samples_per_cell"]) is not int or config["dataset"]["samples_per_cell"] < 1:
        raise ValueError("samples_per_cell must be a positive integer")
    if config["evaluation"]["bootstrap_iterations"] < 100:
        raise ValueError("bootstrap_iterations must be >= 100")
    if config["evaluation"]["retrieval_k"] < 1:
        raise ValueError("retrieval_k must be positive")
    return config


def expand_config(config: dict) -> list[dict]:
    """Produce planned cells; this does not generate scenarios or call an LLM."""
    keys = list(config["grid"])
    return [dict(zip(keys, values), strategy=strategy, dataset_seed=seed, repeat_id=repeat)
            for values in itertools.product(*(config["grid"][key] for key in keys))
            for seed in config["seeds"] for repeat in range(config["repeats"])
            for strategy in config["strategies"]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--input", help="JSONL rows containing scenario and run_result")
    parser.add_argument("--output", required=True, help="output directory")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config)
    out = Path(args.output)
    if args.plan_only:
        out.mkdir(parents=True, exist_ok=True)
        (out / "plan.json").write_text(json.dumps(expand_config(config), ensure_ascii=False, indent=2), encoding="utf-8")
        return
    if not args.input:
        parser.error("--input is required unless --plan-only is set")
    results = []
    for line_number, line in enumerate(Path(args.input).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            result = evaluate(row["scenario"], row["run_result"], strategy=row.get("strategy"),
                              repeat_id=row.get("repeat_id", 0), k=config["evaluation"]["retrieval_k"])
            if result.strategy not in config["strategies"]:
                raise ValueError("strategy is absent from config")
            for key, values in config["grid"].items():
                if result.to_dict().get(key) not in values:
                    raise ValueError(f"result {key} is absent from config grid")
            if not 0 <= result.repeat_id < config["repeats"]:
                raise ValueError("repeat_id is absent from config")
            results.append(result)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"input line {line_number}: {exc}") from exc
    options = dict(bootstrap_iterations=config["evaluation"]["bootstrap_iterations"],
                   seed=config["evaluation"]["bootstrap_seed"])
    summary = aggregate_results(results, group_by=tuple(config["evaluation"]["group_by"]), **options)
    deltas = paired_success_deltas(results, group_by=tuple(config["evaluation"]["delta_group_by"]), **options)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluations.jsonl").write_text("".join(json.dumps(r.to_dict(), ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
    for filename, value in (("summary.json", summary), ("deltas.json", deltas), ("config.json", config)):
        (out / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
