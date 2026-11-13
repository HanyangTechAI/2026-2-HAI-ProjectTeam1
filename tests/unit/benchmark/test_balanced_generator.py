"""Balanced scenarios must exercise real policies and preserve paired inputs.

Oracle traces below validate generated ground truth through the real evaluator;
they are not measurements of agent or LLM performance.
"""

from collections import Counter
import copy
from datetime import datetime
import re
import unittest

from benchmark.evaluator import evaluate
from benchmark.generator import generate_dataset, generate_scenario, render_history
from context.tokenizer import provisional_token_count
from memory.query_analyzer import analyze_query
from memory.schema import MemoryItem
from memory.selector import retrieve_protected
from memory.store import SnapshotMemoryStore


PDF_FILENAME = re.compile(r"[A-Za-z0-9_.-]+\.pdf")
SUBTYPES = (
    "temporal_update/current", "temporal_update/historical",
    "approval/pending", "approval/granted",
)
VARIANTS = (
    {"task_type": "temporal_update", "state_scope": "current"},
    {"task_type": "temporal_update", "state_scope": "historical"},
    {"task_type": "approval", "approval_status": "pending"},
    {"task_type": "approval", "approval_status": "granted"},
)


def config(samples=20, seeds=(42, 43), tasks=("temporal_update", "approval")):
    return {
        "name": "balanced_generator_test",
        "dataset": {
            "source": "synthetic", "scenario_profile": "balanced_v2",
            "samples_per_cell": samples, "task_types": list(tasks),
        },
        "seeds": list(seeds),
        "grid": {
            "context_token_budget": [2048], "history_length_tokens": [10000],
            "horizon_turns": [10], "noise_memory_ratio": [0.5],
            "constraint_count": [3], "state_update_count": [2],
        },
    }


def subtype(scenario):
    metadata = scenario["metadata"]
    variant = metadata.get("approval_status") or metadata["state_scope"]
    return f"{metadata['task_type']}/{variant}"


def oracle(scenario):
    return {
        "strategy": "proposed", "status": "ok", "goal_completed": True,
        "tool_calls": copy.deepcopy(scenario["expected_tool_calls"]),
        "retrieved_memory_ids": list(scenario["target_memory_ids"]),
        "selected_memory_ids": list(scenario["target_memory_ids"]),
        "usage": {"input_tokens": 0},
    }


class BalancedGeneratorTests(unittest.TestCase):
    def scenario(self, **overrides):
        parameters = {
            "scenario_profile": "balanced_v2", "history_length_tokens": 10000,
            "horizon_turns": 10, "constraint_count": 3,
        }
        parameters.update(overrides)
        return generate_scenario(**parameters)

    def test_main_allocation_covers_four_subtypes_evenly(self):
        scenarios = generate_dataset(config())["cells"][0]["scenarios"]
        self.assertEqual(len(scenarios), 40)
        self.assertEqual(Counter(map(subtype, scenarios)), Counter(dict.fromkeys(SUBTYPES, 10)))
        self.assertEqual(len({item["sample_id"] for item in scenarios}), 40)

    def test_awkward_sample_sizes_do_not_repeat_the_same_remainder_bias(self):
        for samples, seeds in ((1, (42, 43)), (3, (42, 43)),
                               (5, (42, 43, 44)), (7, (42, 43))):
            scenarios = generate_dataset(config(samples, seeds))["cells"][0]["scenarios"]
            counts = Counter(map(subtype, scenarios))
            values = [counts[key] for key in SUBTYPES]
            with self.subTest(samples=samples, seeds=seeds):
                self.assertEqual(len(scenarios), samples * len(seeds))
                self.assertLessEqual(max(values) - min(values), 1)
                for seed in seeds:
                    self.assertEqual(sum(item["metadata"]["seed"] == seed for item in scenarios), samples)

    def test_task_subset_is_respected_and_its_variants_remain_balanced(self):
        for task in ("temporal_update", "approval"):
            scenarios = generate_dataset(config(3, tasks=(task,)))["cells"][0]["scenarios"]
            with self.subTest(task=task):
                self.assertEqual({item["metadata"]["task_type"] for item in scenarios}, {task})
                counts = Counter(map(subtype, scenarios))
                relevant = [counts[key] for key in SUBTYPES if key.startswith(task + "/")]
                self.assertLessEqual(max(relevant) - min(relevant), 1)

    def test_candidate_answers_cover_every_label_without_subtype_shortcuts(self):
        for count in (1, 3, 5):
            options = config(24, (42, 43))
            options["grid"]["constraint_count"] = [count]
            scenarios = generate_dataset(options)["cells"][0]["scenarios"]
            for kind in SUBTYPES:
                group = [item for item in scenarios if subtype(item) == kind]
                candidate_count = group[0]["metadata"]["candidate_count"]
                answers = Counter(item["metadata"]["allowed_candidate"] for item in group)
                with self.subTest(constraint_count=count, subtype=kind):
                    self.assertEqual(set(answers), set(range(1, candidate_count + 1)))
                    self.assertLessEqual(max(answers.values()) - min(answers.values()), 2)

    def test_all_constraints_are_applicable_in_ground_truth_and_actual_selector(self):
        for variant in VARIANTS:
            for count in (1, 3, 5):
                scenario = self.scenario(constraint_count=count, **variant)
                memories = [MemoryItem.from_dict(item) for item in scenario["memory_snapshot"]]
                constraints = {item.memory_id for item in memories if item.memory_type.value == "constraint"}
                declared = set(scenario["applicable_constraint_ids"])
                query_context = analyze_query(
                    scenario["query"], scenario["current_turn"],
                    datetime.fromisoformat(scenario["current_time"]),
                )
                selected = retrieve_protected(query_context, SnapshotMemoryStore(memories), scenario["session_id"])
                with self.subTest(variant=variant, count=count):
                    self.assertEqual(len(constraints), count)
                    self.assertEqual(declared, constraints)
                    self.assertEqual({item["constraint_id"] for item in scenario["constraint_rules"]}, declared)
                    self.assertEqual({item.memory_id for item in selected}, declared)
                    self.assertEqual(scenario["metadata"]["behavior_changing_constraint_count"], count)
                    self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_each_filename_prohibition_targets_a_visible_selectable_candidate(self):
        for variant in VARIANTS:
            scenario = self.scenario(**variant)
            by_id = {item["memory_id"]: item for item in scenario["memory_snapshot"]}
            target_text = " ".join(by_id[mid]["content"] for mid in scenario["required_state_ids"])
            all_state_filenames = {
                filename for item in scenario["memory_snapshot"] if item["memory_type"] == "state"
                for filename in PDF_FILENAME.findall(item["content"])
            }
            expected = scenario["expected_tool_calls"][0]
            for rule in scenario["constraint_rules"]:
                patterns = rule.get("forbidden_tool_calls", [])
                if not patterns:
                    continue
                filenames = {
                    value for pattern in patterns for value in pattern.get("arguments", {}).values()
                    if isinstance(value, str) and value.endswith(".pdf")
                }
                with self.subTest(variant=variant, rule=rule["constraint_id"]):
                    self.assertTrue(filenames)
                    self.assertLessEqual(filenames, all_state_filenames)
                    self.assertLessEqual(filenames, set(scenario["environment"]["files"]))
                    matching = [
                        pattern for pattern in patterns
                        if pattern["tool_name"] == expected["tool_name"]
                        and pattern["action"] == expected["action"]
                        and all(str(value) in target_text for value in pattern.get("arguments", {}).values())
                    ]
                    self.assertTrue(matching, "Rule must affect a candidate in the requested temporal version")
                    run = oracle(scenario)
                    run["tool_calls"][0]["arguments"].update(matching[0]["arguments"])
                    result = evaluate(scenario, run)
                    self.assertFalse(result.task_success)
                    self.assertIn(rule["constraint_id"], result.metrics["violated_constraint_ids"])

    def test_approval_rule_still_rejects_an_unapproved_send_with_the_right_file(self):
        for status in ("pending", "granted"):
            scenario = self.scenario(task_type="approval", approval_status=status)
            run = oracle(scenario)
            bad_send = copy.deepcopy(run["tool_calls"][0])
            bad_send["action"] = "send_email"
            bad_send["arguments"].pop("approval_id", None)
            run["tool_calls"].append(bad_send)
            result = evaluate(scenario, run)
            with self.subTest(status=status):
                self.assertFalse(result.task_success)
                self.assertTrue(result.metrics["constraint_violation"])

    def test_granted_approval_is_available_only_through_the_policy_memory(self):
        scenario = self.scenario(task_type="approval", approval_status="granted")
        constraint_memories = [item for item in scenario["memory_snapshot"] if item["memory_type"] == "constraint"]
        other_memories = [item for item in scenario["memory_snapshot"] if item["memory_type"] != "constraint"]
        constraint_turns = {item["turn_id"] for item in constraint_memories}
        grants = scenario["environment"]["valid_approval_ids"]
        self.assertTrue(grants)
        for approval_id in grants:
            with self.subTest(approval_id=approval_id):
                self.assertTrue(any(approval_id in item["content"] for item in constraint_memories))
                self.assertFalse(any(approval_id in item["content"] for item in other_memories))
                self.assertTrue(all(entry["turn_id"] in constraint_turns for entry in scenario["history"]
                                    if approval_id in entry["content"]))
        self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_balanced_profile_is_default_and_has_disjoint_legacy_identity(self):
        balanced = generate_scenario(constraint_count=3)
        explicit = generate_scenario(constraint_count=3, scenario_profile="balanced_v2")
        legacy = generate_scenario(constraint_count=3, scenario_profile="legacy_v1")
        self.assertEqual(balanced, explicit)
        self.assertEqual(balanced["metadata"]["scenario_profile"], "balanced_v2")
        self.assertNotEqual(balanced["sample_id"], legacy["sample_id"])
        self.assertNotEqual(balanced["session_id"], legacy["session_id"])
        self.assertTrue(set(balanced["target_memory_ids"]).isdisjoint(legacy["target_memory_ids"]))

    def test_dataset_honors_explicit_legacy_profile_without_mixing_identities(self):
        balanced = generate_dataset(config(4, (42,)))["cells"][0]["scenarios"]
        legacy_config = config(4, (42,))
        legacy_config["dataset"]["scenario_profile"] = "legacy_v1"
        legacy = generate_dataset(legacy_config)["cells"][0]["scenarios"]
        self.assertEqual({item["metadata"]["scenario_profile"] for item in balanced}, {"balanced_v2"})
        self.assertEqual({item["metadata"]["scenario_profile"] for item in legacy}, {"legacy_v1"})
        self.assertTrue({item["sample_id"] for item in balanced}.isdisjoint(item["sample_id"] for item in legacy))

    def test_balanced_history_horizon_and_noise_are_measured_from_actual_content(self):
        for variant in VARIANTS:
            scenario = self.scenario(**variant)
            with self.subTest(variant=variant):
                self.assertEqual([entry["turn_id"] for entry in scenario["history"]],
                                 list(range(1, scenario["current_turn"])))
                measured = provisional_token_count(render_history(scenario["history"]))
                self.assertEqual(scenario["metadata"]["actual_history_length_tokens"], measured)
                self.assertLessEqual(abs(measured - 10000), 100)
                targets = set(scenario["target_memory_ids"])
                essential = [item for item in scenario["memory_snapshot"] if item["memory_id"] in targets]
                self.assertEqual(max(item["turn_id"] for item in essential), scenario["current_turn"] - 10)
                relevant_tokens = sum(item["token_count"] for item in essential)
                noise_tokens = sum(item["token_count"] for item in scenario["memory_snapshot"]
                                   if "_noise_" in item["memory_id"])
                actual_noise_ratio = noise_tokens / (relevant_tokens + noise_tokens)
                self.assertLessEqual(abs(actual_noise_ratio - 0.5), 0.01)
                self.assertAlmostEqual(scenario["metadata"]["actual_noise_memory_ratio"], actual_noise_ratio)

    def test_budget_horizon_and_filler_changes_preserve_paired_policy_and_answer(self):
        for variant in VARIANTS:
            original = self.scenario(**variant)
            alternatives = (
                self.scenario(context_budget=8192, **variant),
                self.scenario(horizon_turns=20, **variant),
                self.scenario(horizon_generation_mode=2, **variant),
            )
            for other in alternatives:
                with self.subTest(variant=variant, horizon=other["metadata"]["horizon_turns"],
                                  mode=other["metadata"]["horizon_generation_mode"]):
                    for field in ("sample_id", "scenario_id", "session_id", "query", "required_states",
                                  "target_memory_ids", "constraint_rules", "expected_tool_calls", "environment"):
                        self.assertEqual(original[field], other[field], field)

    def test_zero_constraints_or_updates_remain_feasible(self):
        for scope in ("current", "historical"):
            scenario = self.scenario(constraint_count=0, state_update_count=0, state_scope=scope)
            with self.subTest(scope=scope):
                self.assertEqual(scenario["applicable_constraint_ids"], [])
                self.assertEqual(scenario["constraint_rules"], [])
                self.assertEqual(scenario["wrong_version_memory_ids"], [])
                self.assertFalse(scenario["temporal_version_required"])
                self.assertEqual(scenario["metadata"]["candidate_count"], 1)
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)
        for variant in VARIANTS:
            scenario = self.scenario(state_update_count=0, **variant)
            with self.subTest(variant=variant):
                self.assertEqual(scenario["wrong_version_memory_ids"], [])
                self.assertFalse(scenario["temporal_version_required"])
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)


if __name__ == "__main__":
    unittest.main()
