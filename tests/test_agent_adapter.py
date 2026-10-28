
import json
import tempfile
import unittest

from pathlib import Path

from agent.agent import Agent, AgentRunResult
from agent.planner import Planner
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool

from benchmark.runner import (
    _import_callable,
    run_scenarios,
)

from pipeline.agent_adapter import (
    SingleStepAgentAdapter,
    make_agent_adapter,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_FILE = ROOT / "benchmark" / "scenarios.json"

SYSTEM_PROMPT = (
    "주어진 기억을 참고해 올바른 Tool을 실행한다."
)

TOOL_DEFINITIONS = (
    "file.find_file(filename)\n"
    "file.select_file(filename)\n"
    "email.request_approval(recipient, attachment=None)\n"
    "email.send_email(recipient, attachment=None)"
)

SESSION_ID = "agent_adapter_test"


def load_scenario(scenario_id: str) -> dict:
    payload = json.loads(
        SCENARIOS_FILE.read_text(encoding="utf-8")
    )

    scenarios = (
        payload["scenarios"]
        if isinstance(payload, dict)
        else payload
    )

    for scenario in scenarios:
        if scenario["scenario_id"] == scenario_id:
            return scenario

    raise ValueError(
        f"scenario not found: {scenario_id}"
    )


def make_test_adapter() -> SingleStepAgentAdapter:
    """
    테스트용 고정 Tool 환경.

    Runner가 시나리오별 환경을 자동 구성하는 것은
    아직 미구현이므로 이 테스트에서는 명시적으로 주입한다.
    """
    executor = ToolExecutor(
        file=FileTool({
            "report_final.pdf": "final report",
            "report_v1.pdf": "old report",
            "report_v2.pdf": "intermediate report",
        })
    )

    agent = Agent(
        executor=executor,
        planner=Planner(),
    )

    return SingleStepAgentAdapter(agent)


class AgentAdapterIntegrationTests(unittest.TestCase):

    def test_adapter_is_callable_and_returns_agent_run_result(self):
        """
        C Runner가 요구하는 Callable 인터페이스에 따라
        실제 B Agent의 실행 결과를 반환하는지 검증한다.
        """

        adapter = make_test_adapter()

        context = (
            "[SYSTEM]\n"
            "파일을 찾아라.\n\n"
            "[TOOL DEFINITIONS]\n"
            "file.find_file(filename)\n\n"
            "[STATE]\n"
            "- 현재 보고서는 report_final.pdf이다.\n\n"
            "[CURRENT TASK]\n"
            "보고서 파일 찾아줘."
        )

        result = adapter(
            context,
            session_id=SESSION_ID,
        )

        self.assertIsInstance(
            result,
            AgentRunResult,
        )

        self.assertEqual(
            result.stopped_reason,
            "tool_called",
        )

        self.assertFalse(
            result.goal_completed,
        )

        self.assertEqual(
            len(result.tool_calls),
            1,
        )

        self.assertEqual(
            len(result.tool_results),
            1,
        )

        self.assertEqual(
            result.tool_calls[0].tool_name,
            "file",
        )

        self.assertEqual(
            result.tool_calls[0].action,
            "find_file",
        )

        self.assertEqual(
            result.tool_calls[0].arguments["filename"],
            "report_final.pdf",
        )

        self.assertTrue(
            result.tool_results[0].success,
        )

        self.assertTrue(
            result.tool_results[0].output["found"],
        )

        self.assertEqual(
            len(result.interactions),
            1,
        )

        self.assertEqual(
            result.interactions[0].source,
            "tool",
        )

    def test_agent_factory_creates_independent_agents(self):
        """
        Runner가 전략별/반복별로 새로운 Adapter를
        생성해 Tool 상태가 공유되지 않는지 검증한다.
        """

        first = make_agent_adapter()
        second = make_agent_adapter()

        self.assertIsNot(
            first,
            second,
        )

        self.assertIsNot(
            first.agent,
            second.agent,
        )

        self.assertIsNot(
            first.agent.executor,
            second.agent.executor,
        )

        context = (
            "[CURRENT TASK]\n"
            "report_final.pdf 파일을 찾아줘."
        )

        first(
            context,
            session_id=SESSION_ID,
        )

        self.assertEqual(
            len(first.agent.executor.calls),
            1,
        )

        self.assertEqual(
            len(second.agent.executor.calls),
            0,
        )

    def test_agent_factory_can_be_imported_by_runner(self):
        """
        CLI의 module:function 방식으로
        Agent Factory가 로딩되는지 검증한다.
        """

        factory = _import_callable(
            "pipeline.agent_adapter:make_agent_adapter"
        )

        self.assertTrue(
            callable(factory)
        )

        adapter = factory()

        self.assertTrue(
            callable(adapter)
        )

        self.assertIsInstance(
            adapter,
            SingleStepAgentAdapter,
        )

    def test_runner_agent_adapter_smoke(self):
        """
        C Runner
          → Baseline
          → Context Builder
          → AgentAdapter
          → B Agent / Mock Tool
          → Evaluator
          → runs.jsonl / summary.json

        전체 단일 행동 통합을 검증한다.
        """

        scenario = load_scenario("S02")

        with tempfile.TemporaryDirectory() as temp_dir:

            result = run_scenarios(
                [scenario],
                agent_factory=make_test_adapter,
                system_prompt=SYSTEM_PROMPT,
                tool_definitions=TOOL_DEFINITIONS,
                output_directory=temp_dir,
                strategies=("sliding_window",),
                repeats=1,
                bootstrap_iterations=100,
                seed=42,
            )

            self.assertEqual(
                result["n_runs"],
                1,
            )

            self.assertEqual(
                result["n_scenarios"],
                1,
            )

            runs_path = Path(
                result["runs_path"]
            )

            summary_path = Path(
                result["summary_path"]
            )

            self.assertTrue(
                runs_path.exists()
            )

            self.assertTrue(
                summary_path.exists()
            )

            rows = [
                json.loads(line)
                for line in runs_path.read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            self.assertEqual(
                len(rows),
                1,
            )

            row = rows[0]
            run = row["run_result"]
            evaluation = row["evaluation"]

            self.assertEqual(
                row["scenario"]["scenario_id"],
                "S02",
            )

            self.assertEqual(
                row["strategy"],
                "sliding_window",
            )

            self.assertEqual(
                run["status"],
                "ok",
            )

            self.assertTrue(
                run["agent_executed"],
            )

            self.assertEqual(
                run["stopped_reason"],
                "tool_called",
            )

            self.assertEqual(
                len(run["tool_calls"]),
                1,
            )

            self.assertEqual(
                run["tool_calls"][0]["tool_name"],
                "file",
            )

            self.assertEqual(
                run["tool_calls"][0]["action"],
                "find_file",
            )

            self.assertEqual(
                run["tool_calls"][0]["arguments"]["filename"],
                "report_final.pdf",
            )

            self.assertEqual(
                len(run["tool_results"]),
                1,
            )

            self.assertTrue(
                run["tool_results"][0]["success"],
            )

            self.assertTrue(
                run["tool_results"][0]["output"]["found"],
            )

            self.assertTrue(
                evaluation["expected_tool_calls_present"],
            ) if "expected_tool_calls_present" in evaluation else self.assertTrue(
                evaluation["metrics"]["expected_tool_calls_present"]
            )

            self.assertLessEqual(
                run["usage"]["input_tokens"],
                scenario["context_budget"],
            )

            # 현재 AgentAdapter는 단일 Action만 수행한다.
            # Tool 호출 성공만으로 전체 Task 성공을
            # 선언해서는 안 된다.
            self.assertFalse(
                run["goal_completed"],
            )

            self.assertFalse(
                evaluation["task_success"],
            )

    def test_runner_repeats_use_fresh_adapter_instances(self):
        """
        같은 시나리오를 반복해도 새로운 Agent와 Tool이
        사용되는지 검증한다.
        """

        scenario = load_scenario("S02")

        adapters = []

        def tracking_factory():
            adapter = make_test_adapter()
            adapters.append(adapter)
            return adapter

        with tempfile.TemporaryDirectory() as temp_dir:

            result = run_scenarios(
                [scenario],
                agent_factory=tracking_factory,
                system_prompt=SYSTEM_PROMPT,
                tool_definitions=TOOL_DEFINITIONS,
                output_directory=temp_dir,
                strategies=("sliding_window",),
                repeats=2,
                bootstrap_iterations=100,
                seed=42,
            )

            self.assertEqual(
                result["n_runs"],
                2,
            )

            self.assertEqual(
                len(adapters),
                2,
            )

            self.assertIsNot(
                adapters[0].agent,
                adapters[1].agent,
            )

            for adapter in adapters:
                self.assertEqual(
                    len(adapter.agent.executor.calls),
                    1,
                )

            rows = [
                json.loads(line)
                for line in Path(
                    result["runs_path"]
                ).read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            self.assertEqual(
                {row["repeat_id"] for row in rows},
                {0, 1},
            )

            self.assertEqual(
                len(rows),
                2,
            )

    def test_budget_failure_prevents_adapter_creation(self):
        """
        고정 입력만으로 Budget 초과 시
        AgentAdapter가 생성되거나 실행되면 안 된다.
        """

        scenario = dict(
            load_scenario("S02")
        )

        scenario["context_budget"] = 1

        factory_calls = []

        def tracking_factory():
            factory_calls.append(True)
            return make_test_adapter()

        with tempfile.TemporaryDirectory() as temp_dir:

            result = run_scenarios(
                [scenario],
                agent_factory=tracking_factory,
                system_prompt=SYSTEM_PROMPT,
                tool_definitions=TOOL_DEFINITIONS,
                output_directory=temp_dir,
                strategies=("sliding_window",),
                repeats=1,
                bootstrap_iterations=100,
                seed=42,
            )

            self.assertEqual(
                factory_calls,
                [],
            )

            rows = [
                json.loads(line)
                for line in Path(
                    result["runs_path"]
                ).read_text(
                    encoding="utf-8"
                ).splitlines()
                if line.strip()
            ]

            run = rows[0]["run_result"]

            self.assertEqual(
                run["status"],
                "invalid_budget_configuration",
            )

            self.assertFalse(
                run["agent_executed"],
            )

            self.assertEqual(
                run["tool_calls"],
                [],
            )

            self.assertFalse(
                run["goal_completed"],
            )


if __name__ == "__main__":
    unittest.main()
