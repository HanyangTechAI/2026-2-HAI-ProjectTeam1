"""1K/2K Gemini Prompt Token Preflight for HAI ProjectTeam1.

Place this file at the repository root. Commands:
    python preflight_1k2k.py prepare
    python preflight_1k2k.py run --margin 0
    python preflight_1k2k.py report --margin 0
    python preflight_1k2k.py run --margin 32
    python preflight_1k2k.py report --margin 32

This script does not modify the frozen configuration or Main ZIP.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARCHIVE = ROOT / "benchmark/generated/main_gemini_3_1_flash_lite.zip"
CONFIG = ROOT / "experiments/configs/main.json"
DATA_DIR = ROOT / "benchmark/generated/preflight_1k2k"
RESULT_DIR = ROOT / "experiments/results"
BUDGETS = (1024, 2048)
HORIZON = 50
SEEDS = (42, 43)
SAMPLE_IDENTITIES = (
    ("temporal_update", 0, "current"),
    ("temporal_update", 5, "historical"),
    ("approval", 10, "pending"),
    ("approval", 11, "granted"),
)
EXPECTED_SAMPLES_PER_BUDGET = len(SEEDS) * len(SAMPLE_IDENTITIES)
STRATEGY_COUNT = 4


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _data_path(budget: int) -> Path:
    return DATA_DIR / f"preflight_{budget}.json"


def _output_path(budget: int, margin: int) -> Path:
    return RESULT_DIR / f"preflight_{budget}_m{margin}"


def _target_ids() -> set[str]:
    result = set()
    for seed in SEEDS:
        for task, index, variant in SAMPLE_IDENTITIES:
            result.add(f"seed{seed}_{task}_{index:03d}_{variant}")
    return result


def prepare() -> None:
    if not ARCHIVE.is_file():
        raise FileNotFoundError(
            f"Main data archive not found: {ARCHIVE}. "
            "Generate it using benchmark.generator and the frozen model."
        )
    main_config = _read_json(CONFIG)
    chosen = _target_ids()
    selected_by_budget: dict[int, list[dict]] = {}

    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        model = main_config["model"]["name"]
        expected_mode = f"gemini:{model}"
        if manifest.get("token_count_mode") != expected_mode:
            raise ValueError(
                "Main archive token_count_mode mismatch: "
                f"{manifest.get('token_count_mode')!r} != {expected_mode!r}"
            )
        if manifest.get("config", {}).get("model", {}).get("name") != model:
            raise ValueError("Main archive model mismatches frozen main.json")

        archive_freeze = manifest.get("config", {}).get("freeze", {}).get("version")
        configured_freeze = main_config.get("freeze", {}).get("version")
        print(f"Archive freeze={archive_freeze}, current config freeze={configured_freeze}")
        if archive_freeze != configured_freeze:
            print("WARNING: Archive freeze differs from current config; verify data provenance before Main.")

        for budget in BUDGETS:
            matches = [
                cell for cell in manifest["cells"]
                if cell["parameters"].get("context_token_budget") == budget
                and cell["parameters"].get("horizon_turns") == HORIZON
            ]
            if len(matches) != 1:
                raise ValueError(f"Expected one {budget}-token H={HORIZON} cell, got {len(matches)}")
            cell = matches[0]
            payload = json.loads(archive.read(cell["path"]))
            selected = [
                scenario for scenario in payload["scenarios"]
                if scenario["scenario_id"] in chosen
            ]
            if len(selected) != EXPECTED_SAMPLES_PER_BUDGET:
                present = {scenario["scenario_id"] for scenario in selected}
                raise ValueError(f"{budget}: expected {EXPECTED_SAMPLES_PER_BUDGET} samples, missing {sorted(chosen - present)}")
            selected_by_budget[budget] = selected
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            subset = dict(payload)
            subset["scenarios"] = selected
            _data_path(budget).write_text(
                json.dumps(subset, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"Prepared {_data_path(budget).relative_to(ROOT)}: {len(selected)} scenarios")

    one = {s["scenario_id"]: s for s in selected_by_budget[1024]}
    two = {s["scenario_id"]: s for s in selected_by_budget[2048]}
    if set(one) != set(two):
        raise ValueError("Unpaired scenario IDs between 1K and 2K")
    for key in one:
        if one[key]["memory_snapshot"] != two[key]["memory_snapshot"] or one[key]["query"] != two[key]["query"]:
            raise ValueError(f"Sample contents drift across budgets: {key}")
    print("Paired sample identities and memory snapshots: OK")


def run(margin: int, *, resume: bool) -> None:
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("GEMINI_API_KEY is not set in this PowerShell session")
    for budget in BUDGETS:
        data = _data_path(budget)
        output = _output_path(budget, margin)
        if not data.is_file():
            raise FileNotFoundError(f"Missing {data}; run 'prepare' first")
        if (output / "experiment.json").exists() and not resume:
            raise FileExistsError(f"Existing result: {output}. Use 'run --margin {margin} --resume' if compatible, or move the directory.")
        command = [
            sys.executable, "-m", "benchmark.runner",
            "--config", str(CONFIG.relative_to(ROOT)),
            "--scenarios", str(data.relative_to(ROOT)),
            "--environment-agent-factory", "benchmark.experiment_runtime:environment_agent",
            "--output", str(output.relative_to(ROOT)),
            "--repeats", "1",
            "--checkpoint-interval", "1",
            "--request-token-safety-margin", str(margin),
        ]
        should_resume = resume and (output / "experiment.json").exists()
        if should_resume:
            command.append("--resume")
        print(f"\nRunning Budget={budget}, margin={margin}, resume={should_resume}", flush=True)
        subprocess.run(command, cwd=ROOT, check=True)


def report(margin: int) -> None:
    global_deltas: list[int] = []
    ready = True
    for budget in BUDGETS:
        output = _output_path(budget, margin)
        if not (output / "runs.jsonl").exists():
            raise FileNotFoundError(f"Missing results: {output}")
        rows = [
            json.loads(line)
            for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        progress = _read_json(output / "progress.json")
        executed = [r for r in rows if r["run_result"].get("agent_executed") is True]
        no_llm = len(rows) - len(executed)
        missing_api = [
            r for r in executed
            if type((r["run_result"].get("llm_usage") or {}).get("api_calls")) is not int
            or (r["run_result"].get("llm_usage") or {}).get("api_calls", 0) < 1
        ]
        unverified = [
            r for r in executed
            if r["run_result"].get("prompt_token_audit", {}).get("complete") is not True
        ]
        exceeded = [
            r for r in executed
            if r["run_result"].get("prompt_token_audit", {}).get("actual_budget_exceeded") is True
        ]
        counted_overflow = [
            r for r in rows
            if r["run_result"].get("prompt_token_audit", {}).get("counted_budget_exceeded") is True
        ]
        cost_incomplete = [
            r for r in executed
            if (r["run_result"].get("llm_usage") or {}).get("cost_estimate_complete") is not True
        ]
        counted_values = [
            token
            for r in executed
            for token in (r["run_result"].get("prompt_token_audit", {}).get("counted_prompt_tokens_per_call") or [])
            if type(token) is int
        ]
        observed = [
            delta
            for r in executed
            for delta in (r["run_result"].get("prompt_token_audit", {}).get("delta_actual_minus_counted_per_call") or [])
            if type(delta) is int
        ]
        global_deltas.extend(observed)
        expected = EXPECTED_SAMPLES_PER_BUDGET * STRATEGY_COUNT
        print(f"\n=== Budget={budget}, margin={margin} ===")
        print(f"Checkpoint: {progress['state']} ({len(rows)}/{expected} runs)")
        print(f"LLM executed: {len(executed)}, no LLM (e.g. budget stop): {no_llm}")
        print(f"Prompt calls compared: {len(observed)}")
        print(f"Missing API call metadata: {len(missing_api)}")
        print(f"Prompt audit incomplete: {len(unverified)}")
        print(f"Actual budget exceeded: {len(exceeded)}")
        print(f"Counted effective-budget exceeded: {len(counted_overflow)}")
        print(f"Cost incomplete (diagnostic): {len(cost_incomplete)}")
        print(f"Max positive delta (actual - counted): {max([0, *observed]) if observed else 'N/A'}")
        print(f"Max counted request tokens: {max(counted_values) if counted_values else 'N/A'}")
        print(f"Closest observed counted budget gap: {budget - max(counted_values) if counted_values else 'N/A'}")
        for strategy in ("proposed", "sliding_window", "vector_top_k", "utility_per_token"):
            members = [r for r in rows if r["strategy"] == strategy]
            called = sum(r["run_result"].get("agent_executed") is True for r in members)
            print(f"  {strategy}: LLM executed {called}/{len(members)}")
        if progress["state"] != "completed" or len(rows) != expected or not executed or missing_api or unverified or exceeded or counted_overflow:
            ready = False
        if any(not any(
            r["strategy"] == strategy and r["run_result"].get("agent_executed") is True
            for r in rows
        ) for strategy in ("proposed", "sliding_window", "vector_top_k", "utility_per_token")):
            print("WARNING: Some strategies had zero LLM executions in this budget.")
            ready = False

    if global_deltas:
        # Empirical proposal, not a proof: positive max + 16-token buffer,
        # rounded up in units of 16.
        proposed = 16 * math.ceil((max(0, max(global_deltas)) + 16) / 16)
        print(f"\nObserved positive delta max: {max(0, max(global_deltas))}")
        print(f"Candidate common safety margin: {proposed} tokens (max + >=16 guard, round up)")
    else:
        print("\nNo actual Gemini prompt usages recorded: cannot calibrate Safety Margin.")
        ready = False
    print("\nPreflight data-quality gate:", "PASS" if ready else "HOLD")
    print("Note: PASS refers to audited calls only; re-run with the selected margin before Main.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prepare")
    runner = sub.add_parser("run")
    runner.add_argument("--margin", type=int, default=0)
    runner.add_argument("--resume", action="store_true")
    summary = sub.add_parser("report")
    summary.add_argument("--margin", type=int, default=0)
    args = parser.parse_args()
    if args.command == "prepare":
        prepare()
    elif args.command == "run":
        if args.margin < 0 or args.margin >= min(BUDGETS):
            parser.error("margin must satisfy 0 <= margin < 1024")
        run(args.margin, resume=args.resume)
    else:
        report(args.margin)


if __name__ == "__main__":
    main()
