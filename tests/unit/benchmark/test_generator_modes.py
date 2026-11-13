"""Selectable horizon filler preserves paired tasks and measured data contracts."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from benchmark import generator
from benchmark.horizon_filler import HorizonFiller
from context.tokenizer import provisional_token_count


def mode_config():
    return {
        "name": "mode_test",
        "dataset": {
            "source": "synthetic", "samples_per_cell": 4,
            "task_types": ["temporal_update", "approval"],
        },
        "seeds": [42],
        "grid": {
            "context_token_budget": [2048], "history_length_tokens": [10000],
            "horizon_turns": [10], "noise_memory_ratio": [0.5],
            "constraint_count": [1], "state_update_count": [2],
        },
    }


VARIANTS = (
    {},
    {"state_scope": "historical"},
    {"task_type": "approval"},
    {"task_type": "approval", "approval_status": "granted"},
)


class GeneratorModeTests(unittest.TestCase):
    def test_legacy_mode_keeps_existing_content(self):
        # Captured before the selectable-mode implementation; provenance fields
        # are deliberately excluded so this checks data, rather than metadata.
        expected_hashes = (
            "434a8dd9b827973af12b4b0097b200f45f0d9e94e99e1fb985905cf294a52269",
            "8ba4d2745429c1c8cdc1b48db7c700e63b36dcd82d29c7df5dc54de5cfef2ff7",
            "9e815274750d861edeb169d50f3835854c3c6b29f04140c41fa16a5d0938fe28",
            "57cadae49adca9ee12c3b87e72f2c38afdb7a62466aa1869f59c421701c33865",
        )
        for variant, expected in zip(VARIANTS, expected_hashes):
            with self.subTest(variant=variant):
                scenario = generator.generate_scenario(
                    scenario_profile="legacy_v1", horizon_generation_mode=1, **variant,
                )
                content = {key: scenario[key] for key in (
                    "history", "memory_snapshot", "query", "expected_tool_calls",
                )}
                serialized = json.dumps(
                    content, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                ).encode("utf-8")
                self.assertEqual(hashlib.sha256(serialized).hexdigest(), expected)

    def test_each_mode_is_reproducible_and_changes_actual_memory_content(self):
        global_state = random.getstate()
        scenarios = []
        for mode in (1, 2, 3):
            with self.subTest(mode=mode):
                first = generator.generate_scenario(horizon_generation_mode=mode)
                second = generator.generate_scenario(horizon_generation_mode=mode)
                self.assertEqual(first, second)
                self.assertEqual(first["metadata"]["horizon_generation_mode"], mode)
                scenarios.append(first)
        self.assertEqual(random.getstate(), global_state)
        for left, right in ((0, 1), (0, 2), (1, 2)):
            self.assertNotEqual(scenarios[left]["history"], scenarios[right]["history"])
            self.assertNotEqual(
                scenarios[left]["memory_snapshot"], scenarios[right]["memory_snapshot"],
            )
        for candidate in scenarios[1:]:
            for key in (
                "sample_id", "scenario_id", "query", "required_states",
                "target_memory_ids", "constraint_rules", "expected_tool_calls",
                "environment",
            ):
                self.assertEqual(scenarios[0][key], candidate[key])
            self.assertEqual(
                [item["memory_id"] for item in scenarios[0]["memory_snapshot"]],
                [item["memory_id"] for item in candidate["memory_snapshot"]],
            )

    def test_all_modes_preserve_budget_and_horizon_pairing(self):
        for mode in (1, 2, 3):
            for variant in VARIANTS:
                with self.subTest(mode=mode, variant=variant):
                    first = generator.generate_scenario(
                        horizon_generation_mode=mode, context_budget=1024,
                        horizon_turns=10, **variant,
                    )
                    larger_budget = generator.generate_scenario(
                        horizon_generation_mode=mode, context_budget=8192,
                        horizon_turns=10, **variant,
                    )
                    farther = generator.generate_scenario(
                        horizon_generation_mode=mode, context_budget=1024,
                        horizon_turns=50, **variant,
                    )
                    for key in ("history", "memory_snapshot", "query", "expected_tool_calls"):
                        self.assertEqual(first[key], larger_budget[key])
                    for key in (
                        "sample_id", "scenario_id", "query", "required_states",
                        "target_memory_ids", "constraint_rules", "expected_tool_calls",
                    ):
                        self.assertEqual(first[key], farther[key])
                    self.assertEqual(
                        [(m["memory_id"], m["content"]) for m in first["memory_snapshot"]],
                        [(m["memory_id"], m["content"]) for m in farther["memory_snapshot"]],
                    )
                    for scenario in (first, farther):
                        latest_required_turn = max(
                            m["turn_id"] for m in scenario["memory_snapshot"]
                            if m["memory_id"] in scenario["target_memory_ids"]
                        )
                        self.assertEqual(
                            scenario["current_turn"] - latest_required_turn,
                            scenario["metadata"]["horizon_turns"],
                        )

    def test_all_modes_measure_history_memory_and_noise(self):
        for mode in (1, 2, 3):
            for variant in VARIANTS:
                with self.subTest(mode=mode, variant=variant):
                    scenario = generator.generate_scenario(
                        horizon_generation_mode=mode, **variant,
                    )
                    metadata = scenario["metadata"]
                    history_tokens = provisional_token_count(
                        generator.render_history(scenario["history"]),
                    )
                    self.assertEqual(history_tokens, metadata["actual_history_length_tokens"])
                    self.assertLessEqual(abs(history_tokens - 10000), 100)
                    for item in scenario["memory_snapshot"]:
                        self.assertEqual(
                            item["token_count"], provisional_token_count(item["content"]),
                        )
                    relevant_tokens = sum(
                        m["token_count"] for m in scenario["memory_snapshot"]
                        if m["memory_id"] in scenario["target_memory_ids"]
                    )
                    noise_tokens = sum(
                        m["token_count"] for m in scenario["memory_snapshot"]
                        if "_noise_" in m["memory_id"]
                    )
                    actual_ratio = noise_tokens / (relevant_tokens + noise_tokens)
                    self.assertAlmostEqual(actual_ratio, metadata["actual_noise_memory_ratio"])
                    self.assertLessEqual(abs(actual_ratio - 0.5), 0.01)
                    self.assertEqual(
                        [entry["turn_id"] for entry in scenario["history"]],
                        list(range(1, scenario["current_turn"])),
                    )

    def test_default_config_and_explicit_mode_precedence(self):
        config = mode_config()
        with patch.object(generator, "DEFAULT_HORIZON_MODE", 2):
            self.assertEqual(
                generator.generate_scenario()["metadata"]["horizon_generation_mode"], 2,
            )
            self.assertEqual(generator.generate_dataset(config)["horizon_generation_mode"], 2)
            config["dataset"]["horizon_generation_mode"] = 3
            original = copy.deepcopy(config)
            self.assertEqual(generator.generate_dataset(config)["horizon_generation_mode"], 3)
            payload = generator.generate_dataset(config, horizon_generation_mode=1)
            self.assertEqual(payload["horizon_generation_mode"], 1)
            self.assertEqual(config, original)
            self.assertEqual(
                {s["metadata"]["horizon_generation_mode"] for cell in payload["cells"]
                 for s in cell["scenarios"]},
                {1},
            )

    def test_all_modes_fit_an_independent_non_word_token_counter(self):
        def weighted_counter(text):
            return sum(1 + int(len(word) > 6) for word in text.split())

        for mode in (1, 2, 3):
            for variant in VARIANTS:
                with self.subTest(mode=mode, variant=variant):
                    scenario = generator.generate_scenario(
                        horizon_generation_mode=mode, token_counter=weighted_counter,
                        **variant,
                    )
                    metadata = scenario["metadata"]
                    measured = weighted_counter(generator.render_history(scenario["history"]))
                    self.assertEqual(measured, metadata["actual_history_length_tokens"])
                    self.assertLessEqual(abs(measured - 10000), 100)
                    relevant_tokens = noise_tokens = 0
                    for memory in scenario["memory_snapshot"]:
                        tokens = weighted_counter(memory["content"])
                        self.assertEqual(tokens, memory["token_count"])
                        if memory["memory_id"] in scenario["target_memory_ids"]:
                            relevant_tokens += tokens
                        if "_noise_" in memory["memory_id"]:
                            noise_tokens += tokens
                    actual_noise = noise_tokens / (relevant_tokens + noise_tokens)
                    self.assertAlmostEqual(actual_noise, metadata["actual_noise_memory_ratio"])
                    self.assertLessEqual(abs(actual_noise - 0.5), 0.01)

    def test_invalid_modes_are_rejected_in_api_and_config(self):
        for invalid in (True, False, "2", 2.0, [1, 2, 3], 0, 6, -1):
            with self.subTest(mode=invalid):
                with self.assertRaises((TypeError, ValueError)):
                    generator.generate_scenario(horizon_generation_mode=invalid)
                with self.assertRaises((TypeError, ValueError)):
                    generator.generate_dataset(mode_config(), horizon_generation_mode=invalid)
                config = mode_config()
                config["dataset"]["horizon_generation_mode"] = invalid
                with self.assertRaises((TypeError, ValueError)):
                    generator.generate_dataset(config)

    def test_writers_record_one_selected_mode_in_directory_and_archive(self):
        config = mode_config()
        config["dataset"]["horizon_generation_mode"] = 3
        payload = generator.generate_dataset(config)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "dataset"
            manifest = generator.write_dataset(payload, output)
            self.assertEqual(manifest["horizon_generation_mode"], 3)
            for cell in manifest["cells"]:
                record = json.loads((output / cell["path"]).read_text(encoding="utf-8"))
                self.assertEqual(record["horizon_generation_mode"], 3)
                self.assertEqual(
                    {s["metadata"]["horizon_generation_mode"] for s in record["scenarios"]},
                    {3},
                )
            archive_path = Path(directory) / "dataset.zip"
            generator.write_dataset_archive(payload, archive_path)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(json.loads(archive.read("manifest.json")), manifest)
                for cell in manifest["cells"]:
                    self.assertEqual(
                        archive.read(cell["path"]), (output / cell["path"]).read_bytes(),
                    )

    def test_cli_selects_a_single_mode_and_overrides_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = mode_config()
            config["dataset"]["horizon_generation_mode"] = 2
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            for selected in (None, 1, 2, 3):
                with self.subTest(selected=selected):
                    output = Path(directory) / f"selected_{selected}"
                    args = [
                        "generator", "--config", str(config_path), "--output", str(output),
                    ]
                    if selected is not None:
                        args.extend(["--horizon-mode", str(selected)])
                    with patch.object(sys, "argv", args), redirect_stdout(io.StringIO()):
                        generator.main()
                    expected_mode = 2 if selected is None else selected
                    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
                    self.assertEqual(manifest["horizon_generation_mode"], expected_mode)
                    self.assertEqual(manifest["n_cells"], 1)
                    self.assertEqual(manifest["n_scenarios"], 4)
                    for cell in manifest["cells"]:
                        record = json.loads((output / cell["path"]).read_text(encoding="utf-8"))
                        self.assertEqual(
                            {s["metadata"]["horizon_generation_mode"]
                             for s in record["scenarios"]},
                            {expected_mode},
                        )

    def test_cli_rejects_unknown_mode_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "should_not_exist"
            args = ["generator", "--horizon-mode", "6", "--output", str(output)]
            with (
                patch.object(sys, "argv", args), redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as caught,
            ):
                generator.main()
            self.assertEqual(caught.exception.code, 2)
            self.assertFalse(output.exists())

    def test_cli_default_paths_separate_modes_and_archive_extensions(self):
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            config = mode_config()
            config["dataset"]["samples_per_cell"] = 1
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            try:
                os.chdir(directory)
                for mode in (1, 2, 3):
                    for archived in (False, True):
                        with self.subTest(mode=mode, archived=archived):
                            args = [
                                "generator", "--config", str(config_path),
                                "--horizon-mode", str(mode),
                            ]
                            if archived:
                                args.append("--archive")
                            output = io.StringIO()
                            with patch.object(sys, "argv", args), redirect_stdout(output):
                                generator.main()
                            name = "mode_test_balanced_v2" if mode == 1 else f"mode_test_balanced_v2_horizon{mode}"
                            target = Path("benchmark/generated") / (
                                name + ".zip" if archived else name
                            )
                            self.assertEqual(Path(json.loads(output.getvalue())["output"]), target)
                            self.assertTrue(target.exists())
                            if archived:
                                with zipfile.ZipFile(target) as archive:
                                    manifest = json.loads(archive.read("manifest.json"))
                            else:
                                manifest = json.loads(
                                    (target / "manifest.json").read_text(encoding="utf-8"),
                                )
                            self.assertEqual(manifest["horizon_generation_mode"], mode)
                            self.assertEqual(manifest["scenario_profile"], "balanced_v2")
                            self.assertEqual(manifest["n_scenarios"], 1)
            finally:
                os.chdir(original_directory)

    def test_cli_explicit_legacy_profile_keeps_legacy_output_path(self):
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            config = mode_config()
            config["dataset"].update(samples_per_cell=1, scenario_profile="balanced_v2")
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            try:
                os.chdir(directory)
                args = ["generator", "--config", str(config_path), "--archive",
                        "--scenario-profile", "legacy_v1", "--horizon-mode", "2"]
                output = io.StringIO()
                with patch.object(sys, "argv", args), redirect_stdout(output):
                    generator.main()
                target = Path("benchmark/generated/mode_test_horizon2.zip")
                self.assertEqual(Path(json.loads(output.getvalue())["output"]), target)
                with zipfile.ZipFile(target) as archive:
                    manifest = json.loads(archive.read("manifest.json"))
                self.assertEqual(manifest["scenario_profile"], "legacy_v1")
            finally:
                os.chdir(original_directory)


class HorizonFillerTests(unittest.TestCase):
    def filler(self, mode=2, sample_id="seed42_temporal_update_000_current"):
        return HorizonFiller(sample_id, mode, " ".join(generator.NARRATIVE))

    def test_length_probe_order_reads_a_fixed_prefix(self):
        for mode in (2, 3):
            with self.subTest(mode=mode):
                filler = self.filler(mode)
                expected = self.filler(mode).words(300, key="history")
                for length in (250, 7, 100, 0, 300, 7):
                    self.assertEqual(
                        filler.words(length, key="history"),
                        " ".join(expected.split()[:length]),
                    )
                    self.assertEqual(len(filler.words(length, key="history").split()), length)
                # Inspecting another stream or asking for sentences cannot
                # change the words already selected for length fitting.
                filler.words(600, key="noise")
                filler.sentence(key="history", index=50)
                self.assertEqual(filler.words(300, key="history"), expected)

    def test_natural_stream_varies_by_sample_and_purpose(self):
        first = self.filler().words(200, key="history")
        self.assertNotEqual(first, self.filler().words(200, key="noise"))
        self.assertNotEqual(first, self.filler(sample_id="seed43_other").words(200, key="history"))
        counts = self.filler().counts(200, key="history")
        self.assertEqual(counts["repeated"], 0)
        self.assertGreater(counts["random_natural"], 1)

    def test_mixed_stream_alternates_sentence_pairs_and_counts_partial_tail(self):
        filler = self.filler(mode=3)
        first_repeated = filler.sentence(key="history", index=0)
        repeated_pool = {
            sentence.strip() + "."
            for sentence in " ".join(generator.NARRATIVE).rstrip(".").split(". ")
        }
        self.assertIn(first_repeated, repeated_pool)
        self.assertEqual(filler.counts(0, key="history"), {"repeated": 0, "random_natural": 0})
        self.assertEqual(
            filler.counts(len(first_repeated.split()), key="history"),
            {"repeated": 1, "random_natural": 0},
        )
        self.assertEqual(
            filler.counts(len(first_repeated.split()) + 1, key="history"),
            {"repeated": 1, "random_natural": 1},
        )
        total_words = 0
        for index in range(20):
            sentence = filler.sentence(key="history", index=index)
            total_words += len(sentence.split())
            self.assertTrue(sentence.endswith("."))
            if index % 2 == 0:
                self.assertIn(sentence, repeated_pool)
            else:
                self.assertNotIn(sentence, repeated_pool)
        self.assertEqual(
            filler.counts(total_words, key="history"),
            {"repeated": 10, "random_natural": 10},
        )
        for length in (1, 10, 20, 100, 1000):
            counts = filler.counts(length, key="another_stream")
            self.assertLessEqual(abs(counts["repeated"] - counts["random_natural"]), 1)

    def test_natural_filler_does_not_leak_task_facts_from_sample_or_key(self):
        sample_id = "HAI-secret_report_v9.pdf_user@external.test_approved_123"
        for mode in (2, 3):
            with self.subTest(mode=mode):
                text = self.filler(mode, sample_id).words(4000, key=sample_id)
                self.assertNotRegex(
                    text,
                    r"(?i)(?:[\w.-]+\.(?:pdf|docx?|txt|csv|xlsx?)\b|@|approved_|HAI-|승인|금지|발송)",
                )


if __name__ == "__main__":
    unittest.main()
