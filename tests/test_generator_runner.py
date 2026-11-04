"""Generated initial tool state reaches the real runner without oracle input."""

from contextlib import redirect_stderr, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent.agent import Agent
from agent.tools.email import EmailTool
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from benchmark.generator import generate_scenario
from benchmark.runner import main, run_scenarios
from context.tokenizer import provisional_token_count


def completed_action(response, calls, results):
    """Runtime completion check without task-specific expected values."""
    return bool(response and calls and results and all(result.success for result in results))


def environment_agent(environment):
    """Seed mock tools only; the normal Planner still chooses its own actions."""
    return Agent(
        executor=ToolExecutor(
            file=FileTool(environment.get("files", {})),
            email=EmailTool(environment.get("valid_approval_ids", [])),
        ),
        goal_checker=completed_action,
    )


class GeneratorRunnerTests(unittest.TestCase):
    def scenario(self, **parameters):
        return generate_scenario(context_budget=8192, **parameters)

    def run_rows(self, scenarios, **parameters):
        with tempfile.TemporaryDirectory() as directory:
            result = run_scenarios(
                scenarios, output_directory=directory,
                system_prompt="과거 기억과 제약에 따라 도구를 사용하세요.",
                tool_definitions="file.find_file email.request_approval email.send_email",
                strategies=("sliding_window",), bootstrap_iterations=100,
                **parameters,
            )
            return [json.loads(line) for line in
                    Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]

    def test_generated_files_and_preexisting_approvals_reach_tools(self):
        scenarios = [
            self.scenario(task_type="temporal_update"),
            self.scenario(task_type="approval", approval_status="pending"),
            self.scenario(task_type="approval", approval_status="granted"),
        ]
        before = copy.deepcopy(scenarios)
        rows = self.run_rows(scenarios, environment_agent_factory=environment_agent)
        self.assertEqual(len(rows), 3)
        self.assertEqual(scenarios, before)
        for row in rows:
            self.assertTrue(row["evaluation"]["task_success"])
            self.assertEqual(row["run_result"]["status"], "ok")
        found, pending, granted = [row["run_result"] for row in rows]
        self.assertEqual(found["tool_calls"][0]["action"], "find_file")
        self.assertTrue(found["tool_results"][0]["output"]["found"])
        self.assertEqual(pending["tool_calls"][0]["action"], "request_approval")
        self.assertEqual(pending["tool_results"][0]["output"]["status"], "pending")
        self.assertFalse(any(call["action"] == "send_email" for call in pending["tool_calls"]))
        self.assertIn("승인을 기다리고", pending["response"])
        self.assertEqual(granted["tool_calls"][0]["action"], "send_email")
        self.assertTrue(granted["tool_results"][0]["output"]["approved"])

    def test_factory_receives_only_copied_initial_state_and_fresh_agents(self):
        scenario = self.scenario(task_type="approval", approval_status="granted")
        # Unknown nested annotations must not become a route around the input
        # boundary even if a caller places them inside environment by mistake.
        scenario["environment"]["query"] = "secret query annotation"
        scenario["environment"]["expected_tool_calls"] = [{"secret": "oracle"}]
        original = copy.deepcopy(scenario)
        environments = []
        agents = []

        def factory(environment):
            environments.append(copy.deepcopy(environment))
            agent = environment_agent(environment)
            agents.append(agent)
            environment["files"].clear()
            environment["valid_approval_ids"].clear()
            return agent

        rows = self.run_rows([scenario], environment_agent_factory=factory, repeats=2)
        self.assertEqual(len(environments), 2)
        self.assertEqual(environments[0], environments[1])
        self.assertEqual(set(environments[0]), {"files", "valid_approval_ids"})
        self.assertEqual(environments[0]["files"], original["environment"]["files"])
        self.assertEqual(environments[0]["valid_approval_ids"],
                         original["environment"]["valid_approval_ids"])
        self.assertIsNot(agents[0], agents[1])
        self.assertIsNot(agents[0].executor, agents[1].executor)
        self.assertEqual(scenario, original)
        self.assertTrue(all(row["evaluation"]["task_success"] for row in rows))

    def test_zero_argument_factory_remains_compatible(self):
        scenario = self.scenario()
        rows = self.run_rows([scenario], agent_factory=lambda: environment_agent(scenario["environment"]))
        self.assertTrue(rows[0]["evaluation"]["task_success"])
        self.assertTrue(rows[0]["run_result"]["tool_results"][0]["output"]["found"])

    def test_candidate_and_metric_k_are_independent(self):
        scenario = self.scenario()
        rows = self.run_rows(
            [scenario], environment_agent_factory=environment_agent,
            top_k=20, metric_k=5,
        )
        row = rows[0]
        self.assertEqual(row["run_result"]["strategy_options"]["top_k"], 20)
        self.assertEqual(row["run_result"]["strategy_options"]["candidate_top_k"], 20)
        self.assertEqual(row["run_result"]["strategy_options"]["metric_k"], 5)
        self.assertEqual(row["evaluation"]["retrieval_k"], 5)

    def test_exactly_one_factory_is_required(self):
        for factories in ({}, {"agent_factory": Agent,
                                "environment_agent_factory": environment_agent}):
            with self.subTest(factories=list(factories)):
                with self.assertRaisesRegex(ValueError, "exactly one"):
                    self.run_rows([self.scenario()], **factories)
        with self.assertRaises(TypeError):
            self.run_rows([self.scenario()], environment_agent_factory=123)

    def test_malformed_environment_is_rejected(self):
        for environment in (None, [], {"files": []}, {"files": {"report.pdf": 10}},
                            {"valid_approval_ids": "approved_001"},
                            {"valid_approval_ids": [""]}):
            scenario = self.scenario()
            scenario["environment"] = environment
            with self.subTest(environment=environment):
                with self.assertRaises(ValueError):
                    self.run_rows([scenario], environment_agent_factory=environment_agent)

    def test_cli_environment_factory_runs_generated_input(self):
        scenario = self.scenario(task_type="approval", approval_status="granted")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenarios_path = root / "scenarios.json"
            scenarios_path.write_text(json.dumps({"scenarios": [scenario]}), encoding="utf-8")
            system_path = root / "system.txt"
            system_path.write_text("기억의 제약을 준수하세요.", encoding="utf-8")
            tools_path = root / "tools.txt"
            tools_path.write_text("email.request_approval email.send_email", encoding="utf-8")
            arguments = [
                "runner", "--scenarios", str(scenarios_path), "--strategies", "sliding_window",
                "--environment-agent-factory", "test_factory:build",
                "--system-prompt-file", str(system_path), "--tool-definitions-file", str(tools_path),
                "--output", str(root / "results"), "--bootstrap-iterations", "100",
                "--token-counter", "context.tokenizer:provisional_token_count",
            ]
            with patch.object(sys, "argv", arguments), patch(
                "benchmark.runner._import_callable", side_effect=lambda spec: (
                    provisional_token_count if spec == "context.tokenizer:provisional_token_count"
                    else environment_agent),
            ) as imported, redirect_stdout(io.StringIO()) as stdout:
                main()
            self.assertEqual([call.args[0] for call in imported.call_args_list],
                             ["context.tokenizer:provisional_token_count", "test_factory:build"])
            report = json.loads(stdout.getvalue())
            self.assertEqual(report["n_runs"], 1)
            row = json.loads((root / "results" / "runs.jsonl").read_text(encoding="utf-8"))
            self.assertTrue(row["evaluation"]["task_success"])
            self.assertTrue(row["run_result"]["tool_results"][0]["output"]["approved"])

    def test_cli_factory_options_are_required_and_mutually_exclusive(self):
        common = ["runner", "--system-prompt-file", "unused", "--tool-definitions-file", "unused"]
        for options in ([], ["--agent-factory", "some:factory", "--environment-agent-factory", "some:other"]):
            with self.subTest(options=options), patch.object(sys, "argv", common + options):
                with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                    main()
                self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
