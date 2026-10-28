from datetime import datetime, timezone
import unittest

from context.builder import (
    build_context,
    fixed_context_tokens,
    provisional_token_count,
)
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus, SelectionResult


NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)

SYSTEM_PROMPT = "제약을 지키고 필요한 상태를 사용한다."
TOOL_DEFINITIONS = "email.send_email()\nfile.select_file()"
QUERY = "교수님께 최신 보고서를 보내줘."


def memory(
    memory_id: str,
    content: str,
    *,
    memory_type: MemoryType = MemoryType.FACT,
    token_count: int = 1,
    is_protected: bool = False,
    memory_key: str | None = None,
) -> MemoryItem:
    if memory_type == MemoryType.STATE and memory_key is None:
        memory_key = f"state.{memory_id}"

    return MemoryItem(
        memory_id=memory_id,
        session_id="session_test",
        turn_id=1,
        memory_type=memory_type,
        content=content,
        token_count=token_count,
        created_at=NOW,
        valid_from=NOW,
        is_active=True,
        is_protected=is_protected,
        memory_key=memory_key,
    )


def selection(
    *,
    protected=(),
    states=(),
    flexible=(),
    status: PipelineStatus = PipelineStatus.OK,
) -> SelectionResult:
    selected = tuple(protected) + tuple(states) + tuple(flexible)

    return SelectionResult(
        status=status,
        protected=tuple(protected),
        states=tuple(states),
        flexible=tuple(flexible),
        selected_memory_ids=tuple(item.memory_id for item in selected),
        memory_tokens=sum(item.token_count for item in selected),
    )


class ContextBuilderTests(unittest.TestCase):
    def setUp(self):
        self.protected = memory(
            "approval_rule",
            "외부 이메일은 승인 후 발송한다.",
            memory_type=MemoryType.CONSTRAINT,
            token_count=6,
            is_protected=True,
            memory_key="email.external.requires_approval",
        )

        self.state = memory(
            "report_final",
            "현재 보고서는 report_final.pdf이다.",
            memory_type=MemoryType.STATE,
            token_count=4,
            memory_key="report.current_file",
        )

        self.flexible_1 = memory(
            "preference_1",
            "사용자는 간결한 이메일을 선호한다.",
            memory_type=MemoryType.PREFERENCE,
            token_count=5,
        )

        self.flexible_2 = memory(
            "fact_1",
            "교수님의 연구실은 공학관에 있다.",
            memory_type=MemoryType.FACT,
            token_count=5,
        )

    def test_fixed_context_tokens_matches_formatted_fixed_context(self):
        expected = (
            "[SYSTEM]\n"
            f"{SYSTEM_PROMPT}\n\n"
            "[TOOL DEFINITIONS]\n"
            f"{TOOL_DEFINITIONS}\n\n"
            "[CURRENT TASK]\n"
            f"{QUERY}"
        )

        result = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
        )

        self.assertEqual(
            result,
            provisional_token_count(expected),
        )

    def test_build_context_contains_sections_in_expected_order(self):
        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(self.flexible_1,),
            ),
            total_budget=1000,
        )

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertIsNotNone(result.context)

        context = result.context

        self.assertLess(
            context.index("[SYSTEM]"),
            context.index("[TOOL DEFINITIONS]"),
        )
        self.assertLess(
            context.index("[TOOL DEFINITIONS]"),
            context.index("[PROTECTED CONSTRAINTS]"),
        )
        self.assertLess(
            context.index("[PROTECTED CONSTRAINTS]"),
            context.index("[STATE]"),
        )
        self.assertLess(
            context.index("[STATE]"),
            context.index("[RELEVANT MEMORY]"),
        )
        self.assertLess(
            context.index("[RELEVANT MEMORY]"),
            context.index("[CURRENT TASK]"),
        )

        self.assertIn(
            "- 외부 이메일은 승인 후 발송한다.",
            context,
        )
        self.assertIn(
            "- 현재 보고서는 report_final.pdf이다.",
            context,
        )
        self.assertIn(
            "- 사용자는 간결한 이메일을 선호한다.",
            context,
        )

    def test_empty_memory_sections_are_omitted(self):
        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(),
            total_budget=1000,
        )

        self.assertEqual(result.status, PipelineStatus.OK)

        context = result.context

        self.assertIn("[SYSTEM]", context)
        self.assertIn("[TOOL DEFINITIONS]", context)
        self.assertIn("[CURRENT TASK]", context)

        self.assertNotIn(
            "[PROTECTED CONSTRAINTS]",
            context,
        )
        self.assertNotIn(
            "[STATE]",
            context,
        )
        self.assertNotIn(
            "[RELEVANT MEMORY]",
            context,
        )

        self.assertEqual(result.selected_memory_ids, ())
        self.assertEqual(result.dropped_flexible_ids, ())

    def test_selected_memory_ids_follow_context_order(self):
        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(
                    self.flexible_1,
                    self.flexible_2,
                ),
            ),
            total_budget=1000,
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
                "preference_1",
                "fact_1",
            ),
        )

    def test_section_token_counts_use_formatted_sections(self):
        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(self.flexible_1,),
            ),
            total_budget=1000,
        )

        protected_text = (
            "[PROTECTED CONSTRAINTS]\n"
            "- 외부 이메일은 승인 후 발송한다."
        )

        state_text = (
            "[STATE]\n"
            "- 현재 보고서는 report_final.pdf이다."
        )

        flexible_text = (
            "[RELEVANT MEMORY]\n"
            "- 사용자는 간결한 이메일을 선호한다."
        )

        self.assertEqual(
            result.protected_tokens,
            provisional_token_count(protected_text),
        )
        self.assertEqual(
            result.state_tokens,
            provisional_token_count(state_text),
        )
        self.assertEqual(
            result.flexible_tokens,
            provisional_token_count(flexible_text),
        )

        self.assertEqual(
            result.total_input_tokens,
            provisional_token_count(result.context),
        )

        self.assertEqual(
            result.unused_budget,
            1000 - result.total_input_tokens,
        )

    def test_budget_overflow_drops_lowest_priority_flexible_first(self):
        original_selection = selection(
            protected=(self.protected,),
            states=(self.state,),
            flexible=(
                self.flexible_1,
                self.flexible_2,
            ),
        )

        without_last = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(self.flexible_1,),
            ),
            total_budget=1000,
        )

        budget = without_last.total_input_tokens

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            original_selection,
            total_budget=budget,
        )

        self.assertEqual(result.status, PipelineStatus.OK)

        self.assertEqual(
            result.dropped_flexible_ids,
            ("fact_1",),
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
                "preference_1",
            ),
        )

        self.assertNotIn(
            self.flexible_2.content,
            result.context,
        )
        self.assertIn(
            self.flexible_1.content,
            result.context,
        )

        self.assertLessEqual(
            result.total_input_tokens,
            budget,
        )

        self.assertEqual(
            original_selection.flexible,
            (
                self.flexible_1,
                self.flexible_2,
            ),
        )

    def test_all_flexible_memories_can_be_dropped(self):
        mandatory_only = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
            ),
            total_budget=1000,
        )

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(
                    self.flexible_1,
                    self.flexible_2,
                ),
            ),
            total_budget=mandatory_only.total_input_tokens,
        )

        self.assertEqual(result.status, PipelineStatus.OK)

        self.assertEqual(
            result.dropped_flexible_ids,
            (
                "fact_1",
                "preference_1",
            ),
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
            ),
        )

        self.assertNotIn(
            "[RELEVANT MEMORY]",
            result.context,
        )

        self.assertEqual(
            result.total_input_tokens,
            mandatory_only.total_input_tokens,
        )

    def test_mandatory_context_overflow_returns_insufficient_budget(self):
        mandatory = selection(
            protected=(self.protected,),
            states=(self.state,),
        )

        valid = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            mandatory,
            total_budget=1000,
        )

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            mandatory,
            total_budget=valid.total_input_tokens - 1,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

        self.assertIsNone(result.context)

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
            ),
        )

        self.assertEqual(
            result.dropped_flexible_ids,
            (),
        )

        self.assertGreater(
            result.total_input_tokens,
            valid.total_input_tokens - 1,
        )

        self.assertEqual(result.unused_budget, 0)

    def test_flexible_is_removed_before_mandatory_overflow_failure(self):
        mandatory_only = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
            ),
            total_budget=1000,
        )

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
                states=(self.state,),
                flexible=(
                    self.flexible_1,
                    self.flexible_2,
                ),
            ),
            total_budget=mandatory_only.total_input_tokens - 1,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

        self.assertEqual(
            result.dropped_flexible_ids,
            (
                "fact_1",
                "preference_1",
            ),
        )

        self.assertEqual(
            result.selected_memory_ids,
            (
                "approval_rule",
                "report_final",
            ),
        )

        self.assertIsNone(result.context)

    def test_selector_failure_is_propagated_without_building_context(self):
        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            SelectionResult(
                status=PipelineStatus.INVALID_BUDGET_CONFIGURATION,
            ),
            total_budget=100,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INVALID_BUDGET_CONFIGURATION,
        )
        self.assertIsNone(result.context)
        self.assertEqual(result.selected_memory_ids, ())
        self.assertEqual(result.dropped_flexible_ids, ())
        self.assertEqual(result.total_input_tokens, 0)
        self.assertEqual(result.unused_budget, 100)

    def test_custom_token_counter_is_used(self):
        calls: list[str] = []

        def fake_counter(text: str) -> int:
            calls.append(text)
            return len(text)

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(
                protected=(self.protected,),
            ),
            total_budget=10000,
            token_counter=fake_counter,
        )

        self.assertEqual(result.status, PipelineStatus.OK)

        self.assertEqual(
            result.total_input_tokens,
            len(result.context),
        )

        self.assertGreater(len(calls), 1)

    def test_negative_budget_raises_value_error(self):
        with self.assertRaises(ValueError):
            build_context(
                SYSTEM_PROMPT,
                TOOL_DEFINITIONS,
                QUERY,
                selection(),
                total_budget=-1,
            )
    
    def test_same_memories_produce_same_context_across_policies(self):
        """
        Baseline과 Proposed가 동일한 Memory를 선택했으면
        Context 형식도 같아야 한다.
        """

        proposed = selection(
            protected=(self.protected,),
            states=(self.state,),
        )

        baseline = selection(
            flexible=(
                self.protected,
                self.state,
            ),
        )

        proposed_result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            proposed,
            total_budget=1000,
        )

        baseline_result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            baseline,
            total_budget=1000,
        )

        self.assertEqual(
            proposed_result.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            baseline_result.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            proposed_result.context,
            baseline_result.context,
        )

        self.assertEqual(
            proposed_result.total_input_tokens,
            baseline_result.total_input_tokens,
        )

        self.assertEqual(
            proposed_result.selected_memory_ids,
            baseline_result.selected_memory_ids,
        )


    def test_baseline_protected_memory_remains_droppable(self):
        """
        Baseline에서 선택된 Protected Constraint가
        표시상 PROTECTED 섹션에 들어갔다고 해서
        Mandatory가 되어서는 안 된다.
        """

        baseline = selection(
            flexible=(self.protected,),
        )

        fixed_only = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            selection(),
            total_budget=1000,
        )

        result = build_context(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            baseline,
            total_budget=fixed_only.total_input_tokens,
        )

        self.assertEqual(result.status, PipelineStatus.OK)

        self.assertEqual(
            result.dropped_flexible_ids,
            (self.protected.memory_id,),
        )

        self.assertEqual(
            result.selected_memory_ids,
            (),
        )

        self.assertEqual(result.context, fixed_only.context)



if __name__ == "__main__":
    unittest.main()