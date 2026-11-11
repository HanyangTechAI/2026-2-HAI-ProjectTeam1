import unittest
from datetime import datetime, timezone

from benchmark.action_time_preflight import (
    LEVELS,
    calibrate,
    evaluate_level,
    summarize,
)
from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem, MemoryType


NOW = datetime(2026, 11, 11, tzinfo=timezone.utc)


def embedder(text):
    # 테스트 전용 결정적 임베딩
    return (1.0, 0.0)


def make_memory(memory_id, content, importance):
    return MemoryItem(
        memory_id=memory_id,
        session_id="test_session",
        turn_id=2,
        memory_type=MemoryType.FACT,
        content=content,
        token_count=provisional_token_count(content),
        created_at=NOW,
        valid_from=NOW,
        importance=importance,
    ).to_dict()


def scenario(labels=None):
    metadata = {}

    if labels is not None:
        metadata["critical_flexible_memory_ids"] = labels

    return {
        "scenario_id": "preflight_fixture_1",
        "session_id": "test_session",
        "query": "회의 자료를 확인해줘",
        "current_turn": 5,
        "current_time": NOW.isoformat(),
        "metadata": metadata,
        "memory_snapshot": [
            make_memory(
                "fact_a",
                "회의 자료는 발표 슬라이드입니다.",
                0.9,
            ),
            make_memory(
                "fact_b",
                "회의 장소는 304호입니다.",
                0.5,
            ),
            make_memory(
                "noise_c",
                "개인 메모를 정리했습니다.",
                0.1,
            ),
        ],
    }


class ActionTimePreflightTests(unittest.TestCase):
    def setUp(self):
        self.system_prompt = "필요한 정보를 활용하세요."
        self.tool_definitions = "file.find_file(filename)"
        self.counter = provisional_token_count

    def _calibrate(self, s):
        return calibrate(
            s,
            self.system_prompt,
            self.tool_definitions,
            embedder,
            self.counter,
            top_k=5,
        )

    def _evaluate(self, s, cal, level):
        return evaluate_level(
            s,
            cal,
            level,
            self.system_prompt,
            self.tool_definitions,
            embedder,
            self.counter,
            top_k=5,
        )

    def test_floor_ceiling_and_same_budget(self):
        s = scenario(["fact_a"])
        cal = self._calibrate(s)

        self.assertGreater(
            cal["ceiling"],
            cal["floor"],
        )

        for level in LEVELS:
            rows = self._evaluate(s, cal, level)

            self.assertEqual(len(rows), 2)

            self.assertEqual(
                rows[0]["nominal_budget"],
                rows[1]["nominal_budget"],
            )

            self.assertEqual(
                rows[0]["effective_budget"],
                rows[1]["effective_budget"],
            )

            self.assertTrue(
                all(r["within_budget"] for r in rows)
            )

            self.assertTrue(
                all(r["mandatory_ok"] for r in rows)
            )

    def test_endpoint_controls_and_critical_labels(self):
        s = scenario(["fact_a"])
        cal = self._calibrate(s)

        low = self._evaluate(s, cal, 0.0)
        high = self._evaluate(s, cal, 1.0)

        self.assertTrue(
            all(
                not r["selected_flexible_ids"]
                for r in low
            )
        )

        expected = set(
            cal["reference_flexible_ids"]
        )

        self.assertEqual(
            len(expected),
            3,
        )

        self.assertTrue(
            all(
                set(r["selected_flexible_ids"]) == expected
                for r in high
            )
        )

        self.assertTrue(
            all(
                r["critical_flexible_recall"] == 0.0
                for r in low
            )
        )

        self.assertTrue(
            all(
                r["critical_flexible_recall"] == 1.0
                for r in high
            )
        )

    def test_unlabeled_recall_is_null(self):
        s = scenario()
        cal = self._calibrate(s)

        self.assertIsNone(cal["critical_ids"])

        self.assertTrue(
            all(
                r["critical_flexible_recall"] is None
                for r in self._evaluate(s, cal, 0.5)
            )
        )

    def test_invalid_critical_label_is_rejected(self):
        with self.assertRaises(ValueError):
            self._calibrate(
                scenario(["missing_id"])
            )

    def test_summary_pairing(self):
        s = scenario(["fact_a"])
        cal = self._calibrate(s)

        rows = [
            r
            for level in LEVELS
            for r in self._evaluate(s, cal, level)
        ]

        summary = summarize(rows)

        self.assertEqual(len(rows), 10)
        self.assertEqual(len(summary), 5)

        self.assertTrue(
            all(
                item["paired_cases"] == 1
                for item in summary
            )
        )


if __name__ == "__main__":
    unittest.main()