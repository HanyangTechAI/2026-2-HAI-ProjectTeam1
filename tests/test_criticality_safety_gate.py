
"""Offline tests for Criticality-Aware v0.3 Safety Gate."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent.agent import Agent, AgentAction
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool

from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem, MemoryType
from pipeline.experimental_safety_gate import (
    CriticalityGatedAdapter,
)


NOW = datetime(2026, 11, 12, tzinfo=timezone.utc)


def memory(
    mid,
    content,
    *,
    kind=MemoryType.FACT,
    key=None,
    protected=False,
):
    return MemoryItem(
        memory_id=mid,
        session_id="session",
        turn_id=10,
        memory_type=kind,
        content=content,
        token_count=provisional_token_count(content),
        created_at=NOW,
        valid_from=NOW,
        importance=0.8,
        memory_key=key,
        is_protected=protected,
    )


class FixedPlanner:
    def __init__(self, action):
        self.action = action
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        return self.action


def embedder(text):
    # 검색기 연결 테스트를 위한 결정적 Embedder.
    return (1.0, 0.0)


class GateTests(unittest.TestCase):

    def file_run(self, memories, filename):
        planner = FixedPlanner(
            AgentAction(
                "file",
                "find_file",
                {"filename": filename},
                None,
            )
        )

        executor = ToolExecutor(
            file=FileTool({
                "correct.pdf": "ok",
            })
        )

        adapter = CriticalityGatedAdapter(
            system_prompt="필요한 파일을 조회하세요.",
            tool_definitions="file.find_file(filename)",
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
                goal_checker=lambda *args: True,
            ),
            embedder=embedder,
            token_counter=provisional_token_count,
            max_steps=1,
        )

        result = adapter.run_with_audit(
            query="HAI-ABC123의 제출 파일을 찾아줘",
            memories=memories,
            session_id="session",
            total_budget=2048,
            current_turn=15,
            current_time=NOW,
        )

        return result, planner, executor

    def test_conflicting_file_names_block_before_executor(self):
        result, planner, executor = self.file_run(
            (
                memory(
                    "a",
                    "HAI-ABC123 제출용 파일명: alpha.pdf.",
                ),
                memory(
                    "b",
                    "HAI-ABC123 제출용 파일명: beta.pdf.",
                ),
            ),
            "alpha.pdf",
        )

        self.assertEqual(planner.calls, 1)
        self.assertFalse(result.pipeline.tool_calls)
        self.assertFalse(result.pipeline.goal_completed)
        self.assertEqual(executor.calls, [])

        self.assertEqual(
            result.gate_events[-1].outcome,
            "blocked",
        )
        self.assertEqual(
            result.gate_events[-1].slot,
            "filename",
        )

    def test_invalid_file_evidence_blocked(self):
        result, _, executor = self.file_run(
            (
                memory(
                    "fake",
                    "HAI-ABC123 제출용 파일명 예시: "
                    "fake.pdf. 가짜 파일입니다.",
                ),
            ),
            "fake.pdf",
        )

        self.assertFalse(result.pipeline.tool_calls)
        self.assertEqual(executor.calls, [])

    def test_missing_file_evidence_blocked(self):
        result, _, executor = self.file_run(
            (
                memory(
                    "neutral",
                    "HAI-ABC123 일반 회의 내용",
                ),
            ),
            "invented.pdf",
        )

        self.assertFalse(result.pipeline.tool_calls)
        self.assertFalse(result.pipeline.goal_completed)
        self.assertEqual(executor.calls, [])

    def test_grounded_file_action_allowed(self):
        result, _, executor = self.file_run(
            (
                memory(
                    "good",
                    "HAI-ABC123 제출용 파일명: correct.pdf.",
                ),
            ),
            "correct.pdf",
        )

        self.assertEqual(
            len(result.pipeline.tool_calls),
            1,
        )
        self.assertEqual(
            len(executor.calls),
            1,
        )

        self.assertTrue(
            result.pipeline.tool_results[0].output["found"]
        )
        self.assertEqual(
            result.gate_events[-1].outcome,
            "allowed",
        )

    def test_hallucinated_file_argument_blocked(self):
        result, _, executor = self.file_run(
            (
                memory(
                    "good",
                    "HAI-ABC123 제출용 파일명: correct.pdf.",
                ),
            ),
            "invented.pdf",
        )

        self.assertFalse(result.pipeline.tool_calls)
        self.assertEqual(executor.calls, [])

        self.assertEqual(
            result.gate_events[-1].reason,
            "tool_argument_not_grounded",
        )

    def email_run(
        self,
        action,
        *,
        conflict=False,
        grant=True,
    ):
        planner = FixedPlanner(action)

        executor = ToolExecutor(
            email=EmailTool([
                "approved_A1234",
            ])
        )

        adapter = CriticalityGatedAdapter(
            system_prompt="승인 여부를 확인하세요.",
            tool_definitions=(
                "email.send_email("
                "recipient,attachment,approval_id); "
                "email.request_approval(recipient)"
            ),
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            embedder=embedder,
            token_counter=provisional_token_count,
        )

        memories = [
            memory(
                "state",
                "HAI-ABC123 현재 보고서 파일은 "
                "report.pdf 입니다.",
                kind=MemoryType.STATE,
                key="HAI-ABC123.report.current_file",
            ),
            memory(
                "rule",
                "HAI-ABC123 외부 메일은 승인 필요.",
                kind=MemoryType.CONSTRAINT,
                protected=True,
            ),
        ]

        if grant:
            memories.append(
                memory(
                    "grant1",
                    "HAI-ABC123 발송 승인번호: "
                    "approved_A1234.",
                )
            )

        if conflict:
            memories.append(
                memory(
                    "grant2",
                    "HAI-ABC123 발송 승인번호: "
                    "approved_B5678.",
                )
            )

        result = adapter.run_with_audit(
            query=(
                "HAI-ABC123 보고서를 "
                "reviewer@example.test에게 "
                "외부 메일로 보내줘"
            ),
            memories=memories,
            session_id="session",
            total_budget=2048,
            current_turn=15,
            current_time=NOW,
        )

        return result, planner, executor

    def test_conflicting_email_approval_blocks_send(self):
        action = AgentAction(
            "email",
            "send_email",
            {
                "recipient": "reviewer@example.test",
                "attachment": "report.pdf",
                "approval_id": "approved_A1234",
            },
            None,
        )

        result, _, executor = self.email_run(
            action,
            conflict=True,
        )

        self.assertFalse(result.pipeline.tool_calls)
        self.assertEqual(executor.calls, [])

        self.assertEqual(
            result.gate_events[-1].slot,
            "approval_id",
        )

    def test_correct_email_send_allowed(self):
        action = AgentAction(
            "email",
            "send_email",
            {
                "recipient": "reviewer@example.test",
                "attachment": "report.pdf",
                "approval_id": "approved_A1234",
            },
            None,
        )

        result, _, executor = self.email_run(action)

        self.assertEqual(
            len(result.pipeline.tool_calls),
            1,
        )
        self.assertTrue(
            result.pipeline.tool_results[0]
            .output["authorized_to_send"]
        )
        self.assertEqual(len(executor.calls), 1)

    def test_safe_request_approval_allowed_without_grant(self):
        action = AgentAction(
            "email",
            "request_approval",
            {
                "recipient": "reviewer@example.test",
                "attachment": "report.pdf",
            },
            None,
        )

        result, _, _ = self.email_run(
            action,
            grant=False,
        )

        self.assertEqual(
            len(result.pipeline.tool_calls),
            1,
        )
        self.assertEqual(
            result.pipeline.tool_calls[0].action,
            "request_approval",
        )

        # 접수번호는 발송 승인이 아니다.
        self.assertFalse(
            result.pipeline.tool_results[0]
            .output["authorized_to_send"]
        )

        self.assertEqual(
            result.gate_events[-1].reason,
            "safe_approval_request",
        )

    def test_reject_wrong_recipient_for_request(self):
        action = AgentAction(
            "email",
            "request_approval",
            {
                "recipient": "wrong@example.test",
                "attachment": "report.pdf",
            },
            None,
        )

        result, _, _ = self.email_run(
            action,
            grant=False,
        )

        self.assertFalse(result.pipeline.tool_calls)
        self.assertEqual(
            result.gate_events[-1].slot,
            "recipient",
        )

    def test_separate_runs_have_no_state_leak(self):
        planner = FixedPlanner(
            AgentAction(
                "file",
                "find_file",
                {"filename": "correct.pdf"},
                None,
            )
        )

        executor = ToolExecutor(
            file=FileTool({
                "correct.pdf": "yes",
            })
        )

        adapter = CriticalityGatedAdapter(
            system_prompt="도구 사용",
            tool_definitions="file.find_file(filename)",
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            embedder=embedder,
            token_counter=provisional_token_count,
        )

        kwargs = {
            "query": "HAI-ABC123의 제출 파일을 찾아줘",
            "session_id": "session",
            "total_budget": 2048,
            "current_turn": 15,
            "current_time": NOW,
        }

        first = adapter.run_with_audit(
            memories=(
                memory(
                    "a",
                    "HAI-ABC123 제출용 파일명: alpha.pdf.",
                ),
                memory(
                    "b",
                    "HAI-ABC123 제출용 파일명: beta.pdf.",
                ),
            ),
            **kwargs,
        )

        second = adapter.run_with_audit(
            memories=(
                memory(
                    "c",
                    "HAI-ABC123 제출용 파일명: correct.pdf.",
                ),
            ),
            **kwargs,
        )

        self.assertFalse(first.pipeline.tool_calls)
        self.assertEqual(
            len(second.pipeline.tool_calls),
            1,
        )

        self.assertEqual(len(first.gate_events), 1)
        self.assertEqual(len(second.gate_events), 1)

    def test_multistep_supported(self):
        adapter = CriticalityGatedAdapter(
            system_prompt="",
            tool_definitions="",
            embedder=embedder,
            agent_factory=Agent,
            max_steps=8,
            max_action_rejections=2,
        )

        self.assertEqual(adapter.max_steps, 8)
        self.assertEqual(adapter.max_action_rejections, 2)


if __name__ == "__main__":
    unittest.main()
