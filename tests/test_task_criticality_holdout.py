import copy
import unittest

from benchmark.task_criticality_holdout import (
    TEMPLATE_VERSION,
    generate_holdout,
)
from benchmark.task_criticality_stress import (
    generate_dataset,
    validate_dataset,
)
from context.tokenizer import provisional_token_count


class IndependentHoldoutTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.counter = staticmethod(provisional_token_count)

        cls.dataset, cls.oracle = generate_holdout(
            counter=provisional_token_count,
            seed_start=1000,
            cases_per_family=2,
        )

    def test_split_and_version(self):
        self.assertEqual(
            self.dataset["parameters"]["split"],
            "holdout",
        )
        self.assertEqual(
            self.oracle["split"],
            "holdout",
        )
        self.assertEqual(
            self.dataset["parameters"]["template_version"],
            TEMPLATE_VERSION,
        )
        self.assertEqual(
            len(self.dataset["scenarios"]),
            16,
        )

    def test_dataset_validates(self):
        validate_dataset(
            self.dataset,
            self.oracle,
            provisional_token_count,
        )

    def test_new_templates(self):
        development, _ = generate_dataset(
            counter=provisional_token_count,
            seed_start=1000,
            cases_per_family=2,
        )

        old = {
            x["scenario_id"]: x
            for x in development["scenarios"]
        }
        new = {
            x["scenario_id"]: x
            for x in self.dataset["scenarios"]
        }

        self.assertEqual(set(old), set(new))

        for sid in old:
            self.assertNotEqual(
                old[sid]["query"],
                new[sid]["query"],
            )

            self.assertNotEqual(
                [
                    m["content"]
                    for m in old[sid]["memory_snapshot"]
                ],
                [
                    m["content"]
                    for m in new[sid]["memory_snapshot"]
                ],
            )

    def test_oracle_is_separate(self):
        forbidden = {
            "environment",
            "expected_tool_calls",
            "target_memory_ids",
            "critical_flexible_memory_ids",
            "constraint_rules",
            "required_states",
        }

        for scenario in self.dataset["scenarios"]:
            self.assertTrue(
                forbidden.isdisjoint(scenario)
            )

    def test_split_tampering_rejected(self):
        broken = copy.deepcopy(self.dataset)
        broken["parameters"]["split"] = "development"

        with self.assertRaises(ValueError):
            validate_dataset(
                broken,
                self.oracle,
                provisional_token_count,
            )

    def test_deterministic_generation(self):
        second, labels = generate_holdout(
            counter=provisional_token_count,
            seed_start=1000,
            cases_per_family=2,
        )

        self.assertEqual(second, self.dataset)
        self.assertEqual(labels, self.oracle)
        
        
    def test_invalid_approval_evidence(self):
        from memory.schema import MemoryItem
        from memory.task_criticality import _assess_memory

        for scenario in self.dataset["scenarios"]:
            label = self.oracle["labels"][scenario["scenario_id"]]

            if label["task_family"] != "approved_email":
                continue

            by_id = {
                item["memory_id"]: MemoryItem.from_dict(item)
                for item in scenario["memory_snapshot"]
            }

            for mid in label["invalid_evidence_ids"]:
                evidence, reason = _assess_memory(
                    by_id[mid],
                    query=scenario["query"],
                    slots=("approval_id",),
                )

                self.assertEqual(evidence, ())
                self.assertIsNotNone(
                    reason,
                    f"Invalid approval not detected: {mid}",
                )


if __name__ == "__main__":
    unittest.main()