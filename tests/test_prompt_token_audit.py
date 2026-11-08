import unittest
from types import SimpleNamespace

from benchmark.runner import _prompt_token_audit


def step(tokens: int):
    return SimpleNamespace(
        context_result=SimpleNamespace(
            total_input_tokens=tokens,
        )
    )


class PromptTokenAuditTests(unittest.TestCase):
    def test_exact_match(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(800),
                step(900),
            ],
            actual_prompt_tokens=[
                800,
                900,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertTrue(
            result["complete"]
        )

        self.assertFalse(
            result["actual_budget_exceeded"]
        )

        self.assertEqual(
            result["max_positive_delta"],
            0,
        )

    def test_small_counttokens_difference_is_recorded(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(800),
                step(900),
            ],
            actual_prompt_tokens=[
                801,
                902,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertEqual(
            result[
                "delta_actual_minus_counted_per_call"
            ],
            [1, 2],
        )

        self.assertEqual(
            result["max_positive_delta"],
            2,
        )

        self.assertFalse(
            result["actual_budget_exceeded"]
        )

    def test_actual_budget_overflow_is_detected(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(1024),
            ],
            actual_prompt_tokens=[
                1026,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertFalse(
            result["counted_budget_exceeded"]
        )

        self.assertTrue(
            result["actual_budget_exceeded"]
        )

    def test_missing_usage_metadata_is_incomplete(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(800),
                step(900),
            ],
            actual_prompt_tokens=[
                801,
            ],
            nominal_context_budget=1024,
            effective_context_budget=1024,
        )

        self.assertFalse(
            result["call_count_match"]
        )

        self.assertFalse(
            result["complete"]
        )

        self.assertIsNone(
            result["actual_budget_exceeded"]
        )
        
        
    def test_safety_margin_absorbs_token_difference(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(990),
            ],
            actual_prompt_tokens=[
                1008,
            ],
            nominal_context_budget=1024,
            effective_context_budget=992,
        )

        self.assertTrue(result["complete"])
        self.assertFalse(result["counted_budget_exceeded"])
        self.assertFalse(result["actual_budget_exceeded"])

        self.assertEqual(
            result["request_token_safety_margin"],
            32,
        )

        self.assertEqual(
            result["max_positive_delta"],
            18,
        )


    def test_actual_overflow_after_margin_is_detected(self):
        result = _prompt_token_audit(
            executed_steps=[
                step(992),
            ],
            actual_prompt_tokens=[
                1026,
            ],
            nominal_context_budget=1024,
            effective_context_budget=992,
        )

        self.assertTrue(result["complete"])
        self.assertFalse(result["counted_budget_exceeded"])
        self.assertTrue(result["actual_budget_exceeded"])


if __name__ == "__main__":
    unittest.main()