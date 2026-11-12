from __future__ import annotations

from datetime import datetime, timezone
import unittest

from memory.retrieval import (
    cosine_similarity,
    embed_vector,
    semantic_retrieve,
)
from memory.schema import (
    MemoryItem,
    MemoryType,
)


NOW = datetime(
    2026,
    11,
    1,
    tzinfo=timezone.utc,
)


def fake_embedder(
    text: str,
) -> tuple[float, float]:
    """
    테스트용 deterministic embedding.
    """
    return (
        float(
            "report" in text.lower()
            or "보고서" in text
        ),
        float(
            "email" in text.lower()
            or "메일" in text
        ),
    )


def memory(
    memory_id: str,
    content: str,
) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        session_id="test",
        turn_id=1,
        memory_type=MemoryType.FACT,
        content=content,
        token_count=10,
        created_at=NOW,
        valid_from=NOW,
    )


class RetrievalTests(
    unittest.TestCase,
):
    def test_embed_vector(self):
        vector = embed_vector(
            "보고서",
            fake_embedder,
        )

        self.assertEqual(
            vector,
            (1.0, 0.0),
        )

    def test_cosine_identical(self):
        score = cosine_similarity(
            (1.0, 0.0),
            (1.0, 0.0),
        )

        self.assertAlmostEqual(
            score,
            1.0,
        )

    def test_cosine_orthogonal(self):
        score = cosine_similarity(
            (1.0, 0.0),
            (0.0, 1.0),
        )

        self.assertAlmostEqual(
            score,
            0.0,
        )

    def test_zero_vector(self):
        score = cosine_similarity(
            (0.0, 0.0),
            (1.0, 0.0),
        )

        self.assertEqual(
            score,
            0.0,
        )

    def test_dimension_mismatch(self):
        with self.assertRaises(
            ValueError
        ):
            cosine_similarity(
                (1.0, 0.0),
                (1.0,),
            )

    def test_semantic_retrieve(self):
        memories = [
            memory(
                "email",
                "외부 이메일 발송 규칙",
            ),
            memory(
                "report",
                "현재 보고서는 report_final.pdf",
            ),
        ]

        hits = semantic_retrieve(
            query="현재 보고서 찾아줘",
            memories=memories,
            embedder=fake_embedder,
            top_k=2,
        )

        self.assertEqual(
            hits[0].memory.memory_id,
            "report",
        )

        self.assertGreater(
            hits[0].score,
            hits[1].score,
        )

    def test_top_k(self):
        memories = [
            memory(
                "m1",
                "보고서 A",
            ),
            memory(
                "m2",
                "보고서 B",
            ),
            memory(
                "m3",
                "메일 C",
            ),
        ]

        hits = semantic_retrieve(
            query="보고서",
            memories=memories,
            embedder=fake_embedder,
            top_k=1,
        )

        self.assertEqual(
            len(hits),
            1,
        )

    def test_tie_breaks_by_memory_id(self):
        memories = [
            memory(
                "b",
                "보고서 B",
            ),
            memory(
                "a",
                "보고서 A",
            ),
        ]

        hits = semantic_retrieve(
            query="보고서",
            memories=memories,
            embedder=fake_embedder,
            top_k=2,
        )

        self.assertEqual(
            [
                hit.memory.memory_id
                for hit in hits
            ],
            [
                "a",
                "b",
            ],
        )


if __name__ == "__main__":
    unittest.main()