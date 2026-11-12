
"""Offline tests for selection-only S2/S3/S5 stress generation."""
from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from benchmark.task_criticality_stress import (
    DATASET_VERSION,
    VARIANTS,
    generate_dataset,
    load_and_validate,
    save_dataset,
    validate_dataset,
)
from context.tokenizer import provisional_token_count
from memory.experimental_packing import pack_flexible
from memory.schema import MemoryItem, MemoryType
from memory.selector import MemoryCandidate
from memory.task_criticality import pack_task_criticality


class CriticalityStressGeneratorTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # staticmethod로 함수 바인딩 오류 방지.
        cls.counter = staticmethod(
            provisional_token_count
        )

        cls.dataset, cls.oracle = generate_dataset(
            counter=cls.counter,
            seed_start=200,
            cases_per_family=2,
        )

    def test_balance_and_label_separation(self):
        self.assertEqual(
            self.dataset["dataset_version"],
            DATASET_VERSION,
        )
        self.assertEqual(
            len(self.dataset["scenarios"]), 16
        )
        self.assertEqual(
            len(self.oracle["labels"]), 16
        )
        self.assertEqual(len(VARIANTS), 4)

        self.assertEqual(
            {
                s["metadata"]["variant"]
                for s in self.dataset["scenarios"]
            },
            set(VARIANTS),
        )

        for scenario in self.dataset["scenarios"]:
            self.assertFalse(
                scenario["benchmark_enabled"]
            )
            self.assertEqual(
                scenario["validation_scope"],
                "module",
            )

            for forbidden in (
                "expected_tool_calls",
                "environment",
                "target_memory_ids",
                "required_states",
            ):
                self.assertNotIn(
                    forbidden, scenario
                )

            self.assertNotIn(
                "critical_flexible_memory_ids",
                scenario["metadata"],
            )

        validate_dataset(
            self.dataset,
            self.oracle,
            self.counter,
        )

    def test_packing_evidence_and_negative_cases(self):
        for scenario in self.dataset["scenarios"]:
            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                memories = [
                    MemoryItem.from_dict(m)
                    for m in scenario["memory_snapshot"]
                ]

                mandatory = [
                    m for m in memories
                    if (
                        m.memory_type == MemoryType.STATE
                        or m.is_protected
                    )
                ]

                flexible = [
                    MemoryCandidate(
                        memory=m,
                        utility_score=0.6,
                        relevance_score=0.5,
                    )
                    for m in memories
                    if m.memory_type == MemoryType.FACT
                ]

                result = pack_task_criticality(
                    flexible,
                    query=scenario["query"],
                    mandatory=mandatory,
                    budget=10000,
                )

                expected = set(
                    label["expected_promoted_ids"]
                )

                self.assertEqual(
                    set(result.promoted_ids),
                    expected,
                )

                self.assertTrue(
                    set(result.promoted_ids).isdisjoint(
                        label["invalid_evidence_ids"]
                    )
                )

                self.assertEqual(
                    set(result.ambiguous_slots),
                    set(
                        label["expected_ambiguous_slots"]
                    ),
                )

                if label["variant"] == "S3_EMPTY":
                    self.assertIn(
                        label["slot"],
                        result.unresolved_slots,
                    )

                self.assertLessEqual(
                    sum(
                        c.memory.token_count
                        for c in result.selected
                    ),
                    10000,
                )

    def test_s2_both_rankers_can_drop_low_utility_critical(self):
        s = next(
            s for s in self.dataset["scenarios"]
            if (
                s["metadata"]["variant"] == "S2"
                and s["metadata"]["task_family"]
                == "file_lookup"
            )
        )

        label = self.oracle["labels"][
            s["scenario_id"]
        ]

        gold = label["expected_promoted_ids"][0]
        candidates = []

        for raw in s["memory_snapshot"]:
            m = MemoryItem.from_dict(raw)

            if m.memory_type != MemoryType.FACT:
                continue

            utility = (
                0.6 * 0.5
                + 0.3 * m.importance
                + 0.1 / (
                    1 + s["current_turn"] - m.turn_id
                )
            )

            candidates.append(
                MemoryCandidate(
                    memory=m,
                    utility_score=utility,
                    relevance_score=0.5,
                )
            )

        critical = next(
            c for c in candidates
            if c.memory.memory_id == gold
        )

        noise = [
            c for c in candidates
            if c.memory.memory_id != gold
        ]

        budget = (
            critical.memory.token_count
            + min(
                c.memory.token_count
                for c in noise
            )
        )

        for alpha in (0.0, 1.0):
            baseline = pack_flexible(
                candidates,
                budget,
                alpha=alpha,
            )

            self.assertNotIn(
                gold,
                {
                    c.memory.memory_id
                    for c in baseline
                },
            )

        aware = pack_task_criticality(
            candidates,
            query=s["query"],
            budget=budget,
        )

        self.assertIn(
            gold,
            {
                c.memory.memory_id
                for c in aware.selected
            },
        )

        self.assertLessEqual(
            sum(
                c.memory.token_count
                for c in aware.selected
            ),
            budget,
        )

    def test_manifest_roundtrip_and_tamper_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "dataset.json"

            manifest = save_dataset(
                self.dataset,
                self.oracle,
                output,
            )

            self.assertEqual(
                manifest["scenario_count"],
                16,
            )

            loaded, labels, check = load_and_validate(
                output,
                self.counter,
            )

            self.assertEqual(
                loaded, self.dataset
            )
            self.assertEqual(
                labels, self.oracle
            )
            self.assertEqual(
                check, manifest
            )

            with self.assertRaises(FileExistsError):
                save_dataset(
                    self.dataset,
                    self.oracle,
                    output,
                )

            label_path = output.with_name(
                "dataset_labels.json"
            )

            label_path.write_text(
                label_path.read_text(
                    encoding="utf-8"
                ) + " ",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                ValueError, "checksum"
            ):
                load_and_validate(
                    output,
                    self.counter,
                )

    def test_validation_rejects_missing_label_and_token_drift(self):
        changed = copy.deepcopy(self.oracle)
        changed["labels"].pop(
            next(iter(changed["labels"]))
        )

        with self.assertRaisesRegex(
            ValueError, "cardinality"
        ):
            validate_dataset(
                self.dataset,
                changed,
                self.counter,
            )

        changed = copy.deepcopy(self.dataset)

        changed["scenarios"][0][
            "memory_snapshot"
        ][0]["token_count"] += 1

        with self.assertRaisesRegex(
            ValueError, "token count"
        ):
            validate_dataset(
                changed,
                self.oracle,
                self.counter,
            )


if __name__ == "__main__":
    unittest.main()
