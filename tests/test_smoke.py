"""smoke.json 설정으로 Gemini 3.1 Flash-Lite LLM Planner가 mock tool을 호출하는지 확인한다.

GEMINI_API_KEY가 없으면 건너뛴다. 전체 단위 테스트에 실제 모델 호출을 섞지 않기 위해서다.
툴 정의 파일 경로는 smoke.json에 없으므로 experiments/prompts/tools_v1.txt를 쓴다.
시나리오 그리드는 benchmark/generated/smoke_gemini를 쓴다. 생성 LLM은 gemini-3.1-flash-lite이고,
토큰 측정은 CountTokens를 부르지 않도록 provisional로 맞춘다.

실행: python -m unittest tests.test_smoke
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

from agent.llm_planner import LLMRunConfig, shared_system_instruction
from benchmark.evaluator import load_config
from benchmark.runner import run_scenarios
from context.tokenizer import provisional_token_count

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "experiments" / "configs" / "smoke.json"
DATA_DIR = ROOT / "benchmark" / "generated" / "smoke_gemini"
TOOLS_PATH = ROOT / "experiments" / "prompts" / "tools_v1.txt"
KNOWN_TOOLS = frozenset({"email", "file", "calendar", "task"})


@unittest.skipUnless(os.getenv("GEMINI_API_KEY"), "GEMINI_API_KEY is not set")
class SmokeConfigTests(unittest.TestCase):
    def test_llm_planner_calls_tools_under_smoke_config(self):
        from benchmark.experiment_runtime import environment_agent, multilingual_embedder

        config = load_config(CONFIG_PATH)
        model = config["model"]
        model_name = model["name"].strip()
        system_prompt = (ROOT / model["system_prompt_file"]).read_text(encoding="utf-8")
        scenarios = _scenarios(config)
        output = ROOT / config["output_directory"]

        result = run_scenarios(
            scenarios,
            environment_agent_factory=environment_agent,
            embedder=multilingual_embedder,
            system_prompt=system_prompt,
            tool_definitions=TOOLS_PATH.read_text(encoding="utf-8"),
            output_directory=output,
            strategies=tuple(config["strategies"]),
            token_counter=provisional_token_count,
            top_k=config["evaluation"]["retrieval_k"],
            repeats=config["repeats"],
            bootstrap_iterations=config["evaluation"]["bootstrap_iterations"],
            seed=config["evaluation"]["bootstrap_seed"],
            llm_config=LLMRunConfig(
                model=model_name,
                temperature=model["temperature"],
                system_instruction=shared_system_instruction(system_prompt),
                max_output_tokens=model["max_output_tokens"],
            ),
        )

        expected_runs = len(scenarios) * len(config["strategies"]) * config["repeats"]
        self.assertEqual(result["n_runs"], expected_runs)
        experiment = json.loads((output / "experiment.json").read_text(encoding="utf-8"))
        self.assertEqual(experiment["system_prompt"], system_prompt)
        self.assertEqual(experiment["llm"]["provider"], "google")
        self.assertEqual(experiment["llm"]["model"], model_name)
        self.assertEqual(experiment["llm"]["temperature"], float(model["temperature"]))
        self.assertEqual(experiment["llm"]["max_output_tokens"], model["max_output_tokens"])
        self.assertEqual(experiment["strategies"], list(config["strategies"]))

        rows = [
            json.loads(line)
            for line in (output / "runs.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(len(rows), expected_runs)
        failures = []
        for row in rows:
            run = row["run_result"]
            options = run["strategy_options"]
            if options.get("model") != model_name or options.get("temperature") != float(model["temperature"]):
                failures.append(f"{row['strategy']} llm settings drifted: {options}")
            calls = run.get("tool_calls") or []
            results = run.get("tool_results") or []
            succeeded = [
                call for call, tool_result in zip(calls, results)
                if tool_result.get("success") and call.get("tool_name") in KNOWN_TOOLS
            ]
            rendered = ", ".join(f"{call.get('tool_name')}.{call.get('action')}" for call in calls) or "(none)"
            print(
                f"{row['strategy']} {row['scenario'].get('scenario_id')} tools={rendered} "
                f"stopped={run.get('stopped_reason')}",
                flush=True,
            )
            if not succeeded:
                failures.append(
                    f"{row['strategy']} {row['scenario'].get('scenario_id')} "
                    f"tools={rendered} response={run.get('response')}"
                )
        self.assertEqual(failures, [])


def _scenarios(config: dict) -> list[dict]:
    manifest = json.loads((DATA_DIR / "manifest.json").read_text(encoding="utf-8"))
    scenarios: list[dict] = []
    for cell in manifest["cells"]:
        parameters = cell["parameters"]
        for key, allowed in config["grid"].items():
            if parameters.get(key) not in allowed:
                raise AssertionError(f"생성 셀의 {key}={parameters.get(key)} 값이 smoke.json 그리드에 없습니다.")
        payload = json.loads((DATA_DIR / cell["path"]).read_text(encoding="utf-8"))
        scenarios.extend(payload["scenarios"])
    expected = config["dataset"]["samples_per_cell"] * len(manifest["cells"])
    if len(scenarios) != expected:
        raise AssertionError(f"시나리오 수가 smoke.json과 다릅니다: {len(scenarios)} != {expected}")
    rewritten = []
    for scenario in scenarios:
        item = dict(scenario)
        item["token_count_mode"] = "provisional"
        rewritten.append(item)
    return rewritten


if __name__ == "__main__":
    unittest.main()
