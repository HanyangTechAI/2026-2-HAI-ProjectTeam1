"""Four information topics get independent quotas and real memory roles.

Oracle executions validate ground truth only; these tests do not measure LLM
success or require equal performance gaps between ablations.
"""

from collections import Counter
import copy
from datetime import datetime
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import json
import zipfile

from benchmark.evaluator import evaluate
from benchmark.generator import generate_dataset, generate_scenario, render_history, write_dataset_archive
from context.tokenizer import GeminiTokenCounter, provisional_token_count
from memory.schema import MemoryItem
from memory.ablation import get_ablation_config
from memory.query_analyzer import analyze_query
from memory.selector import select_memory, retrieve_protected
from memory.store import SnapshotMemoryStore
from context.builder import build_context, fixed_context_tokens


TOPICS = ("constraint", "temporal", "state", "flexible")
TASKS = ("temporal_update", "approval")


def config(samples=20, seeds=(42, 43), tasks=TASKS, topics=None):
    dataset = {
        "source": "synthetic", "samples_per_cell": samples,
        "task_types": list(tasks),
    }
    if topics is not None:
        dataset["topics"] = list(topics)
    return {
        "name": "topic_generator_test", "dataset": dataset,
        "seeds": list(seeds),
        "grid": {
            "context_token_budget": [2048], "history_length_tokens": [10000],
            "horizon_turns": [10], "noise_memory_ratio": [0.5],
            "constraint_count": [3], "state_update_count": [2],
        },
    }


def oracle(scenario):
    return {
        "strategy": "proposed", "status": "ok", "goal_completed": True,
        "tool_calls": copy.deepcopy(scenario["expected_tool_calls"]),
        "retrieved_memory_ids": list(scenario["target_memory_ids"]),
        "selected_memory_ids": list(scenario["target_memory_ids"]),
        "usage": {"input_tokens": 0},
    }


class TopicGeneratorTests(unittest.TestCase):
    def scenario(self, topic, **overrides):
        options = {
            "scenario_profile": "topic_balanced_v3", "primary_topic": topic,
            "history_length_tokens": 10000, "horizon_turns": 10,
            "constraint_count": 3, "state_update_count": 2,
        }
        options.update(overrides)
        return generate_scenario(**options)

    def test_new_default_balances_information_topics_instead_of_only_task_types(self):
        payload = generate_dataset(config())
        scenarios = payload["cells"][0]["scenarios"]
        self.assertEqual(payload["scenario_profile"], "topic_balanced_v3")
        self.assertEqual(len(scenarios), 40)
        self.assertEqual(Counter(item["metadata"]["primary_topic"] for item in scenarios),
                         Counter(dict.fromkeys(TOPICS, 10)))
        self.assertEqual(len({item["sample_id"] for item in scenarios}), 40)
        self.assertEqual({item["metadata"]["scenario_profile"] for item in scenarios},
                         {"topic_balanced_v3"})
        for item in scenarios:
            self.assertIs(type(item["metadata"]["topic_index"]), int)
            self.assertGreaterEqual(item["metadata"]["topic_index"], 0)

    def test_remainders_stay_balanced_across_seeds_and_small_per_seed_counts(self):
        for samples, seeds in ((1, tuple(range(20))), (1, (42, 43)),
                               (3, (42, 43)), (5, (42, 43, 44)), (7, (42,))):
            scenarios = generate_dataset(config(samples, seeds))["cells"][0]["scenarios"]
            counts = Counter(item["metadata"]["primary_topic"] for item in scenarios)
            values = [counts[key] for key in TOPICS]
            with self.subTest(samples=samples, seeds=seeds):
                self.assertEqual(len(scenarios), samples * len(seeds))
                self.assertLessEqual(max(values) - min(values), 1)
                for seed in seeds:
                    self.assertEqual(sum(item["metadata"]["seed"] == seed for item in scenarios), samples)

    def test_task_families_are_balanced_within_each_information_topic(self):
        for samples, seeds in ((20, (42, 43)), (1, tuple(range(20))), (7, (42, 43))):
            scenarios = generate_dataset(config(samples, seeds))["cells"][0]["scenarios"]
            for topic in TOPICS:
                group = [item for item in scenarios if item["metadata"]["primary_topic"] == topic]
                counts = Counter(item["metadata"]["task_type"] for item in group)
                with self.subTest(topic=topic, samples=samples):
                    self.assertLessEqual(abs(counts[TASKS[0]] - counts[TASKS[1]]), 1)
                    if len(group) >= 2:
                        self.assertEqual(set(counts), set(TASKS))

    def test_requested_topic_and_task_subsets_are_respected(self):
        for topics in (("constraint", "flexible"), ("state",)):
            for task in TASKS:
                scenarios = generate_dataset(config(7, tasks=(task,), topics=topics))["cells"][0]["scenarios"]
                counts = Counter(item["metadata"]["primary_topic"] for item in scenarios)
                with self.subTest(topics=topics, task=task):
                    self.assertEqual(set(counts), set(topics))
                    self.assertEqual({item["metadata"]["task_type"] for item in scenarios}, {task})
                    self.assertLessEqual(max(counts.values()) - min(counts.values()), 1)

    def test_each_topic_exercises_its_actual_memory_role(self):
        for topic in TOPICS:
            for task in TASKS:
                scenario = self.scenario(topic, task_type=task)
                memories = {item["memory_id"]: item for item in scenario["memory_snapshot"]}
                with self.subTest(topic=topic, task=task):
                    self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)
                    if topic == "constraint":
                        self.assertEqual(len(scenario["applicable_constraint_ids"]), 3)
                        self.assertEqual(scenario["required_state_ids"], [])
                        self.assertFalse(scenario["temporal_version_required"])
                        for mid in scenario["applicable_constraint_ids"]:
                            self.assertEqual(memories[mid]["memory_type"], "constraint")
                            self.assertTrue(memories[mid]["is_protected"])
                    else:
                        self.assertEqual(scenario["applicable_constraint_ids"], [])
                        self.assertEqual(scenario["constraint_rules"], [])
                    if topic in ("state", "temporal"):
                        self.assertEqual(len(scenario["required_state_ids"]), 1)
                        self.assertEqual(memories[scenario["required_state_ids"][0]]["memory_type"], "state")
                        self.assertEqual(scenario["temporal_version_required"], topic == "temporal")
                    if topic == "flexible":
                        self.assertEqual(scenario["required_state_ids"], [])
                        self.assertFalse(scenario["temporal_version_required"])

    def test_flexible_answers_are_facts_outside_the_mandatory_state_and_policy_sets(self):
        for task in TASKS:
            scenario = self.scenario("flexible", task_type=task)
            required = scenario["required_flexible_memory_ids"]
            memories = {item["memory_id"]: item for item in scenario["memory_snapshot"]}
            with self.subTest(task=task):
                self.assertTrue(required)
                self.assertEqual(required, scenario["metadata"]["required_flexible_memory_ids"])
                self.assertLessEqual(set(required), set(scenario["target_memory_ids"]))
                self.assertTrue(set(required).isdisjoint(scenario["required_state_ids"]))
                self.assertTrue(set(required).isdisjoint(scenario["applicable_constraint_ids"]))
                for mid in required:
                    self.assertEqual(memories[mid]["memory_type"], "fact")
                    self.assertFalse(memories[mid]["is_protected"])
                call = scenario["expected_tool_calls"][0]
                filename = call["arguments"].get("filename", call["arguments"].get("attachment"))
                self.assertTrue(filename)
                self.assertNotIn(filename, scenario["query"])
                self.assertTrue(any(filename in memories[mid]["content"] for mid in required))
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_constraint_topic_rules_reject_real_forbidden_choices(self):
        for task in TASKS:
            scenario = self.scenario("constraint", task_type=task)
            correct = scenario["expected_tool_calls"][0]
            for rule in scenario["constraint_rules"]:
                run = oracle(scenario)
                if "approval" in rule:
                    bad = copy.deepcopy(correct)
                    bad["action"] = "send_email"
                    bad["arguments"].pop("approval_id", None)
                    run["tool_calls"].append(bad)
                else:
                    forbidden = next(pattern for pattern in rule["forbidden_tool_calls"]
                                     if pattern["tool_name"] == correct["tool_name"]
                                     and pattern["action"] == correct["action"])
                    run["tool_calls"][0]["arguments"].update(forbidden["arguments"])
                result = evaluate(scenario, run)
                with self.subTest(task=task, rule=rule["constraint_id"]):
                    self.assertFalse(result.task_success)
                    self.assertIn(rule["constraint_id"], result.metrics["violated_constraint_ids"])

    def test_temporal_queries_require_the_version_valid_at_the_requested_time(self):
        for scope in ("current", "historical"):
            scenario = self.scenario("temporal", state_scope=scope)
            memories = {item["memory_id"]: MemoryItem.from_dict(item) for item in scenario["memory_snapshot"]}
            requested = datetime.fromisoformat(scenario["query_context"]["target_time"])
            with self.subTest(scope=scope):
                self.assertEqual(len(scenario["wrong_version_memory_ids"]), 2)
                for mid in scenario["required_state_ids"]:
                    self.assertLessEqual(memories[mid].valid_from, requested)
                    if memories[mid].valid_to is not None:
                        self.assertLess(requested, memories[mid].valid_to)
                    self.assertEqual(memories[mid].is_active, scope == "current")

    def test_state_topic_keeps_current_state_without_a_competing_version_chain(self):
        for task in TASKS:
            scenario = self.scenario("state", task_type=task)
            state_ids = set(scenario["required_state_ids"])
            with self.subTest(task=task):
                self.assertEqual(scenario["wrong_version_memory_ids"], [])
                self.assertEqual(len(state_ids), 1)
                target = next(item for item in scenario["memory_snapshot"] if item["memory_id"] in state_ids)
                self.assertTrue(target["is_active"])
                self.assertIsNone(target["supersedes"])
                self.assertIsNone(target["superseded_by"])

    def test_all_default_dataset_oracles_pass_the_real_evaluator(self):
        scenarios = generate_dataset(config())["cells"][0]["scenarios"]
        for scenario in scenarios:
            with self.subTest(sample=scenario["sample_id"]):
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_repeated_generation_and_grid_changes_preserve_paired_task_identity(self):
        options = config(4)
        options["grid"]["context_token_budget"] = [2048, 4096]
        options["grid"]["horizon_turns"] = [10, 50]
        before = copy.deepcopy(options)
        payload = generate_dataset(options)
        self.assertEqual(options, before)
        self.assertEqual(payload, generate_dataset(options))
        reference = {item["sample_id"]: item for item in payload["cells"][0]["scenarios"]}
        for cell in payload["cells"][1:]:
            scenarios = {item["sample_id"]: item for item in cell["scenarios"]}
            self.assertEqual(set(reference), set(scenarios))
            for sid, expected in reference.items():
                for key in ("query", "required_state_ids", "constraint_rules", "required_states",
                            "expected_tool_calls", "target_memory_ids", "environment"):
                    with self.subTest(sample=sid, field=key):
                        self.assertEqual(expected[key], scenarios[sid][key])
                for field in ("primary_topic", "topic_index"):
                    self.assertEqual(expected["metadata"][field], scenarios[sid]["metadata"][field])

    def test_topic_identity_is_disjoint_from_previous_profiles(self):
        current = self.scenario("constraint")
        self.assertEqual(generate_scenario()["metadata"]["scenario_profile"], "topic_balanced_v3")
        for old in ("legacy_v1", "balanced_v2"):
            previous = generate_scenario(scenario_profile=old)
            self.assertNotEqual(current["sample_id"], previous["sample_id"])
            self.assertTrue(set(current["target_memory_ids"]).isdisjoint(previous["target_memory_ids"]))

    def test_topic_history_and_noise_are_measured_from_rendered_content(self):
        for topic in TOPICS:
            scenario = self.scenario(topic)
            metadata = scenario["metadata"]
            with self.subTest(topic=topic):
                self.assertEqual([entry["turn_id"] for entry in scenario["history"]],
                                 list(range(1, scenario["current_turn"])))
                measured = provisional_token_count(render_history(scenario["history"]))
                self.assertEqual(metadata["actual_history_length_tokens"], measured)
                self.assertLessEqual(abs(measured - 10000), 100)
                targets = set(scenario["target_memory_ids"])
                relevant_tokens = sum(item["token_count"] for item in scenario["memory_snapshot"]
                                      if item["memory_id"] in targets)
                noise_tokens = sum(item["token_count"] for item in scenario["memory_snapshot"]
                                   if "_noise_" in item["memory_id"])
                actual = noise_tokens / (relevant_tokens + noise_tokens)
                self.assertAlmostEqual(actual, metadata["actual_noise_memory_ratio"])
                self.assertLessEqual(abs(actual - 0.5), 0.01)

    def test_zero_constraint_and_update_axes_are_explicit_controls(self):
        for topic in TOPICS:
            scenario = self.scenario(topic, constraint_count=0, state_update_count=0)
            with self.subTest(topic=topic):
                self.assertEqual(scenario["applicable_constraint_ids"], [])
                self.assertFalse(scenario["temporal_version_required"])
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_invalid_topic_configuration_is_rejected(self):
        for topics in ([], ["state", "state"], ["unknown"], "state"):
            options = config()
            options["dataset"]["topics"] = topics
            with self.subTest(topics=topics), self.assertRaises((ValueError, TypeError)):
                generate_dataset(options)
        for topic in ("unknown", True, 1, ["state"]):
            with self.subTest(topic=topic), self.assertRaises((ValueError, TypeError)):
                self.scenario(topic)

    def test_compact_topics_support_the_low_noise_stress_axis(self):
        for topic in ("constraint", "temporal"):
            for count in (1, 3, 5):
                scenario = self.scenario(topic, constraint_count=count, noise_memory_ratio=0.1)
                with self.subTest(topic=topic, count=count):
                    self.assertLessEqual(abs(scenario["metadata"]["actual_noise_memory_ratio"] - 0.1), 0.01)
                    self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)

    def test_policy_padding_preserves_actual_applicability(self):
        for mode in (1, 2, 3):
            for task in TASKS:
                scenario = self.scenario("constraint", task_type=task, horizon_generation_mode=mode)
                query = analyze_query(scenario["query"], scenario["current_turn"],
                                      datetime.fromisoformat(scenario["current_time"]))
                store = SnapshotMemoryStore(tuple(MemoryItem.from_dict(item) for item in scenario["memory_snapshot"]))
                protected = retrieve_protected(query, store, scenario["session_id"])
                with self.subTest(mode=mode, task=task):
                    self.assertEqual({item.memory_id for item in protected}, set(scenario["applicable_constraint_ids"]))

    def test_state_and_flexible_topics_have_real_reference_budget_competition(self):
        root = Path(__file__).resolve().parents[3]
        prompt = (root / "experiments/prompts/system_v1.txt").read_text(encoding="utf-8")
        tools = (root / "experiments/prompts/tools_v1.txt").read_text(encoding="utf-8")
        for topic, preset in (("state", "no_state_mandatory"), ("flexible", "no_budget_aware_packing")):
            scenario = self.scenario(topic)
            fixed = fixed_context_tokens(prompt, tools, scenario["query"], provisional_token_count)
            results = {}
            for name in ("full", preset):
                store = SnapshotMemoryStore(tuple(MemoryItem.from_dict(item) for item in scenario["memory_snapshot"]))
                selection = select_memory(
                    scenario["query"], scenario["session_id"], store, 2016, fixed,
                    scenario["current_turn"], datetime.fromisoformat(scenario["current_time"]),
                    embedder=lambda text: (1.0,), top_k=20,
                    token_counter=provisional_token_count, ablation=get_ablation_config(name),
                )
                context = build_context(prompt, tools, scenario["query"], selection, 2016, provisional_token_count)
                self.assertEqual(context.status.value, "ok")
                results[name] = set(context.selected_memory_ids)
            with self.subTest(topic=topic):
                self.assertTrue(set(scenario["target_memory_ids"]) <= results["full"])
                self.assertFalse(set(scenario["target_memory_ids"]) <= results[preset])

    def test_constraint_candidate_answer_uses_policy_and_display_order(self):
        from agent.planner import Planner
        for index in range(8):
            scenario = self.scenario("constraint", topic_index=index, topic_variant_index=index)
            policies = [item["content"] for item in scenario["memory_snapshot"] if item["is_protected"]]
            context = "\n".join(("[PROTECTED CONSTRAINTS]", *policies, "[CURRENT TASK]", scenario["query"]))
            proposed = Planner().plan(context)
            unguarded = Planner().plan("[CURRENT TASK]\n" + scenario["query"])
            with self.subTest(index=index):
                self.assertEqual(proposed.arguments["filename"], scenario["expected_tool_calls"][0]["arguments"]["filename"])
                self.assertNotEqual(proposed.arguments["filename"], unguarded.arguments["filename"])

    def test_generation_uses_installed_prompt_paths_outside_repository_cwd(self):
        previous = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                scenario = self.scenario("flexible")
                self.assertTrue(evaluate(scenario, oracle(scenario)).task_success)
            finally:
                os.chdir(previous)

    def test_reference_budget_includes_planner_request_overhead(self):
        from agent.llm_planner import SYSTEM_INSTRUCTION
        import hashlib

        client = SimpleNamespace(models=SimpleNamespace(
            count_tokens=lambda **kwargs: SimpleNamespace(total_tokens=provisional_token_count(kwargs["contents"])),
        ))
        for instruction in (None, "custom planner instructions"):
            counter = GeminiTokenCounter(client=client)
            if instruction is not None:
                counter.configure_request(system_instruction=instruction)

            def count_request(text):
                self.assertTrue(counter.metadata()["request_aware_budgeting"])
                return provisional_token_count(text) + 300

            with self.subTest(instruction=instruction), patch.object(counter, "count_request", side_effect=count_request):
                scenario = self.scenario("flexible", token_counter=counter)
                root = Path(__file__).resolve().parents[3]
                fixed = fixed_context_tokens(
                    (root / "experiments/prompts/system_v1.txt").read_text(encoding="utf-8"),
                    (root / "experiments/prompts/tools_v1.txt").read_text(encoding="utf-8"),
                    scenario["query"], provisional_token_count,
                ) + 300
                self.assertEqual(scenario["metadata"]["reference_fixed_tokens"], fixed)
                self.assertEqual(scenario["metadata"]["reference_memory_budget"], 2048 - 32 - fixed)
                expected = instruction or SYSTEM_INSTRUCTION
                self.assertEqual(counter.metadata()["system_instruction_sha256"], hashlib.sha256(expected.encode()).hexdigest())

    def test_archive_preserves_topic_plan_and_measured_coverage(self):
        payload = generate_dataset(config())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "topics.zip"
            manifest = write_dataset_archive(payload, path)
            with zipfile.ZipFile(path) as archive:
                saved_manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(saved_manifest, manifest)
                self.assertEqual(manifest["topics"], list(TOPICS))
                for cell in manifest["cells"]:
                    saved = json.loads(archive.read(cell["path"]))
                    self.assertEqual(saved["coverage"]["topic_counts"], dict.fromkeys(TOPICS, 10))
                    self.assertEqual(saved["coverage"], cell["coverage"])
                    self.assertEqual(saved["topics"], list(TOPICS))
                    self.assertEqual(saved["scenarios"], payload["cells"][0]["scenarios"])

    def test_live_smoke_selection_covers_topics_and_stays_paired(self):
        from tests.manual.smoke_4k8k import select_smoke_scenarios

        options = config()
        options["grid"]["context_token_budget"] = [4096, 8192]
        cells = generate_dataset(options)["cells"]
        selected = [select_smoke_scenarios(list(reversed(cell["scenarios"]))) for cell in cells]
        self.assertEqual([s["scenario_id"] for s in selected[0]], [s["scenario_id"] for s in selected[1]])
        self.assertEqual(Counter(s["metadata"]["primary_topic"] for s in selected[0]), dict.fromkeys(TOPICS, 2))
        self.assertEqual(Counter(s["metadata"]["task_family"] for s in selected[0]), {"file": 4, "email": 4})
        with self.assertRaisesRegex(ValueError, "Missing Smoke seed/topic"):
            select_smoke_scenarios([s for s in cells[0]["scenarios"] if s["metadata"]["primary_topic"] != "flexible"])


if __name__ == "__main__":
    unittest.main()
