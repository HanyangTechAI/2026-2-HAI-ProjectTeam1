"""시간/버전 선택 검증. interfaces.md 8~11절, EC-03/05/07/08/09, TV-01/02.

실행: python -B -m unittest memory.test_selector_temporal
외부 API 없이 실제 Analyzer/Selector/Store를 사용한다.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
import unittest

from memory.query_analyzer import analyze_query
from memory.schema import MemoryItem
from memory.selector import (
    MemoryCandidate, PipelineStatus, expand_version_chains,
    resolve_temporal_versions, resolve_required_states,
    is_constraint_applicable, select_memory,
)
from memory.store import MemoryStore, MemoryStoreConsistencyError


KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 12, tzinfo=KST)


def at(month, day=1):
    return datetime(2026, month, day, tzinfo=KST)


def item(mid, *, start=None, end=None, active=True, session="s", turn=1,
         key="report.current_file", kind="state", content=None, protected=False):
    return MemoryItem(memory_id=mid, session_id=session, turn_id=turn, memory_type=kind,
                      content=content or f"report_{mid}.pdf", token_count=5,
                      created_at=start or at(6), valid_from=start or at(6), valid_to=end,
                      is_active=active, memory_key=key, is_protected=protected)


def versions():
    return [item("v1", end=at(8, 15), active=False),
            item("v2", start=at(8, 15), end=at(9, 20), active=False, turn=2),
            item("final", start=at(9, 20), turn=3)]


def candidates(memories):
    return [MemoryCandidate(memory=m, relevance_score=0.9) for m in memories]


def query(text):
    return analyze_query(text, 100, NOW)


def ids(items):
    return [x.memory.memory_id if isinstance(x, MemoryCandidate) else x.memory_id for x in items]


class TemporalSelectionTests(unittest.TestCase):
    def test_current_and_unspecified_ec05(self):
        for text in ("지금 보고서", "보고서 알려줘"):
            with self.subTest(text=text):
                self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query(text))), ["final"])

    def test_historical_superseded_version_tv01(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query("8월 20일 보고서"))), ["v2"])

    def test_relative_version(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query("두 달 전 보고서"))), ["v1"])

    def test_exact_boundary_tv02(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query("8월 15일 보고서"))), ["v2"])

    def test_just_before_boundary(self):
        q = replace(query("8월 15일 보고서"), target_time=at(8, 15) - timedelta(microseconds=1))
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), q)), ["v1"])

    def test_open_ended_version(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query("10월 1일 보고서"))), ["final"])

    def test_no_historical_version_ec03(self):
        self.assertEqual(resolve_temporal_versions(candidates(versions()), query("2024년 8월 보고서")), [])

    def test_range_maximum_overlap(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), query("8월 보고서"))), ["v2"])

    def test_range_longer_overlap_beats_recency(self):
        memories = [item("long", start=at(8), end=at(8, 25), active=False), item("recent", start=at(8, 25))]
        self.assertEqual(ids(resolve_temporal_versions(candidates(memories), query("8월 보고서"))), ["long"])

    def test_range_tie_uses_later_start(self):
        memories = [item("old", start=at(8), end=at(8, 11), active=False), item("new", start=at(8, 11))]
        q = replace(query("8월 보고서"), target_range=(at(8), at(8, 21)))
        self.assertEqual(ids(resolve_temporal_versions(candidates(memories), q)), ["new"])

    def test_range_full_tie_is_order_independent(self):
        memories = [item("z", start=at(8)), item("a", start=at(8))]
        for ordered in (memories, list(reversed(memories))):
            self.assertEqual(ids(resolve_temporal_versions(candidates(ordered), query("8월 보고서"))), ["a"])

    def test_range_excludes_empty_interval_and_touching_end(self):
        memories = [item("zero", start=at(8), end=at(8)), item("before", end=at(8), active=False)]
        self.assertEqual(resolve_temporal_versions(candidates(memories), query("8월 보고서")), [])

    def test_duplicate_active_and_point_valid_versions(self):
        for text in ("현재 보고서", "8월 20일 보고서"):
            with self.subTest(text=text), self.assertRaises(MemoryStoreConsistencyError):
                resolve_temporal_versions(candidates([item("a"), item("b")]), query(text))

    def test_sessions_are_not_grouped_together(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates([item("a"), item("b", session="other")]), query("현재 보고서"))), ["a", "b"])

    def test_future_turn_excluded(self):
        self.assertEqual(ids(resolve_temporal_versions(candidates([item("a"), item("future", turn=101)]), query("현재 보고서"))), ["a"])

    def test_naive_aware_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            resolve_temporal_versions(candidates([item("naive", start=at(6).replace(tzinfo=None))]), query("8월 20일 보고서"))

    def test_equivalent_timezone_instant(self):
        q = replace(query("8월 15일 보고서"), target_time=at(8, 15).astimezone(timezone.utc))
        self.assertEqual(ids(resolve_temporal_versions(candidates(versions()), q)), ["v2"])

    def test_expansion_recovers_missing_current_version_ec08(self):
        store = MemoryStore()
        for memory in versions():
            store.insert(memory)
        hit = MemoryCandidate(store.get("v1"), relevance_score=0.99)
        expanded = expand_version_chains([hit, hit], store)
        self.assertEqual(ids(expanded), ["v1", "v2", "final"])
        self.assertEqual(ids(resolve_temporal_versions(expanded, query("현재 보고서"))), ["final"])
        self.assertEqual(ids(resolve_temporal_versions(expanded, query("8월 20일 보고서"))), ["v2"])

    def test_required_states_exclude_unrelated_state(self):
        rows = candidates([item("report"), item("meeting", key="meeting.start", content="월요일 회의")])
        self.assertEqual(ids(resolve_required_states(rows, query("보고서 파일 찾아줘"))), ["report"])

    def test_constraint_applies_to_action_not_unrelated_query(self):
        constraint = item("approval", kind="constraint", key="email.approval",
                          content="외부 이메일은 승인 후 발송한다.", protected=True)
        self.assertTrue(is_constraint_applicable(constraint, query("교수님께 보고서 보내줘")))
        self.assertFalse(is_constraint_applicable(constraint, query("내일 일정 보여줘")))


class AnalyzerSelectorIntegrationTests(unittest.TestCase):
    def select(self, text):
        store = MemoryStore()
        for memory in versions():
            store.insert(memory)
        store.insert(item("approval", kind="constraint", key="email.approval", start=at(9),
                          content="외부 이메일은 승인 후 발송한다.", protected=True))

        def embed(value):
            # v1만 Top-1에 검색되게 해 전체 버전 조회를 실제로 거치게 한다.
            return (0., 1.) if "report_v2.pdf" in value or "report_final.pdf" in value else (1., 0.)

        return select_memory(text, "s", store, 500, 10, 100, NOW, embedder=embed, top_k=1)

    def test_three_intents_through_real_selector(self):
        for text, expected in (("현재 보고서 찾아줘", "final"), ("8월 20일 보고서 찾아줘", "v2"), ("두 달 전 보고서 찾아줘", "v1")):
            with self.subTest(text=text):
                result = self.select(text)
                self.assertEqual(result.status, PipelineStatus.OK)
                self.assertEqual(ids(result.states), [expected])
                self.assertEqual(result.retrieved_memory_ids, ("v1",))

    def test_historical_state_current_protection_ec09(self):
        result = self.select("6월 보고서를 지금 교수님께 보내줘")
        self.assertEqual(ids(result.states), ["v1"])
        self.assertEqual(ids(result.protected), ["approval"])

    def test_no_version_does_not_fall_back(self):
        result = self.select("2024년 보고서 찾아줘")
        self.assertEqual(result.states, ())
        self.assertEqual(result.flexible, ())


if __name__ == "__main__":
    unittest.main()
