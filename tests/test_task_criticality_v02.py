
"""Regression tests for Task-Criticality-Aware v0.2."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from benchmark.task_criticality_stress import generate_dataset
from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem, MemoryType
from memory.selector import MemoryCandidate
from memory.task_criticality import (
    EvidenceStatus,
    pack_task_criticality,
)


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def candidate(
    mid: str,
    text: str,
    cost: int,
    utility: float = 0.5,
) -> MemoryCandidate:
    memory = MemoryItem(
        memory_id=mid,
        session_id="test_session",
        turn_id=5,
        memory_type=MemoryType.FACT,
        content=text,
        token_count=cost,
        created_at=NOW,
        valid_from=NOW,
        importance=0.5,
    )

    return MemoryCandidate(
        memory=memory,
        utility_score=utility,
        relevance_score=0.5,
    )


class CriticalityV02Tests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dataset, cls.oracle = generate_dataset(
            counter=provisional_token_count,
            seed_start=200,
            cases_per_family=2,
        )

    def _run_case(self, scenario):
        memories = [
            MemoryItem.from_dict(raw)
            for raw in scenario["memory_snapshot"]
        ]

        flexible = [
            MemoryCandidate(
                memory=m,
                utility_score=0.5,
                relevance_score=0.5,
            )
            for m in memories
            if m.memory_type == MemoryType.FACT
        ]

        mandatory = [
            m for m in memories
            if (
                m.memory_type == MemoryType.STATE
                or m.is_protected
            )
        ]

        # Evidence 동작 검증용 충분한 예산.
        return pack_task_criticality(
            flexible,
            query=scenario["query"],
            mandatory=mandatory,
            budget=10000,
        )

    def test_s2_critical_retention(self):
        for scenario in self.dataset["scenarios"]:
            if scenario["metadata"]["variant"] != "S2":
                continue

            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                result = self._run_case(scenario)
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                self.assertEqual(
                    set(result.promoted_ids),
                    set(
                        label["expected_promoted_ids"]
                    ),
                )

                self.assertEqual(
                    result.excluded_invalid_ids,
                    (),
                )

    def test_s3_invalid_evidence_never_selected(self):
        for scenario in self.dataset["scenarios"]:
            if not scenario["metadata"][
                "variant"
            ].startswith("S3"):
                continue

            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                result = self._run_case(scenario)
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                invalid = set(
                    label["invalid_evidence_ids"]
                )

                excluded = set(
                    result.excluded_invalid_ids
                )

                selected = {
                    c.memory.memory_id
                    for c in result.selected
                }

                # 무효 Memory는 Promotion과 Fallback 양쪽에서 제외.
                self.assertTrue(
                    invalid.issubset(excluded)
                )

                self.assertTrue(
                    invalid.isdisjoint(selected)
                )

                self.assertTrue(
                    invalid.isdisjoint(
                        result.promoted_ids
                    )
                )

    def test_s3_valid_preserves_correct_evidence(self):
        for scenario in self.dataset["scenarios"]:
            if scenario["metadata"]["variant"] != "S3_VALID":
                continue

            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                result = self._run_case(scenario)
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                self.assertEqual(
                    set(result.promoted_ids),
                    set(
                        label["expected_promoted_ids"]
                    ),
                )

    def test_s3_empty_no_false_promotion(self):
        for scenario in self.dataset["scenarios"]:
            if scenario["metadata"]["variant"] != "S3_EMPTY":
                continue

            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                result = self._run_case(scenario)
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                self.assertEqual(
                    result.promoted_ids,
                    (),
                )

                self.assertIn(
                    label["slot"],
                    result.unresolved_slots,
                )

                statuses = dict(
                    result.slot_statuses
                )

                self.assertEqual(
                    statuses[label["slot"]],
                    EvidenceStatus.INVALID.value,
                )

    def test_s5_conflict_detection_preserved(self):
        for scenario in self.dataset["scenarios"]:
            if scenario["metadata"]["variant"] != "S5":
                continue

            with self.subTest(
                scenario=scenario["scenario_id"]
            ):
                result = self._run_case(scenario)
                label = self.oracle["labels"][
                    scenario["scenario_id"]
                ]

                self.assertIn(
                    label["slot"],
                    result.ambiguous_slots,
                )

                self.assertEqual(
                    result.promoted_ids,
                    (),
                )

                self.assertEqual(
                    dict(result.slot_statuses)[
                        label["slot"]
                    ],
                    EvidenceStatus.AMBIGUOUS.value,
                )

    def test_mixed_valid_and_invalid_memory_rejected(self):
        item = candidate(
            "mixed",
            (
                "HAI-ABC123 제출용 파일명: correct.pdf. "
                "파일명 예시: demo.pdf."
            ),
            30,
        )

        result = pack_task_criticality(
            [item],
            query="HAI-ABC123의 제출 파일 찾아줘",
            budget=100,
        )

        self.assertEqual(
            result.excluded_invalid_ids,
            ("mixed",),
        )

        self.assertEqual(
            result.selected,
            (),
        )

    def test_unsupported_action_preserves_fallback(self):
        candidates = [
            candidate(
                "a",
                "HAI-ABC123 제출 파일명 예시: demo.pdf",
                8,
                0.9,
            ),
            candidate(
                "b",
                "HAI-ABC123 회의 진행 기록",
                3,
                0.5,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query="내일 회의 내용을 알려줘",
            budget=8,
        )

        self.assertIsNone(result.action)

        self.assertEqual(
            result.excluded_invalid_ids,
            (),
        )

    def test_budget_and_input_integrity(self):
        candidates = [
            candidate(
                "critical",
                "HAI-ABC123 제출용 파일명: final.pdf",
                80,
                0.5,
            ),
            candidate(
                "invalid",
                "HAI-ABC123 파일명 예시: fake.pdf",
                10,
                0.9,
            ),
            candidate(
                "neutral",
                "HAI-ABC123 업무 진행 기록",
                10,
                0.4,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query="HAI-ABC123의 제출 파일 찾아줘",
            budget=85,
        )

        self.assertLessEqual(
            sum(
                c.memory.token_count
                for c in result.selected
            ),
            85,
        )

        self.assertEqual(
            result.promoted_ids,
            ("critical",),
        )

        self.assertEqual(
            result.excluded_invalid_ids,
            ("invalid",),
        )

        self.assertTrue(
            all(
                c.selection_reason is None
                for c in candidates
            )
        )


if __name__ == "__main__":
    unittest.main()
