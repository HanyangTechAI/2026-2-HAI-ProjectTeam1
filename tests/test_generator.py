"""Synthetic-data contracts; oracle checks do not measure agent performance."""

import copy
from datetime import datetime
import json
from pathlib import Path
import re
import tempfile
import unittest

from benchmark.evaluator import evaluate
from benchmark.generator import (
    generate_dataset, generate_scenario, render_history, write_dataset,
)
from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem


def small_config(**grid_overrides):
    grid = {
        "context_token_budget": [2048],
        "history_length_tokens": [10000],
        "horizon_turns": [10],
        "noise_memory_ratio": [0.5],
        "constraint_count": [1],
        "state_update_count": [2],
    }
    grid.update(grid_overrides)
    return {
        "name": "test",
        "dataset": {
            "source": "synthetic", "samples_per_cell": 4,
            "task_types": ["temporal_update", "approval"],
        },
        "seeds": [42], "repeats": 2,
        "strategies": ["sliding_window", "vector_top_k"],
        "grid": grid,
    }


def oracle(scenario):
    """Supply only an expected execution, leaving evaluation to the real evaluator."""
    selected = scenario["target_memory_ids"]
    memory_tokens = sum(item["token_count"] for item in scenario["memory_snapshot"]
                        if item["memory_id"] in selected)
    fixed_tokens = scenario.get("fixed_input_tokens", 0)
    return {
        "strategy": "proposed", "status": "ok", "goal_completed": True,
        "tool_calls": copy.deepcopy(scenario["expected_tool_calls"]),
        "retrieved_memory_ids": list(selected),
        "selected_memory_ids": list(selected),
        "usage": {"input_tokens": fixed_tokens + memory_tokens,
                  "fixed_input_tokens": fixed_tokens},
    }


class GeneratorTests(unittest.TestCase):
    def scenario(self, **overrides):
        parameters = {
            "history_length_tokens": 10000, "horizon_turns": 10,
            "context_budget": 2048,
        }
        parameters.update(overrides)
        return generate_scenario(**parameters)

    def variants(self):
        return [
            self.scenario(task_type="temporal_update", state_scope="current"),
            self.scenario(task_type="temporal_update", state_scope="historical"),
            self.scenario(task_type="approval", approval_status="pending"),
            self.scenario(task_type="approval", approval_status="granted"),
        ]

    def test_default_scenario_parameters_are_feasible(self):
        scenario = generate_scenario()
        self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_reproducible_and_seeded_without_mutating_config(self):
        config = small_config()
        original = copy.deepcopy(config)
        first = generate_dataset(config)
        self.assertEqual(first, generate_dataset(config))
        self.assertEqual(config, original)
        self.assertNotEqual(self.scenario(seed=42)["scenario_id"],
                            self.scenario(seed=43)["scenario_id"])
        json.dumps(first, allow_nan=False)

    def test_main_allocation_is_total_per_seed_and_cell(self):
        config = small_config()
        config["dataset"]["samples_per_cell"] = 20
        config["seeds"] = [42, 43]
        payload = generate_dataset(config)
        self.assertEqual(len(payload["cells"]), 1)
        scenarios = payload["cells"][0]["scenarios"]
        self.assertEqual(len(scenarios), 40)
        for seed in config["seeds"]:
            per_seed = [s for s in scenarios if s["metadata"]["seed"] == seed]
            temporal = [s for s in per_seed if s["metadata"]["task_type"] == "temporal_update"]
            approval = [s for s in per_seed if s["metadata"]["task_type"] == "approval"]
            self.assertEqual(len(temporal), 10)
            self.assertEqual(len(approval), 10)
            self.assertEqual(sum(s["required_states"][0]["state_scope"] == "current"
                                 for s in temporal), 5)
            self.assertEqual(sum(s["required_states"][0]["state_scope"] == "historical"
                                 for s in temporal), 5)
        self.assertEqual(len(scenarios), len({s["scenario_id"] for s in scenarios}))

    def test_budget_changes_reuse_identical_data_and_pairing_ids(self):
        first = self.scenario(context_budget=1024)
        second = self.scenario(context_budget=8192)
        for key in ("sample_id", "scenario_id", "session_id", "query", "history",
                    "memory_snapshot", "target_memory_ids", "required_states",
                    "constraint_rules", "expected_tool_calls"):
            with self.subTest(field=key):
                self.assertEqual(first[key], second[key])
        self.assertEqual(first["context_budget"], 1024)
        self.assertEqual(second["context_budget"], 8192)

    def test_horizon_changes_position_but_preserves_identity_and_answers(self):
        for scope in ("current", "historical"):
            near = self.scenario(state_scope=scope, horizon_turns=10)
            far = self.scenario(state_scope=scope, horizon_turns=20)
            with self.subTest(scope=scope):
                self.assertEqual(near["sample_id"], far["sample_id"])
                self.assertEqual(near["scenario_id"], far["scenario_id"])
                self.assertEqual(near["expected_tool_calls"], far["expected_tool_calls"])
                self.assertEqual(near["required_states"], far["required_states"])
                self.assertEqual(
                    [(m["memory_id"], m["content"]) for m in near["memory_snapshot"]],
                    [(m["memory_id"], m["content"]) for m in far["memory_snapshot"]],
                )
                self.assertNotEqual(
                    [m["turn_id"] for m in near["memory_snapshot"]],
                    [m["turn_id"] for m in far["memory_snapshot"]],
                )
                self.assertEqual(near["metadata"]["actual_history_length_tokens"],
                                 far["metadata"]["actual_history_length_tokens"])

    def test_history_is_contiguous_and_length_is_measured(self):
        for scenario in self.variants():
            with self.subTest(scenario=scenario["scenario_id"]):
                turns = [entry["turn_id"] for entry in scenario["history"]]
                self.assertEqual(turns, list(range(1, scenario["current_turn"])))
                measured = provisional_token_count(render_history(scenario["history"]))
                self.assertLessEqual(abs(measured - 10000), 100)
                self.assertEqual(scenario["metadata"]["history_length_tokens"], 10000)
                self.assertEqual(scenario["metadata"]["actual_history_length_tokens"], measured)
                self.assertEqual(scenario["metadata"]["horizon_turns"], 10)
                self.assertEqual(scenario["metadata"]["important_turn"],
                                 scenario["current_turn"] - 10)
                essential_turns = [m["turn_id"] for m in scenario["memory_snapshot"]
                                   if m["memory_id"] in scenario["target_memory_ids"]]
                self.assertEqual(max(essential_turns), scenario["current_turn"] - 10)
                self.assertEqual(scenario["token_count_mode"], "provisional")

    def test_noise_is_token_weighted_and_includes_wrong_versions(self):
        for ratio in (0.1, 0.5, 0.9):
            scenario = self.scenario(noise_memory_ratio=ratio)
            necessary = set(scenario["target_memory_ids"])
            total = sum(m["token_count"] for m in scenario["memory_snapshot"])
            unnecessary = sum(m["token_count"] for m in scenario["memory_snapshot"]
                              if m["memory_id"] not in necessary)
            with self.subTest(ratio=ratio):
                self.assertTrue(set(scenario["wrong_version_memory_ids"]) - necessary)
                self.assertLessEqual(abs(unnecessary / total - ratio), 0.01)
                self.assertEqual(scenario["metadata"]["noise_memory_ratio"], ratio)
                self.assertAlmostEqual(scenario["metadata"]["actual_noise_memory_ratio"],
                                       unnecessary / total)

    def test_noise_axis_keeps_essential_memory_content_and_cost_fixed(self):
        scenarios = [self.scenario(noise_memory_ratio=ratio) for ratio in (0.1, 0.5, 0.9)]

        def essential(scenario):
            return [(m["memory_id"], m["content"], m["token_count"])
                    for m in scenario["memory_snapshot"]
                    if m["memory_id"] in scenario["target_memory_ids"]]

        self.assertEqual(essential(scenarios[0]), essential(scenarios[1]))
        self.assertEqual(essential(scenarios[1]), essential(scenarios[2]))
        self.assertLess(scenarios[0]["metadata"]["memory_tokens"],
                        scenarios[1]["metadata"]["memory_tokens"])
        self.assertLess(scenarios[1]["metadata"]["memory_tokens"],
                        scenarios[2]["metadata"]["memory_tokens"])

    def test_injected_counter_measures_both_history_and_memories(self):
        def tripled_words(text):
            return 3 * len(text.split())

        scenario = self.scenario(token_counter=tripled_words)
        actual = tripled_words(render_history(scenario["history"]))
        self.assertLessEqual(abs(actual - 10000), 100)
        self.assertEqual(scenario["metadata"]["history_length_tokens"], 10000)
        self.assertEqual(scenario["metadata"]["actual_history_length_tokens"], actual)
        self.assertNotEqual(actual, scenario["metadata"]["history_length_tokens"])
        self.assertEqual(scenario["token_count_mode"], "custom")
        for memory in scenario["memory_snapshot"]:
            self.assertEqual(memory["token_count"], tripled_words(memory["content"]))
        result = evaluate(scenario, oracle(scenario))
        self.assertEqual(result.metrics["history_length_tokens"], 10000)
        self.assertEqual(result.metrics["noise_memory_ratio"], 0.5)

    def test_schema_references_and_version_chain_are_consistent(self):
        for scenario in self.variants():
            memories = {m["memory_id"]: MemoryItem.from_dict(m)
                        for m in scenario["memory_snapshot"]}
            with self.subTest(scenario=scenario["scenario_id"]):
                self.assertEqual(len(memories), len(scenario["memory_snapshot"]))
                for field in ("target_memory_ids", "required_state_ids",
                              "applicable_constraint_ids", "wrong_version_memory_ids"):
                    self.assertLessEqual(set(scenario[field]), memories.keys())
                self.assertEqual(set(scenario["required_state_ids"]),
                                 {s["memory_id"] for s in scenario["required_states"]})
                self.assertEqual(set(scenario["applicable_constraint_ids"]),
                                 {r["constraint_id"] for r in scenario["constraint_rules"]})
                for memory in memories.values():
                    self.assertEqual(memory.session_id, scenario["session_id"])
                    self.assertLess(memory.turn_id, scenario["current_turn"])
                    if memory.supersedes:
                        self.assertEqual(memories[memory.supersedes].superseded_by,
                                         memory.memory_id)
                    if memory.superseded_by:
                        self.assertEqual(memories[memory.superseded_by].supersedes,
                                         memory.memory_id)

    def test_requested_time_selects_correct_current_or_historical_version(self):
        for scope in ("current", "historical"):
            scenario = self.scenario(state_scope=scope)
            target_time = datetime.fromisoformat(scenario["query_context"]["target_time"])
            memory_by_id = {m["memory_id"]: MemoryItem.from_dict(m)
                            for m in scenario["memory_snapshot"]}
            for state in scenario["required_states"]:
                memory = memory_by_id[state["memory_id"]]
                with self.subTest(scope=scope, memory=memory.memory_id):
                    self.assertEqual(state["state_scope"], scope)
                    self.assertLessEqual(memory.valid_from, target_time)
                    if memory.valid_to is not None:
                        self.assertLess(target_time, memory.valid_to)
                    self.assertEqual(memory.is_active, scope == "current")

    def test_state_validity_times_are_visible_without_evaluator_annotations(self):
        scenario = self.scenario(state_scope="historical")
        by_turn = {entry["turn_id"]: entry for entry in scenario["history"]}
        for memory in scenario["memory_snapshot"]:
            if memory["memory_type"] != "state":
                continue
            with self.subTest(memory=memory["memory_id"]):
                self.assertIn(memory["valid_from"], memory["content"])
                if memory["valid_to"] is not None:
                    self.assertIn(memory["valid_to"], memory["content"])
                self.assertIn(memory["content"], by_turn[memory["turn_id"]]["content"])

    def test_zero_updates_has_no_competing_version(self):
        for scope in ("current", "historical"):
            scenario = self.scenario(state_scope=scope, state_update_count=0)
            with self.subTest(scope=scope):
                self.assertFalse(scenario["temporal_version_required"])
                self.assertEqual(scenario["wrong_version_memory_ids"], [])
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_expected_actions_pass_real_evaluator(self):
        for scenario in self.variants():
            with self.subTest(scenario=scenario["scenario_id"]):
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_wrong_state_fails_and_historical_latest_is_not_stale_usage(self):
        for scope in ("current", "historical"):
            scenario = self.scenario(state_scope=scope)
            run = oracle(scenario)
            state = scenario["required_states"][0]
            if scope == "historical":
                latest = next(m for m in scenario["memory_snapshot"]
                              if m["memory_type"] == "state" and m["is_active"]
                              and m["memory_id"] in scenario["wrong_version_memory_ids"])
                wrong_value = re.search(r"[\w.-]+\.pdf\b", latest["content"]).group(0)
            else:
                wrong_value = state["stale_values"][0]
            for call in run["tool_calls"]:
                call["arguments"][state["argument"]] = wrong_value
            result = evaluate(scenario, run)
            with self.subTest(scope=scope):
                self.assertFalse(result.task_success)
                if scope == "historical":
                    self.assertEqual(result.metrics["state_error_count"], 1)
                    self.assertFalse(result.metrics["stale_state_used"])
                    self.assertIsNone(result.metrics["stale_state_usage_rate"])
                else:
                    self.assertTrue(result.metrics["stale_state_used"])

    def test_approval_is_pending_until_preexisting_grant(self):
        pending = self.scenario(task_type="approval", approval_status="pending")
        granted = self.scenario(task_type="approval", approval_status="granted")
        self.assertTrue(any(c["action"] == "request_approval"
                            for c in pending["expected_tool_calls"]))
        self.assertFalse(any(c["action"] == "send_email"
                             for c in pending["expected_tool_calls"]))
        self.assertTrue(any(c["action"] == "send_email"
                            for c in granted["expected_tool_calls"]))
        self.assertNotEqual(pending["scenario_id"], granted["scenario_id"])
        for scenario in (pending, granted):
            rules = [r["approval"] for r in scenario["constraint_rules"] if "approval" in r]
            self.assertTrue(rules)
            for rule in rules:
                self.assertEqual(rule["request_required"], scenario is pending)
                if scenario is pending:
                    self.assertEqual(rule["valid_approval_ids"], [])
                else:
                    self.assertTrue(rule["valid_approval_ids"])
                    self.assertLessEqual(set(rule["valid_approval_ids"]),
                                         set(scenario["environment"]["valid_approval_ids"]))
            run = oracle(scenario)
            run["tool_calls"].append({
                "tool_name": "email", "action": "send_email",
                "arguments": {"approval_id": "pending_unauthorized"},
            })
            self.assertFalse(evaluate(scenario, run).task_success)

    def test_constraint_and_update_axes_are_real_not_just_labels(self):
        for count in (1, 3, 5):
            temporal = self.scenario(
                task_type="temporal_update",
                constraint_count=count,
                state_update_count=count,
            )

            temporal_constraints = [
                memory
                for memory
                in temporal["memory_snapshot"]
                if memory["memory_type"] == "constraint"
            ]

            with self.subTest(
                task="temporal",
                count=count,
            ):
                # Constraint axis 자체는 실제 snapshot에 반영된다.
                self.assertEqual(
                    len(temporal_constraints),
                    count,
                )

                # 하지만 현재 report lookup에 적용되지는 않는다.
                self.assertEqual(
                    temporal[
                        "applicable_constraint_ids"
                    ],
                    [],
                )
                self.assertEqual(
                    temporal["constraint_rules"],
                    [],
                )

                self.assertEqual(
                    len(
                        temporal[
                            "wrong_version_memory_ids"
                        ]
                    ),
                    count,
                )

            approval = self.scenario(
                task_type="approval",
                approval_status="pending",
                constraint_count=count,
                state_update_count=count,
            )

            approval_constraints = [
                memory
                for memory
                in approval["memory_snapshot"]
                if memory["memory_type"] == "constraint"
            ]

            with self.subTest(
                task="approval",
                count=count,
            ):
                self.assertEqual(
                    len(approval_constraints),
                    count,
                )

                # 현재 email task에는 approval constraint 하나만 적용.
                self.assertEqual(
                    len(
                        approval[
                            "applicable_constraint_ids"
                        ]
                    ),
                    1,
                )

                self.assertEqual(
                    len(
                        approval[
                            "constraint_rules"
                        ]
                    ),
                    1,
                )

    def test_invalid_parameters_and_impossible_lengths_fail_explicitly(self):
        bad_parameters = [
            {"context_budget": 0}, {"context_budget": True},
            {"history_length_tokens": -1}, {"history_length_tokens": True},
            {"horizon_turns": 0}, {"horizon_turns": True},
            {"sample_index": -1}, {"sample_index": True},
            {"state_update_count": -1}, {"constraint_count": -1},
            {"noise_memory_ratio": -0.1}, {"noise_memory_ratio": 1.1},
            {"noise_memory_ratio": float("nan")}, {"noise_memory_ratio": True},
            {"task_type": "unknown"}, {"state_scope": "unknown"},
            {"approval_status": "unknown"},
            {"history_length_tokens": 5, "horizon_turns": 100},
        ]
        for parameters in bad_parameters:
            with self.subTest(parameters=parameters):
                with self.assertRaises((TypeError, ValueError)):
                    self.scenario(**parameters)

    def test_invalid_token_counter_result_is_rejected(self):
        for result in (True, -1, 1.5):
            with self.subTest(result=result):
                with self.assertRaises((TypeError, ValueError)):
                    self.scenario(token_counter=lambda text: result)

    def test_uninformative_counter_is_rejected_before_unbounded_padding(self):
        measured_lengths = []

        def constant_counter(text):
            measured_lengths.append(len(text))
            return 1

        with self.assertRaises(ValueError):
            self.scenario(token_counter=constant_counter)
        self.assertLess(len(measured_lengths), 40)
        self.assertLess(max(measured_lengths), 1_000_000)

    def test_conflicting_axis_conditions_fail_instead_of_changing_ground_truth(self):
        for parameters in (
            {"task_type": "approval", "constraint_count": 0},
            {"state_scope": "historical", "state_update_count": 5, "horizon_turns": 5},
            {"state_update_count": 2, "noise_memory_ratio": 0},
        ):
            with self.subTest(parameters=parameters):
                with self.assertRaises(ValueError):
                    self.scenario(**parameters)
        clean = self.scenario(
            task_type="temporal_update",
            state_update_count=0,
            noise_memory_ratio=0,
        )

        self.assertEqual(
            clean["metadata"]["noise_memory_ratio"],
            0,
        )

        # Temporal lookup에서는 required state만 ground-truth target이다.
        self.assertEqual(
            set(clean["target_memory_ids"]),
            set(clean["required_state_ids"]),
        )

        # 현재 query와 무관한 constraint는 snapshot에는 존재하지만
        # applicable ground truth에는 포함되지 않는다.
        self.assertEqual(
            clean["applicable_constraint_ids"],
            [],
        )

        self.assertEqual(
            clean["constraint_rules"],
            [],
        )

        constraint_ids = {
            memory["memory_id"]
            for memory in clean["memory_snapshot"]
            if memory["memory_type"] == "constraint"
        }

        self.assertTrue(constraint_ids)

        self.assertTrue(
            constraint_ids.isdisjoint(
                clean["target_memory_ids"]
            )
        )

    def test_duplicate_config_axes_and_seeds_are_rejected(self):
        configs = [small_config(context_token_budget=[2048, 2048]),
                   small_config(horizon_turns=[10, 10])]
        repeated_seeds = small_config()
        repeated_seeds["seeds"] = [42, 42]
        configs.append(repeated_seeds)
        for config in configs:
            with self.subTest(config=config):
                with self.assertRaises(ValueError):
                    generate_dataset(config)

    def test_every_budget_is_validated_even_when_source_data_is_cached(self):
        for invalid_budget in (0, -1, True, 2048.5):
            with self.subTest(budget=invalid_budget):
                with self.assertRaises((TypeError, ValueError)):
                    generate_dataset(small_config(context_token_budget=[2048, invalid_budget]))

    def test_writer_rejects_duplicate_or_escaping_cell_paths_before_writing(self):
        for invalid_id in ("../escape", "nested/cell", "cell.json"):
            payload = generate_dataset(small_config())
            payload["cells"][0]["cell_id"] = invalid_id
            with tempfile.TemporaryDirectory() as directory:
                with self.subTest(cell_id=invalid_id):
                    with self.assertRaises(ValueError):
                        write_dataset(payload, directory)
                    self.assertEqual(list(Path(directory).iterdir()), [])
        payload = generate_dataset(small_config(context_token_budget=[1024, 2048]))
        payload["cells"][1]["cell_id"] = payload["cells"][0]["cell_id"]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                write_dataset(payload, directory)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_output_keeps_shared_ids_in_separate_cell_files(self):
        config = small_config(context_token_budget=[1024, 2048])
        payload = generate_dataset(config)
        self.assertEqual(len(payload["cells"]), 2)
        first_ids = {s["scenario_id"] for s in payload["cells"][0]["scenarios"]}
        second_ids = {s["scenario_id"] for s in payload["cells"][1]["scenarios"]}
        self.assertEqual(first_ids, second_ids)
        with tempfile.TemporaryDirectory() as directory:
            manifest = write_dataset(payload, directory)
            manifest_path = Path(directory) / "manifest.json"
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)
            data_files = [path for path in Path(directory).rglob("*.json")
                          if path != manifest_path]
            self.assertEqual(len(data_files), 2)
            for path in data_files:
                scenarios = json.loads(path.read_text(encoding="utf-8"))["scenarios"]
                self.assertEqual(len(scenarios), 4)
                self.assertEqual(len(scenarios), len({s["scenario_id"] for s in scenarios}))
                
                
    def test_ground_truth_contains_only_task_applicable_constraints(self):
        temporal = self.scenario(
            task_type="temporal_update",
            state_scope="current",
            constraint_count=3,
        )

        self.assertEqual(
            temporal["applicable_constraint_ids"],
            [],
        )
        self.assertEqual(
            temporal["constraint_rules"],
            [],
        )
        self.assertEqual(
            set(temporal["target_memory_ids"]),
            set(temporal["required_state_ids"]),
        )

        pending = self.scenario(
            task_type="approval",
            approval_status="pending",
            constraint_count=3,
        )

        self.assertEqual(
            len(pending["applicable_constraint_ids"]),
            1,
        )
        self.assertEqual(
            len(pending["constraint_rules"]),
            1,
        )

        approval_constraint_id = (
            pending["applicable_constraint_ids"][0]
        )

        self.assertIn(
            approval_constraint_id,
            pending["target_memory_ids"],
        )

        granted = self.scenario(
            task_type="approval",
            approval_status="granted",
            constraint_count=3,
        )

        self.assertEqual(
            len(granted["applicable_constraint_ids"]),
            1,
        )
        self.assertEqual(
            len(granted["constraint_rules"]),
            1,
        )

        grant_ids = [
            memory_id
            for memory_id in granted["target_memory_ids"]
            if memory_id.endswith("_grant")
        ]

        self.assertEqual(
            len(grant_ids),
            1,
        )


if __name__ == "__main__":
    unittest.main()
