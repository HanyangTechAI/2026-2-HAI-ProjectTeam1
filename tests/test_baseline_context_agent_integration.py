
from datetime import datetime, timedelta, timezone
import unittest

from agent.agent import Agent
from agent.planner import Planner
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool

from context.builder import build_context, fixed_context_tokens
from context.tokenizer import provisional_token_count

from memory.baselines import (
    sliding_window,
    vector_top_k,
    utility_per_token,
)
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus, SelectionResult


BASE_TIME = datetime(2026, 10, 28, tzinfo=timezone.utc)

SESSION_ID = "session_baseline_integration"
CURRENT_TURN = 100

SYSTEM_PROMPT = (
    "주어진 기억을 참고하여 요청에 맞는 Tool을 실행한다."
)

TOOL_DEFINITIONS = (
    "file.find_file(filename)\n"
    "file.select_file(filename)\n"
    "email.request_approval(recipient, attachment=None)\n"
    "email.send_email(recipient, attachment=None)"
)

QUERY = "현재 보고서 파일을 찾아줘."

CONTEXT_BUDGET = 1000


def make_memory(
    memory_id: str,
    content: str,
    *,
    turn_id: int,
    memory_type: MemoryType = MemoryType.FACT,
    memory_key: str | None = None,
    is_protected: bool = False,
    importance: float = 0.5,
    session_id: str = SESSION_ID,
) -> MemoryItem:
    timestamp = BASE_TIME + timedelta(seconds=turn_id)

    if memory_type == MemoryType.STATE and memory_key is None:
        memory_key = f"state.{memory_id}"

    return MemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        turn_id=turn_id,
        memory_type=memory_type,
        content=content,
        token_count=provisional_token_count(content),
        created_at=timestamp,
        valid_from=timestamp,
        importance=importance,
        is_active=True,
        is_protected=is_protected,
        memory_key=memory_key,
    )


def test_embedder(text: str) -> tuple[float, ...]:
    """
    테스트 전용 deterministic embedding.

    실제 embedding 성능을 측정하는 함수가 아니다.
    Baseline → Context → Agent 연결을 재현 가능하게
    검증하기 위한 간단한 특징 벡터다.

    Scenario의 정답이나 평가 metadata는 사용하지 않는다.
    """
    return (
        float("보고서" in text),
        float("회의" in text),
        float("일정" in text),
        float("승인" in text),
        float("파일" in text),
    )


class BaselineContextAgentIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.report = make_memory(
            "report_final",
            "현재 보고서는 report_final.pdf이다.",
            turn_id=20,
            memory_type=MemoryType.STATE,
            memory_key="report.current_file",
            importance=0.9,
        )

        self.noise = make_memory(
            "meeting_note",
            "회의 일정은 다음 주 월요일이다.",
            turn_id=5,
            importance=0.2,
        )

    def make_agent(self) -> Agent:
        """
        테스트마다 독립적인 Agent와 Mock Tool을 생성한다.
        """
        executor = ToolExecutor(
            file=FileTool({
                "report_final.pdf": "final report content",
                "meeting_notes.pdf": "meeting notes content",
            })
        )

        return Agent(
            executor=executor,
            planner=Planner(),
            current_turn=CURRENT_TURN,
            clock=lambda: BASE_TIME,
        )

    def select_baseline(
        self,
        strategy: str,
        memories: list[MemoryItem],
        *,
        query: str = QUERY,
        budget: int = CONTEXT_BUDGET,
        top_k: int = 20,
        current_turn: int = CURRENT_TURN,
    ) -> SelectionResult:
        """
        세 Baseline을 동일한 입력 계약으로 호출한다.
        """
        fixed_tokens = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            query,
            provisional_token_count,
        )

        common_options = {
            "session_id": SESSION_ID,
            "total_budget": budget,
            "fixed_tokens": fixed_tokens,
            "current_turn": current_turn,
            "token_counter": provisional_token_count,
        }

        if strategy == "sliding_window":
            return sliding_window(
                memories,
                **common_options,
            )

        if strategy == "vector_top_k":
            return vector_top_k(
                query,
                memories,
                embedder=test_embedder,
                top_k=top_k,
                **common_options,
            )

        if strategy == "utility_per_token":
            return utility_per_token(
                query,
                memories,
                embedder=test_embedder,
                top_k=top_k,
                **common_options,
            )

        raise ValueError(f"unknown strategy: {strategy}")

    def make_context(
        self,
        selection: SelectionResult,
        *,
        query: str = QUERY,
        budget: int = CONTEXT_BUDGET,
    ):
        return build_context(
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            query=query,
            selection=selection,
            total_budget=budget,
            token_counter=provisional_token_count,
        )

    def test_all_three_baselines_execute_file_tool(self):
        """
        Baseline 3종
            → SelectionResult
            → Context Builder
            → Agent
            → FileTool

        전체 경로가 실제로 동작하는지 검증한다.
        """
        strategies = (
            "sliding_window",
            "vector_top_k",
            "utility_per_token",
        )

        memories = [
            self.noise,
            self.report,
        ]

        for strategy in strategies:
            with self.subTest(strategy=strategy):

                selection = self.select_baseline(
                    strategy,
                    memories,
                )

                self.assertEqual(
                    selection.status,
                    PipelineStatus.OK,
                )

                self.assertIn(
                    "report_final",
                    selection.selected_memory_ids,
                )

                context_result = self.make_context(
                    selection
                )

                self.assertEqual(
                    context_result.status,
                    PipelineStatus.OK,
                )

                self.assertIsNotNone(
                    context_result.context
                )

                self.assertIn(
                    "report_final.pdf",
                    context_result.context,
                )

                self.assertIn(
                    "[RELEVANT MEMORY]",
                    context_result.context,
                )

                self.assertLessEqual(
                    context_result.total_input_tokens,
                    CONTEXT_BUDGET,
                )

                agent = self.make_agent()

                result = agent.run_task(
                    context_result.context,
                    SESSION_ID,
                )

                self.assertEqual(
                    result.stopped_reason,
                    "tool_called",
                )

                self.assertEqual(
                    len(result.tool_calls),
                    1,
                )

                self.assertEqual(
                    len(result.tool_results),
                    1,
                )

                call = result.tool_calls[0]
                tool_result = result.tool_results[0]

                self.assertEqual(
                    call.tool_name,
                    "file",
                )

                self.assertEqual(
                    call.action,
                    "find_file",
                )

                self.assertEqual(
                    call.arguments["filename"],
                    "report_final.pdf",
                )

                self.assertTrue(
                    tool_result.success
                )

                self.assertTrue(
                    tool_result.output["found"]
                )

                self.assertEqual(
                    len(result.interactions),
                    1,
                )

                self.assertEqual(
                    result.interactions[0].session_id,
                    SESSION_ID,
                )

                self.assertEqual(
                    result.interactions[0].source,
                    "tool",
                )

    def test_baseline_selection_changes_agent_action(self):
        """
        동일한 요청이라도 Baseline이 선택한 Memory에 따라
        Agent가 호출하는 파일이 달라지는지 검증한다.

        Sliding Window는 최신 기억을 우선하고,
        Vector Top-k는 질의와 유사한 기억을 우선한다.
        """
        old_report = make_memory(
            "report_final",
            "최종 보고서는 report_final.pdf이다.",
            turn_id=10,
            memory_type=MemoryType.FACT,
        )

        recent_meeting = make_memory(
            "meeting_file",
            "회의 자료는 meeting_notes.pdf이다.",
            turn_id=30,
            memory_type=MemoryType.FACT,
        )

        memories = [
            old_report,
            recent_meeting,
        ]

        selected_filenames = {}

        for strategy in (
            "sliding_window",
            "vector_top_k",
        ):
            with self.subTest(strategy=strategy):

                selection = self.select_baseline(
                    strategy,
                    memories,
                    top_k=1,
                )

                context_result = self.make_context(
                    selection
                )

                self.assertEqual(
                    context_result.status,
                    PipelineStatus.OK,
                )

                agent = self.make_agent()

                result = agent.run_task(
                    context_result.context,
                    SESSION_ID,
                )

                self.assertEqual(
                    len(result.tool_calls),
                    1,
                )

                selected_filenames[strategy] = (
                    result.tool_calls[0].arguments["filename"]
                )

        self.assertEqual(
            selected_filenames["sliding_window"],
            "meeting_notes.pdf",
        )

        self.assertEqual(
            selected_filenames["vector_top_k"],
            "report_final.pdf",
        )

        self.assertNotEqual(
            selected_filenames["sliding_window"],
            selected_filenames["vector_top_k"],
        )

    def test_context_builder_removes_low_priority_memory(self):
        """
        Baseline에서 선택한 기억들이 formatting overhead로
        최종 Budget을 넘으면 Context Builder가 가장 낮은
        우선순위의 Flexible Memory부터 제거해야 한다.

        제거 이후 Agent가 남은 기억을 이용해 올바른
        파일을 선택하는지도 검증한다.
        """
        older_memory = make_memory(
            "older_file",
            "회의록 meeting_notes.pdf",
            turn_id=5,
        )

        memories = [
            self.report,
            older_memory,
        ]

        # 우선순위가 가장 높은 기억 하나만 포함한
        # Context의 정확한 토큰 수를 구한다.
        single_selection = SelectionResult(
            status=PipelineStatus.OK,
            flexible=(self.report,),
        )

        single_context = self.make_context(
            single_selection,
            budget=1000,
        )

        budget = single_context.total_input_tokens

        # Baseline 단계에서는 두 기억 모두
        # memory budget에 들어갈 수 있어야 한다.
        selection = self.select_baseline(
            "sliding_window",
            memories,
            budget=budget,
        )

        self.assertEqual(
            selection.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            selection.selected_memory_ids,
            (
                "report_final",
                "older_file",
            ),
        )

        # 최종 Context formatting overhead를 반영하면
        # 낮은 우선순위의 기억을 제거해야 한다.
        context_result = self.make_context(
            selection,
            budget=budget,
        )

        self.assertEqual(
            context_result.status,
            PipelineStatus.OK,
        )

        self.assertEqual(
            context_result.dropped_flexible_ids,
            ("older_file",),
        )

        self.assertEqual(
            context_result.selected_memory_ids,
            ("report_final",),
        )

        self.assertIn(
            "report_final.pdf",
            context_result.context,
        )

        self.assertNotIn(
            "meeting_notes.pdf",
            context_result.context,
        )

        self.assertLessEqual(
            context_result.total_input_tokens,
            budget,
        )

        agent = self.make_agent()

        result = agent.run_task(
            context_result.context,
            SESSION_ID,
        )

        self.assertEqual(
            result.tool_calls[0].arguments["filename"],
            "report_final.pdf",
        )

        self.assertTrue(
            result.tool_results[0].success,
        )

    def test_tool_result_can_be_used_in_next_context(self):
        """
        첫 번째 Agent Action의 ToolResult를 Interaction으로
        받은 뒤 새 Memory로 구성하면, 두 번째 호출에서
        Agent가 이전 Tool 실행 결과를 인식하는지 검증한다.

        Orchestrator 전체 구현을 대신하는 테스트가 아니라
        현재 B/A 인터페이스의 재호출 가능성을 확인한다.
        """
        agent = self.make_agent()

        first_selection = self.select_baseline(
            "sliding_window",
            [self.report],
        )

        first_context = self.make_context(
            first_selection
        )

        first_result = agent.run_task(
            first_context.context,
            SESSION_ID,
        )

        self.assertEqual(
            first_result.stopped_reason,
            "tool_called",
        )

        self.assertEqual(
            len(first_result.interactions),
            1,
        )

        interaction = first_result.interactions[0]

        self.assertIn(
            "file.find_file",
            interaction.content,
        )

        tool_memory = make_memory(
            "tool_result_1",
            interaction.content,
            turn_id=101,
            memory_type=MemoryType.FACT,
        )

        second_selection = self.select_baseline(
            "sliding_window",
            [
                self.report,
                tool_memory,
            ],
            current_turn=interaction.turn_id,
        )

        second_context = self.make_context(
            second_selection
        )

        self.assertEqual(
            second_context.status,
            PipelineStatus.OK,
        )

        second_result = agent.run_task(
            second_context.context,
            SESSION_ID,
        )

        self.assertEqual(
            second_result.stopped_reason,
            "completed",
        )

        self.assertEqual(
            second_result.tool_calls,
            (),
        )

        self.assertEqual(
            second_result.tool_results,
            (),
        )

        self.assertIn(
            "report_final.pdf",
            second_result.response,
        )

        # 두 번째 호출에서는 추가 Tool 실행이 없어야 한다.
        self.assertEqual(
            len(agent.executor.calls),
            1,
        )

    
    def test_baseline_selected_constraint_is_respected_by_agent(self):
        """
        Baseline이 Protected Constraint를 선택했다면
        Context에서는 PROTECTED CONSTRAINTS로 표시되어야 한다.

        단, Baseline의 SelectionResult.protected는 비어 있어야 한다.
        """

        approval_rule = make_memory(
            "approval_rule",
            "외부 이메일은 반드시 승인 후 발송한다.",
            turn_id=10,
            memory_type=MemoryType.CONSTRAINT,
            memory_key="email.external.requires_approval",
            is_protected=True,
        )

        query = (
            "professor@example.test에게 "
            "report_final.pdf를 메일로 보내줘."
        )

        for strategy in (
            "sliding_window",
            "vector_top_k",
            "utility_per_token",
        ):
            with self.subTest(strategy=strategy):

                selection = self.select_baseline(
                    strategy,
                    [approval_rule, self.report],
                    query=query,
                )

                # Baseline은 여전히 Mandatory Selection을 하지 않는다.
                self.assertEqual(selection.protected, ())
                self.assertEqual(selection.states, ())

                self.assertIn(
                    "approval_rule",
                    selection.selected_memory_ids,
                )

                context_result = self.make_context(
                    selection,
                    query=query,
                )

                self.assertEqual(
                    context_result.status,
                    PipelineStatus.OK,
                )

                self.assertIn(
                    "[PROTECTED CONSTRAINTS]",
                    context_result.context,
                )

                self.assertIn(
                    approval_rule.content,
                    context_result.context,
                )

                agent = self.make_agent()

                result = agent.run_task(
                    context_result.context,
                    SESSION_ID,
                )

                self.assertEqual(
                    result.stopped_reason,
                    "tool_called",
                )

                self.assertEqual(len(result.tool_calls), 1)

                call = result.tool_calls[0]

                self.assertEqual(call.tool_name, "email")
                self.assertEqual(call.action, "request_approval")

                # 승인 요청만 발생하며 발송하지 않는다.
                self.assertEqual(agent.executor.email.sent, [])


    def test_invalid_fixed_budget_prevents_context_creation(self):
        """
        고정 입력만으로 Budget이 초과하면 Baseline과
        Context Builder가 동일한 오류 상태를 전달하는지
        검증한다.

        Context가 None이므로 정상 파이프라인에서는
        Agent를 호출해서는 안 된다.
        """
        fixed_tokens = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            provisional_token_count,
        )

        insufficient_budget = fixed_tokens - 1

        self.assertGreaterEqual(
            insufficient_budget,
            0,
        )

        for strategy in (
            "sliding_window",
            "vector_top_k",
            "utility_per_token",
        ):
            with self.subTest(strategy=strategy):

                selection = self.select_baseline(
                    strategy,
                    [self.report],
                    budget=insufficient_budget,
                )

                self.assertEqual(
                    selection.status,
                    PipelineStatus.INVALID_BUDGET_CONFIGURATION,
                )

                context_result = self.make_context(
                    selection,
                    budget=insufficient_budget,
                )

                self.assertEqual(
                    context_result.status,
                    PipelineStatus.INVALID_BUDGET_CONFIGURATION,
                )

                self.assertIsNone(
                    context_result.context,
                )


if __name__ == "__main__":
    unittest.main()
