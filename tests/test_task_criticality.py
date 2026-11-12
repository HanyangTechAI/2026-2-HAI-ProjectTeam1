
"""Offline tests for experimental Task-Criticality-Aware Packing."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from memory.schema import MemoryItem, MemoryType
from memory.selector import MemoryCandidate
from memory.experimental_packing import pack_flexible
from memory.task_criticality import pack_task_criticality


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def cand(mid, text, cost, utility):
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


def ids(result):
    return [
        item.memory.memory_id
        for item in result.selected
    ]


class TaskCriticalityTests(unittest.TestCase):

    def test_long_critical_beats_short_noise_with_hard_budget(self):
        candidates = [
            cand(
                "critical",
                "HAI-ABC123 제출용 파일명: report_final.pdf.",
                8, 0.9,
            ),
            cand("n1", "HAI-ABC123 일반 회의 기록", 3, 0.5),
            cand("n2", "HAI-ABC123 검토 진행 기록", 3, 0.5),
            cand("n3", "HAI-ABC123 운영 관련 기록", 3, 0.5),
        ]

        baseline = pack_flexible(
            candidates,
            11,
            alpha=1.0,
        )

        self.assertNotIn(
            "critical",
            [x.memory.memory_id for x in baseline],
        )

        result = pack_task_criticality(
            candidates,
            query="HAI-ABC123의 제출 자료 파일을 찾아줘",
            budget=11,
        )

        self.assertIn("critical", ids(result))

        self.assertEqual(
            result.promoted_ids,
            ("critical",),
        )

        self.assertLessEqual(
            sum(
                x.memory.token_count
                for x in result.selected
            ),
            11,
        )

        self.assertTrue(
            all(x.selection_reason is None for x in candidates)
        )

    def test_low_utility_critical_beats_both_baselines(self):
        candidates = [
            cand(
                "critical",
                "HAI-ABC123 제출용 파일명: correct.pdf",
                4, 0.3,
            ),
            cand(
                "distractor",
                "HAI-ABC123 검토 메모 내용",
                8, 0.9,
            ),
        ]

        for alpha in (0.0, 1.0):
            baseline = pack_flexible(
                candidates,
                8,
                alpha=alpha,
            )

            self.assertNotIn(
                "critical",
                [x.memory.memory_id for x in baseline],
            )

        result = pack_task_criticality(
            candidates,
            query="HAI-ABC123의 제출 파일을 찾아줘",
            budget=8,
        )

        self.assertIn("critical", ids(result))

    def test_approval_grant_and_mandatory_attachment(self):
        candidates = [
            cand(
                "grant",
                "HAI-ABC123 발송 승인번호: approved_ab12345.",
                9, 0.9,
            ),
            cand(
                "noise",
                "HAI-ABC123 일반 진행 기록",
                3, 0.6,
            ),
        ]

        state = cand(
            "state",
            "HAI-ABC123 현재 보고서 파일은 final.pdf입니다.",
            5, 0.7,
        ).memory

        result = pack_task_criticality(
            candidates,
            query=(
                "HAI-ABC123 보고서를 "
                "reviewer@example.test에게 외부 메일로 보내줘"
            ),
            mandatory=(state,),
            budget=10,
        )

        self.assertEqual(
            result.grounded_slots,
            ("attachment",),
        )

        self.assertEqual(
            result.promoted_ids,
            ("grant",),
        )

        self.assertEqual(
            result.unresolved_slots,
            (),
        )

    def test_receipt_and_false_example_not_promoted(self):
        candidates = [
            cand(
                "receipt",
                "HAI-ABC123 승인 요청 접수번호 approval_001",
                3, 0.6,
            ),
            cand(
                "fake",
                "HAI-ABC123 발송 승인번호 예시: approved_wrong",
                3, 0.6,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query=(
                "HAI-ABC123 보고서를 "
                "reviewer@example.test에게 메일로 보내줘"
            ),
            budget=6,
        )

        self.assertEqual(
            result.promoted_ids,
            (),
        )

        self.assertIn(
            "approval_id",
            result.unresolved_slots,
        )

    def test_competing_values_abstain(self):
        candidates = [
            cand(
                "a",
                "HAI-ABC123 제출용 파일명: alpha.pdf",
                4, 0.9,
            ),
            cand(
                "b",
                "HAI-ABC123 제출용 파일명: beta.pdf",
                4, 0.8,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query="HAI-ABC123의 제출 파일을 찾아줘",
            budget=4,
        )

        self.assertEqual(
            result.promoted_ids,
            (),
        )

        self.assertEqual(
            result.ambiguous_slots,
            ("filename",),
        )

        self.assertEqual(
            result.unresolved_slots,
            ("filename",),
        )

    def test_wrong_project_not_promoted(self):
        candidates = [
            cand(
                "wrong",
                "HAI-OTHER7 제출용 파일명: wrong.pdf",
                4, 0.9,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query="HAI-ABC123의 제출 파일을 찾아줘",
            budget=4,
        )

        self.assertEqual(
            result.promoted_ids,
            (),
        )

    def test_unsupported_action_matches_baseline(self):
        candidates = [
            cand(
                "a",
                "HAI-ABC123 제출용 파일명: alpha.pdf",
                8, 0.9,
            ),
            cand(
                "b",
                "HAI-ABC123 일반 정보",
                3, 0.5,
            ),
        ]

        result = pack_task_criticality(
            candidates,
            query="내일 회의 일정을 알려줘",
            budget=8,
        )

        baseline = pack_flexible(
            candidates,
            8,
            alpha=1.0,
        )

        self.assertEqual(
            ids(result),
            [x.memory.memory_id for x in baseline],
        )

        self.assertIsNone(result.action)

    def test_invalid_budget_and_duplicates(self):
        item = cand(
            "a",
            "HAI-ABC123 제출용 파일명: a.pdf",
            4, 0.9,
        )

        with self.assertRaises(ValueError):
            pack_task_criticality(
                [item, item],
                query="HAI-ABC123의 파일 찾아줘",
                budget=4,
            )

        with self.assertRaises(ValueError):
            pack_task_criticality(
                [item],
                query="HAI-ABC123의 파일 찾아줘",
                budget=-1,
            )


if __name__ == "__main__":
    unittest.main()
