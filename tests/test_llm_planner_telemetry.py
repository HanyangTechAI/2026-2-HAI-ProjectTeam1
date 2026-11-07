import unittest

from agent.llm_planner import (
    LLMPlanner,
    GEMINI_31_FLASH_LITE_INPUT_USD_PER_MILLION,
    GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_MILLION,
    GEMINI_31_FLASH_LITE_CACHED_INPUT_USD_PER_MILLION,
)


class FakeUsageMetadata:
    prompt_token_count = 100
    cached_content_token_count = 10
    tool_use_prompt_token_count = 0

    candidates_token_count = 20
    thoughts_token_count = 5
    total_token_count = 125


class FakeResponse:
    usage_metadata = FakeUsageMetadata()

    parsed = {
        "tool_name": "",
        "action": "",
        "arguments": {},
        "text_response": "완료",
    }

    text = None


class FakeModels:
    def generate_content(
        self,
        *,
        model,
        contents,
        config,
    ):
        return FakeResponse()


class FakeClient:
    def __init__(self):
        self.models = FakeModels()


class LLMPlannerTelemetryTests(unittest.TestCase):
    def test_usage_metadata_is_accumulated(self):
        planner = LLMPlanner(
            client=FakeClient(),
            temperature=0,
        )

        action = planner.plan("테스트 context")

        self.assertEqual(
            action.text_response,
            "완료",
        )

        telemetry = planner.telemetry()

        self.assertEqual(
            telemetry.planner_calls,
            1,
        )
        self.assertEqual(
            telemetry.api_calls,
            1,
        )
        self.assertEqual(
            telemetry.usage_metadata_calls,
            1,
        )

        self.assertEqual(
            telemetry.prompt_tokens,
            100,
        )
        self.assertEqual(
            telemetry.cached_prompt_tokens,
            10,
        )
        self.assertEqual(
            telemetry.candidate_tokens,
            20,
        )
        self.assertEqual(
            telemetry.thoughts_tokens,
            5,
        )

        # 실제 과금 출력 = candidate + thinking.
        self.assertEqual(
            telemetry.output_tokens,
            25,
        )

        self.assertEqual(
            telemetry.total_tokens,
            125,
        )

        self.assertGreaterEqual(
            telemetry.planning_latency_ms,
            0.0,
        )

        expected_cost = (
            90
            * GEMINI_31_FLASH_LITE_INPUT_USD_PER_MILLION
            + 10
            * GEMINI_31_FLASH_LITE_CACHED_INPUT_USD_PER_MILLION
            + 25
            * GEMINI_31_FLASH_LITE_OUTPUT_USD_PER_MILLION
        ) / 1_000_000.0

        self.assertAlmostEqual(
            telemetry.estimated_cost_usd,
            expected_cost,
            places=12,
        )

    def test_multiple_calls_are_accumulated(self):
        planner = LLMPlanner(
            client=FakeClient(),
        )

        planner.plan("step 1")
        planner.plan("step 2")
        planner.plan("step 3")

        telemetry = planner.telemetry()

        self.assertEqual(
            telemetry.planner_calls,
            3,
        )
        self.assertEqual(
            telemetry.api_calls,
            3,
        )

        self.assertEqual(
            telemetry.prompt_tokens,
            300,
        )
        self.assertEqual(
            telemetry.candidate_tokens,
            60,
        )
        self.assertEqual(
            telemetry.thoughts_tokens,
            15,
        )
        self.assertEqual(
            telemetry.output_tokens,
            75,
        )
        self.assertEqual(
            telemetry.total_tokens,
            375,
        )

    def test_no_api_call_has_zero_cost(self):
        planner = LLMPlanner(
            client=FakeClient(),
        )

        telemetry = planner.telemetry()

        self.assertEqual(
            telemetry.api_calls,
            0,
        )
        self.assertEqual(
            telemetry.output_tokens,
            0,
        )
        self.assertEqual(
            telemetry.estimated_cost_usd,
            0.0,
        )


if __name__ == "__main__":
    unittest.main()