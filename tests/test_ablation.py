from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from memory.ablation import (
    ABLATION_PRESETS,
    FULL_ABLATION,
    SelectorAblationConfig,
    get_ablation_config,
)
from memory.query_analyzer import (
    QueryContext,
    TemporalIntent,
)
from memory.schema import (
    MemoryItem,
    MemoryType,
)
from memory.selector import (
    MemoryCandidate,
    PipelineStatus,
    retrieve_candidates,
    select_flexible,
    select_memory,
)


NOW = datetime(
    2026,
    11,
    3,
    12,
    0,
    tzinfo=timezone.utc,
)

SESSION = "ablation_test"


# ============================================================
# Helpers
# ============================================================


def make_memory(
    memory_id: str,
    *,
    content: str,
    memory_type: MemoryType = MemoryType.FACT,
    token_count: int = 10,
    turn_id: int = 1,
    importance: float = 0.5,
    is_active: bool = True,
    is_protected: bool = False,
    memory_key: str | None = None,
    valid_from: datetime = NOW,
    valid_to: datetime | None = None,
    supersedes: str | None = None,
    superseded_by: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        session_id=SESSION,
        turn_id=turn_id,
        memory_type=memory_type,
        content=content,
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


def fake_embedder(
    text: str,
) -> tuple[float, float, float]:
    """
    Ablation 테스트용 deterministic embedding.

    report / email / misc 축.
    """
    lowered = text.casefold()

    return (
        float(
            "report" in lowered
            or "보고서" in lowered
        ),
        float(
            "email" in lowered
            or "메일" in lowered
            or "이메일" in lowered
        ),
        float(
            "misc" in lowered
            or "기타" in lowered
        ),
    )


class FakeMemoryStore:
    """
    Selector의 MemoryStoreProtocol만 구현하는
    read-only 테스트 Store.
    """

    def __init__(
        self,
        memories,
    ):
        self.memories = tuple(memories)

    def list_session_memories(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        return [
            memory
            for memory in self.memories
            if memory.session_id == session_id
        ]

    def find_all_by_key(
        self,
        session_id: str,
        memory_key: str,
    ) -> list[MemoryItem]:
        result = [
            memory
            for memory in self.memories
            if (
                memory.session_id == session_id
                and memory.memory_key == memory_key
            )
        ]

        return sorted(
            result,
            key=lambda memory: (
                memory.valid_from,
                memory.turn_id,
                memory.memory_id,
            ),
        )

    def find_active_protected_constraints(
        self,
        session_id: str,
    ) -> list[MemoryItem]:
        return [
            memory
            for memory in self.memories
            if (
                memory.session_id == session_id
                and memory.memory_type
                == MemoryType.CONSTRAINT
                and memory.is_active
                and memory.is_protected
            )
        ]


def query_context(
    query: str = "현재 보고서 찾아줘",
) -> QueryContext:
    return QueryContext(
        query=query,
        temporal_intent=TemporalIntent.CURRENT,
        current_turn=100,
        current_time=NOW,
    )


# ============================================================
# Config Tests
# ============================================================


class AblationConfigTests(
    unittest.TestCase,
):

    def test_full_ablation_enables_all_components(
        self,
    ):
        self.assertEqual(
            FULL_ABLATION.name,
            "full",
        )

        self.assertTrue(
            FULL_ABLATION.constraint_preservation
        )

        self.assertTrue(
            FULL_ABLATION.temporal_resolution
        )

        self.assertTrue(
            FULL_ABLATION.mandatory_state
        )

        self.assertTrue(
            FULL_ABLATION.token_aware_packing
        )

    def test_expected_presets_exist(
        self,
    ):
        self.assertEqual(
            set(ABLATION_PRESETS),
            {
                "full",
                "no_constraint_preservation",
                "no_temporal",
                "no_state_mandatory",
                "no_budget_aware_packing",
            },
        )

    def test_get_ablation_config(
        self,
    ):
        config = get_ablation_config(
            "no_constraint_preservation"
        )

        self.assertFalse(
            config.constraint_preservation
        )

        self.assertTrue(
            config.temporal_resolution
        )

        self.assertTrue(
            config.mandatory_state
        )

        self.assertTrue(
            config.token_aware_packing
        )

    def test_unknown_ablation_is_rejected(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            get_ablation_config(
                "unknown"
            )

    def test_no_temporal_disables_state_mandatory(
        self,
    ):
        config = get_ablation_config(
            "no_temporal"
        )

        self.assertFalse(
            config.temporal_resolution
        )

        self.assertFalse(
            config.mandatory_state
        )

    def test_invalid_temporal_state_combination_is_rejected(
        self,
    ):
        with self.assertRaises(
            ValueError
        ):
            SelectorAblationConfig(
                name="invalid",
                temporal_resolution=False,
                mandatory_state=True,
            )

    def test_to_dict_is_serializable_shape(
        self,
    ):
        result = FULL_ABLATION.to_dict()

        self.assertEqual(
            result,
            {
                "name": "full",
                "constraint_preservation": True,
                "temporal_resolution": True,
                "mandatory_state": True,
                "token_aware_packing": True,
            },
        )


# ============================================================
# Constraint Preservation Hook
# ============================================================


class ConstraintAblationTests(
    unittest.TestCase,
):

    def setUp(self):
        self.constraint = make_memory(
            "approval_rule",
            content=(
                "외부 이메일 발송 전에는 "
                "반드시 승인이 필요하다."
            ),
            memory_type=MemoryType.CONSTRAINT,
            token_count=5,
            is_protected=True,
            memory_key=(
                "email.external."
                "requires_approval"
            ),
        )

        self.email_fact = make_memory(
            "email_fact",
            content=(
                "외부 이메일 담당자는 "
                "professor@example.com 이다."
            ),
            token_count=5,
        )

        self.store = FakeMemoryStore(
            (
                self.constraint,
                self.email_fact,
            )
        )

    def test_full_mode_excludes_protected_from_general_retrieval(
        self,
    ):
        candidates = retrieve_candidates(
            query_context=query_context(
                "외부 이메일 보내줘"
            ),
            memory_store=self.store,
            session_id=SESSION,
            embedder=fake_embedder,
            top_k=10,
            exclude_active_protected=True,
        )

        ids = {
            candidate.memory.memory_id
            for candidate in candidates
        }

        self.assertNotIn(
            "approval_rule",
            ids,
        )

        self.assertIn(
            "email_fact",
            ids,
        )

    def test_no_constraint_preservation_allows_constraint_into_general_pool(
        self,
    ):
        candidates = retrieve_candidates(
            query_context=query_context(
                "외부 이메일 보내줘"
            ),
            memory_store=self.store,
            session_id=SESSION,
            embedder=fake_embedder,
            top_k=10,
            exclude_active_protected=False,
        )

        ids = {
            candidate.memory.memory_id
            for candidate in candidates
        }

        self.assertIn(
            "approval_rule",
            ids,
        )

        self.assertIn(
            "email_fact",
            ids,
        )


# ============================================================
# Token-aware Packing Hook
# ============================================================


class TokenAwarePackingTests(
    unittest.TestCase,
):

    def test_token_aware_and_utility_only_choose_different_memories(
        self,
    ):
        expensive = MemoryCandidate(
            memory=make_memory(
                "expensive",
                content="expensive memory",
                token_count=10,
            ),
            relevance_score=0.9,
            utility_score=0.9,
        )

        efficient = MemoryCandidate(
            memory=make_memory(
                "efficient",
                content="efficient memory",
                token_count=2,
            ),
            relevance_score=0.5,
            utility_score=0.5,
        )

        token_aware = select_flexible(
            (
                expensive,
                efficient,
            ),
            budget=10,
            token_aware=True,
        )

        # 새 Candidate를 사용한다.
        # select_flexible이 selection_reason을 변경하기 때문.
        expensive_2 = MemoryCandidate(
            memory=expensive.memory,
            relevance_score=0.9,
            utility_score=0.9,
        )

        efficient_2 = MemoryCandidate(
            memory=efficient.memory,
            relevance_score=0.5,
            utility_score=0.5,
        )

        utility_only = select_flexible(
            (
                expensive_2,
                efficient_2,
            ),
            budget=10,
            token_aware=False,
        )

        self.assertEqual(
            [
                candidate.memory.memory_id
                for candidate in token_aware
            ],
            [
                "efficient",
            ],
        )

        self.assertEqual(
            [
                candidate.memory.memory_id
                for candidate in utility_only
            ],
            [
                "expensive",
            ],
        )

    def test_token_aware_false_still_respects_hard_budget(
        self,
    ):
        too_large = MemoryCandidate(
            memory=make_memory(
                "too_large",
                content="large",
                token_count=20,
            ),
            utility_score=1.0,
        )

        small = MemoryCandidate(
            memory=make_memory(
                "small",
                content="small",
                token_count=5,
            ),
            utility_score=0.5,
        )

        selected = select_flexible(
            (
                too_large,
                small,
            ),
            budget=10,
            token_aware=False,
        )

        self.assertEqual(
            [
                candidate.memory.memory_id
                for candidate in selected
            ],
            [
                "small",
            ],
        )

        self.assertEqual(
            too_large.selection_reason,
            "rejected_budget",
        )


# ============================================================
# End-to-End Selector Ablation
# ============================================================


class SelectorAblationIntegrationTests(
    unittest.TestCase,
):

    def setUp(self):
        old_time = (
            NOW
            - timedelta(days=30)
        )

        final_time = (
            NOW
            - timedelta(days=10)
        )

        self.report_v1 = make_memory(
            "report_v1",
            content=(
                "현재 보고서는 "
                "report_v1.pdf이다."
            ),
            memory_type=MemoryType.STATE,
            token_count=5,
            turn_id=10,
            memory_key="report.current_file",
            valid_from=old_time,
            valid_to=final_time,
            is_active=False,
            superseded_by="report_final",
        )

        self.report_final = make_memory(
            "report_final",
            content=(
                "현재 보고서는 "
                "report_final.pdf이다."
            ),
            memory_type=MemoryType.STATE,
            token_count=5,
            turn_id=20,
            memory_key="report.current_file",
            valid_from=final_time,
            is_active=True,
            supersedes="report_v1",
        )

        self.constraint = make_memory(
            "approval_rule",
            content=(
                "외부 이메일은 반드시 "
                "승인 후 발송해야 한다."
            ),
            memory_type=MemoryType.CONSTRAINT,
            token_count=5,
            turn_id=1,
            memory_key=(
                "email.external."
                "requires_approval"
            ),
            is_protected=True,
        )

        self.store = FakeMemoryStore(
            (
                self.report_v1,
                self.report_final,
                self.constraint,
            )
        )

    def test_full_mode_preserves_current_state(
        self,
    ):
        result = select_memory(
            query="현재 보고서 찾아줘",
            session_id=SESSION,
            memory_store=self.store,
            total_budget=100,
            fixed_tokens=10,
            current_turn=100,
            current_time=NOW,
            embedder=fake_embedder,
            top_k=10,
            ablation=FULL_ABLATION,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.OK,
        )

        self.assertIn(
            "report_final",
            result.selected_memory_ids,
        )

        self.assertNotIn(
            "report_v1",
            result.selected_memory_ids,
        )

        self.assertEqual(
            [
                memory.memory_id
                for memory in result.states
            ],
            [
                "report_final",
            ],
        )

    def test_no_state_mandatory_removes_state_from_mandatory_section(
        self,
    ):
        result = select_memory(
            query="현재 보고서 찾아줘",
            session_id=SESSION,
            memory_store=self.store,
            total_budget=100,
            fixed_tokens=10,
            current_turn=100,
            current_time=NOW,
            embedder=fake_embedder,
            top_k=10,
            ablation=get_ablation_config(
                "no_state_mandatory"
            ),
        )

        self.assertEqual(
            result.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            result.states,
            (),
        )

        # State 자체를 삭제하는 ablation이 아니라
        # mandatory 보장을 제거하는 ablation이다.
        self.assertIn(
            "report_final",
            {
                memory.memory_id
                for memory in result.flexible
            },
        )

    def test_full_constraint_mode_uses_protected_section(
        self,
    ):
        result = select_memory(
            query=(
                "외부 이메일을 보내줘"
            ),
            session_id=SESSION,
            memory_store=self.store,
            total_budget=100,
            fixed_tokens=10,
            current_turn=100,
            current_time=NOW,
            embedder=fake_embedder,
            top_k=10,
            ablation=FULL_ABLATION,
        )

        self.assertIn(
            "approval_rule",
            {
                memory.memory_id
                for memory in result.protected
            },
        )

        self.assertNotIn(
            "approval_rule",
            {
                memory.memory_id
                for memory in result.flexible
            },
        )

    def test_no_constraint_preservation_removes_mandatory_protection(
        self,
    ):
        result = select_memory(
            query=(
                "외부 이메일을 보내줘"
            ),
            session_id=SESSION,
            memory_store=self.store,
            total_budget=100,
            fixed_tokens=10,
            current_turn=100,
            current_time=NOW,
            embedder=fake_embedder,
            top_k=10,
            ablation=get_ablation_config(
                "no_constraint_preservation"
            ),
        )

        self.assertEqual(
            result.protected,
            (),
        )

        # constraint 자체를 삭제하는 게 아니라
        # 일반 retrieval/flexible 경쟁으로 보낸다.
        self.assertIn(
            "approval_rule",
            result.retrieved_memory_ids,
        )

    def test_no_temporal_does_not_expand_or_resolve_versions(
        self,
    ):
        result = select_memory(
            query="현재 보고서 찾아줘",
            session_id=SESSION,
            memory_store=self.store,
            total_budget=100,
            fixed_tokens=10,
            current_turn=100,
            current_time=NOW,
            embedder=fake_embedder,

            # tie-break상 report_final만 초기 retrieval되게 한다.
            top_k=1,

            ablation=get_ablation_config(
                "no_temporal"
            ),
        )

        self.assertEqual(
            result.states,
            (),
        )

        # no_temporal에서는 initial retrieval 결과를
        # 그대로 사용하므로 version-chain mandatory
        # resolution이 발생하지 않는다.
        self.assertEqual(
            set(result.selected_memory_ids),
            set(result.retrieved_memory_ids),
        )


# ============================================================
# Independence Tests
# ============================================================


class AblationIndependenceTests(
    unittest.TestCase,
):

    def test_no_constraint_changes_only_constraint_flag(
        self,
    ):
        config = get_ablation_config(
            "no_constraint_preservation"
        )

        self.assertFalse(
            config.constraint_preservation
        )

        self.assertTrue(
            config.temporal_resolution
        )

        self.assertTrue(
            config.mandatory_state
        )

        self.assertTrue(
            config.token_aware_packing
        )

    def test_no_state_mandatory_keeps_other_components(
        self,
    ):
        config = get_ablation_config(
            "no_state_mandatory"
        )

        self.assertTrue(
            config.constraint_preservation
        )

        self.assertTrue(
            config.temporal_resolution
        )

        self.assertFalse(
            config.mandatory_state
        )

        self.assertTrue(
            config.token_aware_packing
        )

    def test_no_budget_aware_changes_only_packing(
        self,
    ):
        config = get_ablation_config(
            "no_budget_aware_packing"
        )

        self.assertTrue(
            config.constraint_preservation
        )

        self.assertTrue(
            config.temporal_resolution
        )

        self.assertTrue(
            config.mandatory_state
        )

        self.assertFalse(
            config.token_aware_packing
        )


if __name__ == "__main__":
    unittest.main()