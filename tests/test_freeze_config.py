"""Machine-enforced architecture/parameter freeze behavior."""

from contextlib import redirect_stdout
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
from benchmark.runner import (
    _config_strategy_plan,
    _load_runner_config,
    _require_executable_config,
    main,
    run_scenarios,
)
from context.tokenizer import GeminiTokenCounter


ROOT = Path(__file__).resolve().parents[1]
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
                self.assertEqual(config["freeze"]["version"], "1.0")
                self.assertEqual(config["scope"], name)
                self.assertEqual(config["status"], status)
                self.assertIs(config["executable"], executable)
                self.assertEqual(config["model"]["name"], "gemini-3.1-flash-lite")
                self.assertEqual(config["runtime"]["token_counter"]["model"],
                                 config["model"]["name"])
                self.assertEqual(config["runtime"]["candidate_top_k"], 20)

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
        self.assertEqual(captured["cli_overrides"]["candidate_top_k"]["effective"], 7)
        self.assertEqual(captured["cli_overrides"]["metric_k"]["effective"], 3)
        self.assertEqual(captured["cli_overrides"]["repeats"]["effective"], 1)
        self.assertEqual(llm_arguments["system_instruction"], SYSTEM_INSTRUCTION)
        self.assertNotIn(captured["system_prompt"].strip(), llm_arguments["system_instruction"])
        self.assertEqual(llm_arguments["max_output_tokens"], 4096)
        self.assertEqual(llm_arguments["thinking_level"], "LOW")
        self.assertEqual(llm_arguments["max_retries"], 2)
        self.assertEqual(llm_arguments["retry_base_seconds"], 0.5)
        self.assertEqual(llm_arguments["timeout_ms"], 60000)


if __name__ == "__main__":
    unittest.main()
