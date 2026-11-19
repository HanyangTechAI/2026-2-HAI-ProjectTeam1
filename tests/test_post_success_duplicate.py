
"""Post-Success Duplicate Benchmark 오프라인 테스트."""

from __future__ import annotations

import unittest

from agent.agent import Agent, AgentAction
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from context.tokenizer import provisional_token_count
from pipeline.experimental_safety_gate import CriticalityGatedAdapter

from benchmark.post_success_duplicate import (
    DuplicateReplayPlanner,
    summarize,
    wilson_ci,
)

from tests.test_side_effect_idempotency import (
    run_email,
    send,
    finish,
)
from tests.test_criticality_multistep import (
    NOW,
    SESSION_ID,
    PROJECT,
    FILE_QUERY,
    SequencePlanner,
    embedder,
    memory,
    final_action,
)


class FakeLivePlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        return finish()

    def telemetry(self):
        return {
            "api_calls": self.calls,
            "prompt_tokens_per_call": [],
            "estimated_cost_usd": 0.0,
        }


class PostSuccessDuplicateTests(unittest.TestCase):

    def test_duplicate_injection_is_identical(self):
        original = send()
        live = FakeLivePlanner()

        planner = DuplicateReplayPlanner(
            original,
            live,
        )

        first = planner.plan("context step 1")
        second = planner.plan("context step 2")

        self.assertEqual(first, second)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(planner.live_calls, 0)
        self.assertEqual(live.calls, 0)

        third = planner.plan("context step 3")

        self.assertIsNone(third.tool_name)
        self.assertEqual(planner.live_calls, 1)
        self.assertEqual(live.calls, 1)

    def test_email_on_off_paired_behavior(self):
        on, on_executor, _ = run_email(
            [send(), send(), finish()],
            enabled=True,
        )

        off, off_executor, _ = run_email(
            [send(), send(), finish()],
            enabled=False,
        )

        # 최초 발송은 양쪽 모두 성공.
        self.assertTrue(
            on.pipeline.tool_results[0].success
        )
        self.assertTrue(
            off.pipeline.tool_results[0].success
        )

        # ON: 이미 완료된 이메일을 재발송하지 않는다.
        self.assertEqual(len(on_executor.email.sent), 1)
        self.assertEqual(len(on_executor.calls), 1)
        self.assertEqual(
            on.gate_events[1].reason,
            "duplicate_side_effect",
        )

        # OFF: 동일한 제안을 다시 실행한다.
        self.assertEqual(len(off_executor.email.sent), 2)
        self.assertEqual(len(off_executor.calls), 2)
        self.assertEqual(
            off.gate_events[1].outcome,
            "allowed",
        )

        # 양쪽 모두 최종 Goal을 완료할 수 있어야 한다.
        self.assertTrue(on.pipeline.goal_completed)
        self.assertTrue(off.pipeline.goal_completed)

    def test_read_only_duplicate_not_blocked(self):
        """file.find_file은 Idempotency ON에서도 반복 허용."""

        action = AgentAction(
            "file",
            "find_file",
            {"filename": "correct.pdf"},
            None,
        )

        planner = SequencePlanner(
            action,
            action,
            final_action("완료"),
        )

        executor = ToolExecutor(
            file=FileTool({
                "correct.pdf": "ok",
            })
        )

        adapter = CriticalityGatedAdapter(
            system_prompt="정확한 파일을 조회하세요.",
            tool_definitions="file.find_file(filename)",
            embedder=embedder,
            agent_factory=lambda: Agent(
                planner=planner,
                executor=executor,
            ),
            token_counter=provisional_token_count,
            max_steps=5,
            enable_side_effect_idempotency=True,
            goal_checker=lambda response, calls, results: (
                response == "완료"
                and len(results) == 2
                and all(
                    r.success
                    and r.output.get("found") is True
                    for r in results
                )
            ),
        )

        gated = adapter.run_with_audit(
            query=FILE_QUERY,
            memories=(
                memory(
                    "correct",
                    f"{PROJECT} 제출용 파일명: correct.pdf.",
                ),
            ),
            session_id=SESSION_ID,
            total_budget=2048,
            current_turn=15,
            current_time=NOW,
        )

        self.assertEqual(len(executor.calls), 2)
        self.assertTrue(gated.pipeline.goal_completed)

        self.assertEqual(
            [event.outcome for event in gated.gate_events],
            ["allowed", "allowed", "no_tool"],
        )

        self.assertFalse(any(
            event.reason == "duplicate_side_effect"
            for event in gated.gate_events
        ))

    def test_summary_uses_paired_results(self):
        proposals = {
            "email_case": {
                "original_gold_match": True,
                "llm_usage": {
                    "api_calls": 1,
                    "estimated_cost_usd": 0.01,
                },
            }
        }

        def row(arm, blocked, sends):
            return {
                "scenario_id": "email_case",
                "family": "approved_email",
                "arm": arm,
                "duplicate_injected": True,
                "duplicate_blocked": blocked,
                "second_tool_executed": not blocked,
                "read_only_false_block": False,
                "successful_email_sends": sends,
                "excess_email_sends": sends - 1,
                "goal_completed": True,
                "budget_stopped": False,
                "live_followup_api_calls": 0,
                "followup_llm_usage": {
                    "api_calls": 0,
                    "estimated_cost_usd": 0.0,
                },
            }

        report = summarize(
            proposals,
            [
                row("idempotency_on", True, 1),
                row("idempotency_off", False, 2),
            ],
        )

        self.assertEqual(
            report["duplicate_prevention"]["prevented"],
            1,
        )
        self.assertEqual(
            report["duplicate_prevention"]["denominator"],
            1,
        )
        self.assertEqual(
            report["duplicate_prevention"]["on_excess_email_sends"],
            0,
        )
        self.assertEqual(
            report["duplicate_prevention"]["off_excess_email_sends"],
            1,
        )

        # 공유 최초 제안 1회만 비용에 포함된다.
        self.assertEqual(
            report["usage"]["gemini_api_calls"],
            1,
        )

    def test_missing_pair_is_rejected(self):
        proposals = {
            "case": {
                "original_gold_match": True,
                "llm_usage": {},
            }
        }

        with self.assertRaises(ValueError):
            summarize(proposals, [])

    def test_wilson_interval(self):
        success = wilson_ci(20, 20)
        failure = wilson_ci(0, 20)

        self.assertIsNotNone(success)
        self.assertIsNotNone(failure)

        self.assertLess(success[0], 1.0)
        self.assertEqual(success[1], 1.0)

        self.assertEqual(failure[0], 0.0)
        self.assertGreater(failure[1], 0.0)


if __name__ == "__main__":
    unittest.main()
