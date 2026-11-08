
"""Gemini usage completeness와 Prompt Budget 검증 테스트."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent.llm_planner import LLMPlanner
from benchmark.runner import _prompt_token_audit


def complete_usage():
    return SimpleNamespace(
        prompt_token_count=100,
        cached_content_token_count=10,
        tool_use_prompt_token_count=0,
        candidates_token_count=20,
        thoughts_token_count=5,
        total_token_count=125,
    )


def response(usage):
    return SimpleNamespace(
        usage_metadata=usage,
        parsed={
            "tool_name": "",
            "action": "",
            "arguments": {},
            "text_response": "완료",
        },
        text=None,
    )


def step(tokens):
    return SimpleNamespace(
        context_result=SimpleNamespace(
            total_input_tokens=tokens,
        )
    )


class Provider503(Exception):
    status_code = 503


class ScriptedModels:
    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        item = self.items.pop(0)

        if isinstance(item, Exception):
            raise item

        return item


def make_planner(items, retries=0):
    models = ScriptedModels(items)

    planner = LLMPlanner(
        client=SimpleNamespace(models=models),
        max_retries=retries,
        retry_base_seconds=0,
        sleeper=lambda seconds: None,
    )

    return planner, models


class UsageCompletenessTests(unittest.TestCase):

    def test_complete_usage_has_estimated_cost(self):
        planner, _ = make_planner([
            response(complete_usage()),
        ])

        planner.plan("context")

        telemetry = planner.telemetry()

        self.assertTrue(
            telemetry.cost_estimate_complete
        )
        self.assertTrue(
            telemetry.prompt_usage_complete
        )
        self.assertEqual(
            telemetry.successful_response_calls,
            1,
        )
        self.assertEqual(
            telemetry.cost_usage_complete_calls,
            1,
        )
        self.assertIsNotNone(
            telemetry.estimated_cost_usd
        )

    def test_missing_usage_is_not_counted_as_zero(self):
        planner, _ = make_planner([
            response(complete_usage()),
            response(None),
        ])

        planner.plan("step 1")
        planner.plan("step 2")

        telemetry = planner.telemetry()

        self.assertEqual(telemetry.api_calls, 2)
        self.assertEqual(
            telemetry.successful_response_calls,
            2,
        )
        self.assertEqual(
            telemetry.usage_metadata_calls,
            1,
        )
        self.assertEqual(
            telemetry.cost_usage_complete_calls,
            1,
        )

        self.assertEqual(
            telemetry.prompt_tokens_per_call,
            (100, None),
        )
        self.assertFalse(
            telemetry.prompt_usage_complete
        )
        self.assertFalse(
            telemetry.cost_estimate_complete
        )

        self.assertIsNone(telemetry.prompt_tokens)
        self.assertIsNone(telemetry.output_tokens)
        self.assertIsNone(telemetry.estimated_cost_usd)
        self.assertEqual(
            telemetry.to_dict()["accounting_status"],
            "incomplete",
        )

    def test_missing_candidate_tokens_invalidates_cost(self):
        incomplete = SimpleNamespace(
            prompt_token_count=100,
            candidates_token_count=None,
            response_token_count=None,
        )

        planner, _ = make_planner([
            response(incomplete),
        ])

        planner.plan("context")
        telemetry = planner.telemetry()

        # Prompt token은 측정 가능하다.
        self.assertTrue(telemetry.prompt_usage_complete)
        self.assertEqual(
            telemetry.prompt_tokens_per_call,
            (100,),
        )

        # 출력 토큰을 모르면 전체 비용은 확정할 수 없다.
        self.assertFalse(
            telemetry.cost_estimate_complete
        )
        self.assertIsNone(
            telemetry.estimated_cost_usd
        )

    def test_retry_can_verify_budget_but_not_full_cost(self):
        planner, models = make_planner(
            [
                Provider503("temporary failure"),
                response(complete_usage()),
            ],
            retries=1,
        )

        planner.plan("context")
        telemetry = planner.telemetry()

        self.assertEqual(models.calls, 2)
        self.assertEqual(telemetry.api_calls, 2)
        self.assertEqual(
            telemetry.successful_response_calls,
            1,
        )
        self.assertEqual(
            telemetry.billing_uncertain_attempts,
            1,
        )

        self.assertTrue(
            telemetry.prompt_usage_complete
        )
        self.assertFalse(
            telemetry.cost_estimate_complete
        )
        self.assertIsNone(
            telemetry.estimated_cost_usd
        )

        audit = _prompt_token_audit(
            executed_steps=[step(100)],
            actual_prompt_tokens=(
                telemetry.prompt_tokens_per_call
            ),
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertTrue(audit["complete"])
        self.assertEqual(
            audit["verification_status"],
            "verified",
        )

    def test_missing_prompt_usage_is_unverified(self):
        audit = _prompt_token_audit(
            executed_steps=[
                step(800),
                step(900),
            ],
            actual_prompt_tokens=[
                801,
                None,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertTrue(audit["call_count_match"])
        self.assertFalse(audit["complete"])
        self.assertEqual(
            audit["verification_status"],
            "unverified",
        )
        self.assertIsNone(
            audit["actual_budget_exceeded"]
        )

    def test_known_overflow_remains_true_when_incomplete(self):
        audit = _prompt_token_audit(
            executed_steps=[
                step(1000),
                step(900),
            ],
            actual_prompt_tokens=[
                1030,
                None,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertFalse(audit["complete"])
        self.assertEqual(
            audit["verification_status"],
            "exceeded",
        )
        self.assertTrue(
            audit["actual_budget_exceeded"]
        )

    def test_no_api_call_is_zero_cost(self):
        planner, _ = make_planner([])
        telemetry = planner.telemetry()

        self.assertEqual(telemetry.api_calls, 0)
        self.assertTrue(
            telemetry.cost_estimate_complete
        )
        self.assertEqual(telemetry.output_tokens, 0)
        self.assertEqual(
            telemetry.estimated_cost_usd,
            0.0,
        )


if __name__ == "__main__":
    unittest.main()
