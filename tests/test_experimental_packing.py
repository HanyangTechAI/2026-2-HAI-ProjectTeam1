
"""Offline regression tests for experimental packing and frozen parity."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from benchmark.critical_flexible_generator import generate_dataset
from benchmark.packing_policy_preflight import evaluate_policies
from context.tokenizer import provisional_token_count
from memory.experimental_packing import (
    pack_flexible,
    packing_score,
    repack_reference,
    validate_alpha,
)
from memory.schema import MemoryItem, MemoryType
from memory.selector import (
    MemoryCandidate,
    PipelineStatus,
    SelectionResult,
)

NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def memory(mid, cost):
    return MemoryItem(
        memory_id=mid,
        session_id="s",
        turn_id=3,
        memory_type=MemoryType.FACT,
        content=f"sample {mid}",
        token_count=cost,
        created_at=NOW,
        valid_from=NOW,
        importance=0.5,
    )


def candidate(mid, cost, utility):
    return MemoryCandidate(
        memory=memory(mid, cost),
        utility_score=utility,
        relevance_score=0.5,
        recency_score=0.25,
    )


def fake_embedder(text):
    return (1.0, 0.0)


class ExperimentalPackingTests(unittest.TestCase):

    def test_length_penalty_changes_critical_selection(self):
        candidates = [
            candidate("critical", 8, 0.9),
            candidate("noise_a", 3, 0.5),
            candidate("noise_b", 3, 0.5),
            candidate("noise_c", 3, 0.5),
        ]

        utility_only = pack_flexible(
            candidates, 11, alpha=0.0
        )
        token_aware = pack_flexible(
            candidates, 11, alpha=1.0
        )
        hybrid = pack_flexible(
            candidates, 11, alpha=0.5
        )

        self.assertIn(
            "critical",
            {c.memory.memory_id for c in utility_only},
        )

        self.assertNotIn(
            "critical",
            {c.memory.memory_id for c in token_aware},
        )

        self.assertIn(
            "critical",
            {c.memory.memory_id for c in hybrid},
        )

        for selected in (
            utility_only,
            token_aware,
            hybrid,
        ):
            self.assertLessEqual(
                sum(
                    c.memory.token_count
                    for c in selected
                ),
                11,
            )

        # 입력 Candidate는 변경되지 않아야 한다.
        self.assertTrue(
            all(
                c.selection_reason is None
                for c in candidates
            )
        )

    def test_deterministic_tie_breaking(self):
        first = [
            candidate("b", 3, 0.5),
            candidate("a", 3, 0.5),
        ]

        second = list(reversed(first))

        self.assertEqual(
            [
                c.memory.memory_id
                for c in pack_flexible(
                    first, 3, alpha=1.0
                )
            ],
            ["a"],
        )

        self.assertEqual(
            [
                c.memory.memory_id
                for c in pack_flexible(
                    second, 3, alpha=1.0
                )
            ],
            ["a"],
        )

    def test_invalid_inputs(self):
        for alpha in (
            -0.1,
            1.1,
            float("nan"),
            float("inf"),
        ):
            with self.assertRaises(ValueError):
                validate_alpha(alpha)

        with self.assertRaises(TypeError):
            validate_alpha(True)

        with self.assertRaises(ValueError):
            pack_flexible(
                [candidate("x", 2, 0.4)],
                -1,
                alpha=0.5,
            )

        with self.assertRaises(ValueError):
            pack_flexible(
                [
                    candidate("x", 2, 0.4),
                    candidate("x", 2, 0.4),
                ],
                4,
                alpha=0.5,
            )

        with self.assertRaises(ValueError):
            packing_score(
                candidate("x", 3, float("nan")),
                0.5,
            )

    def test_mandatory_is_never_traded_away(self):
        protected = memory("constraint", 8)
        critical = memory("critical", 4)

        reference = SelectionResult(
            status=PipelineStatus.OK,
            protected=(protected,),
            flexible=(critical,),
            selected_memory_ids=(
                "constraint",
                "critical",
            ),
        )

        scores = {
            "critical": {
                "utility_score": 0.9,
                "semantic_score": 0.5,
            }
        }

        selection = repack_reference(
            reference,
            scores,
            total_budget=10,
            fixed_tokens=0,
            current_turn=4,
            alpha=0.5,
        )

        self.assertEqual(
            selection.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            selection.selected_memory_ids,
            ("constraint",),
        )

        failed = repack_reference(
            reference,
            scores,
            total_budget=7,
            fixed_tokens=0,
            current_turn=4,
            alpha=0.5,
        )

        self.assertEqual(
            failed.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

        invalid = repack_reference(
            reference,
            scores,
            total_budget=2,
            fixed_tokens=3,
            current_turn=4,
            alpha=0.5,
        )

        self.assertEqual(
            invalid.status,
            PipelineStatus.INVALID_BUDGET_CONFIGURATION,
        )

        with self.assertRaises(ValueError):
            repack_reference(
                reference,
                {},
                total_budget=10,
                fixed_tokens=0,
                current_turn=4,
                alpha=0.5,
            )

    def test_dev_scenarios_match_frozen_controls(self):
        # API 키 없이 축소된 개발 데이터로 동등성 검사
        dataset = generate_dataset(
            counter=provisional_token_count,
            seed_start=42,
            cases_per_family=1,
        )

        rows, report, checks = evaluate_policies(
            dataset["scenarios"],
            system_prompt="과거 기억을 사용하세요.",
            tool_definitions=(
                "file.find_file\nemail.send_email"
            ),
            embedder=fake_embedder,
            counter=provisional_token_count,
            alphas=(0.0, 0.5, 1.0),
            levels=(0.75,),
        )

        self.assertEqual(len(rows), 8 * 3)
        self.assertEqual(checks, 8 * 2)
        self.assertEqual(len(report), 5 * 3)

        self.assertFalse(
            any(
                row["budget_overflow"]
                for row in rows
            )
        )

        self.assertTrue(
            all(
                row["mandatory_retained"]
                for row in rows
            )
        )

        for row in rows:
            if row["critical_in_context"]:
                self.assertTrue(
                    row["retrieval_hit"]
                )

        c = [
            row for row in report
            if row["condition"] == "C"
            and row["level"] == 0.75
        ]

        self.assertEqual(len(c), 3)

        self.assertTrue(
            all(row["n"] == 2 for row in c)
        )

    def test_invalid_grid(self):
        from benchmark.packing_policy_preflight import (
            _validate_grid,
        )

        with self.assertRaisesRegex(
            ValueError,
            "duplicate alpha",
        ):
            _validate_grid(
                (0.0, 0.0),
                (0.75,),
            )

        with self.assertRaisesRegex(
            ValueError,
            "levels",
        ):
            _validate_grid(
                (0.0,),
                (0.63,),
            )


if __name__ == "__main__":
    unittest.main()
