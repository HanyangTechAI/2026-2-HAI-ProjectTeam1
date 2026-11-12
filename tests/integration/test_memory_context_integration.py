from datetime import datetime, timedelta, timezone
import unittest

from context.builder import build_context
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus, SelectionResult
from memory.store import MemoryStore


BASE_TIME = datetime(
    2026,
    10,
    26,
    12,
    0,
    tzinfo=timezone.utc,
)

SESSION_ID = "session_integration"

SYSTEM_PROMPT = (
    "사용자의 제약을 준수하고 현재 상태를 기준으로 행동한다."
)

TOOL_DEFINITIONS = (
    "email.request_approval(recipient, attachment)\n"
    "email.send_email(recipient, attachment)\n"
    "file.select_file(filename)"
)

QUERY = "교수님께 최신 보고서를 보내줘."


def make_memory(
    memory_id: str,
    *,
    memory_type: MemoryType,
    content: str,
    turn_id: int,
    valid_from: datetime,
    token_count: int = 5,
    memory_key: str | None = None,
    is_protected: bool = False,
) -> MemoryItem:
    return MemoryItem(
        memory_id=memory_id,
        session_id=SESSION_ID,
        turn_id=turn_id,
        memory_type=memory_type,
        content=content,
        token_count=token_count,
        created_at=valid_from,
        valid_from=valid_from,
        memory_key=memory_key,
        is_active=True,
        is_protected=is_protected,
    )


def selection_from_store(
    store: MemoryStore,
    *,
    state_keys: tuple[str, ...] = (),
) -> SelectionResult:
    """
    Selector가 아직 미구현인 3주차 단계에서
    MemoryStore → Context Builder integration만 검증하기 위한 helper.
    """

    protected = tuple(
        store.find_active_protected_constraints(
            SESSION_ID
        )
    )

    states = []

    for memory_key in state_keys:
        state = store.find_active_by_key(
            SESSION_ID,
            memory_key,
        )

        if state is not None:
            states.append(state)

    states_tuple = tuple(states)

    selected = protected + states_tuple

    return SelectionResult(
        status=PipelineStatus.OK,
        protected=protected,
        states=states_tuple,
        flexible=(),
        selected_memory_ids=tuple(
            memory.memory_id
            for memory in selected
        ),
        memory_tokens=sum(
            memory.token_count
            for memory in selected
        ),
    )


class MemoryContextIntegrationTests(
    unittest.TestCase
):
    def setUp(self):
        self.store = MemoryStore()

        self.approval_rule = make_memory(
            "approval_rule",
            memory_type=MemoryType.CONSTRAINT,
            content=(
                "외부 이메일은 반드시 사용자 승인 후 "
                "발송해야 한다."
            ),
            turn_id=1,
            valid_from=BASE_TIME,
            token_count=8,
            memory_key=(
                "email.external.requires_approval"
            ),
            is_protected=True,
        )

        self.report_v1 = make_memory(
            "report_v1",
            memory_type=MemoryType.STATE,
            content=(
                "현재 보고서는 report_v1.pdf이다."
            ),
            turn_id=2,
            valid_from=(
                BASE_TIME
                + timedelta(hours=1)
            ),
            token_count=6,
            memory_key="report.current_file",
        )

        self.report_final = make_memory(
            "report_final",
            memory_type=MemoryType.STATE,
            content=(
                "현재 보고서는 report_final.pdf이다."
            ),
            turn_id=3,
            valid_from=(
                BASE_TIME
                + timedelta(hours=2)
            ),
            token_count=6,
            memory_key="report.current_file",
        )

    def test_active_state_and_protected_constraint_are_built_into_context(
        self,
    ):
        """
        MemoryStore에서 조회한 Protected Constraint와
        최신 State가 실제 Context에 포함되는지 검증한다.
        """

        self.store.insert(
            self.approval_rule
        )
        self.store.insert(
            self.report_v1
        )
        self.store.insert(
            self.report_final
        )

        selection = selection_from_store(
            self.store,
            state_keys=(
                "report.current_file",
            ),
        )

        result = build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=QUERY,
            selection=selection,
            total_budget=1000,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.OK,
        )

        self.assertIsNotNone(
            result.context
        )

        self.assertIn(
            "[PROTECTED CONSTRAINTS]",
            result.context,
        )

        self.assertIn(
            self.approval_rule.content,
            result.context,
        )

        self.assertIn(
            "[STATE]",
            result.context,
        )

        self.assertIn(
            self.report_final.content,
            result.context,
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
            ),
        )

    def test_superseded_state_is_not_added_to_context(
        self,
    ):
        """
        report_v1 → report_final로 State가 갱신되면
        이전 버전은 Store에는 남지만 Context에는 들어가지 않아야 한다.
        """

        self.store.insert(
            self.report_v1
        )
        self.store.insert(
            self.report_final
        )

        old_state = self.store.get(
            "report_v1"
        )

        active_state = (
            self.store.find_active_by_key(
                SESSION_ID,
                "report.current_file",
            )
        )

        self.assertIsNotNone(
            old_state
        )
        self.assertIsNotNone(
            active_state
        )

        self.assertFalse(
            old_state.is_active
        )

        self.assertEqual(
            old_state.superseded_by,
            "report_final",
        )

        self.assertEqual(
            active_state.memory_id,
            "report_final",
        )

        selection = selection_from_store(
            self.store,
            state_keys=(
                "report.current_file",
            ),
        )

        result = build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=QUERY,
            selection=selection,
            total_budget=1000,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.OK,
        )

        self.assertIn(
            "report_final.pdf",
            result.context,
        )

        self.assertNotIn(
            "report_v1.pdf",
            result.context,
        )

        self.assertNotIn(
            "report_v1",
            result.selected_memory_ids,
        )

        self.assertIn(
            "report_final",
            result.selected_memory_ids,
        )

    def test_superseded_protected_constraint_is_not_added_to_context(
        self,
    ):
        """
        Protected Constraint도 새 버전으로 대체되면
        이전 Constraint가 Context에 남지 않는지 검증한다.
        """

        old_rule = make_memory(
            "approval_rule_v1",
            memory_type=MemoryType.CONSTRAINT,
            content=(
                "외부 이메일은 바로 발송할 수 있다."
            ),
            turn_id=1,
            valid_from=BASE_TIME,
            token_count=7,
            memory_key=(
                "email.external.requires_approval"
            ),
            is_protected=True,
        )

        new_rule = make_memory(
            "approval_rule_v2",
            memory_type=MemoryType.CONSTRAINT,
            content=(
                "외부 이메일은 반드시 승인 후 발송한다."
            ),
            turn_id=2,
            valid_from=(
                BASE_TIME
                + timedelta(hours=1)
            ),
            token_count=7,
            memory_key=(
                "email.external.requires_approval"
            ),
            is_protected=True,
        )

        self.store.insert(
            old_rule
        )
        self.store.insert(
            new_rule
        )

        stored_old_rule = self.store.get(
            "approval_rule_v1"
        )

        self.assertFalse(
            stored_old_rule.is_active
        )

        self.assertFalse(
            stored_old_rule.is_protected
        )

        protected = (
            self.store
            .find_active_protected_constraints(
                SESSION_ID
            )
        )

        self.assertEqual(
            [
                memory.memory_id
                for memory in protected
            ],
            [
                "approval_rule_v2",
            ],
        )

        selection = selection_from_store(
            self.store
        )

        result = build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=QUERY,
            selection=selection,
            total_budget=1000,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.OK,
        )

        self.assertIn(
            new_rule.content,
            result.context,
        )

        self.assertNotIn(
            old_rule.content,
            result.context,
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule_v2",
            ),
        )

    def test_mandatory_store_memories_over_budget_stop_context_build(
        self,
    ):
        """
        Store에서 읽은 Protected + State가 필수 정보인데
        최종 Context Budget에 들어가지 않으면
        Context 생성이 중단되어야 한다.
        """

        self.store.insert(
            self.approval_rule
        )
        self.store.insert(
            self.report_final
        )

        selection = selection_from_store(
            self.store,
            state_keys=(
                "report.current_file",
            ),
        )

        valid_result = build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=QUERY,
            selection=selection,
            total_budget=1000,
        )

        self.assertEqual(
            valid_result.status,
            PipelineStatus.OK,
        )

        insufficient_budget = (
            valid_result.total_input_tokens - 1
        )

        result = build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=QUERY,
            selection=selection,
            total_budget=insufficient_budget,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

        self.assertIsNone(
            result.context
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
            ),
        )

        self.assertEqual(
            result.unused_budget,
            0,
        )


if __name__ == "__main__":
    unittest.main()