from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ALPHAS = (0.0, 0.2, 1.0)
LEVEL = 0.75
MARGIN = 32
EXPECTED_CONDITION = "C"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dump(path: Path, obj: Any) -> None:
    path.write_text(
        json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        ) + "\n",
        encoding="utf-8",
    )


def _verify_manifest(path: Path) -> tuple[dict, str]:
    manifest_path = path.with_name(
        path.stem + "_manifest.json"
    )

    data = json.loads(
        path.read_text(encoding="utf-8")
    )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    digest = _hash(path)

    if manifest.get("dataset_sha256") != digest:
        raise ValueError(
            f"dataset manifest SHA-256 mismatch: {path}"
        )

    return data, digest


def prepare(
    source: Path,
    details: Path,
    output: Path,
) -> dict:
    """개발 데이터의 75% Budget을 고정하고 C 조건만 추출한다."""
    data, dataset_hash = _verify_manifest(source)

    if (
        data.get("generator")
        != "benchmark.critical_flexible_generator"
        or data.get("holdout")
        or data.get("dataset_version")
        != "critical_flexible_v1"
        or data.get("parameters", {}).get("seed_start") != 42
        or data.get("parameters", {}).get("cases_per_family") != 20
    ):
        raise ValueError(
            "expected original development dataset "
            "(seeds 42..61), not holdout"
        )

    scenarios = data.get("scenarios", [])

    if (
        len(scenarios) != 160
        or len({
            s["scenario_id"] for s in scenarios
        }) != 160
    ):
        raise ValueError(
            "expected 160 unique development scenarios"
        )

    grouped: dict[str, dict[float, dict]] = {}

    with details.open(
        encoding="utf-8-sig",
        newline="",
    ) as file:
        for row in csv.DictReader(file):
            if (
                float(row["level"]) != LEVEL
                or row["condition"] != EXPECTED_CONDITION
            ):
                continue

            alpha = float(row["alpha"])

            if alpha not in ALPHAS:
                continue

            sid = row["scenario_id"]
            group = grouped.setdefault(sid, {})

            if alpha in group:
                raise ValueError(
                    f"duplicate calibration: {sid} alpha={alpha}"
                )

            group[alpha] = row

    selected = [
        s for s in scenarios
        if s["metadata"]["stress_condition"]
        == EXPECTED_CONDITION
    ]

    if (
        len(selected) != 40
        or Counter(
            s["metadata"]["task_family"]
            for s in selected
        ) != {
            "file_lookup": 20,
            "approved_email": 20,
        }
    ):
        raise ValueError(
            "expected 40 C cases (20 per task family)"
        )

    if set(grouped) != {
        s["scenario_id"] for s in selected
    }:
        raise ValueError(
            "missing or extra 75% C calibration IDs"
        )

    prepared = copy.deepcopy(data)
    prepared["scenarios"] = []

    prepared["alpha_development"] = {
        "split": "development",
        "condition": EXPECTED_CONDITION,
        "level": LEVEL,
        "safety_margin": MARGIN,
        "alphas": list(ALPHAS),
        "source_dataset_sha256": dataset_hash,
        "policy_details_sha256": _hash(details),
    }

    for scenario in selected:
        sid = scenario["scenario_id"]
        rows = grouped[sid]

        if set(rows) != set(ALPHAS):
            raise ValueError(
                f"missing alpha budget arms: {sid}"
            )

        budgets = {
            (
                int(r["nominal_budget"]),
                int(r["effective_budget"]),
            )
            for r in rows.values()
        }

        if len(budgets) != 1:
            raise ValueError(
                f"unequal policy budgets: {sid}"
            )

        nominal, effective = next(iter(budgets))

        if (
            nominal <= MARGIN
            or nominal - effective != MARGIN
        ):
            raise ValueError(
                f"unexpected safety margin: {sid}"
            )

        for row in rows.values():
            if (
                row["critical_feasible"].lower() != "true"
                or row["retrieval_hit"].lower() != "true"
                or row["context_status"] != "ok"
                or row["selection_status"] != "ok"
            ):
                raise ValueError(
                    f"invalid calibration: {sid}"
                )

        copied = copy.deepcopy(scenario)

        copied["context_budget"] = nominal

        copied["metadata"]["context_token_budget"] = nominal
        copied["metadata"]["alpha_dev_level"] = LEVEL
        copied["metadata"]["alpha_dev_effective_budget"] = (
            effective
        )

        prepared["scenarios"].append(copied)

    manifest_path = output.with_name(
        output.stem + "_manifest.json"
    )

    if output.exists() or manifest_path.exists():
        raise FileExistsError(
            "prepared development dataset already exists"
        )

    output.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    _dump(output, prepared)

    manifest = {
        "dataset_sha256": _hash(output),
        "source_dataset_sha256": dataset_hash,
        "policy_details_sha256": _hash(details),
        "scenario_count": len(prepared["scenarios"]),
        "condition": EXPECTED_CONDITION,
        "level": LEVEL,
        "safety_margin": MARGIN,
    }

    _dump(manifest_path, manifest)

    return manifest


def run_alpha(
    alpha: float,
    source: Path,
    config: Path,
    output: Path,
    *,
    resume: bool,
) -> None:
    """Frozen Runner에 프로세스 한정 실험용 Packing을 적용한다."""
    if (
        isinstance(alpha, bool)
        or not math.isfinite(alpha)
        or alpha not in ALPHAS
    ):
        raise ValueError(
            f"alpha must be one of {ALPHAS}"
        )

    payload, digest = _verify_manifest(source)

    if (
        payload.get("alpha_development", {}).get("split")
        != "development"
        or payload["alpha_development"].get("condition")
        != EXPECTED_CONDITION
        or len(payload.get("scenarios", [])) != 40
    ):
        raise ValueError(
            "expected prepared development C-only dataset"
        )

    from benchmark import runner
    from memory import selector
    from memory.experimental_packing import pack_flexible

    frozen = runner._load_runner_config(config)
    runner._require_executable_config(frozen)

    if (
        frozen.get("freeze", {}).get("version") != "1.0.4"
        or frozen.get("scope") != "ablation"
    ):
        raise ValueError(
            "expected frozen v1.0.4 ablation config"
        )

    marker_path = output.with_name(
        output.name + ".alpha_metadata.json"
    )

    marker = {
        "experimental_overlay": True,
        "alpha": alpha,
        "score": "utility / max(token_count, 1) ** alpha",
        "frozen_selector_file_unchanged": True,
        "source_sha256": digest,
        "source_file": str(source),
        "frozen_config_path": str(config),
        "base_freeze": "1.0.4",
        "scenario_count": 40,
        "condition": EXPECTED_CONDITION,
    }

    if resume:
        if (
            not marker_path.is_file()
            or json.loads(
                marker_path.read_text(encoding="utf-8")
            ) != marker
        ):
            raise ValueError(
                "resume metadata is missing or mismatched"
            )
    else:
        if marker_path.exists() or output.exists():
            raise FileExistsError(
                "run output/metadata already exists"
            )

        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        _dump(marker_path, marker)

    original_select = selector.select_flexible

    def experimental_select(
        candidates,
        budget,
        *,
        token_aware=True,
    ):
        # α=0과 1은 기존 Frozen 구현을 직접 재사용.
        if not token_aware:
            return original_select(
                candidates,
                budget,
                token_aware=False,
            )

        if alpha == 1.0:
            return original_select(
                candidates,
                budget,
                token_aware=True,
            )

        if alpha == 0.0:
            return original_select(
                candidates,
                budget,
                token_aware=False,
            )

        # α=0.2: Flexible Ranking만 변경한다.
        return list(
            pack_flexible(
                candidates,
                budget,
                alpha=alpha,
            )
        )

    arguments = [
        "benchmark.runner",
        "--scenarios", str(source),
        "--config", str(config),
        "--strategies", "proposed",
        "--proposed-ablations", "full",
        "--environment-agent-factory",
        "benchmark.experiment_runtime:environment_agent",
        "--repeats", "1",
        "--max-steps", "1",
        "--request-token-safety-margin", str(MARGIN),
        "--checkpoint-interval", "10",
        "--output", str(output),
    ]

    if resume:
        arguments.append("--resume")

    previous_argv = sys.argv

    # 원본 파일은 변경하지 않는다.
    # 이 프로세스에서 Runner를 호출하는 동안만 함수를 교체한다.
    selector.select_flexible = experimental_select

    try:
        sys.argv = arguments
        runner.main()
    finally:
        selector.select_flexible = original_select
        sys.argv = previous_argv

    experiment_path = output / "experiment.json"

    experiment = json.loads(
        experiment_path.read_text(encoding="utf-8")
    )

    # 기존 Runner의 결과와 별도로 정책 오버레이 출처 기록
    experiment["experimental_packing"] = marker

    _dump(experiment_path, experiment)


def compare_runs(
    runs_by_alpha: dict[float, list[dict]],
) -> tuple[list[dict], list[dict], list[dict]]:
    """세 전략의 저장된 첫 행동 결과를 동일 Scenario로 연결한다."""
    from benchmark.first_action_evaluator import (
        evaluate_first_action,
    )

    if set(runs_by_alpha) != set(ALPHAS):
        raise ValueError(
            "all three alpha arms are required"
        )

    details: list[dict] = []

    groups: dict[
        tuple[str, int], dict[float, dict]
    ] = {}

    for alpha, rows in runs_by_alpha.items():
        for item in rows:
            if (
                item.get("strategy") != "proposed"
                or item.get("repeat_id") != 0
            ):
                raise ValueError(
                    "expected proposed/full, one repeat"
                )

            detail = evaluate_first_action(item)
            detail["alpha"] = alpha

            key = (
                detail["scenario_id"],
                detail["repeat_id"],
            )

            by_alpha = groups.setdefault(key, {})

            if alpha in by_alpha:
                raise ValueError(
                    f"duplicate scenario within alpha arm: {key}"
                )

            by_alpha[alpha] = detail
            details.append(detail)

    if len(groups) != 40:
        raise ValueError(
            f"expected 40 paired development scenarios, "
            f"got {len(groups)}"
        )

    pairs = []

    for (sid, repeat), arms in sorted(groups.items()):
        if set(arms) != set(ALPHAS):
            raise ValueError(
                f"missing alpha arm: {sid}"
            )

        baseline = arms[0.0]

        for alpha in ALPHAS:
            other = arms[alpha]

            for field in (
                "base_case_id",
                "task_family",
                "condition",
                "context_budget",
                "expected_arguments",
                "expected_tool",
                "expected_action",
            ):
                if baseline[field] != other[field]:
                    raise ValueError(
                        f"unpaired {field} for {sid}"
                    )

            if other["condition"] != EXPECTED_CONDITION:
                raise ValueError(
                    "only condition C is supported"
                )

        pairs.append({
            "scenario_id": sid,
            "repeat_id": repeat,
            "base_case_id": baseline["base_case_id"],
            "task_family": baseline["task_family"],
            "context_budget": baseline["context_budget"],

            **{
                f"alpha_{alpha:g}_correct": (
                    arms[alpha]["first_action_correct"]
                )
                for alpha in ALPHAS
            },

            **{
                f"alpha_{alpha:g}_critical": (
                    arms[alpha]["critical_in_context"]
                )
                for alpha in ALPHAS
            },

            "delta_0_vs_0.2": (
                int(arms[0.0]["first_action_correct"])
                - int(arms[0.2]["first_action_correct"])
            ),

            "delta_0.2_vs_1": (
                int(arms[0.2]["first_action_correct"])
                - int(arms[1.0]["first_action_correct"])
            ),
        })

    summary = []

    for family in (
        "ALL",
        "file_lookup",
        "approved_email",
    ):
        for alpha in ALPHAS:
            subset = [
                row for row in details
                if row["alpha"] == alpha
                and (
                    family == "ALL"
                    or row["task_family"] == family
                )
            ]

            n = len(subset)

            summary.append({
                "task_family": family,
                "condition": EXPECTED_CONDITION,
                "alpha": alpha,
                "n": n,

                "n_base_cases": len({
                    r["base_case_id"] for r in subset
                }),

                "first_action_correct": sum(
                    r["first_action_correct"]
                    for r in subset
                ),

                "first_action_accuracy": (
                    sum(
                        r["first_action_correct"]
                        for r in subset
                    ) / n
                ),

                "critical_in_context": sum(
                    r["critical_in_context"]
                    for r in subset
                ),

                "critical_recall": (
                    sum(
                        r["critical_in_context"]
                        for r in subset
                    ) / n
                ),

                "no_first_tool_call": sum(
                    r["failure_reason"] == "no_first_tool_call"
                    for r in subset
                ),

                "budget_verification_missing": sum(
                    not r["prompt_budget_verified"]
                    for r in subset
                ),

                "budget_overflow": sum(
                    r["actual_budget_exceeded"] is True
                    for r in subset
                ),
            })

    return details, pairs, summary


def _csv(
    path: Path,
    rows: list[dict],
) -> None:
    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=list(rows[0]),
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in row.items()
            })


def compare(
    paths: dict[float, Path],
    output: Path,
) -> dict:
    if output.exists():
        raise FileExistsError(
            "comparison output already exists"
        )

    rows_by_alpha = {}
    provenance = {}
    source_hashes = set()

    for alpha in ALPHAS:
        folder = paths[alpha]

        marker_path = folder.with_name(
            folder.name + ".alpha_metadata.json"
        )

        marker = json.loads(
            marker_path.read_text(encoding="utf-8")
        )

        if (
            marker.get("alpha") != alpha
            or not marker.get("experimental_overlay")
        ):
            raise ValueError(
                f"alpha metadata mismatch: {folder}"
            )

        source_hashes.add(
            marker.get("source_sha256")
        )

        experiment = json.loads(
            (folder / "experiment.json").read_text(
                encoding="utf-8"
            )
        )

        if experiment.get("experimental_packing") != marker:
            raise ValueError(
                f"experiment provenance mismatch: {folder}"
            )

        runs_path = folder / "runs.jsonl"

        rows = [
            json.loads(line)
            for line in runs_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]

        if len(rows) != 40:
            raise ValueError(
                f"expected 40 runs for alpha={alpha}"
            )

        rows_by_alpha[alpha] = rows

        provenance[str(alpha)] = {
            "run_sha256": _hash(runs_path),
            "experiment_path": str(folder),
        }

    if len(source_hashes) != 1:
        raise ValueError(
            "alpha arms use different scenario datasets"
        )

    details, pairs, summary = compare_runs(
        rows_by_alpha
    )

    output.mkdir(
        parents=True,
        exist_ok=False,
    )

    (output / "first_action_details.jsonl").write_text(
        "".join(
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
            ) + "\n"
            for row in details
        ),
        encoding="utf-8",
    )

    _csv(
        output / "paired_alpha.csv",
        pairs,
    )

    _csv(
        output / "alpha_summary.csv",
        summary,
    )

    failures = [
        row for row in details
        if not row["first_action_correct"]
    ]

    if failures:
        _csv(
            output / "failure_details.csv",
            failures,
        )
    else:
        (output / "failure_details.csv").write_text(
            "scenario_id,alpha,failure_reason\n",
            encoding="utf-8-sig",
        )

    validation = {
        "kind": "development_first_action_experiment",
        "source_sha256": next(iter(source_hashes)),
        "total_llm_runs": sum(
            len(x) for x in rows_by_alpha.values()
        ),
        "paired_cases": len(pairs),
        "alphas": list(ALPHAS),
        "budget_level": LEVEL,
        "condition": EXPECTED_CONDITION,

        "actual_budget_overflow": sum(
            row["actual_budget_exceeded"] is True
            for row in details
        ),

        "prompt_verification_missing": sum(
            not row["prompt_budget_verified"]
            for row in details
        ),

        "source_runs": provenance,

        "note": (
            "Development data, not independent holdout; "
            "metric is first-action, not TSR"
        ),
    }

    _dump(
        output / "validation.json",
        validation,
    )

    return {
        "summary": summary,
        "validation": validation,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # 개발 데이터의 75% 예산 고정
    prep = commands.add_parser("prepare")

    prep.add_argument(
        "--dataset",
        type=Path,
        default=Path(
            "benchmark/generated/critical_flexible_v1/dataset.json"
        ),
    )

    prep.add_argument(
        "--policy-details",
        type=Path,
        required=True,
    )

    prep.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    # α별 LLM 실행
    run = commands.add_parser("run")

    run.add_argument(
        "--alpha",
        type=float,
        required=True,
    )

    run.add_argument(
        "--scenarios",
        type=Path,
        required=True,
    )

    run.add_argument(
        "--config",
        type=Path,
        default=Path(
            "experiments/configs/ablation.json"
        ),
    )

    run.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    run.add_argument(
        "--resume",
        action="store_true",
    )

    # 모든 α의 결과 비교
    comp = commands.add_parser("compare")

    comp.add_argument(
        "--alpha-0",
        type=Path,
        required=True,
    )

    comp.add_argument(
        "--alpha-02",
        type=Path,
        required=True,
    )

    comp.add_argument(
        "--alpha-1",
        type=Path,
        required=True,
    )

    comp.add_argument(
        "--output",
        type=Path,
        required=True,
    )

    args = parser.parse_args()

    if args.command == "prepare":
        result = prepare(
            args.dataset,
            args.policy_details,
            args.output,
        )

        print(
            "[PASS] Prepared",
            result["scenario_count"],
            "C scenarios",
            args.output,
        )

    elif args.command == "run":
        run_alpha(
            args.alpha,
            args.scenarios,
            args.config,
            args.output,
            resume=args.resume,
        )

    else:
        result = compare(
            {
                0.0: args.alpha_0,
                0.2: args.alpha_02,
                1.0: args.alpha_1,
            },
            args.output,
        )

        print(
            "[PASS] Compared",
            result["validation"]["paired_cases"],
            "pairs",
        )

        for row in result["summary"]:
            if row["task_family"] == "ALL":
                print(
                    f"alpha={row['alpha']}: "
                    f"first_action="
                    f"{row['first_action_correct']}/{row['n']}, "
                    f"critical="
                    f"{row['critical_in_context']}/{row['n']}"
                )


if __name__ == "__main__":
    main()
