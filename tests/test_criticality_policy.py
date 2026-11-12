
"""No-API integration/regression tests for Task-Criticality-Aware policy."""
from __future__ import annotations

import unittest
from datetime import datetime

from agent.agent import Agent
from agent.planner import Planner
from benchmark.action_time_preflight import calibrate
from benchmark.critical_flexible_generator import make_scenario
from context.builder import build_context, fixed_context_tokens
from context.tokenizer import provisional_token_count
from memory.criticality_policy import make_criticality_policy
from memory.schema import MemoryItem
from memory.selector import PipelineStatus
from pipeline.orchestrator import make_proposed_policy
from pipeline.pipeline_adapter import PipelineAdapter


def embedder(text):
    # 같은 벡터 -> 실제 검색 점수와 무관한 구조 검증
    return (1.0, 0.0)


class CriticalityPolicyTests(unittest.TestCase):

    def setUp(self):
        self.counter = provisional_token_count
        self.system_prompt = "기억을 활용해 필요한 도구를 호출하세요."
        self.tools = (
            "file.find_file(filename); "
            "email.send_email(attachment, approval_id)"
        )
        self.decisions = []

        self.critical = make_criticality_policy(
            embedder=embedder,
            top_k=5,
            on_decision=self.decisions.append,
        )

        self.frozen = make_proposed_policy(
            embedder=embedder,
            top_k=5,
        )

    def _input(self, scenario, budget, query=None):
        query = scenario["query"] if query is None else query

        return dict(
            query=query,
            session_id=scenario["session_id"],
            memories=tuple(
                MemoryItem.from_dict(x)
                for x in scenario["memory_snapshot"]
            ),
            total_budget=budget,
            fixed_tokens=fixed_context_tokens(
                self.system_prompt,
                self.tools,
                query,
                self.counter,
            ),
            current_turn=scenario["current_turn"],
            current_time=datetime.fromisoformat(
                scenario["current_time"]
            ),
            token_counter=self.counter,
        )

    def test_unsupported_action_exact_frozen_parity(self):
        scenario = make_scenario(
            "file_lookup", 42, "C", counter=self.counter
        )

        kwargs = self._input(
            scenario,
            1000,
            query="회의 내용을 간단하게 요약해줘",
        )

        a = self.frozen(**kwargs)
        b = self.critical(**kwargs)

        self.assertEqual(a.status, b.status)
        self.assertEqual(
            a.selected_memory_ids,
            b.selected_memory_ids,
        )
        self.assertEqual(
            a.rejected_memory_ids,
            b.rejected_memory_ids,
        )
        self.assertEqual(
            a.memory_tokens,
            b.memory_tokens,
        )
        self.assertIsNone(self.decisions[-1].action)

    def test_long_critical_kept_and_mandatory_preserved(self):
        for family in ("file_lookup", "approved_email"):
            with self.subTest(family=family):
                scenario = make_scenario(
                    family, 42, "C", counter=self.counter
                )

                cal = calibrate(
                    scenario,
                    self.system_prompt,
                    self.tools,
                    embedder,
                    self.counter,
                    top_k=5,
                )

                effective = cal["floor"] + (
                    3 * (cal["ceiling"] - cal["floor"])
                ) // 4

                kwargs = self._input(scenario, effective)

                selection = self.critical(**kwargs)

                ctx = build_context(
                    self.system_prompt,
                    self.tools,
                    scenario["query"],
                    selection,
                    effective,
                    self.counter,
                )

                self.assertEqual(
                    selection.status,
                    PipelineStatus.OK,
                )
                self.assertEqual(
                    ctx.status,
                    PipelineStatus.OK,
                )
                self.assertLessEqual(
                    ctx.total_input_tokens,
                    effective,
                )
                self.assertIn(
                    scenario["metadata"][
                        "critical_flexible_memory_ids"
                    ][0],
                    ctx.selected_memory_ids,
                )
                self.assertEqual(
                    len(self.decisions[-1].promoted_ids),
                    1,
                )

                for mid in (
                    scenario["required_state_ids"]
                    + scenario["applicable_constraint_ids"]
                ):
                    self.assertIn(
                        mid,
                        ctx.selected_memory_ids,
                    )

    def test_budget_failures_respect_frozen_semantics(self):
        scenario = make_scenario(
            "approved_email", 42, "C", counter=self.counter
        )

        kwargs = self._input(scenario, 0)

        a = self.frozen(**kwargs)
        b = self.critical(**kwargs)

        self.assertEqual(
            a.status,
            PipelineStatus.INVALID_BUDGET_CONFIGURATION,
        )
        self.assertEqual(a.status, b.status)
        self.assertFalse(self.decisions)

        fixed = kwargs["fixed_tokens"]
        kwargs["total_budget"] = fixed + 1

        self.assertEqual(
            self.critical(**kwargs).status,
            PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
        )

    def test_orchestrator_pipeline_adapter_without_api(self):
        scenario = make_scenario(
            "file_lookup", 42, "C", counter=self.counter
        )

        adapter = PipelineAdapter(
            policy=self.critical,
            agent_factory=lambda: Agent(planner=Planner()),
            system_prompt=self.system_prompt,
            tool_definitions=self.tools,
            token_counter=self.counter,
            max_steps=1,
        )

        outcome = adapter(
            query=scenario["query"],
            session_id=scenario["session_id"],
            memories=tuple(
                MemoryItem.from_dict(x)
                for x in scenario["memory_snapshot"]
            ),
            total_budget=1000,
            current_turn=scenario["current_turn"],
            current_time=datetime.fromisoformat(
                scenario["current_time"]
            ),
        )

        self.assertEqual(
            outcome.status,
            PipelineStatus.OK,
        )
        self.assertTrue(outcome.steps)
        self.assertEqual(
            outcome.steps[0].context_result.status,
            PipelineStatus.OK,
        )


if __name__ == "__main__":
    unittest.main()
