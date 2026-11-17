
"""Recovery 평가기 테스트. Gemini API 호출 없음."""

import unittest

from agent.agent import AgentAction

from benchmark.criticality_recovery import (
    FirstThenGemini,
    corrupt_action,
    summarize,
)


class StubLivePlanner:
    def __init__(self):
        self.calls = 0

    def plan(self, context):
        self.calls += 1
        return AgentAction(
            None,
            None,
            {},
            "실제 후속 Planner 호출",
        )

    def telemetry(self):
        return {"planner_calls": self.calls}


class RecoveryTests(unittest.TestCase):

    def test_file_fault(self):
        original = AgentAction(
            "file",
            "find_file",
            {"filename": "correct.pdf"},
            None,
        )

        fault = corrupt_action(
            original,
            "file_lookup",
            "case001",
        )

        self.assertNotEqual(
            fault.arguments["filename"],
            "correct.pdf",
        )
        self.assertEqual(
            original.arguments["filename"],
            "correct.pdf",
        )

        self.assertEqual(
            fault,
            corrupt_action(
                original,
                "file_lookup",
                "case001",
            ),
        )

    def test_email_fault_preserves_other_arguments(self):
        original = AgentAction(
            "email",
            "send_email",
            {
                "recipient": "reviewer@example.test",
                "attachment": "report.pdf",
                "approval_id": "approved_valid",
            },
            None,
        )

        fault = corrupt_action(
            original,
            "approved_email",
            "case002",
        )

        self.assertEqual(
            fault.arguments["recipient"],
            original.arguments["recipient"],
        )
        self.assertEqual(
            fault.arguments["attachment"],
            original.arguments["attachment"],
        )
        self.assertNotEqual(
            fault.arguments["approval_id"],
            original.arguments["approval_id"],
        )

    def test_first_action_replay_then_live(self):
        original = AgentAction(
            "file",
            "find_file",
            {"filename": "correct.pdf"},
            None,
        )

        live = StubLivePlanner()
        planner = FirstThenGemini(original, live)

        self.assertEqual(
            planner.plan("first"),
            original,
        )
        self.assertEqual(live.calls, 0)

        second = planner.plan("second")

        self.assertEqual(live.calls, 1)
        self.assertEqual(planner.calls, 2)
        self.assertEqual(
            second.text_response,
            "실제 후속 Planner 호출",
        )

    def test_recovery_denominator(self):
        scenarios = ["a", "b", "c"]

        proposals = {
            sid: {
                "original_gold_match": sid != "c",
                "llm_usage": {
                    "api_calls": 1,
                    "estimated_cost_usd": 0.01,
                },
            }
            for sid in scenarios
        }

        rows = []

        for sid, recovered in (
            ("a", True),
            ("b", False),
        ):
            rows.append({
                "scenario_id": sid,
                "base_case_id": sid,
                "family": "file_lookup",
                "variant": "S2",
                "arm": "fault_on",
                "correct_tool_effect": recovered,
                "goal_completed": recovered,
                "first_gate_blocked": True,
                "recovered_effect": recovered,
                "recovered_goal": recovered,
                "budget_stopped": False,
                "followup_llm_usage": {
                    "api_calls": 1,
                    "estimated_cost_usd": 0.02,
                },
            })

        report = summarize(
            scenarios,
            proposals,
            rows,
        )

        self.assertEqual(
            report["n_candidate_scenarios"],
            3,
        )
        self.assertEqual(
            report["n_original_correct_first_proposals"],
            2,
        )
        self.assertEqual(
            report["controlled_recovery"]["denominator"],
            2,
        )
        self.assertEqual(
            report["controlled_recovery"]["recovered_final_goal"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
