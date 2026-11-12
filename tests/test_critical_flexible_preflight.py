
"""Critical-Flexible Preflight regression tests."""
from __future__ import annotations

import copy
import unittest
from collections import Counter

from benchmark.critical_flexible_generator import generate_dataset
from benchmark.critical_flexible_preflight import (
    LEVELS,
    PRESETS,
    preflight_scenarios,
    summarize_pairs,
)
from context.tokenizer import provisional_token_count


def fake_embedder(text: str) -> tuple[float, float]:
    # 테스트 전용 결정적 임베딩
    return (1.0, 0.0)


class CriticalFlexiblePreflightTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = generate_dataset(
            counter=provisional_token_count,
            cases_per_family=1,
        )

        (
            cls.calibration,
            cls.records,
            cls.summary,
        ) = preflight_scenarios(
            cls.data["scenarios"],
            system_prompt="과거 기억을 사용하세요.",
            tool_definitions=(
                "file.find_file\nemail.send_email"
            ),
            embedder=fake_embedder,
            counter=provisional_token_count,
            top_k=5,
        )

    def test_record_counts(self):
        self.assertEqual(len(self.calibration), 8)
        self.assertEqual(len(self.records), 80)
        self.assertEqual(len(self.summary), 25)

        counts = Counter(
            x["condition"]
            for x in self.calibration
        )

        self.assertEqual(
            counts,
            {
                "A": 2,
                "B": 2,
                "C": 2,
                "D": 2,
            },
        )

    def test_paired_budgets(self):
        pairs = {}

        for row in self.records:
            key = (
                row["scenario_id"],
                row["level"],
            )

            pairs.setdefault(key, {})[
                row["preset"]
            ] = row

        self.assertEqual(len(pairs), 40)

        for pair in pairs.values():
            self.assertEqual(
                set(pair),
                set(PRESETS),
            )

            self.assertEqual(
                pair["full"]["effective_budget"],
                pair["no_budget_aware_packing"][
                    "effective_budget"
                ],
            )

    def test_critical_reference_and_budget(self):
        for cal in self.calibration:
            critical = cal["critical_id"]

            self.assertIn(
                critical,
                cal["reference_flexible_ids"],
            )

            self.assertIn(
                critical,
                cal["candidate_scores"],
            )

            self.assertLessEqual(
                cal["floor"],
                cal["critical_min_effective_budget"],
            )

            self.assertLessEqual(
                cal["critical_min_effective_budget"],
                cal["ceiling"],
            )

        for row in self.records:
            self.assertFalse(
                row["budget_overflow"]
            )

            self.assertTrue(
                row["mandatory_retained"]
            )

            self.assertTrue(
                row["critical_retrieved"]
            )

            self.assertEqual(
                row["nominal_budget"]
                - row["effective_budget"],
                32,
            )

    def test_full_capacity_retains_critical(self):
        for row in self.records:
            if row["level"] != 1.0:
                continue

            self.assertTrue(
                row["critical_in_context"]
            )

            self.assertTrue(
                row["critical_feasible"]
            )

            self.assertEqual(
                row["critical_omission_reason"],
                "retained",
            )

    def test_feasibility_is_monotonic(self):
        by_case = {}

        for row in self.records:
            key = (
                row["scenario_id"],
                row["preset"],
            )

            by_case.setdefault(
                key, []
            ).append(row)

        for rows in by_case.values():
            ordered = sorted(
                rows,
                key=lambda r: r["level"],
            )

            self.assertEqual(
                [r["level"] for r in ordered],
                list(LEVELS),
            )

            feasibility = [
                r["critical_feasible"]
                for r in ordered
            ]

            self.assertEqual(
                feasibility,
                sorted(feasibility),
            )

    def test_duplicate_scenario_rejected(self):
        scenarios = [
            self.data["scenarios"][0],
            copy.deepcopy(
                self.data["scenarios"][0]
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "duplicate scenario_id",
        ):
            preflight_scenarios(
                scenarios,
                system_prompt="system",
                tool_definitions="tools",
                embedder=fake_embedder,
                counter=provisional_token_count,
            )

    def test_unmatched_pair_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "unmatched",
        ):
            summarize_pairs([
                self.records[0]
            ])

    def test_summary_denominators(self):
        pooled = [
            row
            for row in self.summary
            if row["condition"] == "ALL"
        ]

        self.assertEqual(len(pooled), 5)

        for row in pooled:
            self.assertEqual(
                row["pairs"], 8
            )

            self.assertEqual(
                row["unique_base_cases"], 2
            )

            self.assertEqual(
                row["overflow_count"], 0
            )

            self.assertTrue(
                0 <= row["full_critical_recall"] <= 1
            )


if __name__ == "__main__":
    unittest.main()
