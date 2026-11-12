"""smoke.json 설정으로 Gemini 3.1 Flash-Lite LLM Planner가 mock tool을 호출하는지 확인한다.

GEMINI_API_KEY가 없으면 건너뛴다. 전체 단위 테스트에 실제 모델 호출을 섞지 않기 위해서다.
툴 정의 파일 경로는 smoke.json에 없으므로 experiments/prompts/tools_v1.txt를 쓴다.
시나리오 그리드는 benchmark/generated/smoke_gemini_3_1_flash_lite를 쓴다.
생성과 CountTokens에 같은 모델을 사용하며 다른 모델로 측정한 데이터는 거부한다.

실행: python -m unittest tests.test_smoke
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import unittest
from datetime import datetime

from agent.llm_planner import LLMRunConfig, SYSTEM_INSTRUCTION
from benchmark.evaluator import load_config
from benchmark.runner import run_scenarios
from context.tokenizer import GeminiTokenCounter

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "experiments" / "configs" / "smoke.json"
DATA_DIR = ROOT / "benchmark" / "generated" / "smoke_gemini_3_1_flash_lite"
TOOLS_PATH = ROOT / "experiments" / "prompts" / "tools_v1.txt"
KNOWN_TOOLS = frozenset({"email", "file", "calendar", "task"})


@unittest.skipUnless(
    os.getenv("GEMINI_API_KEY")
    and os.getenv("RUN_LIVE_GEMINI_TESTS") == "1",
    "Live Gemini smoke test is disabled",
)
class SmokeConfigTests(unittest.TestCase):
    def test_llm_planner_calls_tools_under_smoke_config(self):
        from benchmark.experiment_runtime import environment_agent, multilingual_embedder

        config = load_config(CONFIG_PATH)
        model = config["model"]
        model_name = model["name"].strip()
        system_prompt = (ROOT / model["system_prompt_file"]).read_text(encoding="utf-8")
        scenarios = _scenarios(config)

        output = ROOT / "experiments" / "results" / f"smoke_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"

        result = run_scenarios(
            scenarios,
            environment_agent_factory=environment_agent,
            embedder=multilingual_embedder,
            system_prompt=system_prompt,
            tool_definitions=TOOLS_PATH.read_text(encoding="utf-8"),
            output_directory=output,
            strategies=tuple(config["strategies"]),
            token_counter=GeminiTokenCounter(model=model_name),
            top_k=config["evaluation"]["retrieval_k"],
            repeats=config["repeats"],
            bootstrap_iterations=config["evaluation"]["bootstrap_iterations"],
            seed=config["evaluation"]["bootstrap_seed"],
            llm_config=LLMRunConfig(
                model=model_name,
                temperature=model["temperature"],
                system_instruction=SYSTEM_INSTRUCTION,
                max_output_tokens=model["max_output_tokens"],
                thinking_level=model["thinking_level"],
                max_retries=model["max_retries"],
                retry_base_seconds=model["retry_base_seconds"],
                timeout_ms=model["timeout_ms"],
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
        successful_tools_by_strategy = {
            strategy: 0
            for strategy in config["strategies"]
        }

        for row in rows:
            strategy = row["strategy"]
            run = row["run_result"]
            evaluation = row["evaluation"]
            options = run["strategy_options"]

            scenario_id = row["scenario"]["scenario_id"]

            # 모든 전략에서 동일한 LLM 설정을 사용하는지 확인
            if (
                options.get("model") != model_name
                or options.get("temperature")
                != float(model["temperature"])
            ):
                failures.append(
                    f"{strategy} {scenario_id}: LLM settings drift"
                )

            # Pipeline이 실제로 Agent를 실행했는지 확인
            if run.get("status") != "ok":
                failures.append(
                    f"{strategy} {scenario_id}: "
                    f"pipeline status={run.get('status')}"
                )

            if run.get("agent_executed") is not True:
                failures.append(
                    f"{strategy} {scenario_id}: Agent not executed"
                )

            # 실제 Gemini API가 호출됐는지 확인
            usage = run.get("llm_usage") or {}
            api_calls = usage.get("api_calls")

            if type(api_calls) is not int or api_calls < 1:
                failures.append(
                    f"{strategy} {scenario_id}: No Gemini API call"
                )

            # Baseline 실패도 평가 결과로 기록되어야 함
            if type(evaluation.get("task_success")) is not bool:
                failures.append(
                    f"{strategy} {scenario_id}: "
                    "Missing task_success evaluation"
                )

            calls = run.get("tool_calls") or []
            results = run.get("tool_results") or []

            succeeded = [
                call
                for call, tool_result in zip(calls, results)
                if (
                    tool_result.get("success") is True
                    and call.get("tool_name") in KNOWN_TOOLS
                )
            ]

            successful_tools_by_strategy[strategy] += len(succeeded)

            rendered = (
                ", ".join(
                    f"{call.get('tool_name')}.{call.get('action')}"
                    for call in calls
                )
                or "(none)"
            )

            audit = run.get("prompt_token_audit") or {}

            print(
                f"{strategy} {scenario_id} "
                f"tools={rendered} "
                f"task_success={evaluation.get('task_success')} "
                f"prompt_audit={audit.get('verification_status')} "
                f"stopped={run.get('stopped_reason')}",
                flush=True,
            )

        # 각 전략의 Tool 실행 경로가 실제로 작동했는지 확인.
        # 다만 모든 시나리오에서 성공을 강제하지 않는다.
        for strategy, count in successful_tools_by_strategy.items():
            if count == 0:
                failures.append(
                    f"{strategy}: No successful tool execution"
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
    expected_mode = f"gemini:{config['model']['name']}"
    if manifest.get("token_count_mode") != expected_mode or any(
        scenario.get("token_count_mode") != expected_mode for scenario in scenarios
    ):
        raise AssertionError("생성 모델과 같은 CountTokens 모델로 시나리오를 재생성해야 합니다.")
    return scenarios


if __name__ == "__main__":
    unittest.main()
