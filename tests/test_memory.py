from datetime import datetime, timedelta, timezone
import unittest

from memory.schema import MemoryItem, MemoryType
from memory.store import MemoryStore


BASE_TIME = datetime(
    2026,
    10,
    26,
    12,
    0,
    tzinfo=timezone.utc,
)


def memory(
    memory_id: str,
    *,
    session_id: str = "session_test",
    turn_id: int = 1,
    memory_type: MemoryType = MemoryType.FACT,
    content: str | None = None,
    token_count: int = 10,
    valid_from: datetime = BASE_TIME,
    valid_to: datetime | None = None,
    importance: float = 0.5,
    is_active: bool = True,
    is_protected: bool = False,
    supersedes: str | None = None,
    superseded_by: str | None = None,
    memory_key: str | None = None,
) -> MemoryItem:
    if memory_type == MemoryType.STATE and memory_key is None:
        memory_key = f"state.{memory_id}"

    return MemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        turn_id=turn_id,
        memory_type=memory_type,
        content=content or f"content:{memory_id}",
        token_count=token_count,
        created_at=valid_from,
        valid_from=valid_from,
        valid_to=valid_to,
        importance=importance,
        is_active=is_active,
        is_protected=is_protected,
        supersedes=supersedes,
        superseded_by=superseded_by,
        memory_key=memory_key,
    )


class MemoryStoreBasicTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_insert_and_get_memory(self):
        item = memory("fact_1")

        stored = self.store.insert(item)

        self.assertEqual(stored, item)
        self.assertEqual(
            self.store.get("fact_1"),
            item,
        )

    def test_get_unknown_memory_returns_none(self):
        self.assertIsNone(
            self.store.get("missing"),
        )

    def test_insert_rejects_non_memory_item(self):
        with self.assertRaises(TypeError):
            self.store.insert("not-a-memory")

    def test_duplicate_memory_id_is_rejected(self):
        first = memory("duplicate")
        second = memory(
            "duplicate",
            content="different content",
        )

        self.store.insert(first)

        with self.assertRaises(ValueError):
            self.store.insert(second)

    def test_get_rejects_empty_memory_id(self):
        with self.assertRaises(ValueError):
            self.store.get("")

        with self.assertRaises(ValueError):
            self.store.get("   ")

    def test_list_session_memories_returns_only_requested_session(self):
        self.store.insert(
            memory(
                "s1_m2",
                session_id="session_1",
                turn_id=2,
            )
        )
        self.store.insert(
            memory(
                "s2_m1",
                session_id="session_2",
                turn_id=1,
            )
        )
        self.store.insert(
            memory(
                "s1_m1",
                session_id="session_1",
                turn_id=1,
            )
        )

        result = self.store.list_session_memories(
            "session_1"
        )

        self.assertEqual(
            [item.memory_id for item in result],
            [
                "s1_m1",
                "s1_m2",
            ],
        )

    def test_list_unknown_session_returns_empty_list(self):
        self.assertEqual(
            self.store.list_session_memories(
                "unknown_session"
            ),
            [],
        )


class MemoryStoreKeyLookupTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_find_active_by_key_returns_active_memory(self):
        item = memory(
            "report_final",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
        )

        self.store.insert(item)

        result = self.store.find_active_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertIsNotNone(result)
        self.assertEqual(
            result.memory_id,
            "report_final",
        )

    def test_find_active_by_key_returns_none_when_missing(self):
        result = self.store.find_active_by_key(
            "session_test",
            "missing.key",
        )

        self.assertIsNone(result)

    def test_find_all_by_key_returns_all_versions(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        t3 = BASE_TIME + timedelta(days=2)

        v1 = memory(
            "v1",
            turn_id=1,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t1,
        )

        v2 = memory(
            "v2",
            turn_id=2,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t2,
        )

        final = memory(
            "final",
            turn_id=3,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t3,
        )

        self.store.insert(v1)
        self.store.insert(v2)
        self.store.insert(final)

        result = self.store.find_all_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            [item.memory_id for item in result],
            [
                "v1",
                "v2",
                "final",
            ],
        )

    def test_find_all_by_key_is_sorted_by_valid_from(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)
        t3 = BASE_TIME + timedelta(days=2)

        # 이미 history 정보가 들어 있는 snapshot 형태
        v1 = memory(
            "v1",
            turn_id=1,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t1,
            valid_to=t2,
            is_active=False,
            superseded_by="v2",
        )

        v2 = memory(
            "v2",
            turn_id=2,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t2,
            valid_to=t3,
            is_active=False,
            supersedes="v1",
            superseded_by="final",
        )

        final = memory(
            "final",
            turn_id=3,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=t3,
            supersedes="v2",
        )

        self.store.insert(v1)
        self.store.insert(v2)
        self.store.insert(final)

        result = self.store.find_all_by_key(
            "session_test",
            "report.current_file",
        )

        self.assertEqual(
            [item.memory_id for item in result],
            [
                "v1",
                "v2",
                "final",
            ],
        )

    def test_same_key_in_different_sessions_is_separated(self):
        self.store.insert(
            memory(
                "session_1_report",
                session_id="session_1",
                memory_type=MemoryType.STATE,
                memory_key="report.current_file",
            )
        )

        self.store.insert(
            memory(
                "session_2_report",
                session_id="session_2",
                memory_type=MemoryType.STATE,
                memory_key="report.current_file",
            )
        )

        session_1 = self.store.find_active_by_key(
            "session_1",
            "report.current_file",
        )

        session_2 = self.store.find_active_by_key(
            "session_2",
            "report.current_file",
        )

        self.assertEqual(
            session_1.memory_id,
            "session_1_report",
        )
        self.assertEqual(
            session_2.memory_id,
            "session_2_report",
        )


class ProtectedConstraintTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryStore()

    def test_find_active_protected_constraints(self):
        protected = memory(
            "approval_rule",
            memory_type=MemoryType.CONSTRAINT,
            memory_key=(
                "email.external.requires_approval"
            ),
            is_protected=True,
        )

        unprotected = memory(
            "normal_rule",
            turn_id=2,
            memory_type=MemoryType.CONSTRAINT,
            memory_key="email.normal_rule",
            is_protected=False,
        )

        fact = memory(
            "fact_1",
            turn_id=3,
            memory_type=MemoryType.FACT,
            is_protected=True,
        )

        self.store.insert(protected)
        self.store.insert(unprotected)
        self.store.insert(fact)

        result = (
            self.store
            .find_active_protected_constraints(
                "session_test"
            )
        )

        self.assertEqual(
            [item.memory_id for item in result],
            ["approval_rule"],
        )

    def test_superseded_constraint_is_not_returned(self):
        t1 = BASE_TIME
        t2 = BASE_TIME + timedelta(days=1)

        old_rule = memory(
            "approval_old",
            turn_id=1,
            memory_type=MemoryType.CONSTRAINT,
            memory_key=(
                "email.external.requires_approval"
            ),
            content=(
                "외부 이메일은 항상 "
                "승인을 받아야 한다."
            ),
            is_protected=True,
            valid_from=t1,
        )

        new_rule = memory(
            "approval_new",
            turn_id=2,
            memory_type=MemoryType.CONSTRAINT,
            memory_key=(
                "email.external.requires_approval"
            ),
            content=(
                "교수님 이메일은 "
                "승인을 받아야 한다."
            ),
            is_protected=True,
            valid_from=t2,
        )

        self.store.insert(old_rule)
        self.store.insert(new_rule)

        result = (
            self.store
            .find_active_protected_constraints(
                "session_test"
            )
        )

        self.assertEqual(
            [item.memory_id for item in result],
            ["approval_new"],
        )

        stored_old = self.store.get(
            "approval_old"
        )

        self.assertFalse(
            stored_old.is_active
        )
        self.assertFalse(
            stored_old.is_protected
        )


class VersionReferenceValidationTests(
    unittest.TestCase
):
    def setUp(self):
        self.store = MemoryStore()

    def test_unknown_supersedes_reference_is_rejected(self):
        item = memory(
            "v2",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            supersedes="missing_v1",
        )

        with self.assertRaises(ValueError):
            self.store.insert(item)

    def test_versioned_memory_without_key_is_rejected(self):
        item = memory(
            "fact_v2",
            memory_type=MemoryType.FACT,
            supersedes="fact_v1",
            memory_key=None,
        )

        with self.assertRaises(ValueError):
            self.store.insert(item)

    def test_supersedes_memory_from_different_session_is_rejected(self):
        old = memory(
            "old",
            session_id="session_1",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
        )

        self.store.insert(old)

        new = memory(
            "new",
            session_id="session_2",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            supersedes="old",
            valid_from=(
                BASE_TIME
                + timedelta(days=1)
            ),
        )

        with self.assertRaises(ValueError):
            self.store.insert(new)

    def test_supersedes_memory_with_different_key_is_rejected(self):
        old = memory(
            "old",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
        )

        self.store.insert(old)

        new = memory(
            "new",
            memory_type=MemoryType.STATE,
            memory_key="report.backup_file",
            supersedes="old",
            valid_from=(
                BASE_TIME
                + timedelta(days=1)
            ),
        )

        with self.assertRaises(ValueError):
            self.store.insert(new)

    def test_older_version_cannot_replace_newer_version(self):
        newer = memory(
            "newer",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=(
                BASE_TIME
                + timedelta(days=2)
            ),
        )

        self.store.insert(newer)

        older = memory(
            "older",
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            valid_from=BASE_TIME,
        )

        with self.assertRaises(ValueError):
            self.store.insert(older)


if __name__ == "__main__":
    unittest.main()