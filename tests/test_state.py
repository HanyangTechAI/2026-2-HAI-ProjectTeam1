from datetime import datetime, timedelta, timezone
import unittest

from memory.schema import MemoryItem, MemoryType
from memory.store import (
    MemoryStore,
    MemoryStoreConsistencyError,
)


BASE_TIME = datetime(
    2026,
    10,
    26,
    12,
    0,
    tzinfo=timezone.utc,
)


def state(
    memory_id: str,
    *,
    session_id: str = "session_test",
    turn_id: int = 1,
    memory_key: str = "report.current_file",
    content: str | None = None,
    valid_from: datetime = BASE_TIME,
    valid_to: datetime | None = None,
    is_active: bool = True,
    supersedes: str | None = None,
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        turn_id=turn_id,
        memory_type=MemoryType.STATE,
        content=content or memory_id,
        token_count=10,
        created_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_to,
        importance=0.8,
        is_active=is_active,
        is_protected=False,
        supersedes=supersedes,
        superseded_by=superseded_by,
        memory_key=memory_key,
    )


class StateVersioningTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_first_state_becomes_active(self):
        v1 = state(
            "v1",
            content="report_v1.pdf",
        )

        stored = self.store.insert(v1)

        self.assertTrue(stored.is_active)
        self.assertIsNone(stored.supersedes)
        self.assertIsNone(stored.superseded_by)

        active = self.store.find_active_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            active.memory_id,
            "v1",
        )

    def test_new_state_supersedes_existing_active_state(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)

        v1 = state(
            "v1",
            turn_id=1,
            content="report_v1.pdf",
            valid_from=t1,
        )

        v2 = state(
            "v2",
            turn_id=2,
            content="report_v2.pdf",
            valid_from=t2,
        )

        self.store.insert(v1)
        stored_v2 = self.store.insert(v2)

        stored_v1 = self.store.get("v1")

        self.assertFalse(stored_v1.is_active)
        self.assertEqual(
            stored_v1.valid_to,
            t2,
        )
        self.assertEqual(
            stored_v1.superseded_by,
            "v2",
        )

        self.assertTrue(stored_v2.is_active)
        self.assertEqual(
            stored_v2.supersedes,
            "v1",
        )
        self.assertIsNone(
            stored_v2.superseded_by
        )

    def test_active_state_is_always_latest_version(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        t3 = BASE_TIME + timedelta(days=2)

        self.store.insert(
            state(
                "v1",
                turn_id=1,
                valid_from=t1,
            )
        )

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                valid_from=t2,
            )
        )

        self.store.insert(
            state(
                "final",
                turn_id=3,
                valid_from=t3,
            )
        )

        active = self.store.find_active_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            active.memory_id,
            "final",
        )

    def test_three_version_chain_is_linked_correctly(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        t3 = BASE_TIME + timedelta(days=2)

        self.store.insert(
            state(
                "v1",
                turn_id=1,
                content="report_v1.pdf",
                valid_from=t1,
            )
        )

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                content="report_v2.pdf",
                valid_from=t2,
            )
        )

        self.store.insert(
            state(
                "final",
                turn_id=3,
                content="report_final.pdf",
                valid_from=t3,
            )
        )

        v1 = self.store.get("v1")
        v2 = self.store.get("v2")
        final = self.store.get("final")

        self.assertFalse(v1.is_active)
        self.assertEqual(
            v1.superseded_by,
            "v2",
        )
        self.assertEqual(
            v1.valid_to,
            t2,
        )

        self.assertFalse(v2.is_active)
        self.assertEqual(
            v2.supersedes,
            "v1",
        )
        self.assertEqual(
            v2.superseded_by,
            "final",
        )
        self.assertEqual(
            v2.valid_to,
            t3,
        )

        self.assertTrue(final.is_active)
        self.assertEqual(
            final.supersedes,
            "v2",
        )
        self.assertIsNone(
            final.superseded_by
        )

    def test_version_chain_keeps_all_historical_states(self):
        self.store.insert(
            state(
                "v1",
                turn_id=1,
                valid_from=BASE_TIME,
            )
        )

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                valid_from=(
                    BASE_TIME
                    + timedelta(days=1)
                ),
            )
        )

        self.store.insert(
            state(
                "final",
                turn_id=3,
                valid_from=(
                    BASE_TIME
                    + timedelta(days=2)
                ),
            )
        )

        versions = self.store.find_all_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            [item.memory_id for item in versions],
            [
                "v1",
                "v2",
                "final",
            ],
        )

        self.assertEqual(
            len(versions),
            3,
        )

    def test_only_one_active_state_exists_per_key(self):
        self.store.insert(
            state(
                "v1",
                turn_id=1,
                valid_from=BASE_TIME,
            )
        )

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                valid_from=(
                    BASE_TIME
                    + timedelta(days=1)
                ),
            )
        )

        self.store.insert(
            state(
                "v3",
                turn_id=3,
                valid_from=(
                    BASE_TIME
                    + timedelta(days=2)
                ),
            )
        )

        versions = self.store.find_all_by_key(
            "session_test",
            "report.current_file",
        )

        active_versions = [
            item
            for item in versions
            if item.is_active
        ]

        self.assertEqual(
            len(active_versions),
            1,
        )

        self.assertEqual(
            active_versions[0].memory_id,
            "v3",
        )

    def test_different_memory_keys_do_not_supersede_each_other(self):
        report = state(
            "report",
            memory_key="report.current_file",
            content="report_final.pdf",
        )

        dataset = state(
            "dataset",
            memory_key="dataset.current_file",
            content="dataset_final.csv",
        )

        self.store.insert(report)
        self.store.insert(dataset)

        report_active = (
            self.store.find_active_by_key(
                "session_test",
                "report.current_file",
            )
        )

        dataset_active = (
            self.store.find_active_by_key(
                "session_test",
                "dataset.current_file",
            )
        )

        self.assertTrue(
            report_active.is_active
        )
        self.assertTrue(
            dataset_active.is_active
        )

        self.assertEqual(
            report_active.memory_id,
            "report",
        )

        self.assertEqual(
            dataset_active.memory_id,
            "dataset",
        )

    def test_same_key_in_different_sessions_is_independent(self):
        session_1_v1 = state(
            "s1_v1",
            session_id="session_1",
            valid_from=BASE_TIME,
        )

        session_2_v1 = state(
            "s2_v1",
            session_id="session_2",
            valid_from=BASE_TIME,
        )

        self.store.insert(session_1_v1)
        self.store.insert(session_2_v1)

        self.store.insert(
            state(
                "s1_v2",
                session_id="session_1",
                turn_id=2,
                valid_from=(
                    BASE_TIME
                    + timedelta(days=1)
                ),
            )
        )

        active_1 = (
            self.store.find_active_by_key(
                "session_1",
                "report.current_file",
            )
        )

        active_2 = (
            self.store.find_active_by_key(
                "session_2",
                "report.current_file",
            )
        )

        self.assertEqual(
            active_1.memory_id,
            "s1_v2",
        )

        self.assertEqual(
            active_2.memory_id,
            "s2_v1",
        )

        self.assertTrue(
            active_2.is_active
        )

    def test_historical_inactive_state_does_not_replace_active_state(self):
        current = state(
            "current",
            turn_id=10,
            content="report_final.pdf",
            valid_from=(
                BASE_TIME
                + timedelta(days=10)
            ),
        )

        historical = state(
            "historical",
            turn_id=1,
            content="report_old.pdf",
            valid_from=BASE_TIME,
            valid_to=(
                BASE_TIME
                + timedelta(days=5)
            ),
            is_active=False,
        )

        self.store.insert(current)
        self.store.insert(historical)

        active = self.store.find_active_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            active.memory_id,
            "current",
        )

        self.assertTrue(
            active.is_active
        )

    def test_input_memory_object_is_not_mutated(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)

        original_v1 = state(
            "v1",
            valid_from=t1,
        )

        self.store.insert(original_v1)

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                valid_from=t2,
            )
        )

        # MemoryItem은 frozen dataclass이고,
        # Store는 replace()로 내부 객체를 교체한다.
        self.assertTrue(
            original_v1.is_active
        )
        self.assertIsNone(
            original_v1.valid_to
        )
        self.assertIsNone(
            original_v1.superseded_by
        )

        stored_v1 = self.store.get("v1")

        self.assertFalse(
            stored_v1.is_active
        )
        self.assertEqual(
            stored_v1.valid_to,
            t2,
        )
        self.assertEqual(
            stored_v1.superseded_by,
            "v2",
        )


class StateConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_older_state_cannot_replace_newer_active_state(self):
        newer = state(
            "newer",
            valid_from=(
                BASE_TIME
                + timedelta(days=2)
            ),
        )

        older = state(
            "older",
            turn_id=2,
            valid_from=BASE_TIME,
        )

        self.store.insert(newer)

        with self.assertRaises(ValueError):
            self.store.insert(older)

    def test_explicit_supersedes_must_match_current_active_state(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        t3 = BASE_TIME + timedelta(days=2)

        self.store.insert(
            state(
                "v1",
                valid_from=t1,
            )
        )

        self.store.insert(
            state(
                "v2",
                turn_id=2,
                valid_from=t2,
            )
        )

        invalid_v3 = state(
            "v3",
            turn_id=3,
            valid_from=t3,
            supersedes="v1",
        )

        with self.assertRaises(
            MemoryStoreConsistencyError
        ):
            self.store.insert(
                invalid_v3
            )


if __name__ == "__main__":
    unittest.main()