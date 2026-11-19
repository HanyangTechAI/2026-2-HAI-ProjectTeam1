
"""Side-Effect Idempotency 오프라인 통합 테스트."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent.agent import Agent, AgentAction
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from agent.tools.types import ToolCall

from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem, MemoryType

from pipeline.experimental_safety_gate import (
    CriticalityGatedAdapter,
)
from pipeline.side_effect_idempotency import (
    RunSideEffectLedger,
)


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
PROJECT = "HAI-ABC123"
RECIPIENT = "reviewer@example.test"
APPROVAL = "approved_A1234"


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


def email_memories():
    return (
        memory(
            "state",
            f"{PROJECT} 현재 보고서 파일은 report.pdf 입니다.",
            kind=MemoryType.STATE,
            key=f"{PROJECT}.report.current_file",
        ),
        memory(
            "constraint",
            f"{PROJECT} 외부 이메일은 사전 승인 필요.",
            kind=MemoryType.CONSTRAINT,
            protected=True,
        ),
        memory(
            "grant",
            f"{PROJECT} 발송 승인번호: {APPROVAL}.",
        ),
    )


def send(*, body=None):
    return AgentAction(
        "email",
        "send_email",
        {
            "recipient": RECIPIENT,
            "attachment": "report.pdf",
            "approval_id": APPROVAL,
            "body": body,
        } if body is not None else {
            "recipient": RECIPIENT,
            "attachment": "report.pdf",
            "approval_id": APPROVAL,
        },
        None,
    )


def lookup():
    return AgentAction(
        "file",
        "find_file",
        {"filename": "correct.pdf"},
        None,
    )


def finish():
    return AgentAction(
        None,
        None,
        {},
        "완료",
    )


class SequencePlanner:
    def __init__(self, actions):
        self.actions = list(actions)
        self.calls = 0

    def plan(self, context):
        index = self.calls
        self.calls += 1

        if index >= len(self.actions):
            return finish()

        return self.actions[index]


def embedder(text):
    return (1.0, 0.0)


def run_email(
    actions,
    *,
    enabled=True,
    executor=None,
):
    planner = SequencePlanner(actions)

    if executor is None:
        executor = ToolExecutor(
            email=EmailTool([APPROVAL])
        )

    adapter = CriticalityGatedAdapter(
        system_prompt="승인된 이메일만 발송하세요.",
        tool_definitions=(
            "email.send_email("
            "recipient,attachment,approval_id,body)"
        ),
        embedder=embedder,
        agent_factory=lambda: Agent(
            planner=planner,
            executor=executor,
        ),
        token_counter=provisional_token_count,
        max_steps=8,
        enable_side_effect_idempotency=enabled,
        goal_checker=lambda response, calls, results: (
            bool(response)
            and any(
                result.success
                and result.tool_name == "email"
                and result.action == "send_email"
                and result.output.get(
                    "authorized_to_send"
                ) is True
                for result in results
            )
        ),
    )

    result = adapter.run_with_audit(
        query=(
            f"{PROJECT} 보고서를 "
            f"{RECIPIENT}에게 외부 메일로 보내줘"
        ),
        memories=email_memories(),
        session_id="session",
        total_budget=2048,
        current_turn=15,
        current_time=NOW,
    )

    return result, executor, planner


class SideEffectIdempotencyTests(unittest.TestCase):

    def test_duplicate_email_blocked_before_executor(self):
        result, executor, planner = run_email(
            [
                send(),
                send(),
                finish(),
            ],
            enabled=True,
        )

        self.assertEqual(
            len(executor.calls),
            1,
        )
        self.assertEqual(
            len(executor.email.sent),
            1,
        )
        self.assertEqual(
            len(result.pipeline.tool_calls),
            1,
        )

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["allowed", "blocked"],
        )
        self.assertEqual(
            result.gate_events[-1].reason,
            "duplicate_side_effect",
        )

        self.assertEqual(planner.calls, 2)
        self.assertTrue(result.pipeline.goal_completed)
        self.assertEqual(
            result.pipeline.stopped_reason,
            "completed",
        )

    def test_changed_body_cannot_repeat_same_task_send(self):
        """같은 사용자 발송 작업에서 메시지 일부 변경으로 중복 우회 불가."""

        result, executor, _ = run_email(
            [
                send(body="original body"),
                send(body="modified body"),
                finish(),
            ],
            enabled=True,
        )

        self.assertEqual(
            len(executor.email.sent),
            1,
        )
        self.assertEqual(
            result.gate_events[-1].reason,
            "duplicate_side_effect",
        )
        self.assertTrue(result.pipeline.goal_completed)

    def test_disabled_mode_preserves_legacy_behavior(self):
        result, executor, planner = run_email(
            [
                send(),
                send(),
                finish(),
            ],
            enabled=False,
        )

        # 이전 실험의 동작은 변경하지 않는다.
        self.assertEqual(
            len(executor.email.sent),
            2,
        )
        self.assertEqual(planner.calls, 3)
        self.assertTrue(result.pipeline.goal_completed)

        self.assertEqual(
            [event.outcome for event in result.gate_events],
            ["allowed", "allowed", "no_tool"],
        )

    def test_new_pipeline_run_is_independent(self):
        """새 사용자 작업 실행은 별도의 논리적 작업."""

        shared_executor = ToolExecutor(
            email=EmailTool([APPROVAL])
        )

        first, _, _ = run_email(
            [send(), finish()],
            enabled=True,
            executor=shared_executor,
        )

        second, _, _ = run_email(
            [send(), finish()],
            enabled=True,
            executor=shared_executor,
        )

        self.assertTrue(first.pipeline.goal_completed)
        self.assertTrue(second.pipeline.goal_completed)

        # 동일 Executor를 재사용해도 두 실행은 독립.
        self.assertEqual(
            len(shared_executor.email.sent),
            2,
        )
        self.assertEqual(
            len(shared_executor.calls),
            2,
        )

    def test_failed_tool_is_not_marked_completed(self):
        executor = ToolExecutor(
            email=EmailTool([APPROVAL])
        )
        ledger = RunSideEffectLedger(executor)

        wrong_arguments = {
            "recipient": RECIPIENT,
            "attachment": "report.pdf",
            "approval_id": "approved_UNKNOWN",
        }

        wrong = ToolCall(
            "email",
            "send_email",
            wrong_arguments,
        )

        failed = executor.execute(wrong)

        self.assertFalse(failed.success)

        self.assertFalse(
            ledger.already_completed(
                send()
            )
        )

        correct = ToolCall(
            "email",
            "send_email",
            send().arguments,
        )

        succeeded = executor.execute(correct)

        self.assertTrue(succeeded.success)

        self.assertTrue(
            ledger.already_completed(
                send()
            )
        )

    def test_read_only_tool_never_deduplicated(self):
        executor = ToolExecutor(
            file=FileTool({
                "correct.pdf": "test file",
            })
        )
        ledger = RunSideEffectLedger(executor)

        for _ in range(2):
            result = executor.execute(
                ToolCall(
                    "file",
                    "find_file",
                    {"filename": "correct.pdf"},
                )
            )
            self.assertTrue(result.success)

        self.assertFalse(
            ledger.already_completed(
                lookup()
            )
        )
        self.assertEqual(
            len(executor.calls),
            2,
        )

    def test_same_approval_request_is_deduplicated(self):
        executor = ToolExecutor(
            email=EmailTool()
        )
        ledger = RunSideEffectLedger(executor)

        arguments = {
            "recipient": RECIPIENT,
            "attachment": "report.pdf",
        }

        first = executor.execute(
            ToolCall(
                "email",
                "request_approval",
                arguments,
            )
        )

        self.assertTrue(first.success)

        self.assertTrue(
            ledger.already_completed(
                AgentAction(
                    "email",
                    "request_approval",
                    arguments,
                    None,
                )
            )
        )

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(TypeError):
            CriticalityGatedAdapter(
                system_prompt="",
                tool_definitions="",
                embedder=embedder,
                agent_factory=Agent,
                enable_side_effect_idempotency="true",
            )
            
            
    def test_distinct_email_operations_are_not_duplicates(self):
        """같은 실행의 서로 다른 정상 이메일 2건은 모두 허용."""

        result, executor, _ = run_email(
            [
                send(body="첫 번째 보고 메일입니다."),
                send(body="두 번째 보고 메일입니다."),
                finish(),
            ],
            enabled=True,
        )

        # 논리적으로 서로 다른 작업이므로 발송 2회가 필요.
        self.assertEqual(len(executor.email.sent), 2)

        self.assertEqual(
            [item["body"] for item in executor.email.sent],
            [
                "첫 번째 보고 메일입니다.",
                "두 번째 보고 메일입니다.",
            ],
        )

        self.assertTrue(result.pipeline.goal_completed)


if __name__ == "__main__":
    unittest.main()
