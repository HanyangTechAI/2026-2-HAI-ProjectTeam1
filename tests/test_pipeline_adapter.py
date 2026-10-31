"""PipelineAdapter와 runner 연결을 검증한다. 실제 LLM은 호출하지 않는다.

실제 Agent, Orchestrator, mock Tool, 평가기를 사용한다.
결과 파일은 임시 폴더에만 생성하고 테스트 종료 후 삭제한다.
"""

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
import unittest

from agent.agent import Agent, AgentAction
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from benchmark.evaluator import aggregate_results, evaluate
from benchmark.runner import run_scenarios
from context.tokenizer import provisional_token_count
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus
from pipeline.orchestrator import PipelineRunResult, make_baseline_policy
from pipeline.pipeline_adapter import PipelineAdapter, make_baseline_pipeline_adapter


NOW = datetime(2026, 10, 29, tzinfo=timezone.utc)
SESSION = "adapter_test"
QUERY = "현재 보고서 파일을 찾아줘."
SYSTEM = "주어진 기억에서 파일을 찾는다."
TOOLS = "file.find_file(filename)"
FILENAME = "report_final.pdf"


class FeedbackPlanner:
    """선택된 파일을 조회하고, 다음 Context의 Tool 결과를 확인한 뒤 종료한다."""

    def __init__(self):
        self.contexts = []

    def plan(self, context):
        self.contexts.append(context)
        if "[RECENT TOOL RESULTS]" in context:
            return AgentAction(None, None, {}, FILENAME)
        filename = re.search(r"[\w.-]+\.pdf", context).group()
        return AgentAction("file", "find_file", {"filename": filename}, None)


def goal_checker(response, calls, results):
    return bool(
        response == FILENAME
        and len(calls) == len(results) == 1
        and results[0].success
        and results[0].output.get("found")
    )


def embed(text):
    """검색 연결 검증용 고정 embedding이며 모델 성능을 측정하지 않는다."""
    return (float("보고서" in text), 1.0)


class PipelineAdapterTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryItem(
            memory_id="report_final", session_id=SESSION, turn_id=3,
            memory_type=MemoryType.STATE, content=f"현재 보고서는 {FILENAME}이다.",
            memory_key="report.current_file", token_count=100,
            created_at=NOW, valid_from=NOW,
        )
        self.scenario = {
            "scenario_id": "adapter_integration", "session_id": SESSION,
            "query": QUERY, "current_turn": 10, "context_budget": 1000,
            "memory_snapshot": [self.memory.to_dict()],
            "target_memory_ids": ["report_final"],
            "required_state_ids": ["report_final"],
            "required_states": [{
                "memory_id": "report_final", "state_scope": "current",
                "tool_call": {"tool_name": "file", "action": "find_file"},
                "argument": "filename", "expected_value": FILENAME,
            }],
            "applicable_constraint_ids": [], "constraint_rules": [],
            "expected_tool_calls": [{
                "tool_name": "file", "action": "find_file",
                "arguments": {"filename": FILENAME},
            }],
        }
        self.agents = []

    def factory(self):
        agent = Agent(
            planner=FeedbackPlanner(),
            executor=ToolExecutor(file=FileTool({FILENAME: "보고서 내용"})),
            goal_checker=goal_checker, clock=lambda: NOW,
        )
        self.agents.append(agent)
        return agent

    def adapter(self, **options):
        return make_baseline_pipeline_adapter(
            "sliding_window", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory, **options,
        )

    def call_adapter(self, adapter, budget=1000):
        return adapter(
            query=QUERY, session_id=SESSION, memories=(self.memory,),
            total_budget=budget, current_turn=10,
        )

    def run_runner(self, output, **options):
        settings = dict(
            agent_factory=self.factory, system_prompt=SYSTEM,
            tool_definitions=TOOLS, output_directory=output,
            strategies=("sliding_window",), bootstrap_iterations=100,
        )
        settings.update(options)
        return run_scenarios([self.scenario], **settings)

    def test_execution_inputs_reach_policy_and_feedback_reaches_agent(self):
        """입력 다섯 항목과 공통 설정이 실제 정책·Agent 실행까지 전달되는지 확인한다."""
        received = []
        baseline = make_baseline_policy("sliding_window")

        def policy(**kwargs):
            received.append(kwargs)
            return baseline(**kwargs)

        adapter = PipelineAdapter(
            policy=policy, system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory,
        )
        outcome = self.call_adapter(adapter)
        self.assertIsInstance(outcome, PipelineRunResult)
        self.assertEqual(outcome.status, PipelineStatus.OK)
        self.assertTrue(outcome.goal_completed)
        self.assertEqual(len(outcome.steps), 2)
        self.assertEqual(received[0]["query"], QUERY)
        self.assertEqual(received[0]["memories"], (self.memory,))
        self.assertEqual(received[0]["session_id"], SESSION)
        self.assertEqual(received[0]["total_budget"], 1000)
        self.assertEqual(received[0]["current_turn"], 10)
        self.assertIs(received[0]["token_counter"], provisional_token_count)
        self.assertGreater(received[1]["current_turn"], received[0]["current_turn"])
        self.assertIn("[RECENT TOOL RESULTS]", self.agents[0].planner.contexts[1])
        self.assertEqual(outcome.tool_calls[0].arguments["filename"], FILENAME)
        self.assertEqual(outcome.final_turn, 11)

    def test_adapter_creates_fresh_agent_for_each_call(self):
        adapter = self.adapter()
        outcomes = [self.call_adapter(adapter), self.call_adapter(adapter)]
        self.assertEqual(len(self.agents), 2)
        self.assertIsNot(self.agents[0], self.agents[1])
        for outcome in outcomes:
            self.assertTrue(outcome.goal_completed)
            self.assertEqual(len(outcome.tool_calls), 1)
            self.assertEqual(outcome.final_turn, 11)

    def test_initial_budget_failure_skips_planner_and_embedding(self):
        def forbidden_embedder(_):
            self.fail("예산 중단 전에 embedding을 호출하면 안 된다.")

        adapter = make_baseline_pipeline_adapter(
            "vector_top_k", embedder=forbidden_embedder,
            system_prompt=SYSTEM, tool_definitions=TOOLS, agent_factory=self.factory,
        )
        outcome = self.call_adapter(adapter, budget=0)
        self.assertEqual(outcome.status, PipelineStatus.INVALID_BUDGET_CONFIGURATION)
        self.assertFalse(outcome.agent_executed)
        self.assertEqual(outcome.total_input_tokens, 0)
        self.assertEqual(self.agents[0].planner.contexts, [])
        self.assertEqual(self.agents[0].executor.calls, [])

    def test_invalid_policy_and_agent_factory_are_rejected(self):
        with self.assertRaisesRegex(TypeError, "policy must be callable"):
            PipelineAdapter(policy=None, system_prompt=SYSTEM, tool_definitions=TOOLS)
        adapter = make_baseline_pipeline_adapter(
            "sliding_window", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=lambda: object(),
        )
        with self.assertRaisesRegex(TypeError, "Agent instance"):
            self.call_adapter(adapter)

    def test_runner_saves_all_baselines_and_replays_evaluations(self):
        """세 전략의 실행 결과와 저장된 집계를 독립적으로 재평가한다."""
        before = copy.deepcopy(self.scenario)
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(
                output, strategies=("sliding_window", "vector_top_k", "utility_per_token"),
                embedder=embed, repeats=2,
            )
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
            summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
            self.assertEqual(result["n_runs"], 6)
            self.assertEqual(len(self.agents), 6)
            self.assertEqual(len({id(agent) for agent in self.agents}), 6)
            self.assertEqual(len(summary), 3)
            for row in rows:
                run = row["run_result"]
                self.assertTrue(row["evaluation"]["task_success"])
                self.assertEqual(len(run["steps"]), 2)
                self.assertEqual(run["final_turn"], 11)
                costs = [step["context_result"]["total_input_tokens"] for step in run["steps"]]
                self.assertEqual(run["usage"]["input_tokens"], max(costs))
                self.assertEqual(run["usage"]["total_input_tokens"], sum(costs))
                self.assertEqual(run["context_result"]["selected_memory_ids"], ["report_final"])
                replay = evaluate(
                    row["scenario"], run, strategy=row["strategy"], repeat_id=row["repeat_id"],
                )
                self.assertEqual(replay.to_dict(), row["evaluation"])
            self.assertEqual(
                aggregate_results([row["evaluation"] for row in rows], bootstrap_iterations=100),
                summary,
            )
        self.assertEqual(self.scenario, before)

    def test_runner_preserves_executed_step_before_feedback_budget_failure(self):
        """후속 단계가 예산으로 중단돼도 이전 행동과 누적 사용량을 보존한다."""
        first = self.call_adapter(self.adapter(token_counter=len, max_steps=1))
        first_cost = first.steps[0].context_result.total_input_tokens
        self.scenario["context_budget"] = first_cost
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, token_counter=len)
            row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
        run = row["run_result"]
        self.assertEqual(run["status"], "invalid_budget_configuration")
        self.assertEqual(len(run["steps"]), 2)
        self.assertTrue(run["agent_executed"])
        self.assertEqual(len(run["tool_calls"]), 1)
        self.assertIsNone(run["steps"][1]["action_result"])
        self.assertEqual(run["usage"]["total_input_tokens"], first_cost)
        self.assertIsNone(run["usage"]["input_tokens"])
        self.assertFalse(row["evaluation"]["task_success"])

    def test_max_steps_keeps_task_incomplete(self):
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, max_steps=1)
            row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
        self.assertEqual(row["run_result"]["stopped_reason"], "max_steps")
        self.assertEqual(len(row["run_result"]["tool_calls"]), 1)
        self.assertFalse(row["run_result"]["goal_completed"])
        self.assertFalse(row["evaluation"]["task_success"])


if __name__ == "__main__":
    unittest.main()
