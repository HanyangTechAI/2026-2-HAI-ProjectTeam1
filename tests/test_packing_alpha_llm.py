
"""Offline tests for the development-only alpha LLM experiment."""
import csv
import tempfile
import unittest

from pathlib import Path
from unittest.mock import patch

from benchmark.packing_alpha_llm import (
    ALPHAS,
    compare_runs,
    prepare,
    _dump,
    _hash,
    _verify_manifest,
)


class PackingAlphaLLMTests(unittest.TestCase):

    def make_sources(self, root: Path):
        dataset = root / "dataset.json"
        details = root / "policy_details.csv"

        cases = []

        for family in (
            "file_lookup",
            "approved_email",
        ):
            for seed in range(42, 62):
                for condition in "ABCD":
                    cases.append({
                        "scenario_id": (
                            f"cf_v1_{family}_{seed:03d}_{condition}"
                        ),
                        "context_budget": 2048,
                        "metadata": {
                            "stress_condition": condition,
                            "task_family": family,
                        },
                    })

        _dump(dataset, {
            "generator": (
                "benchmark.critical_flexible_generator"
            ),
            "dataset_version": "critical_flexible_v1",
            "parameters": {
                "seed_start": 42,
                "cases_per_family": 20,
            },
            "scenarios": cases,
        })

        _dump(
            root / "dataset_manifest.json",
            {"dataset_sha256": _hash(dataset)},
        )

        fields = (
            "scenario_id",
            "condition",
            "level",
            "alpha",
            "nominal_budget",
            "effective_budget",
            "critical_feasible",
            "retrieval_hit",
            "selection_status",
            "context_status",
        )

        with details.open(
            "w",
            encoding="utf-8",
            newline="",
        ) as file:
            writer = csv.DictWriter(
                file,
                fieldnames=fields,
            )

            writer.writeheader()

            for scenario in cases:
                for alpha in ALPHAS:
                    writer.writerow({
                        "scenario_id": scenario["scenario_id"],
                        "condition": (
                            scenario["metadata"]["stress_condition"]
                        ),
                        "level": 0.75,
                        "alpha": alpha,
                        "nominal_budget": 900,
                        "effective_budget": 868,
                        "critical_feasible": True,
                        "retrieval_hit": True,
                        "selection_status": "ok",
                        "context_status": "ok",
                    })

        return dataset, details

    def test_prepare_40_paired_scenarios_and_manifest(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            dataset, details = self.make_sources(root)
            output = root / "prepared.json"

            result = prepare(
                dataset,
                details,
                output,
            )

            self.assertEqual(
                result["scenario_count"],
                40,
            )

            prepared, _ = _verify_manifest(output)

            self.assertEqual(
                len(prepared["scenarios"]),
                40,
            )

            self.assertTrue(
                all(
                    s["metadata"]["stress_condition"] == "C"
                    for s in prepared["scenarios"]
                )
            )

            self.assertTrue(
                all(
                    s["context_budget"] == 900
                    for s in prepared["scenarios"]
                )
            )

            self.assertEqual(
                set(prepared["alpha_development"]["alphas"]),
                set(ALPHAS),
            )

            with self.assertRaises(FileExistsError):
                prepare(
                    dataset,
                    details,
                    output,
                )

    def test_reject_budget_inequality(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)

            dataset, details = self.make_sources(root)

            with details.open(encoding="utf-8") as file:
                rows = list(csv.DictReader(file))

            target = next(
                row for row in rows
                if row["condition"] == "C"
                and float(row["alpha"]) == 0.2
            )

            target["nominal_budget"] = "901"

            with details.open(
                "w",
                encoding="utf-8",
                newline="",
            ) as file:
                writer = csv.DictWriter(
                    file,
                    fieldnames=list(rows[0]),
                )

                writer.writeheader()
                writer.writerows(rows)

            with self.assertRaisesRegex(
                ValueError,
                "unequal policy budgets",
            ):
                prepare(
                    dataset,
                    details,
                    root / "prepared.json",
                )

    def test_compare_requires_paired_alpha_arms(self):
        def fake_evaluator(item):
            sid = item["sid"]
            i = int(sid[-2:])

            correct = (
                item["fake_alpha"] == 0.0
                or (
                    item["fake_alpha"] == 0.2
                    and i >= 9
                )
            )

            return {
                "scenario_id": sid,
                "repeat_id": 0,
                "base_case_id": f"base_{i:02d}",
                "task_family": (
                    "file_lookup"
                    if i < 20
                    else "approved_email"
                ),
                "condition": "C",
                "context_budget": 850,

                "expected_arguments": {
                    "filename": "target.pdf"
                },
                "expected_tool": "file",
                "expected_action": "find_file",

                "first_action_correct": correct,
                "critical_in_context": correct,

                "failure_reason": (
                    "ok" if correct else "wrong_action"
                ),

                "prompt_budget_verified": True,
                "actual_budget_exceeded": False,
            }

        rows = {
            alpha: [
                {
                    "strategy": "proposed",
                    "repeat_id": 0,
                    "sid": f"case_{i:02d}",
                    "fake_alpha": alpha,
                }
                for i in range(40)
            ]
            for alpha in ALPHAS
        }

        with patch(
            "benchmark.first_action_evaluator.evaluate_first_action",
            side_effect=fake_evaluator,
        ):
            details, pairs, summary = compare_runs(rows)

        self.assertEqual(len(details), 120)
        self.assertEqual(len(pairs), 40)
        self.assertEqual(len(summary), 9)

        overall = {
            r["alpha"]: r
            for r in summary
            if r["task_family"] == "ALL"
        }

        self.assertEqual(
            overall[0.0]["first_action_correct"],
            40,
        )
        self.assertEqual(
            overall[0.2]["first_action_correct"],
            31,
        )
        self.assertEqual(
            overall[1.0]["first_action_correct"],
            0,
        )

        rows[1.0].pop()

        with patch(
            "benchmark.first_action_evaluator.evaluate_first_action",
            side_effect=fake_evaluator,
        ):
            with self.assertRaisesRegex(
                ValueError,
                "missing alpha arm",
            ):
                compare_runs(rows)


if __name__ == "__main__":
    unittest.main()
