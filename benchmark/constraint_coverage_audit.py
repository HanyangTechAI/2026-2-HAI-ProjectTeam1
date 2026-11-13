"""Offline constraint coverage audit for generator cells; never calls an LLM.

Example::

    python -m benchmark.constraint_coverage_audit \
        --dataset benchmark/generated/main_gemini_3_1_flash_lite.zip \
        --validation-file docs/results/ablation_2k_h100_20261111/validation.json

The evaluator controls are artificial tool traces, not observed agent outcomes.
Only Python's standard library and the existing offline evaluator are used.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any
import zipfile

from benchmark.evaluator import evaluate


DEFAULT_OUTPUT = "docs/results/constraint_coverage_20261113"
PDF_FILENAME = re.compile(r"[A-Za-z0-9_.-]+\.pdf")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _select_cell(manifest: dict[str, Any], budget: int, horizon: int) -> dict[str, Any]:
    matches = [
        cell for cell in manifest["cells"]
        if cell["parameters"]["context_token_budget"] == budget
        and cell["parameters"]["horizon_turns"] == horizon
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly one cell for budget={budget}, horizon={horizon}; "
            f"found {len(matches)}. Supply a single cell JSON to disambiguate."
        )
    return matches[0]


def load_cell(dataset: Path, budget: int, horizon: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """Read a ZIP, manifest directory/file or cell without extracting an archive."""
    source = dataset / "manifest.json" if dataset.is_dir() else dataset
    source_bytes = source.read_bytes()
    provenance: dict[str, Any] = {
        "dataset_path": dataset.as_posix(),
        "source_path": source.as_posix(),
        "source_sha256": _sha256(source_bytes),
    }
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            manifests = [name for name in archive.namelist() if PurePosixPath(name).name == "manifest.json"]
            if len(manifests) != 1:
                raise ValueError(f"Expected exactly one manifest.json in ZIP; found {len(manifests)}")
            manifest_name = manifests[0]
            manifest_bytes = archive.read(manifest_name)
            cell_meta = _select_cell(json.loads(manifest_bytes), budget, horizon)
            cell_name = str(PurePosixPath(manifest_name).parent / cell_meta["path"])
            cell_bytes = archive.read(cell_name)
            provenance.update(manifest_path=manifest_name, manifest_sha256=_sha256(manifest_bytes), cell_path=cell_name)
    else:
        payload = json.loads(source_bytes)
        if "cells" in payload:
            cell_meta = _select_cell(payload, budget, horizon)
            cell_path = source.parent / cell_meta["path"]
            cell_bytes = cell_path.read_bytes()
            provenance.update(manifest_path=source.as_posix(), manifest_sha256=_sha256(source_bytes), cell_path=cell_path.as_posix())
        else:
            cell_bytes = source_bytes
            provenance["cell_path"] = source.as_posix()
    cell = json.loads(cell_bytes)
    parameters = cell.get("parameters", {})
    if parameters.get("context_token_budget") != budget or parameters.get("horizon_turns") != horizon:
        raise ValueError("Selected cell parameters do not match requested budget/horizon")
    provenance["cell_sha256"] = _sha256(cell_bytes)
    return cell, provenance


def _subtype(scenario: dict[str, Any]) -> str:
    metadata = scenario["metadata"]
    variant = metadata.get("approval_status") or metadata.get("state_scope")
    return f"{metadata['task_type']}/{variant}"


def _run(scenario: dict[str, Any], calls: list[dict[str, Any]]) -> dict[str, Any]:
    """Artificial compliant execution with all constraint memories omitted."""
    return {
        "strategy": "proposed", "status": "ok", "goal_completed": True,
        "tool_calls": calls,
        "selected_memory_ids": list(scenario["required_state_ids"]),
        "retrieved_memory_ids": list(scenario["required_state_ids"]),
        "usage": {"input_tokens": 0},
    }


def _synthetic_controls(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, Counter] = defaultdict(Counter)
    for scenario in scenarios:
        group = groups[_subtype(scenario)]
        group["n_scenarios"] += 1
        oracle = copy.deepcopy(scenario["expected_tool_calls"])
        rule_removed = copy.deepcopy(scenario)
        rule_removed["constraint_rules"] = []
        rule_removed["applicable_constraint_ids"] = []
        for label, ground_truth in (("original_gt", scenario), ("rule_gt_removed", rule_removed)):
            group[f"{label}_oracle_success"] += evaluate(ground_truth, _run(scenario, oracle)).task_success
        if scenario["metadata"]["task_type"] != "approval":
            continue
        bad_send = copy.deepcopy(oracle[0])
        bad_send["action"] = "send_email"
        bad_send["arguments"].pop("approval_id", None)
        for label, ground_truth in (("original_gt", scenario), ("rule_gt_removed", rule_removed)):
            for control_name, calls in (("unapproved_send_only", [bad_send]), ("oracle_plus_unapproved_send", oracle + [bad_send])):
                result = evaluate(ground_truth, _run(scenario, calls)).to_dict()
                group[f"{label}_{control_name}_success"] += result["task_success"]
                group[f"{label}_{control_name}_constraint_violation"] += result["constraint_violation"]
                group[f"{label}_{control_name}_expected_missing"] += not result["expected_tool_calls_present"]
                group[f"{label}_{control_name}_forbidden_hit"] += result["forbidden_tool_call_used"]
    return {
        "kind": "synthetic_evaluator_controls_not_llm_results",
        "description": (
            "Artificial tool traces with goal_completed=True and input_tokens=0. "
            "Only required state IDs are selected. Rule-GT removal is a diagnostic "
            "of overlapping evaluation checks, never a valid memory ablation."
        ),
        "by_subtype": dict(sorted(groups.items())),
    }


def audit(cell: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    scenarios = cell["scenarios"]
    sample_ids = [scenario["sample_id"] for scenario in scenarios]
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("Cell contains repeated sample IDs; cannot count independent scenarios")
    if not scenarios:
        raise ValueError("Cell contains no scenarios")
    rows = []
    for scenario in scenarios:
        memories = scenario["memory_snapshot"]
        constraints = [memory for memory in memories if memory["memory_type"] == "constraint"]
        stored_ids = {memory["memory_id"] for memory in constraints}
        applicable_ids = set(scenario["applicable_constraint_ids"])
        if applicable_ids != {rule["constraint_id"] for rule in scenario["constraint_rules"]}:
            raise ValueError(f"Constraint ground truth mismatch: {scenario['sample_id']}")
        if not applicable_ids <= stored_ids:
            raise ValueError(f"Applicable constraint absent from memory: {scenario['sample_id']}")
        grants = [
            memory for memory in memories
            if memory["memory_type"] == "fact"
            and (memory["memory_id"].endswith("_grant") or "승인 번호" in memory["content"])
        ]
        prohibited_filenames = {
            filename for memory in constraints if "금지" in memory["content"]
            for filename in PDF_FILENAME.findall(memory["content"])
        }
        expected_values = {
            state["expected_value"] for state in scenario["required_states"]
            if isinstance(state["expected_value"], str)
        }
        collisions = prohibited_filenames & expected_values
        required_state_ids = set(scenario["required_state_ids"])
        visible_candidates = {
            filename for memory in memories if memory["memory_id"] in required_state_ids
            for filename in PDF_FILENAME.findall(memory["content"])
        }
        candidate_bound_rules = [
            rule["constraint_id"] for rule in scenario["constraint_rules"]
            if any(pattern.get("arguments", {}).get(argument) in visible_candidates
                   for pattern in rule.get("forbidden_tool_calls", [])
                   for argument in ("filename", "attachment"))
        ]
        rows.append({
            "sample_id": scenario["sample_id"], "subtype": _subtype(scenario),
            "scenario_profile": scenario["metadata"].get("scenario_profile", "legacy_v1"),
            "stored_constraint_count": len(constraints),
            "applicable_constraint_count": len(applicable_ids),
            "dormant_constraint_count": len(stored_ids - applicable_ids),
            "applicable_constraint_ids": sorted(applicable_ids),
            "dormant_constraint_ids": sorted(stored_ids - applicable_ids),
            "explicit_grant_fact_count": len(grants),
            "explicit_grant_fact_ids": [memory["memory_id"] for memory in grants],
            "filename_prohibition_count": len(prohibited_filenames),
            "prohibited_filenames": sorted(prohibited_filenames),
            "required_state_expected_values": sorted(expected_values),
            "filename_prohibition_collisions": sorted(collisions),
            "filename_prohibition_collision_count": len(collisions),
            "rules_restricting_visible_candidates_count": len(candidate_bound_rules),
            "rules_restricting_visible_candidates_ids": candidate_bound_rules,
            "approval_rule_count": sum(bool(rule.get("approval")) for rule in scenario["constraint_rules"]),
            "top_level_forbidden_call_count": len(scenario.get("forbidden_tool_calls", [])),
            "expected_actions": [f"{call['tool_name']}.{call['action']}" for call in scenario["expected_tool_calls"]],
        })
    n = len(rows)
    stored = sum(row["stored_constraint_count"] for row in rows)
    applicable = sum(row["applicable_constraint_count"] for row in rows)
    dormant = sum(row["dormant_constraint_count"] for row in rows)
    assert stored == applicable + dormant
    applicable_scenarios = sum(row["applicable_constraint_count"] > 0 for row in rows)
    subtype_counts = Counter(row["subtype"] for row in rows)
    summary = {
        "parameters": cell["parameters"],
        "n_independent_scenarios": n,
        "subtype_counts": dict(sorted(subtype_counts.items())),
        "scenario_profiles": dict(sorted(Counter(row["scenario_profile"] for row in rows).items())),
        "subtype_ratios": {key: count / n for key, count in sorted(subtype_counts.items())},
        "stored_constraint_memory_count": stored,
        "applicable_constraint_memory_count": applicable,
        "dormant_constraint_memory_count": dormant,
        "applicable_fraction_of_stored_constraints": applicable / stored if stored else None,
        "scenarios_with_stored_constraints": sum(row["stored_constraint_count"] > 0 for row in rows),
        "scenarios_with_applicable_constraints": applicable_scenarios,
        "applicable_scenario_ratio": applicable_scenarios / n,
        "scenarios_without_applicable_constraints": n - applicable_scenarios,
        "explicit_grant_fact_count": sum(row["explicit_grant_fact_count"] for row in rows),
        "scenarios_with_explicit_grant_fact": sum(row["explicit_grant_fact_count"] > 0 for row in rows),
        "scenarios_with_applicable_constraint_without_explicit_grant_fact": sum(
            row["applicable_constraint_count"] > 0 and row["explicit_grant_fact_count"] == 0 for row in rows
        ),
        "filename_prohibition_count": sum(row["filename_prohibition_count"] for row in rows),
        "filename_prohibition_collision_count": sum(row["filename_prohibition_collision_count"] for row in rows),
        "scenarios_with_filename_prohibition_collision": sum(row["filename_prohibition_collision_count"] > 0 for row in rows),
        "rules_restricting_visible_candidates_count": sum(row["rules_restricting_visible_candidates_count"] for row in rows),
        "approval_rule_count": sum(row["approval_rule_count"] for row in rows),
        "definitions": {
            "dormant": "Stored constraint memory IDs absent from applicable_constraint_ids; not a proof of semantic irrelevance in arbitrary inputs.",
            "explicit_grant_fact": "Generator fact memory with _grant ID suffix or the literal Korean phrase for approval number.",
            "filename_collision": "PDF filename in a prohibition constraint equals a required_states.expected_value; lexical generator-schema check only.",
            "visible_candidate_rule": "Forbidden filename/attachment in evaluator rules is present in the required state; supports numbered candidate policies without literal filenames in constraint text.",
            "independent_scenario": "Unique sample_id within the one selected cell; budget/horizon variants and execution repeats are not pooled.",
        },
        "synthetic_evaluator_controls": _synthetic_controls(scenarios),
    }
    return summary, rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=2048)
    parser.add_argument("--horizon", type=int, default=100)
    parser.add_argument("--output-dir", type=Path, default=Path(DEFAULT_OUTPUT))
    parser.add_argument("--validation-file", type=Path)
    args = parser.parse_args()
    cell, provenance = load_cell(args.dataset, args.budget, args.horizon)
    if args.validation_file:
        validation_bytes = args.validation_file.read_bytes()
        validation = json.loads(validation_bytes)
        expected_hash = validation["dataset_cell_sha256"]
        if provenance["cell_sha256"] != expected_hash:
            raise ValueError("Dataset cell SHA-256 does not match supplied ablation validation file")
        provenance["ablation_validation"] = {
            "path": args.validation_file.as_posix(),
            "sha256": _sha256(validation_bytes),
            "dataset_cell_sha256": expected_hash,
            "cell_hash_matches": True,
        }
    summary, rows = audit(cell)
    summary = {"audit_version": 2, "execution_scope": "offline_no_llm_no_network", "provenance": provenance, **summary}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "details.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else value for key, value in row.items()} for row in rows)
    print(json.dumps({
        "output_dir": args.output_dir.as_posix(),
        "cell_sha256": provenance["cell_sha256"],
        "n_independent_scenarios": summary["n_independent_scenarios"],
        "subtype_counts": summary["subtype_counts"],
        "stored_constraints": summary["stored_constraint_memory_count"],
        "applicable_constraints": summary["applicable_constraint_memory_count"],
        "applicable_scenario_ratio": summary["applicable_scenario_ratio"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
