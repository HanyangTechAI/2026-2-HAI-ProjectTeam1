
from datetime import datetime, timedelta, timezone
import unittest

from agent.agent import Agent, AgentAction, AgentRunResult
from agent.planner import Planner
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from agent.tools.types import ToolCall, ToolResult

from context.builder import fixed_context_tokens
from context.tokenizer import provisional_token_count

from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus, SelectionResult

from pipeline.orchestrator import (
    Orchestrator,
    PipelineRunResult,
    make_baseline_policy,
)


BASE_TIME = datetime(2026, 10, 28, tzinfo=timezone.utc)

SESSION_ID = "session_pipeline_test"
CURRENT_TURN = 50

SYSTEM_PROMPT = "주어진 기억과 제약에 따라 작업을 수행한다."

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
) -> MemoryItem:

    timestamp = BASE_TIME + timedelta(minutes=turn_id)

    if memory_type == MemoryType.STATE and memory_key is None:
        memory_key = f"state.{memory_id}"

    return MemoryItem(
        memory_id=memory_id,
        session_id=SESSION_ID,
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
    통합 테스트 전용 결정적 Embedder.

    실제 Embedding 품질 측정에 사용하지 않는다.
    """
    return (
        float("보고서" in text),
        float("파일" in text),
        float("회의" in text),
        float("승인" in text),
    )


class AlwaysFindPlanner:
    """
    매번 동일한 Tool을 호출하는 테스트용 Planner.

    max_steps 및 반복 종료를 검증한다.
    """

    def plan(self, context: str) -> AgentAction:
        return AgentAction(
            tool_name="file",
            action="find_file",
            arguments={"filename": "report_final.pdf"},
            text_response=None,
        )


class InvalidFilenamePlanner:
    """
    잘못된 Tool 인자를 반환하는 테스트용 Planner.
    """

    def plan(self, context: str) -> AgentAction:
        return AgentAction(
            tool_name="file",
            action="find_file",
            arguments={"filename": ""},
            text_response=None,
        )


class PipelineIntegrationTests(unittest.TestCase):

    def setUp(self):
        self.report = make_memory(
            "report_final",
            "현재 보고서는 report_final.pdf이다.",
            turn_id=10,
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

    def make_agent(
        self,
        *,
        planner=None,
        goal_checker=None,
    ) -> Agent:

        executor = ToolExecutor(
            file=FileTool({
                "report_final.pdf": "final report content",
                "meeting_notes.pdf": "meeting notes content",
            })
        )

        return Agent(
            executor=executor,
            planner=planner if planner is not None else Planner(),
            goal_checker=goal_checker,
            current_turn=0,
            clock=lambda: BASE_TIME,
        )

    def make_orchestrator(
        self,
        *,
        agent: Agent | None = None,
        policy=None,
        strategy: str = "sliding_window",
        max_steps: int = 5,
        goal_checker=None,
    ) -> Orchestrator:

        if agent is None:
            agent = self.make_agent()

        if policy is None:
            policy = make_baseline_policy(
                strategy,
                embedder=(
                    test_embedder
                    if strategy != "sliding_window"
                    else None
                ),
            )

        return Orchestrator(
            agent=agent,
            policy=policy,
            system_prompt=SYSTEM_PROMPT,
            tool_definitions=TOOL_DEFINITIONS,
            token_counter=provisional_token_count,
            max_steps=max_steps,
            goal_checker=goal_checker,
        )

    def run_pipeline(
        self,
        orchestrator: Orchestrator,
        *,
        query: str = QUERY,
        memories=None,
        budget: int = CONTEXT_BUDGET,
        current_turn: int = CURRENT_TURN,
    ) -> PipelineRunResult:

        if memories is None:
            memories = [self.report, self.noise]

        return orchestrator.run(
            query=query,
            session_id=SESSION_ID,
            memories=memories,
            total_budget=budget,
            current_turn=current_turn,
        )

    def test_multistep_execution_rebuilds_context(self):
        """
        첫 번째 Agent Action → ToolResult
        → Context 재구성 → 두 번째 Agent Action
        → 완료 흐름을 검증한다.
        """

        def goal_checker(response, calls, results):
            return (
                response is not None
                and "report_final.pdf" in response
                and len(calls) == 1
                and len(results) == 1
                and results[0].success
                and results[0].output.get("found") is True
            )

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
            goal_checker=goal_checker,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertEqual(result.stopped_reason, "completed")
        self.assertTrue(result.goal_completed)
        self.assertTrue(result.agent_executed)

        self.assertEqual(len(result.steps), 2)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(len(result.tool_results), 1)
        self.assertEqual(len(result.interactions), 1)

        call = result.tool_calls[0]
        tool_result = result.tool_results[0]

        self.assertEqual(call.tool_name, "file")
        self.assertEqual(call.action, "find_file")
        self.assertEqual(
            call.arguments["filename"],
            "report_final.pdf",
        )

        self.assertTrue(tool_result.success)
        self.assertTrue(tool_result.output["found"])

        self.assertEqual(
            result.steps[0].action_result.stopped_reason,
            "tool_called",
        )

        self.assertEqual(
            result.steps[1].action_result.stopped_reason,
            "completed",
        )

        second_context = result.steps[1].context_result.context

        self.assertIn("[RECENT TOOL RESULTS]", second_context)
        self.assertIn(
            "file.find_file success=true",
            second_context,
        )
        self.assertIn("report_final.pdf", result.response)

        self.assertEqual(result.final_turn, CURRENT_TURN + 1)

        expected_tokens = sum(
            step.context_result.total_input_tokens
            for step in result.steps
        )
        self.assertEqual(
            result.total_input_tokens,
            expected_tokens,
        )

        for step in result.steps:
            self.assertLessEqual(
                step.context_result.total_input_tokens,
                CONTEXT_BUDGET,
            )

        converted = result.to_agent_run_result()

        self.assertIsInstance(converted, AgentRunResult)
        self.assertEqual(converted.tool_calls, result.tool_calls)
        self.assertEqual(converted.tool_results, result.tool_results)
        self.assertTrue(converted.goal_completed)

    def test_all_baseline_policies_execute_pipeline(self):
        """
        Sliding Window, Vector Top-k, Utility-per-Token을
        동일한 Orchestrator에서 실행할 수 있는지 검증한다.
        """

        for strategy in (
            "sliding_window",
            "vector_top_k",
            "utility_per_token",
        ):
            with self.subTest(strategy=strategy):

                orchestrator = self.make_orchestrator(
                    strategy=strategy,
                )

                result = self.run_pipeline(orchestrator)

                self.assertEqual(result.status, PipelineStatus.OK)
                self.assertEqual(result.stopped_reason, "completed")

                self.assertEqual(len(result.tool_calls), 1)
                self.assertEqual(len(result.tool_results), 1)

                self.assertEqual(
                    result.tool_calls[0].arguments["filename"],
                    "report_final.pdf",
                )

                self.assertTrue(result.tool_results[0].success)

                self.assertEqual(
                    result.final_turn,
                    CURRENT_TURN + 1,
                )

    def test_interaction_turn_is_updated_and_policy_reruns(self):
        """
        ToolResult 이후 current_turn이 갱신되고,
        다음 단계에서 Memory Policy가 다시 호출되는지 검증한다.

        이전에 발생했던 current_turn 미갱신 문제의 회귀 테스트.
        """

        observed_turns = []
        observed_snapshots = []

        baseline = make_baseline_policy("sliding_window")

        def tracking_policy(**kwargs):
            observed_turns.append(kwargs["current_turn"])
            observed_snapshots.append(
                tuple(
                    memory.memory_id
                    for memory in kwargs["memories"]
                )
            )
            return baseline(**kwargs)

        orchestrator = self.make_orchestrator(
            policy=tracking_policy,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(
            observed_turns,
            [CURRENT_TURN, CURRENT_TURN + 1],
        )

        self.assertEqual(
            result.steps[0].current_turn,
            CURRENT_TURN,
        )

        self.assertEqual(
            result.steps[1].current_turn,
            CURRENT_TURN + 1,
        )

        self.assertEqual(
            result.interactions[0].turn_id,
            CURRENT_TURN + 1,
        )

        self.assertEqual(
            result.final_turn,
            CURRENT_TURN + 1,
        )

        self.assertEqual(
            observed_snapshots[0],
            observed_snapshots[1],
        )

        # Tool 결과는 별도 피드백으로 제공되며,
        # 장기 Memory에 자동 삽입되지 않는다.
        self.assertEqual(
            observed_snapshots[0],
            ("report_final", "meeting_note"),
        )

        second_context = result.steps[1].context_result.context
        self.assertIn(
            "file.find_file success=true",
            second_context,
        )

    def test_goal_checker_is_used_only_on_completion(self):
        """
        실제 완료 판단은 GoalChecker가 수행해야 한다.
        """

        checked = []

        def goal_checker(response, calls, results):
            checked.append((response, calls, results))
            return (
                len(calls) == 1
                and len(results) == 1
                and results[0].success
            )

        orchestrator = self.make_orchestrator(
            goal_checker=goal_checker,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(result.stopped_reason, "completed")
        self.assertTrue(result.goal_completed)

        self.assertEqual(len(checked), 1)

        response, calls, results = checked[0]

        self.assertIsInstance(response, str)
        self.assertIsInstance(calls[0], ToolCall)
        self.assertIsInstance(results[0], ToolResult)

    def test_completed_without_goal_checker_is_not_success(self):
        """
        Planner가 completed를 반환하더라도
        GoalChecker가 없으면 성공으로 판단하지 않는다.
        """

        orchestrator = self.make_orchestrator()

        result = self.run_pipeline(orchestrator)

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertEqual(result.stopped_reason, "completed")
        self.assertFalse(result.goal_completed)

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(len(result.steps), 2)

    def test_tool_failure_stops_pipeline(self):
        """
        Tool 실행 실패 시 후속 Action 없이 종료하는지 검증한다.
        """

        agent = self.make_agent(
            planner=InvalidFilenamePlanner(),
        )

        orchestrator = self.make_orchestrator(
            agent=agent,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertEqual(result.stopped_reason, "tool_failure")
        self.assertFalse(result.goal_completed)

        self.assertEqual(len(result.steps), 1)
        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(len(result.tool_results), 1)
        self.assertEqual(len(result.interactions), 1)

        self.assertFalse(result.tool_results[0].success)
        self.assertIsNotNone(result.tool_results[0].error)

        self.assertEqual(
            result.final_turn,
            CURRENT_TURN + 1,
        )

        self.assertEqual(
            len(agent.executor.calls),
            1,
        )

    def test_max_steps_prevents_infinite_tool_loop(self):
        """
        Planner가 계속 같은 Tool을 실행하더라도
        max_steps에서 반드시 종료하는지 검증한다.
        """

        max_steps = 3

        agent = self.make_agent(
            planner=AlwaysFindPlanner(),
        )

        orchestrator = self.make_orchestrator(
            agent=agent,
            max_steps=max_steps,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertEqual(result.stopped_reason, "max_steps")
        self.assertFalse(result.goal_completed)

        self.assertEqual(len(result.steps), max_steps)
        self.assertEqual(len(result.tool_calls), max_steps)
        self.assertEqual(len(result.tool_results), max_steps)
        self.assertEqual(len(result.interactions), max_steps)

        self.assertEqual(
            result.final_turn,
            CURRENT_TURN + max_steps,
        )

        self.assertEqual(
            len(agent.executor.calls),
            max_steps,
        )

        self.assertTrue(
            all(tool_result.success for tool_result in result.tool_results)
        )

    def test_invalid_fixed_budget_skips_policy_and_agent(self):
        """
        고정 Context 자체가 예산을 초과하면
        Policy와 Agent를 실행하지 않아야 한다.
        """

        policy_calls = []

        def tracking_policy(**kwargs):
            policy_calls.append(kwargs)
            return SelectionResult(status=PipelineStatus.OK)

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
            policy=tracking_policy,
        )

        fixed_tokens = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
            provisional_token_count,
        )

        result = self.run_pipeline(
            orchestrator,
            budget=fixed_tokens - 1,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INVALID_BUDGET_CONFIGURATION,
        )

        self.assertEqual(
            result.stopped_reason,
            "invalid_budget_configuration",
        )

        self.assertFalse(result.agent_executed)
        self.assertFalse(result.goal_completed)

        self.assertEqual(policy_calls, [])
        self.assertEqual(agent.executor.calls, [])

        self.assertEqual(result.tool_calls, ())
        self.assertEqual(result.tool_results, ())
        self.assertEqual(result.total_input_tokens, 0)

        self.assertEqual(len(result.steps), 1)
        self.assertIsNone(result.steps[0].action_result)

    def test_mandatory_memory_overflow_skips_agent(self):
        """
        Protected Constraint가 필수 선택인데
        최종 Budget에 들어가지 않으면 Context 생성이
        중단되고 Agent가 실행되지 않아야 한다.
        """

        protected = make_memory(
            "large_constraint",
            "승인 없이 실행하면 안 된다. " * 150,
            turn_id=10,
            memory_type=MemoryType.CONSTRAINT,
            memory_key="action.requires_approval",
            is_protected=True,
        )

        def mandatory_policy(**kwargs):
            return SelectionResult(
                status=PipelineStatus.OK,
                protected=(protected,),
                selected_memory_ids=(protected.memory_id,),
            )

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
            policy=mandatory_policy,
        )

        fixed_tokens = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            QUERY,
        )

        result = self.run_pipeline(
            orchestrator,
            memories=[protected],
            budget=fixed_tokens + 5,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

        self.assertEqual(
            result.stopped_reason,
            "context_budget",
        )

        self.assertFalse(result.agent_executed)
        self.assertFalse(result.goal_completed)

        self.assertEqual(result.tool_calls, ())
        self.assertEqual(agent.executor.calls, [])

        self.assertEqual(len(result.steps), 1)
        self.assertIsNone(result.steps[0].context_result.context)

        self.assertIn(
            "large_constraint",
            result.steps[0].context_result.selected_memory_ids,
        )

    def test_tool_feedback_can_trigger_fixed_budget_overflow(self):
        """
        첫 호출은 Budget 내에서 실행되지만,
        Tool 결과가 추가되면서 다음 호출의 고정 입력이
        예산을 초과할 수 있다.

        이때 추가 Tool 실행 없이 종료해야 한다.
        """

        query = "report_final.pdf 파일 찾아줘."

        fixed_tokens = fixed_context_tokens(
            SYSTEM_PROMPT,
            TOOL_DEFINITIONS,
            query,
        )

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
            max_steps=4,
        )

        result = self.run_pipeline(
            orchestrator,
            query=query,
            memories=[],
            budget=fixed_tokens,
        )

        self.assertEqual(
            result.status,
            PipelineStatus.INVALID_BUDGET_CONFIGURATION,
        )

        self.assertEqual(
            result.stopped_reason,
            "invalid_budget_configuration",
        )

        # 첫 Tool은 실행된다.
        self.assertEqual(len(result.tool_calls), 1)
        self.assertTrue(result.tool_results[0].success)

        # 두 번째 Context에서는 Agent가 실행되지 않는다.
        self.assertEqual(len(result.steps), 2)
        self.assertIsNotNone(result.steps[0].action_result)
        self.assertIsNone(result.steps[1].action_result)

        self.assertGreater(
            result.steps[1].fixed_tokens,
            fixed_tokens,
        )

        self.assertEqual(
            result.total_input_tokens,
            result.steps[0].context_result.total_input_tokens,
        )

        self.assertEqual(len(agent.executor.calls), 1)
        self.assertFalse(result.goal_completed)

    def test_baseline_selected_constraint_prevents_unapproved_email(self):
        """
        Baseline이 선택한 Protected Constraint를
        Context Builder가 올바른 섹션에 표시하고,
        Agent가 승인 요청 후 대기하는지 검증한다.

        Pending approval은 발송 승인으로 취급하면 안 된다.
        """

        approval_rule = make_memory(
            "approval_rule",
            "외부 이메일은 반드시 사용자 승인 후 발송한다.",
            turn_id=10,
            memory_type=MemoryType.CONSTRAINT,
            memory_key="email.external.requires_approval",
            is_protected=True,
        )

        query = (
            "professor@example.test에게 "
            "report_final.pdf를 메일로 보내줘."
        )

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
        )

        result = self.run_pipeline(
            orchestrator,
            query=query,
            memories=[approval_rule, self.report],
        )

        self.assertEqual(result.status, PipelineStatus.OK)
        self.assertEqual(result.stopped_reason, "completed")

        self.assertEqual(len(result.tool_calls), 1)
        self.assertEqual(
            result.tool_calls[0].action,
            "request_approval",
        )

        self.assertEqual(
            result.tool_results[0].output["status"],
            "pending",
        )

        self.assertEqual(agent.executor.email.sent, [])

        self.assertIn(
            "[PROTECTED CONSTRAINTS]",
            result.steps[0].context_result.context,
        )

        self.assertIn(
            "승인을 기다리고 있습니다",
            result.response,
        )

        self.assertFalse(result.goal_completed)

    def test_policy_failure_and_invalid_return_are_handled(self):
        """
        Selection Policy가 오류 상태를 반환하면
        Context Builder가 이를 전파하고 Agent를 실행하지 않는다.

        잘못된 반환 타입은 명시적으로 거부한다.
        """

        def failed_policy(**kwargs):
            return SelectionResult(
                status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            )

        agent = self.make_agent()

        orchestrator = self.make_orchestrator(
            agent=agent,
            policy=failed_policy,
        )

        result = self.run_pipeline(orchestrator)

        self.assertEqual(
            result.status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )
        self.assertEqual(result.stopped_reason, "context_budget")
        self.assertFalse(result.agent_executed)
        self.assertEqual(agent.executor.calls, [])

        invalid_orchestrator = self.make_orchestrator(
            policy=lambda **kwargs: None,
        )

        with self.assertRaises(TypeError):
            self.run_pipeline(invalid_orchestrator)

    def test_configuration_validation(self):
        """
        잘못된 Strategy, max_steps, 입력 파라미터를
        거부하는지 검증한다.
        """

        with self.assertRaises(ValueError):
            make_baseline_policy("unknown_strategy")

        with self.assertRaises(ValueError):
            make_baseline_policy("vector_top_k")

        with self.assertRaises(ValueError):
            make_baseline_policy("utility_per_token")

        with self.assertRaises(ValueError):
            self.make_orchestrator(max_steps=0)

        orchestrator = self.make_orchestrator()

        with self.assertRaises(ValueError):
            self.run_pipeline(
                orchestrator,
                query="",
            )

        with self.assertRaises(ValueError):
            self.run_pipeline(
                orchestrator,
                budget=-1,
            )

        with self.assertRaises(ValueError):
            self.run_pipeline(
                orchestrator,
                current_turn=-1,
            )

        with self.assertRaises(TypeError):
            self.run_pipeline(
                orchestrator,
                memories=["not-a-memory-item"],
            )


if __name__ == "__main__":
    unittest.main()
