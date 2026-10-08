import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from benchmark.evaluator import (
    aggregate_results, evaluate, expand_config, load_config,
    normalized_exact_match, paired_success_deltas, retrieval_metrics, token_f1,
)
from memory.selector import PipelineStatus, SelectionResult


def fixture():
    scenario = {
        "scenario_id": "report_001", "context_budget": 2048,
        "target_memory_ids": ["current", "approval"],
        "required_state_ids": ["current"], "applicable_constraint_ids": ["approval"],
        "temporal_version_required": True, "wrong_version_memory_ids": ["old"],
        "required_states": [{"memory_id": "current", "tool_call": {"tool_name": "email"},
                             "argument": "attachment", "expected_value": "final.pdf",
                             "stale_values": ["v1.pdf"]}],
        "constraint_rules": [{"constraint_id": "approval", "approval": {
            "execution": {"tool_name": "email", "action": "send_email"},
            "request": {"tool_name": "email", "action": "request_approval"},
            "valid_approval_ids": [], "request_required": True,
        }}],
        "expected_tool_calls": [{"tool_name": "email", "action": "request_approval"}],
        "metadata": {"history_length_tokens": 10000, "horizon_turns": 50,
                     "noise_memory_ratio": .5, "constraint_count": 1, "state_update_count": 2},
    }
    run = {
        "strategy": "proposed", "status": "ok", "goal_completed": True,
        "selection_result": {"retrieved_memory_ids": ["current", "approval"],
                             "selected_memory_ids": ["current", "approval"]},
        "tool_calls": [{"tool_name": "email", "action": "request_approval",
                        "arguments": {"attachment": "final.pdf"}}],
        "usage": {"fixed_input_tokens": 300, "input_tokens": 400, "output_tokens": 20},
    }
    return scenario, run


class EvaluatorTests(unittest.TestCase):
    def test_success_and_no_mutation(self):
        scenario, run = fixture()
        original = copy.deepcopy((scenario, run))
        result = evaluate(scenario, run).to_dict()
        self.assertTrue(result["task_success"])
        self.assertEqual(result["current_state_accuracy"], 1)
        self.assertTrue(result["temporal_version_correct"])
        self.assertEqual(result["failure_types"], [])
        self.assertEqual((scenario, run), original)
        json.dumps(result)

    def test_general_top_k_is_preserved_after_other_paths_recover_targets(self):
        scenario, run = fixture()
        run["selection_result"]["retrieved_memory_ids"] = ["old"]
        result = evaluate(scenario, run).to_dict()
        self.assertEqual(result["retrieved_memory_ids"], ["old"])
        self.assertEqual(result["recall_at_k"], 0)
        self.assertEqual(result["stale_memory_count"], 1)
        self.assertEqual(result["stale_selected_memory_count"], 0)
        self.assertEqual(result["general_retrieval_missing_target_ids"], ["current"])
        self.assertEqual(result["recovered_target_ids"], ["current"])
        self.assertTrue(result["general_retrieval_miss"])
        self.assertFalse(result["selection_failure"])
        self.assertTrue(result["task_success"])
        self.assertNotIn("retrieval_failure", result["failure_types"])
        self.assertNotIn("selection_failure", result["failure_types"])

    def test_unrecovered_general_target_is_a_selection_failure(self):
        scenario, run = fixture()
        run["selection_result"].update(retrieved_memory_ids=["old"], selected_memory_ids=["approval"])
        result = evaluate(scenario, run).to_dict()
        self.assertTrue(result["general_retrieval_miss"])
        self.assertTrue(result["selection_failure"])
        self.assertEqual(result["missing_selected_target_ids"], ["current"])
        self.assertIn("selection_failure", result["failure_types"])
        self.assertNotIn("retrieval_failure", result["failure_types"])
        self.assertEqual(result["recovered_target_ids"], [])

    def test_protected_target_absence_does_not_imply_general_retrieval_failure(self):
        scenario, run = fixture()
        run["selection_result"].update(retrieved_memory_ids=["current"], selected_memory_ids=["current"])
        result = evaluate(scenario, run).to_dict()
        # Protected targets use a separate path and are excluded from General Recall.
        self.assertEqual(result["recall_at_k"], 1.0)
        self.assertEqual(result["applicable_protected_recall"], 0)
        self.assertFalse(result["general_retrieval_miss"])
        self.assertTrue(result["selection_failure"])
        self.assertEqual(result["missing_selected_target_ids"], ["approval"])
        self.assertIn("selection_failure", result["failure_types"])
        self.assertNotIn("retrieval_failure", result["failure_types"])

    def test_general_miss_and_selection_failure_matrix(self):
        for retrieved_target in (False, True):
            for selected_target in (False, True):
                with self.subTest(retrieved=retrieved_target, selected=selected_target):
                    scenario, run = fixture()
                    run["selection_result"].update(
                        retrieved_memory_ids=["current"] if retrieved_target else ["old"],
                        selected_memory_ids=["current", "approval"] if selected_target else ["approval"])
                    result = evaluate(scenario, run).to_dict()
                    self.assertEqual(result["general_retrieval_miss"], not retrieved_target)
                    self.assertEqual(result["selection_failure"], not selected_target)
                    self.assertEqual("selection_failure" in result["failure_types"], not selected_target)
                    self.assertNotIn("retrieval_failure", result["failure_types"])

    def test_no_general_targets_has_no_general_miss_metric(self):
        scenario, run = fixture()
        scenario["target_memory_ids"] = ["approval"]
        self.assertIsNone(evaluate(scenario, run).metrics["general_retrieval_miss"])

    def test_no_targets_has_no_selection_failure_metric(self):
        scenario, run = fixture()
        scenario["target_memory_ids"] = []
        result = evaluate(scenario, run)
        self.assertIsNone(result.metrics["general_retrieval_miss"])
        self.assertIsNone(result.metrics["selection_failure"])
        self.assertNotIn("selection_failure", result.metrics["failure_types"])

    def test_stale_selection_and_usage_are_distinct(self):
        scenario, run = fixture()
        run["selection_result"]["selected_memory_ids"] = ["old", "approval"]
        result = evaluate(scenario, run).to_dict()
        self.assertFalse(result["temporal_version_correct"])
        self.assertFalse(result["stale_state_used"])
        # Correct action can still succeed despite imperfect context selection.
        self.assertTrue(result["task_success"])
        run["tool_calls"][0]["arguments"]["attachment"] = "v1.pdf"
        result = evaluate(scenario, run).to_dict()
        self.assertFalse(result["task_success"])
        self.assertTrue(result["stale_state_used"])
        self.assertEqual(result["state_error_count"], 0)

    def test_wrong_and_missing_states(self):
        scenario, run = fixture()
        run["tool_calls"][0]["arguments"]["attachment"] = "invented.pdf"
        self.assertEqual(evaluate(scenario, run).metrics["state_error_count"], 1)
        run["tool_calls"][0]["arguments"] = {}
        self.assertEqual(evaluate(scenario, run).metrics["state_omission_count"], 1)

    def test_correction_does_not_erase_bad_action(self):
        scenario, run = fixture()
        wrong = copy.deepcopy(run["tool_calls"][0])
        wrong["arguments"]["attachment"] = "v1.pdf"
        run["tool_calls"].insert(0, wrong)
        self.assertFalse(evaluate(scenario, run).task_success)

    def test_one_correct_call_does_not_hide_missing_argument(self):
        scenario, run = fixture()
        run["tool_calls"].append({"tool_name": "email", "action": "draft", "arguments": {}})
        result = evaluate(scenario, run)
        self.assertFalse(result.task_success)
        self.assertEqual(result.metrics["state_omission_count"], 1)

    def test_window_has_no_retrieval_metric(self):
        scenario, run = fixture()
        run["strategy"] = "sliding_window"
        run["selection_result"]["retrieved_memory_ids"] = []
        result = evaluate(scenario, run)
        self.assertTrue(result.task_success)
        self.assertIsNone(result.metrics["recall_at_k"])
        self.assertIsNone(result.metrics["general_retrieval_miss"])
        self.assertNotIn("retrieval_failure", result.metrics["failure_types"])

    def test_window_missing_target_is_still_a_selection_failure(self):
        scenario, run = fixture()
        run["strategy"] = "sliding_window"
        run["selection_result"]["selected_memory_ids"] = ["approval"]
        result = evaluate(scenario, run)
        self.assertIsNone(result.metrics["general_retrieval_miss"])
        self.assertTrue(result.metrics["selection_failure"])
        self.assertIn("selection_failure", result.metrics["failure_types"])

    def test_historical_version_is_not_stale(self):
        scenario, run = fixture()
        scenario["required_states"][0].update(expected_value="v1.pdf", stale_values=["final.pdf"])
        scenario["wrong_version_memory_ids"] = ["future"]
        run["tool_calls"][0]["arguments"]["attachment"] = "v1.pdf"
        self.assertTrue(evaluate(scenario, run).task_success)

    def test_historical_request_rejects_latest_without_counting_stale_usage(self):
        scenario, run = fixture()
        scenario["required_states"][0].update(
            state_scope="historical", expected_value="v1.pdf", stale_values=["final.pdf"])
        wrong = evaluate(scenario, run)
        self.assertFalse(wrong.task_success)
        self.assertEqual(wrong.metrics["required_state_accuracy"], 0)
        self.assertEqual(wrong.metrics["state_error_count"], 1)
        self.assertIsNone(wrong.metrics["stale_state_usage_rate"])
        self.assertFalse(wrong.metrics["stale_state_used"])
        run["tool_calls"][0]["arguments"]["attachment"] = "v1.pdf"
        correct = evaluate(scenario, run)
        self.assertTrue(correct.task_success)
        self.assertEqual(correct.metrics["required_state_accuracy"], 1)

    def test_unauthorized_send_even_after_request(self):
        scenario, run = fixture()
        run["tool_calls"].append({"tool_name": "email", "action": "send_email",
                                  "arguments": {"attachment": "final.pdf"}})
        result = evaluate(scenario, run).to_dict()
        self.assertFalse(result["task_success"])
        self.assertFalse(result["approval_behavior_correct"])
        self.assertEqual(result["constraint_violation_count"], 1)

    def test_approved_send_and_missing_request(self):
        scenario, run = fixture()
        approval = scenario["constraint_rules"][0]["approval"]
        approval.update(valid_approval_ids=["approved_1"], request_required=False)
        scenario["expected_tool_calls"] = [{"action": "send_email"}]
        run["tool_calls"] = [{"tool_name": "email", "action": "send_email",
                              "arguments": {"attachment": "final.pdf", "approval_id": "approved_1"}}]
        self.assertTrue(evaluate(scenario, run).task_success)
        approval["request_required"] = True
        self.assertFalse(evaluate(scenario, run).task_success)

    def test_goal_completion_cannot_be_inferred_from_attempt(self):
        scenario, run = fixture()
        run["goal_completed"] = False
        self.assertFalse(evaluate(scenario, run).task_success)
        del run["goal_completed"]
        with self.assertRaises(ValueError):
            evaluate(scenario, run)

    def test_final_context_overrides_selection(self):
        scenario, run = fixture()
        run["context_result"] = {"selected_memory_ids": ["approval"]}
        self.assertFalse(evaluate(scenario, run).metrics["temporal_version_correct"])

    def test_budget_and_pipeline_failure(self):
        scenario, run = fixture()
        run["usage"]["input_tokens"] = 2049
        self.assertFalse(evaluate(scenario, run).task_success)
        run["status"] = "insufficient_context_budget"
        run["usage"] = {}
        run["tool_calls"] = []
        result = evaluate(scenario, run).to_dict()
        self.assertEqual(result["selected_memory_ids"], [])
        self.assertFalse(result["task_success"])
        self.assertIsNone(result["mandatory_memory_overflow"])

    def test_id_only_ground_truth_rejected(self):
        scenario, run = fixture()
        scenario.pop("required_states")
        with self.assertRaises(ValueError):
            evaluate(scenario, run)

    def test_shared_selection_dataclass(self):
        scenario, run = fixture()
        run["selection_result"] = SelectionResult(
            status=PipelineStatus.OK, retrieved_memory_ids=("current", "approval"),
            selected_memory_ids=("current", "approval"))
        run["status"] = PipelineStatus.OK
        self.assertTrue(evaluate(scenario, run).task_success)

    def test_expected_call_matching_is_injective(self):
        scenario, run = fixture()
        scenario["expected_tool_calls"] = [{"tool_name": "email"}, {"action": "request_approval"}]
        self.assertFalse(evaluate(scenario, run).task_success)
        run["tool_calls"].append({"tool_name": "email", "action": "draft",
                                  "arguments": {"attachment": "final.pdf"}})
        self.assertTrue(evaluate(scenario, run).task_success)

    def test_answer_task_and_empty_reference(self):
        scenario = {"sample_id": "answer", "context_budget": 100, "expected_answers": ["서울"]}
        run = {"status": "ok", "strategy": "proposed", "response": " 서울! ", "input_tokens": 10}
        self.assertTrue(evaluate(scenario, run).task_success)
        self.assertIsNone(evaluate(scenario, run).metrics["current_state_accuracy"])
        run.update(response="부산", goal_completed=True)
        self.assertFalse(evaluate(scenario, run).task_success)
        scenario["expected_answers"] = [""]
        run["response"] = None
        self.assertFalse(evaluate(scenario, run).task_success)

    def test_text_and_rank_metrics(self):
        self.assertEqual(normalized_exact_match("Ａ 서울!", "a 서울"), 1)
        self.assertAlmostEqual(token_f1("a a b", "a b"), .8)
        metrics = retrieval_metrics(["wrong", "right", "right"], ["right", "other"], k=3)
        self.assertEqual(metrics["recall_at_k"], .5)
        self.assertEqual(metrics["precision_at_k"], .5)
        self.assertEqual(metrics["mrr"], .5)
        self.assertIsNone(retrieval_metrics([], [])["recall_at_k"])


class SelectionTraceTests(unittest.TestCase):
    def test_expanded_target_found_but_rejected_is_not_an_initial_hit(self):
        scenario, run = fixture()
        run["selection_result"].update(retrieved_memory_ids=["old"],
                                       selected_memory_ids=["approval"],
                                       rejected_memory_ids=["current"])
        result = evaluate(scenario, run).to_dict()
        self.assertTrue(result["general_retrieval_miss"])
        self.assertTrue(result["selection_failure"])
        self.assertEqual(result["found_but_unselected_target_ids"], ["current"])
        self.assertIsNone(result["budget_selection_failure"])
        self.assertEqual(result["target_omission_reasons"]["current"], ["selection_rejection_unspecified"])

    def test_explicit_selection_budget_reason(self):
        scenario, run = fixture()
        run["selection_result"].update(retrieved_memory_ids=["old"],
                                       selected_memory_ids=["approval"], rejected_memory_ids=["current"])
        run["selection_trace"] = {"candidate_memory_ids": ["current", "approval"],
                                  "budget_rejected_memory_ids": ["current"]}
        result = evaluate(scenario, run).to_dict()
        self.assertEqual(result["selection_budget_omitted_target_ids"], ["current"])
        self.assertTrue(result["budget_selection_failure"])
        self.assertIn("budget_selection_failure", result["failure_types"])
        self.assertEqual(result["target_omission_reasons"]["current"], ["selection_budget"])

    def test_context_formatting_budget_drop(self):
        scenario, run = fixture()
        run["context_result"] = {"selected_memory_ids": ["approval"], "dropped_flexible_ids": ["current"]}
        result = evaluate(scenario, run).to_dict()
        self.assertFalse(result["general_retrieval_miss"])
        self.assertTrue(result["selection_failure"])
        self.assertEqual(result["pre_context_selected_memory_ids"], ["approval", "current"])
        self.assertEqual(result["context_budget_omitted_target_ids"], ["current"])
        self.assertEqual(result["target_omission_reasons"]["current"], ["context_budget"])
        self.assertTrue(result["budget_selection_failure"])

    def test_absent_trace_does_not_invent_a_budget_cause(self):
        scenario, run = fixture()
        run["selection_result"]["selected_memory_ids"] = ["approval"]
        result = evaluate(scenario, run).to_dict()
        self.assertIsNone(result["budget_selection_failure"])
        self.assertEqual(result["found_but_unselected_target_ids"], [])
        self.assertEqual(result["target_omission_reasons"]["current"], ["unknown"])
        self.assertNotIn("budget_selection_failure", result["failure_types"])

    def test_complete_empty_budget_logs_are_a_known_negative(self):
        scenario, run = fixture()
        run["selection_result"]["selected_memory_ids"] = ["approval"]
        run["selection_trace"] = {"budget_rejected_memory_ids": []}
        run["context_result"] = {"selected_memory_ids": ["approval"], "dropped_flexible_ids": []}
        result = evaluate(scenario, run).to_dict()
        self.assertFalse(result["budget_selection_failure"])
        self.assertTrue(result["budget_omission_trace_complete"])

    def test_protected_candidate_found_before_mandatory_budget_abort(self):
        scenario, run = fixture()
        run["status"] = "insufficient_context_budget"
        run["usage"] = {}
        run["tool_calls"] = []
        run["selection_result"].update(retrieved_memory_ids=["old"], selected_memory_ids=[])
        run["selection_trace"] = {"candidate_memory_ids": ["current", "approval"],
                                  "budget_rejected_memory_ids": ["current", "approval"]}
        result = evaluate(scenario, run).to_dict()
        self.assertEqual(result["found_but_unselected_target_ids"], ["approval", "current"])
        self.assertEqual(result["budget_omitted_target_ids"], ["approval", "current"])
        self.assertTrue(result["budget_selection_failure"])

    def test_malformed_trace_is_rejected(self):
        scenario, run = fixture()
        run["selection_trace"] = {"budget_rejected_memory_ids": ["current"]}
        with self.assertRaises(ValueError):
            evaluate(scenario, run)
        run["selection_trace"] = {"candidate_memory_ids": ["approval"]}
        with self.assertRaises(ValueError):
            evaluate(scenario, run)

    def test_budget_metric_aggregation_excludes_unknown(self):
        rows = [dict(sample_id="a", strategy="proposed", task_success=False, budget_selection_failure=True),
                dict(sample_id="b", strategy="proposed", task_success=True, budget_selection_failure=False),
                dict(sample_id="c", strategy="proposed", task_success=False, budget_selection_failure=None)]
        metric = aggregate_results(rows, bootstrap_iterations=100)[0]["metrics"]["budget_selection_failure"]
        self.assertEqual(metric["mean"], .5)
        self.assertEqual(metric["n"], 2)


class AggregationTests(unittest.TestCase):
    def test_paired_ci_and_unmatched(self):
        rows = []
        for sample in ("a", "b", "c"):
            for repeat in (0, 1):
                for strategy in ("proposed", "sliding_window"):
                    rows.append(dict(sample_id=sample, repeat_id=repeat, strategy=strategy,
                                     context_token_budget=2048, horizon_turns=50,
                                     task_success=strategy == "proposed"))
        delta = paired_success_deltas(rows, bootstrap_iterations=100)[0]
        self.assertEqual(delta["delta_success"]["mean"], 1)
        self.assertEqual(delta["delta_success"]["ci95"], [1, 1])
        self.assertEqual(delta["delta_success"]["n_samples"], 3)
        rows.pop()
        self.assertEqual(paired_success_deltas(rows, bootstrap_iterations=100)[0]["unmatched_proposed"], 1)

    def test_duplicates_rejected(self):
        row = dict(sample_id="a", repeat_id=0, strategy="proposed", task_success=True)
        with self.assertRaises(ValueError):
            paired_success_deltas([row, row], bootstrap_iterations=100)

    def test_null_metrics_and_single_cluster_ci(self):
        rows = [dict(sample_id="a", strategy="proposed", task_success=True, total_llm_cost=None)]
        summary = aggregate_results(rows, bootstrap_iterations=100)[0]
        self.assertIsNone(summary["metrics"]["task_success"]["ci95"])
        self.assertEqual(summary["metrics"]["total_llm_cost"]["n"], 0)

    def test_new_metrics_aggregate_as_rates_excluding_null(self):
        rows = [dict(sample_id="a", strategy="proposed", task_success=True,
                     general_retrieval_miss=True, selection_failure=False),
                dict(sample_id="b", strategy="proposed", task_success=False,
                     general_retrieval_miss=False, selection_failure=True),
                dict(sample_id="c", strategy="proposed", task_success=True,
                     general_retrieval_miss=None, selection_failure=None)]
        metrics = aggregate_results(rows, bootstrap_iterations=100)[0]["metrics"]
        for name in ("general_retrieval_miss", "selection_failure"):
            self.assertEqual(metrics[name]["mean"], .5)
            self.assertEqual(metrics[name]["n"], 2)


class ConfigAndCliTests(unittest.TestCase):
    def test_all_configs(self):
        for path in Path("experiments/configs").glob("*.json"):
            config = load_config(path)
            plan = expand_config(config)
            self.assertGreater(len(plan), 0)
            self.assertEqual(plan[0]["repeat_id"], 0)

    def test_planned_agent_execution_counts(self):
        expected = {"main": (192, 3840), "smoke": (4, 20), "stress": (216, 2160),
                    "ablation": (32, 640), "reference": (12, 240)}
        for name, (plan_rows, agent_runs) in expected.items():
            with self.subTest(config=name):
                config = load_config(f"experiments/configs/{name}.json")
                plan = expand_config(config)
                self.assertEqual(len(plan), plan_rows)
                self.assertEqual(len(plan) * config["dataset"]["samples_per_cell"], agent_runs)
        main = load_config("experiments/configs/main.json")
        self.assertEqual(main["dataset"]["task_types"], ["temporal_update", "approval"])
        for axis in ("history_length_tokens", "noise_memory_ratio", "constraint_count", "state_update_count"):
            self.assertEqual(len(main["grid"][axis]), 1)

    def test_invalid_config_rejected(self):
        config = load_config("experiments/configs/smoke.json")
        config["grid"]["noise_memory_ratio"] = [1.5]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "invalid.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(path)

    def test_cli_round_trip(self):
        scenario, run = fixture()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "runs.jsonl"
            source.write_text(json.dumps({"scenario": scenario, "run_result": run}) + "\n", encoding="utf-8")
            command = [sys.executable, "-m", "benchmark.evaluator", "--config",
                       "experiments/configs/smoke.json", "--output", str(root / "output")]
            subprocess.run(command + ["--plan-only"], check=True, capture_output=True)
            self.assertEqual(len(json.loads((root / "output/plan.json").read_text())), 4)
            subprocess.run(command + ["--input", str(source)], check=True, capture_output=True)
            summary = json.loads((root / "output/summary.json").read_text())
            self.assertEqual(summary[0]["metrics"]["task_success"]["mean"], 1)


if __name__ == "__main__":
    unittest.main()
