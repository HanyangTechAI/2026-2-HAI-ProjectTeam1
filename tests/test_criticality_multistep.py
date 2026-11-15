"""Criticality Safety Gate Multi-Step 통합 테스트."""

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


NOW = datetime(2026, 11, 15, tzinfo=timezone.utc)
SESSION_ID = "test_session"
PROJECT = "HAI-ABC123"

FILE_QUERY = f"{PROJECT}의 제출 파일을 찾아줘"

EMAIL_QUERY = (
    f"{PROJECT} 보고서를 reviewer@example.test에게 "
    "외부 메일로 보내줘"
)


def embedder(text: str):
    """테스트 전용 결정적 Embedding."""
    return (1.0, 0.0)


def memory(
    mid: str,
    content: str,
    *,
    kind: MemoryType = MemoryType.FACT,
    protected: bool = False,
    key: str | None = None,
) -> MemoryItem:
    return MemoryItem(
        memory_id=mid,
        session_id=SESSION_ID,
        turn_id=10,
        memory_type=kind,
        content=content,
        token_count=provisional_token_count(content),
        created_at=NOW,
        valid_from=NOW,
        importance=0.8,
        is_active=True,
        is_protected=protected,
        memory_key=key,
    )


def tool_action(
    tool: str,
    action: str,
    arguments: dict,
) -> AgentAction:
    return AgentAction(
        tool_name=tool,
        action=action,
        arguments=arguments,
        text_response=None,
    )


def final_action(text: str) -> AgentAction:
    return AgentAction(
        tool_name=None,
        action=None,
        arguments={},
        text_response=text,
    )


class SequencePlanner:
    """Step마다 지정한 행동을 반환하는 Scripted Planner."""

    def __init__(self, *actions: AgentAction):
        self.actions = actions
        self.calls = 0
        self.contexts: list[str] = []

    def plan(self, context: str) -> AgentAction:
        self.contexts.append(context)

        index = self.calls
        self.calls += 1

        if index >= len(self.actions):
            return final_action("정보 부족으로 안전 보류")

        return self.actions[index]


class CriticalityMultiStepTests(unittest.TestCase):

    def run_file(
        self,
        planner: SequencePlanner,
        *,
        max_steps: int = 8,
        max_action_rejections: int = 2,
        memories: tuple[MemoryItem, ...] | None = None,
    ):
        executor = ToolExecutor(
            file=FileTool({
                "correct.pdf": "test content",
            }),
        )

        if memories is None:
            memories = (
                memory(
                    "correct",
                    f"{PROJECT} 제출용 파일명: correct.pdf.",
                ),
            )

        adapter = CriticalityGatedAdapter(
            system_prompt="정확한 파일을 조회하세요.",
            tool_definitions=(
                "file.find_file(filename)"
            ),
            embedder=embedder,
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            token_counter=provisional_token_count,
            max_steps=max_steps,
            max_action_rejections=max_action_rejections,
            goal_checker=lambda response, calls, results: (
                response == "완료"
                and len(calls) == 1
                and len(results) == 1
                and calls[0].arguments.get("filename")
                == "correct.pdf"
                and results[0].success
                and results[0].output.get("found") is True
            ),
        )

        result = adapter.run_with_audit(
            query=FILE_QUERY,
            memories=memories,
            session_id=SESSION_ID,
            total_budget=2048,
            current_turn=15,
            current_time=NOW,
        )

        return result, executor

    def test_valid_multistep_goal_completed(self):
        """정상 Tool 실행 후 다음 Step에서 완료."""

        planner = SequencePlanner(
            tool_action(
                "file",
                "find_file",
                {"filename": "correct.pdf"},
            ),
            final_action("완료"),
        )

        result, executor = self.run_file(planner)

        self.assertTrue(result.pipeline.goal_completed)
        self.assertEqual(
            result.pipeline.stopped_reason,
            "completed",
        )
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(planner.calls, 2)

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["allowed", "no_tool"],
        )

        self.assertIn(
            "[RECENT TOOL RESULTS]",
            planner.contexts[1],
        )

    def test_block_then_recover(self):
        """잘못된 첫 행동을 차단하고 다음 행동에서 복구."""

        planner = SequencePlanner(
            tool_action(
                "file",
                "find_file",
                {"filename": "invented.pdf"},
            ),
            tool_action(
                "file",
                "find_file",
                {"filename": "correct.pdf"},
            ),
            final_action("완료"),
        )

        result, executor = self.run_file(planner)

        self.assertTrue(result.pipeline.goal_completed)
        self.assertEqual(planner.calls, 3)

        # 위험한 invented.pdf는 실행되지 않았다.
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual(
            executor.calls[0].arguments["filename"],
            "correct.pdf",
        )

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["blocked", "allowed", "no_tool"],
        )

        self.assertIn(
            "[SAFETY GATE FEEDBACK]",
            planner.contexts[1],
        )

        # 거절 피드백은 새 Context에 포함되며 예산도 준수.
        self.assertTrue(
            all(
                step.context_result.total_input_tokens <= 2048
                for step in result.pipeline.steps
            )
        )

        self.assertEqual(
            len(result.pipeline.tool_results),
            1,
        )

    def test_repeated_unsafe_action_abstains(self):
        """같은 위험 행동을 반복해도 Tool은 실행되지 않는다."""

        bad = tool_action(
            "file",
            "find_file",
            {"filename": "invented.pdf"},
        )

        planner = SequencePlanner(
            bad,
            bad,
            bad,
            bad,
        )

        result, executor = self.run_file(
            planner,
            max_action_rejections=2,
        )

        self.assertFalse(result.pipeline.goal_completed)

        self.assertEqual(
            result.pipeline.stopped_reason,
            "gate_abstained",
        )

        self.assertEqual(planner.calls, 2)
        self.assertEqual(len(executor.calls), 0)

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["blocked", "blocked"],
        )

    def test_missing_evidence_safe_abstention(self):
        """근거가 없으면 재계획 이후 안전 보류."""

        planner = SequencePlanner(
            tool_action(
                "file",
                "find_file",
                {"filename": "invented.pdf"},
            ),
            final_action("정확한 파일 정보가 없습니다."),
        )

        result, executor = self.run_file(
            planner,
            memories=(
                memory(
                    "neutral",
                    f"{PROJECT} 일반 회의 기록.",
                ),
            ),
        )

        self.assertFalse(result.pipeline.goal_completed)

        self.assertEqual(
            result.pipeline.stopped_reason,
            "gate_abstained",
        )

        self.assertEqual(len(executor.calls), 0)

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["blocked", "no_tool"],
        )

    def test_request_receipt_cannot_be_used_as_grant(self):
        """승인 요청 접수번호는 실제 발송 허가가 아니다."""

        planner = SequencePlanner(
            tool_action(
                "email",
                "request_approval",
                {
                    "recipient": "reviewer@example.test",
                    "attachment": "report.pdf",
                },
            ),
            tool_action(
                "email",
                "send_email",
                {
                    "recipient": "reviewer@example.test",
                    "attachment": "report.pdf",
                    "approval_id": "approval_001",
                },
            ),
            final_action("승인이 없어 발송을 보류합니다."),
        )

        executor = ToolExecutor(
            email=EmailTool([]),
        )

        adapter = CriticalityGatedAdapter(
            system_prompt="승인을 확인한 뒤에만 발송하세요.",
            tool_definitions=(
                "email.request_approval(recipient,attachment); "
                "email.send_email(recipient,attachment,approval_id)"
            ),
            embedder=embedder,
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            token_counter=provisional_token_count,
            max_steps=8,
            max_action_rejections=2,
        )

        memories = (
            memory(
                "state",
                f"{PROJECT} 현재 보고서 파일은 report.pdf 입니다.",
                kind=MemoryType.STATE,
                key=f"{PROJECT}.report.current_file",
            ),
            memory(
                "constraint",
                f"{PROJECT} 외부 메일 발송은 승인 필요.",
                kind=MemoryType.CONSTRAINT,
                protected=True,
            ),
        )

        result = adapter.run_with_audit(
            query=EMAIL_QUERY,
            memories=memories,
            session_id=SESSION_ID,
            total_budget=2048,
            current_turn=15,
            current_time=NOW,
        )

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["allowed", "blocked", "no_tool"],
        )

        self.assertEqual(
            len(result.pipeline.tool_calls),
            1,
        )
        self.assertEqual(
            result.pipeline.tool_calls[0].action,
            "request_approval",
        )

        self.assertFalse(
            result.pipeline.tool_results[0]
            .output["authorized_to_send"]
        )

        self.assertFalse(result.pipeline.goal_completed)

        self.assertEqual(
            result.pipeline.stopped_reason,
            "gate_abstained",
        )

        self.assertEqual(
            len(executor.calls),
            1,
        )

        self.assertIn(
            "[RECENT TOOL RESULTS]",
            planner.contexts[1],
        )
        self.assertIn(
            "[SAFETY GATE FEEDBACK]",
            planner.contexts[2],
        )

    def test_run_local_gate_state(self):
        """서로 다른 실행에서 Gate 상태가 공유되지 않는다."""

        planner = SequencePlanner(
            tool_action(
                "file",
                "find_file",
                {"filename": "invented.pdf"},
            ),
            final_action("정보 부족"),
        )

        first, _ = self.run_file(planner)

        self.assertEqual(
            first.gate_events[0].outcome,
            "blocked",
        )

        second_planner = SequencePlanner(
            tool_action(
                "file",
                "find_file",
                {"filename": "correct.pdf"},
            ),
            final_action("완료"),
        )

        second, _ = self.run_file(second_planner)

        self.assertTrue(second.pipeline.goal_completed)
        self.assertEqual(
            second.gate_events[0].outcome,
            "allowed",
        )

        self.assertEqual(len(first.gate_events), 2)
        self.assertEqual(len(second.gate_events), 2)


if __name__ == "__main__":
    unittest.main()