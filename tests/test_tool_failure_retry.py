
"""Tool Failure Retry 동작 및 기존 종료 정책 회귀 테스트."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent.agent import Agent, AgentAction
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor

from context.tokenizer import provisional_token_count
from memory.selector import PipelineStatus, SelectionResult
from pipeline.pipeline_adapter import PipelineAdapter


NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)

RECIPIENT = "reviewer@example.test"
CORRECT_APPROVAL = "approved_REAL123"
WRONG_APPROVAL = "approved_UNKNOWN123"


def send(approval_id: str) -> AgentAction:
    return AgentAction(
        tool_name="email",
        action="send_email",
        arguments={
            "recipient": RECIPIENT,
            "attachment": "report.pdf",
            "approval_id": approval_id,
        },
        text_response=None,
    )


def finish() -> AgentAction:
    return AgentAction(
        tool_name=None,
        action=None,
        arguments={},
        text_response="발송을 완료했습니다.",
    )


class SequencePlanner:
    """테스트에만 사용하는 결정적 Planner."""

    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0
        self.contexts = []

    def plan(self, context):
        self.contexts.append(context)

        index = self.calls
        self.calls += 1

        if index >= len(self.actions):
            return finish()

        return self.actions[index]


def policy(**kwargs):
    """Memory가 없는 테스트용 정상 Selection."""
    return SelectionResult(
        status=PipelineStatus.OK,
    )


class ToolFailureRetryTests(unittest.TestCase):

    def run_pipeline(
        self,
        actions,
        *,
        retries=0,
    ):
        planner = SequencePlanner(actions)

        executor = ToolExecutor(
            email=EmailTool([
                CORRECT_APPROVAL,
            ])
        )

        def goal_checker(response, calls, results):
            return bool(response) and any(
                result.success
                and result.action == "send_email"
                and result.output.get("authorized_to_send")
                is True
                for result in results
            )

        adapter = PipelineAdapter(
            policy=policy,
            system_prompt="승인된 이메일만 발송하세요.",
            tool_definitions=(
                "email.send_email("
                "recipient,attachment,approval_id)"
            ),
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            token_counter=provisional_token_count,
            max_steps=8,
            tool_failure_retries=retries,
            goal_checker=goal_checker,
        )

        result = adapter(
            query=(
                "HAI-ABC123 보고서를 "
                f"{RECIPIENT}에게 이메일로 보내줘"
            ),
            memories=(),
            session_id="session",
            total_budget=2048,
            current_turn=10,
            current_time=NOW,
        )

        return result, planner, executor

    def test_original_fault_off_stops_immediately(self):
        """재시도 미설정 시 기존 정책 유지."""

        result, planner, executor = self.run_pipeline(
            [
                send(WRONG_APPROVAL),
                send(CORRECT_APPROVAL),
                finish(),
            ],
            retries=0,
        )

        self.assertEqual(
            result.stopped_reason,
            "tool_failure",
        )
        self.assertFalse(result.goal_completed)

        self.assertEqual(planner.calls, 1)
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(len(result.tool_results), 1)

        self.assertFalse(
            result.tool_results[0].success
        )
        self.assertEqual(
            result.tool_results[0].error,
            "unknown approval_id",
        )

    def test_fault_off_retry_recovers(self):
        """실패 피드백 이후 재계획하여 발송 성공."""

        result, planner, executor = self.run_pipeline(
            [
                send(WRONG_APPROVAL),
                send(CORRECT_APPROVAL),
                finish(),
            ],
            retries=1,
        )

        self.assertEqual(
            result.stopped_reason,
            "completed",
        )
        self.assertTrue(result.goal_completed)

        self.assertEqual(planner.calls, 3)
        self.assertEqual(len(executor.calls), 2)

        self.assertFalse(
            result.tool_results[0].success
        )
        self.assertTrue(
            result.tool_results[1].success
        )

        # 실패 기록이 삭제되지 않고 보존되어야 한다.
        self.assertEqual(len(result.tool_calls), 2)
        self.assertEqual(len(result.interactions), 2)

        # Gemini가 읽는 두 번째 Context에 실패 결과가 포함됨.
        self.assertIn(
            "[RECENT TOOL RESULTS]",
            planner.contexts[1],
        )
        self.assertIn(
            "unknown approval_id",
            planner.contexts[1],
        )

        # 정상적으로 승인된 발송을 완료해야 한다.
        self.assertTrue(
            result.tool_results[1]
            .output["authorized_to_send"]
        )

    def test_retry_limit_prevents_unbounded_loop(self):
        """연속 실패가 허용 횟수를 넘으면 종료."""

        result, planner, executor = self.run_pipeline(
            [
                send(WRONG_APPROVAL),
                send(WRONG_APPROVAL),
                send(CORRECT_APPROVAL),
                finish(),
            ],
            retries=1,
        )

        self.assertFalse(result.goal_completed)
        self.assertEqual(
            result.stopped_reason,
            "tool_failure",
        )

        # 최초 실패 후 재계획 1회.
        # 두 번째 실패 이후 추가 재계획하지 않음.
        self.assertEqual(planner.calls, 2)
        self.assertEqual(len(executor.calls), 2)

    def test_invalid_agent_action_is_not_retried(self):
        """ToolExecutor가 실행되지 않은 실패는 재시도 금지."""

        malformed = AgentAction(
            tool_name=None,
            action="send_email",
            arguments={},
            text_response=None,
        )

        result, planner, executor = self.run_pipeline(
            [
                malformed,
                send(CORRECT_APPROVAL),
                finish(),
            ],
            retries=1,
        )

        self.assertEqual(
            result.stopped_reason,
            "tool_failure",
        )
        self.assertEqual(planner.calls, 1)

        self.assertEqual(len(executor.calls), 0)
        self.assertEqual(len(result.tool_results), 0)

    def test_retry_argument_validation(self):
        with self.assertRaises(ValueError):
            PipelineAdapter(
                policy=policy,
                system_prompt="",
                tool_definitions="",
                tool_failure_retries=-1,
            )


if __name__ == "__main__":
    unittest.main()
