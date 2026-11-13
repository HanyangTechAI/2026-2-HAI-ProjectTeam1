"""Machine-enforced architecture/parameter freeze behavior."""

from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.agent import Agent
from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION
from memory.ablation import get_ablation_config
from benchmark.runner import (
    FREEZE_VERSION,
    FROZEN_ALGORITHM_SHA256,
    FROZEN_EMBEDDER,
    FROZEN_EMBEDDING_MODEL,
    _config_strategy_plan,
    _load_runner_config,
    _require_executable_config,
    _validate_configured_scenario_profile,
    main,
    run_scenarios,
)
from context.tokenizer import GeminiTokenCounter


ROOT = Path(__file__).resolve().parents[3]
CONFIGS = ROOT / "experiments" / "configs"


class FreezeConfigTests(unittest.TestCase):
    def test_frozen_configs_have_explicit_execution_status(self):
        expected = {
            "main": ("executable", True),
            "smoke": ("executable", True),
            "stress": ("executable", True),
            "ablation": ("executable", True),
            "pilot": ("generation_only", False),
            "reference": ("deferred", False),
        }
        for name, (status, executable) in expected.items():
            with self.subTest(config=name):
                config = _load_runner_config(CONFIGS / f"{name}.json")
                self.assertEqual(config["freeze"]["version"], FREEZE_VERSION)
                self.assertEqual(config["scope"], name)
                self.assertEqual(config["dataset"]["scenario_profile"], "balanced_v2")
                self.assertEqual(config["status"], status)
                self.assertIs(config["executable"], executable)
                self.assertEqual(config["model"]["name"], "gemini-3.1-flash-lite")
                self.assertEqual(config["runtime"]["token_counter"]["model"],
                                 config["model"]["name"])
                self.assertEqual(config["runtime"]["candidate_top_k"], 5)
                self.assertEqual(config["runtime"]["embedder"], FROZEN_EMBEDDER)
                self.assertEqual(
                    config["runtime"]["embedding_model"], FROZEN_EMBEDDING_MODEL,
                )

    def test_non_executable_scopes_are_rejected_before_execution(self):
        for name, status in (("pilot", "generation_only"), ("reference", "deferred")):
            config = _load_runner_config(CONFIGS / f"{name}.json")
            with self.subTest(config=name), self.assertRaisesRegex(ValueError, status):
                _require_executable_config(config)
        reference = _load_runner_config(CONFIGS / "reference.json")
        with self.assertRaisesRegex(ValueError, "long_context_adapter_missing"):
            _require_executable_config(reference)

    def test_ablation_labels_map_to_one_base_strategy_and_variants(self):
        config = _load_runner_config(CONFIGS / "ablation.json")
        strategies, ablations = _config_strategy_plan(config["strategies"])
        self.assertEqual(strategies, ("proposed",))
        self.assertEqual(
            ablations,
            ("full", "no_constraint_preservation", "no_temporal",
             "no_state_mandatory", "no_budget_aware_packing"),
        )
        for label, options in config["strategy_options"].items():
            preset = "full" if label == "proposed" else label.split("/", 1)[1]
            expected = get_ablation_config(preset).to_dict()
            expected.pop("name")
            with self.subTest(strategy=label):
                self.assertEqual(options, expected)
        with self.assertRaisesRegex(ValueError, "unsupported config strategy"):
            _config_strategy_plan(["unknown"])

    def test_modified_frozen_provider_is_rejected(self):
        config = copy.deepcopy(_load_runner_config(CONFIGS / "main.json"))
        config["model"]["provider"] = "other"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model.provider"):
                _load_runner_config(path)

    def test_legacy_missing_and_unknown_profiles_are_rejected_by_current_freeze(self):
        for profile in ("legacy_v1", "unknown", None):
            config = copy.deepcopy(_load_runner_config(CONFIGS / "main.json"))
            if profile is None:
                config["dataset"].pop("scenario_profile")
            else:
                config["dataset"]["scenario_profile"] = profile
            with self.subTest(profile=profile), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid-profile.json"
                path.write_text(json.dumps(config), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "dataset.scenario_profile"):
                    _load_runner_config(path)

    def test_frozen_input_profile_checks_selected_enabled_scenarios_and_cell(self):
        balanced = {"scenario_id": "balanced", "metadata": {"scenario_profile": "balanced_v2"}}
        _validate_configured_scenario_profile(
            {"scenario_profile": "balanced_v2"}, [balanced], "balanced_v2",
        )
        _validate_configured_scenario_profile({}, [balanced], "balanced_v2")
        for profile in ("legacy_v1", "unknown", None):
            scenario = {"scenario_id": "old", "metadata": {"scenario_profile": profile}}
            with self.subTest(scenario_profile=profile), self.assertRaisesRegex(
                ValueError, "metadata.scenario_profile",
            ):
                _validate_configured_scenario_profile({}, [balanced, scenario], "balanced_v2")
            with self.subTest(cell_profile=profile), self.assertRaisesRegex(
                ValueError, "input scenario_profile",
            ):
                _validate_configured_scenario_profile(
                    {"scenario_profile": profile}, [balanced], "balanced_v2",
                )
        with self.assertRaisesRegex(ValueError, "metadata.scenario_profile"):
            _validate_configured_scenario_profile({}, [{"scenario_id": "unversioned"}], "balanced_v2")
        _validate_configured_scenario_profile(
            {}, [balanced, {"benchmark_enabled": False}, {"validation_scope": "unit"}], "balanced_v2",
        )

    def test_cli_rejects_old_input_before_constructing_api_clients_or_factories(self):
        with tempfile.TemporaryDirectory() as directory:
            scenarios = Path(directory) / "old-scenarios.json"
            scenarios.write_text(json.dumps({"scenarios": [{"scenario_id": "old"}]}), encoding="utf-8")
            arguments = [
                "runner", "--config", str(CONFIGS / "main.json"),
                "--scenarios", str(scenarios), "--environment-agent-factory", "test:factory",
            ]
            with patch.object(sys, "argv", arguments), patch(
                "benchmark.runner.GeminiTokenCounter",
            ) as counter, patch("benchmark.runner.LLMRunConfig") as llm, patch(
                "benchmark.runner._import_callable",
            ) as imported, redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as error:
                main()
            self.assertEqual(error.exception.code, 2)
            self.assertIn("metadata.scenario_profile", stderr.getvalue())
            counter.assert_not_called()
            llm.assert_not_called()
            imported.assert_not_called()

    def test_cli_without_config_still_accepts_explicit_legacy_input(self):
        counter = lambda text: len(text.split())
        with tempfile.TemporaryDirectory() as directory:
            scenarios = Path(directory) / "legacy.json"
            legacy = {"scenario_id": "legacy", "metadata": {"scenario_profile": "legacy_v1"}}
            scenarios.write_text(json.dumps({"scenario_profile": "legacy_v1", "scenarios": [legacy]}), encoding="utf-8")
            arguments = [
                "runner", "--scenarios", str(scenarios), "--agent-factory", "test:factory",
                "--token-counter", "test:counter", "--system-prompt-file", "experiments/prompts/system_v1.txt",
                "--tool-definitions-file", "experiments/prompts/tools_v1.txt", "--no-progress",
            ]
            with patch.object(sys, "argv", arguments), patch(
                "benchmark.runner._import_callable", side_effect=lambda spec: counter if spec == "test:counter" else lambda: None,
            ), patch("benchmark.runner.run_scenarios", return_value={}) as run, redirect_stdout(io.StringIO()):
                main()
            self.assertEqual(run.call_args.args[0], [legacy])
            self.assertIsNone(run.call_args.kwargs["config_metadata"])

    def test_previous_output_limit_is_rejected_by_new_freeze(self):
        config = copy.deepcopy(_load_runner_config(CONFIGS / "main.json"))
        config["model"]["max_output_tokens"] = 512
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old-output-limit.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "model.max_output_tokens"):
                _load_runner_config(path)

    def test_modified_planner_behavior_is_rejected(self):
        with patch(
            "benchmark.runner.SYSTEM_INSTRUCTION", SYSTEM_INSTRUCTION + "\nchanged",
        ), self.assertRaisesRegex(ValueError, "SYSTEM_INSTRUCTION"):
            _load_runner_config(CONFIGS / "main.json")

        with patch(
            "benchmark.runner.ACTION_SCHEMA", {"type": "object"},
        ), self.assertRaisesRegex(ValueError, "ACTION_SCHEMA"):
            _load_runner_config(CONFIGS / "main.json")

    def test_modified_algorithm_specification_is_rejected(self):
        with patch(
            "benchmark.runner.FROZEN_ALGORITHM_SHA256", "0" * 64,
        ), self.assertRaisesRegex(ValueError, "algorithm specification changed"):
            _load_runner_config(CONFIGS / "main.json")

        self.assertEqual(len(FROZEN_ALGORITHM_SHA256), 64)

    def test_cli_cannot_override_frozen_embedder(self):
        arguments = [
            "runner", "--config", str(CONFIGS / "main.json"),
            "--agent-factory", "agent.agent:Agent",
            "--embedder", "custom.module:embed",
        ]
        stderr = io.StringIO()
        with patch.object(sys, "argv", arguments), redirect_stderr(stderr), self.assertRaises(
            SystemExit,
        ) as raised:
            main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("cannot override the frozen experiment embedder", stderr.getvalue())

    def test_programmatic_llm_and_gemini_counter_models_must_match(self):
        counter = GeminiTokenCounter(model="different-model", client=object())
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(
            ValueError, "model.*must match",
        ):
            run_scenarios(
                [{
                    "scenario_id": "model-mismatch",
                    "benchmark_enabled": True,
                    "validation_scope": "integration",
                }],
                agent_factory=Agent,
                system_prompt="system",
                tool_definitions="tools",
                output_directory=directory,
                strategies=("sliding_window",),
                token_counter=counter,
                bootstrap_iterations=100,
                llm_config=LLMRunConfig(model="gemini-3.1-flash-lite"),
            )

    def test_cli_uses_config_and_records_explicit_overrides(self):
        captured = {}
        llm_arguments = {}

        class FakeLLMRunConfig:
            def __init__(self, **kwargs):
                llm_arguments.update(kwargs)
                self.__dict__.update(kwargs)

        class FakeGeminiTokenCounter:
            def __init__(self, **kwargs):
                self.model = kwargs["model"]

            def cache_info(self):
                return SimpleNamespace(hits=0, misses=0, currsize=0)

        def fake_run(_scenarios, **kwargs):
            captured.update(kwargs)
            return {}

        def fake_import(spec):
            if spec.endswith(":environment_agent"):
                return lambda _environment: None
            if spec.endswith(":multilingual_embedder"):
                return lambda _text: (1.0,)
            raise AssertionError(spec)

        with tempfile.TemporaryDirectory() as directory:
            scenarios = Path(directory) / "scenarios.json"
            scenarios.write_text("[]", encoding="utf-8")
            output = Path(directory) / "override-output"
            arguments = [
                "runner", "--config", str(CONFIGS / "main.json"),
                "--scenarios", str(scenarios),
                "--environment-agent-factory", "benchmark.experiment_runtime:environment_agent",
                "--top-k", "7", "--metric-k", "3", "--repeats", "1",
                "--output", str(output),
            ]
            with patch.object(sys, "argv", arguments), patch(
                "benchmark.runner.LLMRunConfig", FakeLLMRunConfig,
            ), patch(
                "benchmark.runner.GeminiTokenCounter", FakeGeminiTokenCounter,
            ), patch(
                "benchmark.runner._import_callable", side_effect=fake_import,
            ), patch(
                "benchmark.runner.run_scenarios", side_effect=fake_run,
            ), redirect_stdout(io.StringIO()):
                main()

        self.assertEqual(captured["strategies"],
                         ("proposed", "sliding_window", "vector_top_k", "utility_per_token"))
        self.assertEqual(captured["top_k"], 7)
        self.assertEqual(captured["metric_k"], 3)
        self.assertEqual(captured["repeats"], 1)
        self.assertEqual(captured["max_steps"], 8)
        self.assertEqual(captured["utility_weights"], (0.6, 0.3, 0.1))
        self.assertEqual(captured["bootstrap_iterations"], 2000)
        self.assertEqual(captured["seed"], 42)
        self.assertEqual(captured["output_directory"], str(output))
        self.assertEqual(captured["config_metadata"]["status"], "executable")
        self.assertEqual(captured["config_metadata"]["embedder"], {
            "callable": FROZEN_EMBEDDER,
            "model": FROZEN_EMBEDDING_MODEL,
        })
        self.assertEqual(captured["cli_overrides"]["candidate_top_k"]["effective"], 7)
        self.assertEqual(captured["cli_overrides"]["metric_k"]["effective"], 3)
        self.assertEqual(captured["cli_overrides"]["repeats"]["effective"], 1)
        self.assertEqual(llm_arguments["system_instruction"], SYSTEM_INSTRUCTION)
        self.assertNotIn(captured["system_prompt"].strip(), llm_arguments["system_instruction"])
        self.assertEqual(llm_arguments["max_output_tokens"], 2048)
        self.assertEqual(llm_arguments["thinking_level"], "LOW")
        self.assertEqual(llm_arguments["max_retries"], 2)
        self.assertEqual(llm_arguments["retry_base_seconds"], 0.5)
        self.assertEqual(llm_arguments["timeout_ms"], 60000)


if __name__ == "__main__":
    unittest.main()
