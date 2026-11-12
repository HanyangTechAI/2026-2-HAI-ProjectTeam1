
"""Offline tests for independent Critical-Flexible Holdout."""
import copy
import tempfile
import unittest

from collections import Counter
from datetime import datetime
from pathlib import Path

from benchmark.critical_flexible_generator import (
    CONDITIONS,
    TOKEN_BANDS,
    generate_dataset,
    save_dataset,
)

from benchmark.critical_flexible_holdout import (
    TEMPLATE_VERSION,
    generate_holdout,
    prepare_budgeted_holdout,
)

from context.tokenizer import provisional_token_count
from memory.ablation import get_ablation_config
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, select_memory
from memory.store import SnapshotMemoryStore


def fake_calibrations(data):
    return [
        {
            "scenario_id": s["scenario_id"],
            "floor": 600,
            "ceiling": 1000,
        }
        for s in data["scenarios"]
    ]


class CriticalFlexibleHoldoutTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.counter = staticmethod(provisional_token_count)

        cls.data = generate_holdout(
            counter=cls.counter,
            cases_per_family=2,
        )

    def test_balanced_ids_and_metadata(self):
        scenarios = self.data["scenarios"]

        self.assertEqual(len(scenarios), 16)

        self.assertEqual(
            len({
                s["scenario_id"]
                for s in scenarios
            }),
            16,
        )

        self.assertEqual(
            Counter(
                s["metadata"]["stress_condition"]
                for s in scenarios
            ),
            {
                "A": 4,
                "B": 4,
                "C": 4,
                "D": 4,
            },
        )

        self.assertEqual(
            {
                s["metadata"]["seed"]
                for s in scenarios
            },
            {100, 101},
        )

        self.assertTrue(
            all(
                s["metadata"]["template_version"]
                == TEMPLATE_VERSION
                for s in scenarios
            )
        )

        self.assertTrue(
            all(
                s["metadata"]["dataset_split"] == "holdout"
                for s in scenarios
            )
        )

    def test_new_templates_and_unique_answers(self):
        original = generate_dataset(
            counter=self.counter,
            seed_start=100,
            cases_per_family=2,
        )

        original_by_id = {
            s["scenario_id"]: s
            for s in original["scenarios"]
        }

        for s in self.data["scenarios"]:
            old = original_by_id[s["scenario_id"]]

            self.assertNotEqual(
                s["query"],
                old["query"],
            )

            cid = (
                s["metadata"]
                ["critical_flexible_memory_ids"][0]
            )

            by_id = {
                m["memory_id"]: m
                for m in s["memory_snapshot"]
            }

            old_critical = next(
                m["content"]
                for m in old["memory_snapshot"]
                if m["memory_id"] == cid
            )

            self.assertNotEqual(
                by_id[cid]["content"],
                old_critical,
            )

            family = s["metadata"]["task_family"]

            field = (
                "filename"
                if family == "file_lookup"
                else "approval_id"
            )

            answer = (
                s["expected_tool_calls"][0]
                ["arguments"][field]
            )

            self.assertNotIn(
                answer,
                s["query"],
            )

            self.assertEqual(
                sum(
                    answer in m["content"]
                    for m in s["memory_snapshot"]
                ),
                1,
            )

            competing_ids = {
                cid,
                *s["metadata"]["distractor_memory_ids"],
            }

            for m in s["memory_snapshot"]:
                if m["memory_id"] not in competing_ids:
                    continue

                length = (
                    s["metadata"]["critical_length"]
                    if m["memory_id"] == cid
                    else s["metadata"]["noise_length"]
                )

                low, high = TOKEN_BANDS[length]

                actual = self.counter(
                    m["content"]
                )

                self.assertTrue(
                    low <= actual <= high
                )

                self.assertEqual(
                    m["token_count"],
                    actual,
                )

    def test_four_condition_pairing(self):
        groups = {}

        for s in self.data["scenarios"]:
            base = (
                s["metadata"]["base_case_id"]
            )

            groups.setdefault(
                base, []
            ).append(s)

        self.assertEqual(len(groups), 4)

        for variants in groups.values():
            self.assertEqual(
                {
                    x["metadata"]["stress_condition"]
                    for x in variants
                },
                set(CONDITIONS),
            )

            self.assertEqual(
                len({
                    x["query"]
                    for x in variants
                }),
                1,
            )

            self.assertEqual(
                len({
                    str(x["expected_tool_calls"])
                    for x in variants
                }),
                1,
            )

    def test_selector_mandatory_and_constraint(self):
        for s in self.data["scenarios"]:
            store = SnapshotMemoryStore(
                tuple(
                    MemoryItem.from_dict(m)
                    for m in s["memory_snapshot"]
                )
            )

            for preset in (
                "full",
                "no_budget_aware_packing",
            ):
                result = select_memory(
                    query=s["query"],
                    session_id=s["session_id"],
                    memory_store=store,
                    total_budget=10000,
                    fixed_tokens=20,
                    current_turn=s["current_turn"],
                    current_time=datetime.fromisoformat(
                        s["current_time"]
                    ),
                    embedder=lambda text: (1.0, 0.0),
                    token_counter=self.counter,
                    top_k=5,
                    ablation=get_ablation_config(preset),
                )

                self.assertEqual(
                    result.status,
                    PipelineStatus.OK,
                )

                cid = (
                    s["metadata"]
                    ["critical_flexible_memory_ids"][0]
                )

                self.assertIn(
                    cid,
                    result.selected_memory_ids,
                )

                family = (
                    s["metadata"]["task_family"]
                )

                expected_count = (
                    1 if family == "approved_email" else 0
                )

                self.assertEqual(
                    len(result.states),
                    expected_count,
                )

                self.assertEqual(
                    len(result.protected),
                    expected_count,
                )

    def test_budget_preparation_is_score_blind(self):
        rows = fake_calibrations(
            self.data
        )

        prepared = prepare_budgeted_holdout(
            self.data,
            rows,
        )

        self.assertEqual(
            len(prepared["scenarios"]),
            16,
        )

        # Floor=600, Ceiling=1000
        # Effective=900, Nominal=932
        for s in prepared["scenarios"]:
            self.assertEqual(
                s["context_budget"],
                932,
            )

            self.assertEqual(
                s["metadata"]["holdout_effective_budget"],
                900,
            )

            self.assertEqual(
                s["metadata"]["holdout_budget_level"],
                0.75,
            )

            self.assertEqual(
                s["metadata"]["context_token_budget"],
                932,
            )

        self.assertTrue(
            prepared["holdout"]["score_blind_budget_calibration"]
        )

        # 원본 데이터는 변경되면 안 된다.
        self.assertNotIn(
            "holdout_budget_level",
            self.data["scenarios"][0]["metadata"],
        )

    def test_reject_invalid_calibrations(self):
        rows = fake_calibrations(self.data)

        invalid = copy.deepcopy(rows)
        invalid[1]["ceiling"] = invalid[1]["floor"]

        with self.assertRaisesRegex(
            ValueError,
            "invalid floor/ceiling",
        ):
            prepare_budgeted_holdout(
                self.data,
                invalid,
            )

        with self.assertRaisesRegex(
            ValueError,
            "missing or extra",
        ):
            prepare_budgeted_holdout(
                self.data,
                rows[:-1],
            )

        with self.assertRaisesRegex(
            ValueError,
            "duplicate calibration",
        ):
            prepare_budgeted_holdout(
                self.data,
                rows + [rows[0]],
            )

        with self.assertRaisesRegex(
            ValueError,
            "safety_margin",
        ):
            prepare_budgeted_holdout(
                self.data,
                rows,
                safety_margin=-1,
            )

    def test_seed_guard_and_manifest(self):
        with self.assertRaises(ValueError):
            generate_holdout(
                counter=self.counter,
                seed_start=42,
                cases_per_family=1,
            )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "holdout.json"

            manifest = save_dataset(
                self.data,
                path,
            )

            self.assertEqual(
                manifest["scenario_count"],
                16,
            )

            self.assertTrue(
                path.is_file()
            )

            with self.assertRaises(FileExistsError):
                save_dataset(
                    self.data,
                    path,
                )


if __name__ == "__main__":
    unittest.main()
