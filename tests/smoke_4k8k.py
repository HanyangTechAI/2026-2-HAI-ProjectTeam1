"""Real Gemini 4K/8K Smoke gate for HAI ProjectTeam1.

Place in the repo as ``tests/smoke_4k8k.py`` and run from the repo root::

    python tests/smoke_4k8k.py prepare
    python tests/smoke_4k8k.py run
    python tests/smoke_4k8k.py run --resume
    python tests/smoke_4k8k.py report

Actual Gemini requests require GEMINI_API_KEY in the local environment.
Uses frozen main.json and the existing verified main dataset ZIP. Does not
alter the archive, frozen config, or full Main result directories.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "benchmark/generated/main_gemini_3_1_flash_lite.zip"
CONFIG = ROOT / "experiments/configs/main.json"
DATA_DIR = ROOT / "benchmark/generated/smoke_4k8k"
RESULT_ROOT = ROOT / "experiments/results/smoke_4k8k"
BUDGETS = (4096, 8192)
HORIZON = 50
SEEDS = (42, 43)
STRATEGIES = ("proposed", "sliding_window", "vector_top_k", "utility_per_token")
SAMPLE_IDENTITIES = (
    ("temporal_update", 0, "current"),
    ("temporal_update", 5, "historical"),
    ("approval", 10, "pending"),
    ("approval", 11, "granted"),
)
SAMPLE_IDS = frozenset(
    f"seed{seed}_{kind}_{index:03d}_{variant}"
    for seed in SEEDS
    for kind, index, variant in SAMPLE_IDENTITIES
)
EXPECTED_RUNS_PER_BUDGET = len(SAMPLE_IDS) * len(STRATEGIES)


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT))


def data_path(budget: int) -> Path:
    return DATA_DIR / f"smoke_{budget}.json"


def output_path(budget: int) -> Path:
    return RESULT_ROOT / f"budget_{budget}"


def check_frozen_config(config: dict) -> int:
    if config.get("scope") != "main" or config.get("executable") is not True:
        raise ValueError("Expected executable frozen main config")
    if config["model"]["name"] != "gemini-3.1-flash-lite":
        raise ValueError("Unexpected LLM model in main config")
    if config["runtime"]["token_counter"]["model"] != config["model"]["name"]:
        raise ValueError("Gemini CountTokens and generation model differ")
    if config["model"]["max_output_tokens"] != 2048 or config["model"]["thinking_level"] != "LOW":
        raise ValueError("Frozen model output/thinking config changed")
    if config["runtime"]["max_steps"] != 8:
        raise ValueError("Frozen max_steps changed")
    if tuple(config["strategies"]) != STRATEGIES:
        raise ValueError("Frozen strategies differ from Smoke expectations")
    margin = config["budget_policy"].get("request_token_safety_margin")
    if type(margin) is not int or not 0 <= margin < min(BUDGETS):
        raise ValueError(f"Invalid frozen safety margin: {margin!r}")
    return margin


def prepare() -> None:
    config = read_json(CONFIG)
    margin = check_frozen_config(config)
    if not ARCHIVE.is_file():
        raise FileNotFoundError(f"Missing {ARCHIVE}; obtain verified Gemini 3.1 Main ZIP")

    selected_by_budget: dict[int, dict[str, dict]] = {}
    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("token_count_mode") != "gemini:gemini-3.1-flash-lite":
            raise ValueError("Main archive has wrong token_count_mode")
        if manifest.get("config", {}).get("model", {}).get("name") != config["model"]["name"]:
            raise ValueError("Main archive model mismatch")
        if manifest.get("n_cells") != 12 or manifest.get("n_scenarios") != 480:
            raise ValueError("Main archive is not the expected 12-cell / 480-scenario dataset")

        generation_freeze = manifest.get("config", {}).get("freeze", {}).get("version")
        execution_freeze = config.get("freeze", {}).get("version")
        if generation_freeze != execution_freeze:
            print(
                "NOTICE: dataset generation freeze and execution freeze differ: "
                f"{generation_freeze} -> {execution_freeze}. "
                f"The {execution_freeze} execution freeze requires compatibility review."
            )

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        for budget in BUDGETS:
            cells = [
                cell for cell in manifest["cells"]
                if cell["parameters"].get("context_token_budget") == budget
                and cell["parameters"].get("horizon_turns") == HORIZON
            ]
            if len(cells) != 1:
                raise ValueError(f"Expected exactly one cell for budget={budget} H={HORIZON}")

            payload = json.loads(archive.read(cells[0]["path"]))
            if payload.get("token_count_mode") != manifest["token_count_mode"]:
                raise ValueError(f"Cell {budget} token_count_mode mismatch")
            scenarios = [s for s in payload["scenarios"] if s["scenario_id"] in SAMPLE_IDS]
            by_id = {s["scenario_id"]: s for s in scenarios}
            if len(scenarios) != len(SAMPLE_IDS) or set(by_id) != SAMPLE_IDS:
                raise ValueError(
                    f"Budget {budget}: missing/duplicate sample IDs: "
                    f"missing={sorted(SAMPLE_IDS - set(by_id))}"
                )
            for scenario in scenarios:
                if scenario["context_budget"] != budget:
                    raise ValueError(f"Budget mismatch for {scenario['scenario_id']}")
                if scenario.get("current_time") is None:
                    raise ValueError(f"Missing current_time for {scenario['scenario_id']}")
                if scenario.get("token_count_mode") != manifest["token_count_mode"]:
                    raise ValueError(f"Scenario token mode mismatch for {scenario['scenario_id']}")
                if not scenario.get("benchmark_enabled", True):
                    raise ValueError(f"Disabled benchmark scenario: {scenario['scenario_id']}")

            selected_by_budget[budget] = by_id
            selected_payload = dict(payload)
            selected_payload["scenarios"] = scenarios
            path = data_path(budget)
            path.write_text(
                json.dumps(selected_payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Prepared {relative(path)} ({len(scenarios)} scenarios)")

    left, right = (selected_by_budget[budget] for budget in BUDGETS)
    for sample_id in sorted(SAMPLE_IDS):
        for field in ("query", "memory_snapshot", "current_time", "current_turn"):
            if left[sample_id].get(field) != right[sample_id].get(field):
                raise ValueError(f"Unpaired sample content ({field}): {sample_id}")
    with ARCHIVE.open("rb") as stream:
        archive_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    with CONFIG.open("rb") as stream:
        config_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    manifest_record = {
        "purpose": "live Gemini 4K/8K smoke; not Main benchmark result",
        "archive_sha256": archive_sha256,
        "config_sha256": config_sha256,
        "generation_freeze": generation_freeze,
        "execution_freeze": execution_freeze,
        "model": config["model"]["name"],
        "horizon_turns": HORIZON,
        "budgets": list(BUDGETS),
        "nominal_margin": margin,
        "seed_values": list(SEEDS),
        "sample_ids": sorted(SAMPLE_IDS),
        "strategies": list(STRATEGIES),
        "repeats": 1,
        "expected_total_runs": EXPECTED_RUNS_PER_BUDGET * len(BUDGETS),
    }
    (DATA_DIR / "selection_manifest.json").write_text(
        json.dumps(manifest_record, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Paired inputs: PASS | margin={margin} | total expected runs={manifest_record['expected_total_runs']}")


def run(*, resume: bool) -> None:
    if not os.environ.get("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is required for live Gemini Smoke")
    check_frozen_config(read_json(CONFIG))
    if not (DATA_DIR / "selection_manifest.json").is_file():
        raise FileNotFoundError("Run 'prepare' first")
    for budget in BUDGETS:
        source = data_path(budget)
        destination = output_path(budget)
        if not source.is_file():
            raise FileNotFoundError(f"Missing {source}; run 'prepare' first")
        already_exists = any(
            (destination / name).exists()
            for name in ("experiment.json", "runs.jsonl", "progress.json", "summary.json")
        )
        if already_exists and not resume:
            raise FileExistsError(
                f"Output exists: {destination}. Use 'run --resume' with unchanged code/settings."
            )
        cmd = [
            sys.executable, "-m", "benchmark.runner",
            "--config", relative(CONFIG),
            "--scenarios", relative(source),
            "--environment-agent-factory", "benchmark.experiment_runtime:environment_agent",
            "--output", relative(destination),
            "--repeats", "1",
            "--checkpoint-interval", "1",
        ]
        if resume and already_exists:
            cmd.append("--resume")
        print(f"\n>>> Live Gemini Smoke budget={budget}, resume={resume and already_exists}", flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)
    print("\nBoth Smoke runs finished. Run: python tests/smoke_4k8k.py report")


def report() -> int:
    config = read_json(CONFIG)
    margin = check_frozen_config(config)
    issues: list[str] = []
    warnings: list[str] = []
    data: dict[str, dict] = {}
    embedding_revisions: set[str] = set()
    prompt_hashes: set[str] = set()
    all_positive_deltas: list[int] = []
    total_cost_incomplete = 0

    for budget in BUDGETS:
        folder = output_path(budget)
        required = [folder / name for name in ("runs.jsonl", "progress.json", "experiment.json")]
        missing = [p.name for p in required if not p.is_file()]
        if missing:
            issues.append(f"{budget}: missing output files: {missing}")
            continue

        rows = [json.loads(line) for line in (folder / "runs.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        progress = read_json(folder / "progress.json")
        experiment = read_json(folder / "experiment.json")
        expected_keys = {(sid, strategy, 0) for sid in SAMPLE_IDS for strategy in STRATEGIES}
        keys = [(row["scenario"]["scenario_id"], row["strategy"], row["repeat_id"]) for row in rows]
        if len(rows) != EXPECTED_RUNS_PER_BUDGET or set(keys) != expected_keys or len(set(keys)) != len(keys):
            issues.append(f"{budget}: incomplete/duplicate run keys ({len(rows)}/{EXPECTED_RUNS_PER_BUDGET})")
        if progress.get("state") != "completed" or progress.get("completed") != EXPECTED_RUNS_PER_BUDGET:
            issues.append(f"{budget}: checkpoint is not completed ({progress.get('state')})")

        tokenizer = experiment.get("tokenizer") or {}
        if tokenizer.get("model") != config["model"]["name"]:
            issues.append(f"{budget}: tokenizer model mismatch: {tokenizer.get('model')}")
        if experiment.get("request_token_safety_margin") != margin:
            issues.append(f"{budget}: configured margin mismatch")
        if not tokenizer.get("request_aware_budgeting"):
            issues.append(f"{budget}: CountTokens request-aware budgeting is disabled")
        if experiment.get("repeats") != 1:
            issues.append(f"{budget}: Smoke repeats must be 1")
        embedding = (experiment.get("runtime_environment") or {}).get("embedding") or {}
        revision = embedding.get("resolved_revision")
        if not isinstance(revision, str) or not revision:
            issues.append(f"{budget}: missing embedding resolved_revision")
        else:
            embedding_revisions.add(revision)
        checksums = experiment.get("prompt_checksums") or {}
        prompt_hashes.add(json.dumps(checksums, sort_keys=True))

        executed = []
        cost_incomplete = []
        unverified = []
        actual_overflow = []
        counted_overflow = []
        api_calls = 0
        prompt_calls = 0
        per_strategy = {strategy: 0 for strategy in STRATEGIES}
        status_counts: Counter[str] = Counter()
        max_counted = None
        max_actual = None
        deltas: list[int] = []

        for row in rows:
            run_result = row["run_result"]
            status_counts[str(run_result.get("status"))] += 1
            audit = run_result.get("prompt_token_audit") or {}
            llm = run_result.get("llm_usage") or {}
            if audit.get("counted_budget_exceeded") is True:
                counted_overflow.append(row["scenario"]["scenario_id"])
            if run_result.get("agent_executed") is not True:
                continue

            executed.append(row)
            per_strategy[row["strategy"]] += 1
            count = llm.get("api_calls")
            if type(count) is not int or count < 1:
                issues.append(f"{budget}: missing api_calls on {row['strategy']} {row['scenario']['scenario_id']}")
            else:
                api_calls += count
            if audit.get("complete") is not True:
                unverified.append((row["scenario"]["scenario_id"], row["strategy"]))
            if audit.get("actual_budget_exceeded") is True:
                actual_overflow.append((row["scenario"]["scenario_id"], row["strategy"]))
            if llm.get("cost_estimate_complete") is not True:
                cost_incomplete.append((row["scenario"]["scenario_id"], row["strategy"]))
                if run_result.get("total_llm_cost") is not None:
                    issues.append(f"{budget}: cost incomplete but non-null total_llm_cost")
            elif run_result.get("total_llm_cost") is None:
                issues.append(f"{budget}: complete cost but null total_llm_cost")

            prompt_calls += len(audit.get("actual_prompt_tokens_per_call") or [])
            for value in audit.get("delta_actual_minus_counted_per_call") or []:
                if type(value) is int:
                    deltas.append(value)
                    if value > 0:
                        all_positive_deltas.append(value)
            counted = audit.get("max_counted_prompt_tokens")
            actual = audit.get("max_actual_prompt_tokens")
            if type(counted) is int:
                max_counted = counted if max_counted is None else max(max_counted, counted)
            if type(actual) is int:
                max_actual = actual if max_actual is None else max(max_actual, actual)

        if unverified:
            issues.append(f"{budget}: unverified prompt audit ({len(unverified)} runs)")
        if actual_overflow:
            issues.append(f"{budget}: actual prompt budget overflow ({len(actual_overflow)} runs)")
        if counted_overflow:
            issues.append(f"{budget}: CountTokens effective-budget overflow ({len(counted_overflow)} runs)")
        if not executed:
            issues.append(f"{budget}: no live LLM calls were executed")
        for strategy, count in per_strategy.items():
            if count == 0:
                issues.append(f"{budget}: no real LLM execution for {strategy}")
        if cost_incomplete:
            warnings.append(f"{budget}: total cost is incomplete in {len(cost_incomplete)} runs")
        total_cost_incomplete += len(cost_incomplete)

        data[str(budget)] = {
            "expected_runs": EXPECTED_RUNS_PER_BUDGET,
            "completed_runs": len(rows),
            "llm_executed_runs": len(executed),
            "without_llm_runs": len(rows) - len(executed),
            "per_strategy_llm_executions": per_strategy,
            "total_api_attempts_in_completed_runs": api_calls,
            "actual_prompt_calls_observed": prompt_calls,
            "unverified_prompt_runs": len(unverified),
            "actual_budget_overflow_runs": len(actual_overflow),
            "counted_budget_overflow_runs": len(counted_overflow),
            "cost_incomplete_runs": len(cost_incomplete),
            "max_positive_delta": max([0, *deltas]),
            "max_counted_prompt_tokens": max_counted,
            "max_actual_prompt_tokens": max_actual,
            "pipeline_status_counts": dict(status_counts),
            "embedding_revision": revision,
        }
        print(f"\nBudget {budget} | runs={len(rows)}/{EXPECTED_RUNS_PER_BUDGET} | LLM runs={len(executed)}")
        print(f"  API attempts={api_calls} | prompt observations={prompt_calls} | max +delta={max([0,*deltas])}")
        print(f"  Prompt unverified={len(unverified)} | actual overflow={len(actual_overflow)} | counted overflow={len(counted_overflow)}")
        print(f"  Cost incomplete={len(cost_incomplete)} | strategy LLM runs={per_strategy}")
        print(f"  Pipeline statuses={dict(status_counts)}")

    if len(embedding_revisions) != 1:
        issues.append(f"Embedding revision inconsistency: {sorted(embedding_revisions)}")
    if len(prompt_hashes) != 1:
        issues.append("System/tool prompt checksum differs between 4K and 8K")
    observed_positive_delta = max([0, *all_positive_deltas])
    if observed_positive_delta > margin:
        issues.append(f"Maximum positive CountTokens delta {observed_positive_delta} exceeds margin {margin}")

    outcome = {
        "readiness": "PASS" if not issues else "HOLD",
        "model": config["model"]["name"],
        "safety_margin": margin,
        "max_positive_delta": observed_positive_delta,
        "cost_incomplete_runs": total_cost_incomplete,
        "budgets": data,
        "issues": issues,
        "warnings": warnings,
    }
    RESULT_ROOT.mkdir(parents=True, exist_ok=True)
    path = RESULT_ROOT / "smoke_gate_report.json"
    path.write_text(json.dumps(outcome, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nSmoke readiness: {outcome['readiness']} | report={relative(path)}")
    print(f"Safety margin={margin} | max observed +delta={observed_positive_delta}")
    for issue in issues:
        print(f"  HOLD: {issue}")
    for warning in warnings:
        print(f"  WARN: {warning}")
    if total_cost_incomplete:
        print("Cost comparisons remain incomplete for the affected runs; null is not $0.")
    return 0 if not issues else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare", help="Create small paired datasets from verified Main ZIP")
    runner = commands.add_parser("run", help="Execute live Gemini smoke with frozen config")
    runner.add_argument("--resume", action="store_true", help="Resume matching interrupted checkpoints")
    commands.add_parser("report", help="Check prompt-budget, accounting, provenance, checkpoint gates")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "run":
        run(resume=args.resume)
    else:
        raise SystemExit(report())


if __name__ == "__main__":
    main()
