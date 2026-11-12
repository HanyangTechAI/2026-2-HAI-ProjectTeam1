
"""Tests for benchmark.first_action_evaluator (no Gemini/API required)."""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from benchmark.first_action_evaluator import (
    FULL, NO_PACKING,
    evaluate_first_action, make_pairs, summarize, evaluate_file,
)


def sample(strategy=FULL, family="file_lookup", condition="C"):
    is_email = family == "approved_email"
    expected = (
        {"tool_name": "email", "action": "send_email", "arguments": {
            "recipient": "a@example.test", "attachment": "good.pdf", "approval_id": "approved_123"}}
        if is_email else
        {"tool_name": "file", "action": "find_file", "arguments": {"filename": "good.pdf"}}
    )
    actual = copy.deepcopy(expected)
    if is_email:
        actual["arguments"].update(subject="notice", body="hello")
        result = {"tool_name": "email", "action": "send_email", "success": True,
                  "output": {"recipient": "a@example.test", "attachment": "good.pdf",
                             "approval_id": "approved_123", "approved": True,
                             "authorized_to_send": True, "status": "granted"}}
    else:
        result = {"tool_name": "file", "action": "find_file", "success": True,
                  "output": {"found": True, "filename": "good.pdf"}}
    cid = "critical_1"
    return {
        "scenario": {
            "scenario_id": f"case_{family}_{condition}",
            "context_budget": 1000,
            "expected_tool_calls": [expected],
            "metadata": {
                "base_case_id": f"base_{family}", "task_family": family,
                "stress_condition": condition, "critical_flexible_memory_ids": [cid],
                "dataset_version": "critical_flexible_v1", "seed": 42,
            },
        },
        "strategy": strategy,
        "repeat_id": 0,
        "run_result": {
            "status": "ok", "stopped_reason": "max_steps", "goal_completed": False,
            "steps": [{
                "selection_result": {"selected_memory_ids": [cid],
                                     "retrieved_memory_ids": [cid]},
                "context_result": {"selected_memory_ids": [cid]},
                "action_result": {"tool_calls": [actual], "tool_results": [result]},
            }],
            "tool_calls": [actual], "tool_results": [result],
            "prompt_token_audit": {"complete": True, "actual_budget_exceeded": False},
        },
    }


class FirstActionEvaluatorTests(unittest.TestCase):
    def test_correct_file_and_email_with_extra_args(self):
        for family in ("file_lookup", "approved_email"):
            row = evaluate_first_action(sample(family=family))
            self.assertTrue(row["first_action_correct"])
            self.assertTrue(row["first_action_call_correct"])
            self.assertTrue(row["first_action_effect_verified"])
            self.assertTrue(row["critical_in_context"])
            self.assertEqual(row["failure_reason"], "ok")
            self.assertFalse(row["goal_completed"])

    def test_file_not_found_even_when_tool_success(self):
        data = sample()
        data["run_result"]["steps"][0]["action_result"]["tool_results"][0]["output"]["found"] = False
        row = evaluate_first_action(data)
        self.assertTrue(row["first_action_call_correct"])
        self.assertFalse(row["first_action_correct"])
        self.assertEqual(row["failure_reason"], "file_not_found_or_wrong_file")

    def test_wrong_filename_has_wrong_argument_reason(self):
        data = sample()
        data["run_result"]["steps"][0]["action_result"]["tool_calls"][0]["arguments"]["filename"] = "made_up.pdf"
        row = evaluate_first_action(data)
        self.assertEqual(row["wrong_arguments"], ["filename"])
        self.assertEqual(row["failure_reason"], "wrong_required_argument")

    def test_approval_request_is_not_authorized_send(self):
        data = sample(family="approved_email")
        action = data["run_result"]["steps"][0]["action_result"]
        action["tool_calls"][0]["action"] = "request_approval"
        action["tool_results"][0] = {
            "tool_name": "email", "action": "request_approval", "success": True,
            "output": {"approval_id": "approval_001", "authorized_to_send": False}}
        row = evaluate_first_action(data)
        self.assertEqual(row["failure_reason"], "wrong_action")
        self.assertFalse(row["first_action_correct"])

    def test_missing_approval_id(self):
        data = sample(family="approved_email")
        action = data["run_result"]["steps"][0]["action_result"]
        del action["tool_calls"][0]["arguments"]["approval_id"]
        row = evaluate_first_action(data)
        self.assertEqual(row["missing_arguments"], ["approval_id"])
        self.assertFalse(row["first_action_correct"])

    def test_missing_tool_result_is_not_verified(self):
        data = sample()
        data["run_result"]["steps"][0]["action_result"]["tool_results"] = []
        row = evaluate_first_action(data)
        self.assertTrue(row["first_action_call_correct"])
        self.assertFalse(row["first_action_correct"])
        self.assertEqual(row["failure_reason"], "tool_result_missing")

    def test_missing_first_tool_and_later_recovery(self):
        data = sample()
        step = data["run_result"]["steps"][0]
        good = copy.deepcopy(step)
        step["action_result"] = {"tool_calls": [], "tool_results": []}
        data["run_result"]["steps"].append(good)
        row = evaluate_first_action(data)
        self.assertEqual(row["failure_reason"], "no_first_tool_call")
        self.assertFalse(row["first_action_correct"])

    def test_critical_presence_uses_first_step_context(self):
        data = sample()
        data["run_result"]["steps"][0]["context_result"]["selected_memory_ids"] = []
        row = evaluate_first_action(data)
        self.assertFalse(row["critical_in_context"])
        # 모델이 정답을 추측하는 경우를 구분해서 기록한다.
        self.assertTrue(row["first_action_correct"])

    def test_pairing_and_summary(self):
        rows = [evaluate_first_action(sample(strategy=x)) for x in (FULL, NO_PACKING)]
        pairs = make_pairs(rows)
        self.assertEqual(len(pairs), 1)
        summary = summarize(rows, pairs)
        self.assertEqual(summary["overall"]["n_pairs"], 1)
        self.assertEqual(summary["overall"]["paired"]["both_correct"], 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            make_pairs(rows + [rows[0]])
        with self.assertRaisesRegex(ValueError, "unmatched"):
            make_pairs(rows[:1])

    def test_export_and_reject_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d) / "runs.jsonl"
            output = Path(d) / "report"
            rows = [sample(strategy=x) for x in (FULL, NO_PACKING)]
            source.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
            result = evaluate_file(source, output, expected_runs=2)
            self.assertEqual(result["overall"]["n_pairs"], 1)
            for name in (
                "first_action_details.jsonl", "paired_first_action.csv",
                "failure_details.csv", "first_action_summary.json",
                "validation.json",
            ):
                self.assertTrue((output / name).is_file(), name)
            with self.assertRaises(FileExistsError):
                evaluate_file(source, output)


if __name__ == "__main__":
    unittest.main()
