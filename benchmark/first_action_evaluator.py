from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

FULL = "proposed"
NO_PACKING = "proposed/no_budget_aware_packing"
STRATEGIES = (FULL, NO_PACKING)
CONDITIONS = ("A", "B", "C", "D")
FAMILIES = ("file_lookup", "approved_email")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _first_step(run: Mapping[str, Any]) -> tuple[Mapping, Mapping, Mapping, str]:
    """첫 단계만 사용한다. 후속 단계의 복구를 첫 행동 성공으로 세지 않는다."""
    steps = run.get("steps") or []
    if not isinstance(steps, list):
        raise ValueError("run_result.steps must be a list")
    if steps:
        step = _mapping(steps[0], "first step")
        return (
            _mapping(step.get("selection_result") or {}, "first selection"),
            _mapping(step.get("context_result") or {}, "first context"),
            _mapping(step.get("action_result") or {}, "first action"),
            "steps[0]",
        )
    # 과거 단일 행동 실행 기록과의 호환성
    return (
        _mapping(run.get("selection_result") or {}, "selection_result"),
        _mapping(run.get("context_result") or {}, "context_result"),
        {"tool_calls": run.get("tool_calls") or [],
         "tool_results": run.get("tool_results") or []},
        "run_result_fallback",
    )


def _first(values: Any, label: str) -> dict[str, Any] | None:
    if not isinstance(values, list):
        raise ValueError(f"{label} must be an array")
    if not values:
        return None
    return dict(_mapping(values[0], f"{label}[0]"))


def _ids(container: Mapping, key: str) -> set[str]:
    value = container.get(key) or []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"{key} must be an array of strings")
    return set(value)


def _effect(expected: Mapping, actual: Mapping | None,
            result: Mapping | None, args_match: bool) -> tuple[bool, str]:
    """Tool 호출의 성공과 실제 Task 효과를 구분한다."""
    if actual is None:
        return False, "no_tool_call"
    if not args_match:
        return False, "call_mismatch"
    if result is None:
        return False, "tool_result_missing"
    if (result.get("tool_name") != actual.get("tool_name") or
            result.get("action") != actual.get("action")):
        return False, "tool_result_action_mismatch"
    if result.get("success") is not True:
        return False, "tool_execution_failed"
    output = result.get("output")
    if not isinstance(output, dict):
        return False, "tool_output_missing"

    tool = (expected["tool_name"], expected["action"])
    args = expected["arguments"]
    if tool == ("file", "find_file"):
        if output.get("found") is True and output.get("filename") == args["filename"]:
            return True, "verified_file_found"
        return False, "file_not_found_or_wrong_file"
    if tool == ("email", "send_email"):
        if (output.get("authorized_to_send") is True
                and output.get("approved") is True
                and output.get("status") == "granted"
                and output.get("approval_id") == args["approval_id"]
                and output.get("recipient") == args["recipient"]
                and output.get("attachment") == args["attachment"]):
            return True, "verified_authorized_send"
        return False, "email_not_authorized_or_mismatched"
    raise ValueError(f"unsupported expected tool/action: {tool}")


def evaluate_first_action(item: Mapping[str, Any]) -> dict[str, Any]:
    """실행 1건을 평가한다. goal_completed는 첫 행동 성공 기준에서 제외."""
    scenario = _mapping(item.get("scenario"), "scenario")
    run = _mapping(item.get("run_result"), "run_result")
    meta = _mapping(scenario.get("metadata"), "scenario.metadata")

    sid = scenario.get("scenario_id")
    strategy = item.get("strategy")
    repeat = item.get("repeat_id")
    family = meta.get("task_family")
    condition = meta.get("stress_condition")
    base_id = meta.get("base_case_id")
    if not isinstance(sid, str) or not sid or not isinstance(base_id, str) or not base_id:
        raise ValueError("scenario_id and base_case_id required")
    if strategy not in STRATEGIES or type(repeat) is not int or repeat < 0:
        raise ValueError("unsupported strategy or repeat_id")
    if family not in FAMILIES or condition not in CONDITIONS:
        raise ValueError(f"unsupported family/condition: {family}/{condition}")
    if meta.get("dataset_version") != "critical_flexible_v1":
        raise ValueError("expected critical_flexible_v1 dataset")

    expected_calls = scenario.get("expected_tool_calls")
    if not isinstance(expected_calls, list) or len(expected_calls) != 1:
        raise ValueError("exactly one expected first tool call required")
    expected = _mapping(expected_calls[0], "expected_tool_calls[0]")
    expected_args = _mapping(expected.get("arguments"), "expected arguments")
    if (expected.get("tool_name"), expected.get("action")) not in (
        ("file", "find_file"), ("email", "send_email")
    ):
        raise ValueError("unsupported expected first action")
    if family == "file_lookup" and expected.get("action") != "find_file":
        raise ValueError("family/expected action mismatch")
    if family == "approved_email" and expected.get("action") != "send_email":
        raise ValueError("family/expected action mismatch")

    critical_ids = meta.get("critical_flexible_memory_ids")
    if not isinstance(critical_ids, list) or len(critical_ids) != 1 or not isinstance(critical_ids[0], str):
        raise ValueError("exactly one critical memory ID required")
    cid = critical_ids[0]
    selection, context, first_action, trace_source = _first_step(run)
    actual = _first(first_action.get("tool_calls") or [], "first tool_calls")
    result = _first(first_action.get("tool_results") or [], "first tool_results")

    # 메일의 subject/body 등 추가 인자는 허용한다.
    actual_args = _mapping(actual.get("arguments") or {}, "actual arguments") if actual else {}
    same_tool = bool(actual and actual.get("tool_name") == expected["tool_name"])
    same_action = bool(same_tool and actual.get("action") == expected["action"])
    missing_args = sorted(k for k in expected_args if k not in actual_args)
    wrong_args = sorted(k for k in expected_args if k in actual_args and actual_args[k] != expected_args[k])
    call_correct = bool(same_action and not missing_args and not wrong_args)
    effect_ok, effect_reason = _effect(expected, actual, result, call_correct)
    first_correct = bool(run.get("status") == "ok" and call_correct and effect_ok)

    in_context = cid in _ids(context, "selected_memory_ids")
    in_selector = cid in _ids(selection, "selected_memory_ids")
    retrieved = cid in _ids(selection, "retrieved_memory_ids")
    if first_correct:
        failure = "ok"
    elif run.get("status") != "ok":
        failure = "pipeline_failure"
    elif actual is None:
        failure = "no_first_tool_call"
    elif not same_tool:
        failure = "wrong_tool"
    elif not same_action:
        failure = "wrong_action"
    elif missing_args:
        failure = "missing_required_argument"
    elif wrong_args:
        failure = "wrong_required_argument"
    else:
        failure = effect_reason

    audit = _mapping(run.get("prompt_token_audit") or {}, "prompt_token_audit")
    return {
        "scenario_id": sid,
        "base_case_id": base_id,
        "seed": meta.get("seed"),
        "task_family": family,
        "condition": condition,
        "strategy": strategy,
        "repeat_id": repeat,
        "context_budget": scenario.get("context_budget"),
        "trace_source": trace_source,
        "pipeline_status": run.get("status"),
        "stopped_reason": run.get("stopped_reason"),
        "goal_completed": run.get("goal_completed"),
        "critical_id": cid,
        "critical_retrieved": retrieved,
        "critical_in_selector": in_selector,
        "critical_in_context": in_context,
        "expected_tool": expected["tool_name"],
        "expected_action": expected["action"],
        "expected_arguments": dict(expected_args),
        "actual_tool": actual.get("tool_name") if actual else None,
        "actual_action": actual.get("action") if actual else None,
        "actual_arguments": dict(actual_args),
        "missing_arguments": missing_args,
        "wrong_arguments": wrong_args,
        "same_tool": same_tool,
        "same_action": same_action,
        "first_action_call_correct": call_correct,
        "first_action_effect_verified": effect_ok,
        "first_action_correct": first_correct,
        "tool_result_success": result.get("success") if result else None,
        "tool_result_output": result.get("output") if result else None,
        "tool_effect_reason": effect_reason,
        "failure_reason": failure,
        "actual_budget_exceeded": audit.get("actual_budget_exceeded"),
        "prompt_budget_verified": audit.get("complete") is True,
    }


def make_pairs(details: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """동일 Scenario/Repeat의 두 전략만 비교하고 누락·중복은 거부한다."""
    groups: dict[tuple[str, int], dict[str, dict]] = defaultdict(dict)
    for r in details:
        key = (r["scenario_id"], r["repeat_id"])
        if r["strategy"] in groups[key]:
            raise ValueError(f"duplicate pair arm: {key} {r['strategy']}")
        groups[key][r["strategy"]] = r
    pairs = []
    for (sid, repeat), arms in sorted(groups.items()):
        if set(arms) != set(STRATEGIES):
            raise ValueError(f"unmatched strategy pair: {(sid, repeat)}")
        a, b = arms[FULL], arms[NO_PACKING]
        for key in ("base_case_id", "task_family", "condition", "context_budget", "expected_tool", "expected_action", "expected_arguments"):
            if a[key] != b[key]:
                raise ValueError(f"paired inputs differ: {sid}, {key}")
        pairs.append({
            "scenario_id": sid,
            "repeat_id": repeat,
            "base_case_id": a["base_case_id"],
            "task_family": a["task_family"],
            "condition": a["condition"],
            "context_budget": a["context_budget"],
            "full_critical_in_context": a["critical_in_context"],
            "no_packing_critical_in_context": b["critical_in_context"],
            "full_call_correct": a["first_action_call_correct"],
            "no_packing_call_correct": b["first_action_call_correct"],
            "full_first_action_correct": a["first_action_correct"],
            "no_packing_first_action_correct": b["first_action_correct"],
            "delta_full_minus_no_packing": int(a["first_action_correct"]) - int(b["first_action_correct"]),
            "full_failure_reason": a["failure_reason"],
            "no_packing_failure_reason": b["failure_reason"],
        })
    return pairs


def _group_metrics(rows: Sequence[dict[str, Any]], pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    n = len(pairs)
    a = [r for r in rows if r["strategy"] == FULL]
    b = [r for r in rows if r["strategy"] == NO_PACKING]
    if len(a) != n or len(b) != n:
        raise ValueError("summary group must have exactly two arms per pair")
    count = lambda items, key: sum(bool(x[key]) for x in items)
    win_full = sum(p["delta_full_minus_no_packing"] == 1 for p in pairs)
    win_base = sum(p["delta_full_minus_no_packing"] == -1 for p in pairs)
    return {
        "n_pairs": n,
        "n_independent_base_cases": len({p["base_case_id"] for p in pairs}),
        "full": {
            "first_action_correct": count(a, "first_action_correct"),
            "first_action_call_correct": count(a, "first_action_call_correct"),
            "effect_verified": count(a, "first_action_effect_verified"),
            "critical_in_context": count(a, "critical_in_context"),
            "first_action_accuracy": count(a, "first_action_correct") / n if n else None,
        },
        "no_packing": {
            "first_action_correct": count(b, "first_action_correct"),
            "first_action_call_correct": count(b, "first_action_call_correct"),
            "effect_verified": count(b, "first_action_effect_verified"),
            "critical_in_context": count(b, "critical_in_context"),
            "first_action_accuracy": count(b, "first_action_correct") / n if n else None,
        },
        "paired": {
            "both_correct": sum(p["full_first_action_correct"] and p["no_packing_first_action_correct"] for p in pairs),
            "both_wrong": sum(not p["full_first_action_correct"] and not p["no_packing_first_action_correct"] for p in pairs),
            "full_only_correct": win_full,
            "no_packing_only_correct": win_base,
            "delta_full_minus_no_packing": sum(p["delta_full_minus_no_packing"] for p in pairs) / n if n else None,
        },
    }


def summarize(details: Sequence[dict[str, Any]], pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    def group(*, condition=None, family=None):
        subset = [r for r in details if (condition is None or r["condition"] == condition)
                  and (family is None or r["task_family"] == family)]
        selected = [p for p in pairs if (condition is None or p["condition"] == condition)
                    and (family is None or p["task_family"] == family)]
        return _group_metrics(subset, selected)
    return {
        "metric_definition": "first_action_correct = first tool/action and all required arguments match AND task-specific tool effect is verified. Ignore final goal_completed for max_steps=1.",
        "overall": group(),
        "by_condition": {condition: group(condition=condition) for condition in CONDITIONS},
        "by_task_family": {family: group(family=family) for family in FAMILIES},
        "by_condition_and_family": {
            condition: {family: group(condition=condition, family=family) for family in FAMILIES}
            for condition in CONDITIONS
        },
        "failure_reasons": {strategy: dict(sorted(Counter(
            r["failure_reason"] for r in details if r["strategy"] == strategy
        ).items())) for strategy in STRATEGIES},
    }


def _csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot export an empty report")
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (dict, list)) else value
                             for key, value in row.items()})


def evaluate_file(input_file: str | Path, output_dir: str | Path,
                  expected_runs: int | None = None) -> dict[str, Any]:
    """짝이 완전한지 검증한 다음 기존 결과를 덮어쓰지 않고 저장한다."""
    path = Path(input_file)
    raw_rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if expected_runs is not None and len(raw_rows) != expected_runs:
        raise ValueError(f"expected {expected_runs} runs, got {len(raw_rows)}")
    if not raw_rows:
        raise ValueError("runs.jsonl is empty")
    details = [evaluate_first_action(r) for r in raw_rows]
    pairs = make_pairs(details)
    summary = summarize(details, pairs)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=False)
    (output / "first_action_details.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in details), encoding="utf-8")
    _csv(output / "paired_first_action.csv", pairs)
    failures = [r for r in details if not r["first_action_correct"]]
    _csv(output / "failure_details.csv", failures) if failures else (
        output / "failure_details.csv").write_text("scenario_id,strategy,repeat_id,failure_reason\n", encoding="utf-8-sig")
    (output / "first_action_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    validation = {
        "input_path": str(path),
        "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "n_runs": len(raw_rows),
        "n_pairs": len(pairs),
        "dataset_versions": sorted({r["scenario"]["metadata"]["dataset_version"] for r in raw_rows}),
        "git_commits": sorted({r.get("git_commit") for r in raw_rows if r.get("git_commit")}),
        "prompt_budget_verified": sum(r["prompt_budget_verified"] for r in details),
        "actual_budget_overflow": sum(r["actual_budget_exceeded"] is True for r in details),
        "n_steps_other_than_one": sum(len(r["run_result"].get("steps") or []) != 1 for r in raw_rows),
        "note": "First-action scores are derived from recorded first-step traces, not overall task_success.",
    }
    (output / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Runner runs.jsonl")
    parser.add_argument("--output", required=True, help="New output directory; never overwrite")
    parser.add_argument("--expected-runs", type=int, default=None)
    args = parser.parse_args()
    summary = evaluate_file(args.input, args.output, args.expected_runs)
    overall = summary["overall"]
    print(f"[PASS] {overall['n_pairs']} paired first-action comparisons")
    for cond in CONDITIONS:
        group = summary["by_condition"][cond]
        print(f"{cond}: full={group['full']['first_action_correct']}/{group['n_pairs']}; "
              f"no_packing={group['no_packing']['first_action_correct']}/{group['n_pairs']}")
    print(f"Saved: {args.output}")


if __name__ == "__main__":
    main()
