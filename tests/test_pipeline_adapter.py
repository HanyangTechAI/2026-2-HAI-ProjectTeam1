"""PipelineAdapter와 runner 연결을 검증한다. 실제 LLM은 호출하지 않는다.

실제 Agent, Orchestrator, mock Tool, 평가기를 사용한다.
결과 파일은 임시 폴더에만 생성하고 테스트 종료 후 삭제한다.
"""

import copy
from contextlib import redirect_stderr, redirect_stdout
import io
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import asdict, fields, replace
import json
import os
from unittest.mock import Mock, patch
from types import SimpleNamespace
from pathlib import Path
import re
import tempfile
import unittest

from agent.agent import Agent, AgentAction
from agent.tools.executor import ToolExecutor
from agent.tools.file import FileTool
from benchmark.evaluator import aggregate_results, evaluate
from benchmark.runner import (
    FROZEN_ALGORITHM_SHA256, FROZEN_PLANNER_BEHAVIOR_SHA256,
    STRATEGIES, _run_one, checkpoint_status, main, run_scenarios,
)
from agent.llm_planner import LLMRunConfig
from context.tokenizer import (
    GeminiTokenCounter, TARGET_GEMINI_MODEL, provisional_token_count,
    token_count_mode,
)
from memory.query_analyzer import analyze_query, TemporalIntent
from memory.ablation import ABLATION_PRESETS
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus
from pipeline.orchestrator import PipelineRunResult, make_baseline_policy
from pipeline.pipeline_adapter import PipelineAdapter, make_baseline_pipeline_adapter, make_pipeline_adapter


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
            "query": QUERY, "current_turn": 10, "current_time": NOW.isoformat(), "context_budget": 1000,
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
            total_budget=budget, current_turn=10, current_time=NOW,
        )

    def run_runner(self, output, **options):
        settings = dict(
            agent_factory=self.factory, system_prompt=SYSTEM,
            tool_definitions=TOOLS, output_directory=output,
            strategies=("sliding_window",), bootstrap_iterations=100,
            token_counter=provisional_token_count,
        )
        settings.update(options)
        if settings.get("strategies") is None:
            settings.pop("strategies")
        scenario = copy.deepcopy(self.scenario)
        counter = settings["token_counter"]
        scenario["token_count_mode"] = (f"gemini:{os.getenv('GEMINI_TOKEN_MODEL', TARGET_GEMINI_MODEL)}"
                                        if counter is None else token_count_mode(counter))
        return run_scenarios([scenario], **settings)

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

    def test_runner_saves_all_four_strategies_and_replays_evaluations(self):
        """네 전략의 실행 결과와 저장된 집계를 독립적으로 재평가한다."""
        before = copy.deepcopy(self.scenario)
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(
                output, strategies=None,
                embedder=embed, repeats=2,
            )
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
            summary = json.loads(Path(result["summary_path"]).read_text(encoding="utf-8"))
            experiment = json.loads(
                (Path(output) / "experiment.json").read_text(encoding="utf-8")
            )
            self.assertEqual(result["n_runs"], 8)
            self.assertEqual(len(self.agents), 8)
            self.assertEqual(len({id(agent) for agent in self.agents}), 8)
            self.assertEqual(len(summary), 4)
            self.assertEqual({row["strategy"] for row in rows}, set(STRATEGIES))
            self.assertEqual(experiment["algorithm_hash"], FROZEN_ALGORITHM_SHA256)
            self.assertEqual(
                experiment["planner_rules_hash"],
                FROZEN_PLANNER_BEHAVIOR_SHA256["SYSTEM_INSTRUCTION"],
            )
            self.assertEqual(
                experiment["action_schema_hash"],
                FROZEN_PLANNER_BEHAVIOR_SHA256["ACTION_SCHEMA"],
            )
            for row in rows:
                run = row["run_result"]
                self.assertTrue(row["evaluation"]["task_success"])
                self.assertIs(run["mandatory_memory_overflow"], False)
                self.assertIs(row["evaluation"]["mandatory_memory_overflow"], False)
                self.assertEqual(len(run["steps"]), 2)
                self.assertEqual(run["final_turn"], 11)
                costs = [step["context_result"]["total_input_tokens"] for step in run["steps"]]
                self.assertEqual(run["usage"]["input_tokens"], max(costs))
                peak_step = max(
                    run["steps"],
                    key=lambda step:
                        step["context_result"]["total_input_tokens"],
                )
                self.assertEqual(run["usage"]["fixed_input_tokens"], peak_step["fixed_tokens"])
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

    def test_runner_defaults_to_gemini_for_all_four_strategies(self):
        def mock_count(*, model, contents):
            # SDK 응답 계약 검증용이며 실제 Gemini 토큰 수가 아니다.
            return SimpleNamespace(total_tokens=len(contents.split()) + 1)

        from unittest.mock import Mock
        client = SimpleNamespace(models=SimpleNamespace(count_tokens=Mock(side_effect=mock_count)))
        google = SimpleNamespace(genai=SimpleNamespace(Client=lambda **kwargs: client))
        with tempfile.TemporaryDirectory() as output, patch.dict(
            os.environ, {"GEMINI_API_KEY": "test-key"}, clear=True,
        ), patch.dict("sys.modules", {"google": google}):
            result = self.run_runner(output, strategies=None, token_counter=None, embedder=embed)
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
            self.assertEqual(result["n_runs"], 4)
            self.assertGreater(client.models.count_tokens.call_count, 0)
            for row in rows:
                run = row["run_result"]
                self.assertTrue(row["evaluation"]["task_success"])
                self.assertEqual(run["token_count_mode"], f"gemini:{TARGET_GEMINI_MODEL}")
                self.assertEqual(run["token_counter_metadata"]["model"], TARGET_GEMINI_MODEL)
                self.assertEqual(run["token_counter_metadata"]["request_shape"], "single_user_text")
                self.assertNotIn("test-key", json.dumps(row))

    def test_fairness_same_inputs_settings_and_schema_for_all_policies(self):
        """정답 fixture 누출 없이 같은 설정·스냅샷·단계 입력을 전달한다."""
        foreign = replace(self.memory, memory_id="foreign", session_id="other")
        future = replace(self.memory, memory_id="future", turn_id=100)
        self.scenario["memory_snapshot"].extend([foreign.to_dict(), future.to_dict()])
        self.scenario["assertions"] = {"selected_memory_ids": ["oracle_only"]}
        self.scenario["context_fixture"] = {"initial_selected_memory_ids": ["oracle_only"]}
        before = copy.deepcopy(self.scenario)
        settings = {}
        received = {strategy: [] for strategy in STRATEGIES}

        def audited_adapter(strategy, **kwargs):
            settings[strategy] = {key: value for key, value in kwargs.items() if key != "ablation"}
            adapter = make_pipeline_adapter(strategy, **kwargs)
            policy = adapter.policy

            def audit_policy(**inputs):
                received[strategy].append(dict(inputs))
                return policy(**inputs)

            adapter.policy = audit_policy
            return adapter

        with tempfile.TemporaryDirectory() as output, patch(
            "benchmark.runner.make_pipeline_adapter", side_effect=audited_adapter,
        ):
            result = self.run_runner(
                output, strategies=STRATEGIES, embedder=embed, top_k=7, max_steps=3,
                utility_weights=(0.5, 0.4, 0.1), goal_checker=goal_checker,
            )
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]

        reference_settings = settings[STRATEGIES[0]]
        reference_inputs = received[STRATEGIES[0]]
        reference = rows[0]
        self.assertEqual(self.scenario, before)
        self.assertEqual(len(self.agents), 4)
        self.assertEqual(len({id(agent.planner) for agent in self.agents}), 4)
        self.assertEqual(len({id(agent.executor) for agent in self.agents}), 4)
        for strategy in STRATEGIES:
            self.assertEqual(settings[strategy], reference_settings)
            self.assertIs(settings[strategy]["token_counter"], provisional_token_count)
            self.assertIs(settings[strategy]["embedder"], embed)
            self.assertIs(settings[strategy]["goal_checker"], goal_checker)
            self.assertEqual(received[strategy], reference_inputs)
            self.assertEqual(len(received[strategy]), 2)
            for inputs in received[strategy]:
                self.assertEqual(set(inputs), {
                    "query", "session_id", "memories", "total_budget", "fixed_tokens",
                    "current_turn", "current_time", "token_counter",
                })
                self.assertEqual(inputs["memories"], (self.memory,))
                self.assertEqual(inputs["total_budget"], 1000)
        for row in rows:
            self.assertEqual(set(row), set(reference))
            self.assertEqual(set(row["run_result"]), set(reference["run_result"]))
            self.assertEqual(set(row["evaluation"]), set(reference["evaluation"]))
            for key in ("selection_result", "context_result", "usage", "strategy_options"):
                self.assertEqual(set(row["run_result"][key]), set(reference["run_result"][key]))
            self.assertTrue(row["evaluation"]["task_success"])
            self.assertEqual(len(row["run_result"]["steps"]), 2)
            replay = evaluate(row["scenario"], row["run_result"], strategy=row["strategy"], k=7)
            self.assertEqual(replay.to_dict(), row["evaluation"])

    def test_fairness_identical_policy_produces_identical_pipeline_and_evaluation(self):
        """전략 이름만 바꾸고 선택 정책을 고정하면 실행 결과 전체가 같아야 한다."""
        control_policy = make_baseline_policy("sliding_window")
        outcomes = []
        for strategy in STRATEGIES:
            adapter = make_pipeline_adapter(
                strategy, system_prompt=SYSTEM, tool_definitions=TOOLS,
                agent_factory=self.factory, embedder=embed,
            )
            adapter.policy = control_policy
            outcome = self.call_adapter(adapter)
            self.assertIs(type(outcome), PipelineRunResult)
            self.assertEqual(set(asdict(outcome)), {field.name for field in fields(PipelineRunResult)})
            outcomes.append(outcome)
        self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))

        def control_adapter(strategy, **options):
            adapter = make_pipeline_adapter(strategy, **options)
            adapter.policy = control_policy
            return adapter

        with tempfile.TemporaryDirectory() as output, patch(
            "benchmark.runner.make_pipeline_adapter", side_effect=control_adapter,
        ):
            result = self.run_runner(output, strategies=STRATEGIES, embedder=embed)
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
        controls = []
        for row in rows:
            run = dict(row["run_result"])
            run.pop("total_latency_ms")
            controls.append((run, evaluate(row["scenario"], run, strategy="control").to_dict()))
        self.assertTrue(all(control == controls[0] for control in controls))
        # Sliding Window의 검색 지표 비적용은 기존 evaluator 계약 그대로 유지한다.
        for row in rows:
            self.assertEqual(row["evaluation"]["retrieval_applicable"], row["strategy"] != "sliding_window")

    def test_fairness_actual_policy_difference_changes_only_memory_driven_behavior(self):
        old = replace(
            self.memory, memory_id="old", content="이전 보고서는 report_old.pdf이다.",
            turn_id=1, is_active=False, created_at=NOW - timedelta(days=2),
            valid_from=NOW - timedelta(days=2), valid_to=NOW,
            superseded_by=self.memory.memory_id,
        )
        current = replace(self.memory, supersedes="old")
        self.scenario["memory_snapshot"] = [old.to_dict(), current.to_dict()]
        before = copy.deepcopy(self.scenario)

        def retrieve_old(text):
            return (1.0, 0.0) if text == QUERY or "report_old.pdf" in text else (0.0, 1.0)

        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, strategies=STRATEGIES, embedder=retrieve_old, top_k=1)
            rows = {row["strategy"]: row for row in (
                json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()
            )}
        for strategy in STRATEGIES:
            row = rows[strategy]
            use_current = strategy in ("sliding_window", "proposed")
            self.assertEqual(row["run_result"]["tool_calls"][0]["arguments"]["filename"],
                             FILENAME if use_current else "report_old.pdf")
            self.assertEqual(row["evaluation"]["task_success"], use_current)
            self.assertEqual(set(row["run_result"]), set(rows["proposed"]["run_result"]))
            self.assertEqual(set(row["evaluation"]), set(rows["proposed"]["evaluation"]))
        self.assertEqual(rows["proposed"]["run_result"]["steps"][0]["selection_result"]["retrieved_memory_ids"], ["old"])
        self.assertEqual(rows["proposed"]["run_result"]["steps"][0]["context_result"]["selected_memory_ids"], ["report_final"])
        self.assertEqual(self.scenario, before)

    def test_fairness_budget_failure_has_common_result_and_evaluator_schema(self):
        def forbidden_embedder(_):
            self.fail("고정 입력 예산 실패 시 검색하면 안 된다")

        outcomes = []
        for strategy in STRATEGIES:
            adapter = make_pipeline_adapter(
                strategy, system_prompt=SYSTEM, tool_definitions=TOOLS,
                agent_factory=self.factory, embedder=forbidden_embedder,
            )
            outcome = self.call_adapter(adapter, budget=0)
            self.assertIs(type(outcome), PipelineRunResult)
            self.assertFalse(outcome.agent_executed)
            outcomes.append(outcome)
        self.assertTrue(all(outcome == outcomes[0] for outcome in outcomes))
        self.scenario["context_budget"] = 0
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, strategies=STRATEGIES, embedder=forbidden_embedder)
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
        for row in rows:
            self.assertEqual(row["run_result"]["status"], "invalid_budget_configuration")
            self.assertEqual(set(row["run_result"]), set(rows[0]["run_result"]))
            self.assertEqual(set(row["evaluation"]), set(rows[0]["evaluation"]))
            self.assertFalse(row["evaluation"]["task_success"])
            self.assertFalse(row["run_result"]["agent_executed"])
            self.assertIsNone(row["run_result"]["usage"]["input_tokens"])
            self.assertIsNone(row["run_result"]["mandatory_memory_overflow"])
            self.assertIsNone(row["evaluation"]["mandatory_memory_overflow"])
            self.assertEqual(evaluate(row["scenario"], row["run_result"], strategy=row["strategy"]).to_dict(), row["evaluation"])
        for agent in self.agents:
            self.assertEqual(agent.planner.contexts, [])
            self.assertEqual(agent.executor.calls, [])

    def test_fairness_gemini_cache_is_reset_and_strategy_order_does_not_change_results(self):
        method = Mock(side_effect=lambda **kwargs: SimpleNamespace(total_tokens=len(kwargs["contents"].split()) + 1))
        counter = GeminiTokenCounter(
            model="test-model", client=SimpleNamespace(models=SimpleNamespace(count_tokens=method)),
        )
        api_counts = {}

        def audited_run(scenario, strategy, repeat_id, **options):
            before = method.call_count
            row = _run_one(scenario, strategy, repeat_id, **options)
            api_counts.setdefault(strategy, []).append(method.call_count - before)
            return row

        counter("preexisting cached text")
        orders = []
        with tempfile.TemporaryDirectory() as output, patch(
            "benchmark.runner._run_one", side_effect=audited_run,
        ), patch.object(counter, "clear_cache", wraps=counter.clear_cache) as clear:
            for order in (STRATEGIES, tuple(reversed(STRATEGIES))):
                result = self.run_runner(Path(output) / order[0], strategies=order, embedder=embed,
                                         token_counter=counter, repeats=2)
                rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
                normalized = {}
                for row in rows:
                    row["run_result"].pop("total_latency_ms")
                    row["evaluation"].pop("total_latency_ms")
                    normalized[(row["strategy"], row["repeat_id"])] = row
                orders.append(normalized)
            self.assertEqual(clear.call_count, 32)
        self.assertEqual(orders[0], orders[1])
        for counts in api_counts.values():
            self.assertGreater(counts[0], 0)
            self.assertEqual(counts, [counts[0]] * 4)

    def test_latest_ablation_variants_keep_common_execution_and_evaluator_schema(self):
        before = copy.deepcopy(self.scenario)
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(
                output, strategies=STRATEGIES, embedder=embed,
                proposed_ablations=tuple(ABLATION_PRESETS),
            )
            rows = [json.loads(line) for line in Path(result["runs_path"]).read_text(encoding="utf-8").splitlines()]
        expected = set(STRATEGIES) | {f"proposed/{name}" for name in ABLATION_PRESETS if name != "full"}
        self.assertEqual({row["strategy"] for row in rows}, expected)
        self.assertEqual(result["n_runs"], 8)
        for row in rows:
            self.assertEqual(set(row["run_result"]), set(rows[0]["run_result"]))
            self.assertEqual(set(row["evaluation"]), set(rows[0]["evaluation"]))
            if row["strategy"].startswith("proposed"):
                self.assertEqual(row["ablation"], ABLATION_PRESETS[
                    row["strategy"].split("/", 1)[1] if "/" in row["strategy"] else "full"
                ].to_dict())
            else:
                self.assertIsNone(row["ablation"])
            self.assertEqual(evaluate(row["scenario"], row["run_result"], strategy=row["strategy"]).to_dict(), row["evaluation"])
        self.assertEqual(self.scenario, before)

    def test_proposed_expands_top_k_to_current_version_and_recounts_tokens(self):
        old = replace(
            self.memory, memory_id="old", content="old.pdf", is_active=False,
            created_at=NOW - timedelta(days=2), valid_from=NOW - timedelta(days=2),
            valid_to=NOW, superseded_by=self.memory.memory_id,
        )
        current = replace(self.memory, supersedes="old")
        future = replace(self.memory, memory_id="future", turn_id=100)
        foreign = replace(self.memory, memory_id="foreign", session_id="other")

        def retrieve_old(text):
            return (1.0, 0.0) if text in (QUERY, "old.pdf") else (0.0, 1.0)

        adapter = make_pipeline_adapter(
            "proposed", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory, embedder=retrieve_old, top_k=1,
        )
        result = adapter(query=QUERY, session_id=SESSION,
                         memories=(old, current, future, foreign),
                         total_budget=1000, current_turn=10, current_time=NOW)
        self.assertTrue(result.goal_completed)
        for step in result.steps:
            self.assertEqual(step.selection_result.retrieved_memory_ids, ("old",))
            self.assertEqual(step.selection_result.selected_memory_ids, ("report_final",))
            self.assertEqual(step.selection_result.states[0].token_count,
                             provisional_token_count(current.content))
        self.assertEqual(current.token_count, 100)

    def test_proposed_historical_query_selects_inactive_version(self):
        old = replace(
            self.memory, memory_id="old", content="old.pdf", is_active=False,
            created_at=NOW - timedelta(days=2), valid_from=NOW - timedelta(days=2),
            valid_to=NOW, superseded_by=self.memory.memory_id,
        )
        adapter = make_pipeline_adapter(
            "proposed", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory, embedder=embed, max_steps=1,
        )
        result = adapter(query="2026-10-28 보고서 파일을 찾아줘", session_id=SESSION,
                         memories=(old, self.memory), total_budget=1000, current_turn=10, current_time=NOW)
        self.assertEqual(result.steps[0].selection_result.selected_memory_ids, ("old",))
        self.assertEqual(result.tool_calls[0].arguments["filename"], "old.pdf")

    def test_proposed_protected_outside_top_k_overflow_skips_agent(self):
        constraint = replace(
            self.memory, memory_id="constraint", memory_type=MemoryType.CONSTRAINT,
            memory_key="file.access", is_protected=True, content="파일 접근 승인 필요 " * 100,
        )
        adapter = make_pipeline_adapter(
            "proposed", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory, embedder=embed, top_k=1,
        )
        result = adapter(query=QUERY, session_id=SESSION,
                         memories=(self.memory, constraint), total_budget=100, current_turn=10, current_time=NOW)
        selection = result.steps[0].selection_result
        self.assertEqual(selection.retrieved_memory_ids, ("report_final",))
        self.assertEqual(selection.protected, (replace(
            constraint, token_count=provisional_token_count(constraint.content)),))
        self.assertEqual(result.status, PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET)
        self.assertFalse(result.agent_executed)
        self.assertEqual(self.agents[0].planner.contexts, [])
        self.assertEqual(self.agents[0].executor.calls, [])

    def test_proposed_unresolved_past_is_rejected_before_agent_execution(self):
        adapter = make_pipeline_adapter(
            "proposed", system_prompt=SYSTEM, tool_definitions=TOOLS,
            agent_factory=self.factory, embedder=embed, max_steps=1,
        )
        with self.assertRaisesRegex(ValueError, "시간 표현"):
            adapter(query="예전 보고서 파일", session_id=SESSION,
                    memories=(self.memory,), total_budget=1000, current_turn=10, current_time=NOW)
        self.assertEqual(self.agents[0].planner.contexts, [])

    def test_query_time_rules_cover_month_and_relative_month_boundary(self):
        now = NOW.replace(month=3, day=31)
        relative = analyze_query("1개월 전 보고서", 10, now)
        self.assertEqual(relative.temporal_intent, TemporalIntent.RELATIVE_TIME)
        self.assertEqual(relative.target_time, now.replace(month=2, day=28))
        month = analyze_query("2026년 10월 보고서", 10, NOW)
        self.assertEqual(month.target_range,
                         (NOW.replace(day=1), NOW.replace(month=11, day=1)))

    def test_proposed_requires_embedder(self):
        with self.assertRaisesRegex(TypeError, "embedder"):
            make_pipeline_adapter("proposed", system_prompt=SYSTEM, tool_definitions=TOOLS)

    def test_runner_records_protected_and_required_state_overflow(self):
        for kind in (MemoryType.CONSTRAINT, MemoryType.STATE):
            with self.subTest(kind=kind):
                if kind == MemoryType.CONSTRAINT:
                    oversized = replace(
                        self.memory, memory_id="large_constraint", memory_type=kind,
                        memory_key="file.access", is_protected=True,
                        content="파일 접근 승인 필요 " * 100,
                    )
                    snapshot = [self.memory, oversized]
                else:
                    oversized = replace(self.memory, content=self.memory.content + " 기억" * 100)
                    snapshot = [oversized]
                self.scenario["memory_snapshot"] = [memory.to_dict() for memory in snapshot]
                self.scenario["context_budget"] = 100
                with tempfile.TemporaryDirectory() as output:
                    result = self.run_runner(output, strategies=("proposed",), embedder=embed)
                    row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
                run = row["run_result"]
                self.assertEqual(run["selection_result"]["status"], "insufficient_context_budget")
                self.assertIsNone(run["usage"]["input_tokens"])
                self.assertEqual(run["usage"]["total_input_tokens"], 0)
                self.assertIs(run["mandatory_memory_overflow"], True)
                self.assertIs(row["evaluation"]["mandatory_memory_overflow"], True)
                self.assertFalse(run["agent_executed"])
                self.assertEqual(self.agents[-1].planner.contexts, [])
                self.assertEqual(self.agents[-1].executor.calls, [])
                self.assertEqual(evaluate(row["scenario"], run, strategy="proposed").to_dict(), row["evaluation"])

    def test_runner_records_mandatory_formatting_overflow(self):
        def formatted_count(text):
            return 500 if "[STATE]" in text else provisional_token_count(text)

        self.scenario["context_budget"] = 100
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, strategies=("proposed",), embedder=embed,
                                     token_counter=formatted_count)
            row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
        run = row["run_result"]
        self.assertEqual(run["selection_result"]["status"], "ok")
        self.assertEqual(run["context_result"]["status"], "insufficient_context_budget")
        self.assertIs(run["mandatory_memory_overflow"], True)
        self.assertIs(row["evaluation"]["mandatory_memory_overflow"], True)
        self.assertFalse(run["agent_executed"])

    def test_runner_does_not_treat_flexible_drop_as_mandatory_overflow(self):
        noise = replace(
            self.memory, memory_id="noise", memory_type=MemoryType.FACT,
            memory_key=None, turn_id=1, content="OVERSIZE_NOISE",
        )
        self.scenario["memory_snapshot"].append(noise.to_dict())

        def formatted_count(text):
            if "[SYSTEM]" in text and "OVERSIZE_NOISE" in text:
                return 500
            return provisional_token_count(text)

        self.scenario["context_budget"] = 100
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, token_counter=formatted_count)
            row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
        run = row["run_result"]
        self.assertEqual(run["steps"][0]["context_result"]["dropped_flexible_ids"], ["noise"])
        self.assertIs(run["mandatory_memory_overflow"], False)
        self.assertIs(row["evaluation"]["mandatory_memory_overflow"], False)
        self.assertTrue(row["evaluation"]["task_success"])

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
        self.assertIs(run["mandatory_memory_overflow"], False)
        self.assertIs(row["evaluation"]["mandatory_memory_overflow"], False)
        self.assertFalse(row["evaluation"]["task_success"])

    def test_max_steps_keeps_task_incomplete(self):
        with tempfile.TemporaryDirectory() as output:
            result = self.run_runner(output, max_steps=1)
            row = json.loads(Path(result["runs_path"]).read_text(encoding="utf-8"))
        self.assertEqual(row["run_result"]["stopped_reason"], "max_steps")
        self.assertEqual(len(row["run_result"]["tool_calls"]), 1)
        self.assertFalse(row["run_result"]["goal_completed"])
        self.assertFalse(row["evaluation"]["task_success"])


    def test_progress_counts_ablation_repeats_and_keeps_stdout_clean(self):
        stderr, stdout = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as output, redirect_stderr(stderr), redirect_stdout(stdout):
            result = self.run_runner(
                output, strategies=STRATEGIES, embedder=embed, repeats=2,
                proposed_ablations=tuple(ABLATION_PRESETS), show_progress=True,
            )
        self.assertEqual(result["n_runs"], 16)
        self.assertEqual(stdout.getvalue(), "")
        lines = stderr.getvalue().splitlines()
        self.assertIn("0/16 (0.0%)", lines[0])
        self.assertIn("16/16 (100.0%)", lines[-1])
        self.assertIn("saved", lines[-1])
        self.assertIn("proposed/no_temporal", stderr.getvalue())
        self.assertIn("repeat 2/2", stderr.getvalue())
        self.assertIn("ETA~", stderr.getvalue())
        silent = io.StringIO()
        with tempfile.TemporaryDirectory() as output, redirect_stderr(silent):
            self.run_runner(output, show_progress=False)
        self.assertEqual(silent.getvalue(), "")

    def test_progress_failure_preserves_completed_count_and_existing_results(self):
        calls = 0

        def fail_second(*args, **options):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("failed execution")
            return _run_one(*args, **options)

        stderr = io.StringIO()
        with tempfile.TemporaryDirectory() as output:
            prior = Path(output) / "runs.jsonl"
            with patch("benchmark.runner._run_one", side_effect=fail_second), redirect_stderr(stderr):
                with self.assertRaisesRegex(RuntimeError, "failed execution"):
                    self.run_runner(output, strategies=STRATEGIES, embedder=embed, show_progress=True)
            self.assertEqual(len(prior.read_text(encoding="utf-8").splitlines()), 1)
            status = checkpoint_status(output)
            self.assertEqual(status["state"], "interrupted")
            self.assertEqual(status["completed"], 1)
            self.assertFalse(status["runner_active"])
            with prior.open("ab") as stream:
                stream.write(b'{"torn_record":')
            with patch("benchmark.runner._run_one", wraps=_run_one) as resumed:
                result = self.run_runner(output, strategies=STRATEGIES, embedder=embed, resume=True)
            self.assertEqual(resumed.call_count, 3)
            rows = [json.loads(line) for line in prior.read_text().splitlines()]
            self.assertEqual(len(rows), 4)
            self.assertEqual(len({(r["strategy"], r["repeat_id"]) for r in rows}), 4)
            self.assertEqual(result["summary"], aggregate_results(
                [r["evaluation"] for r in rows], bootstrap_iterations=100, seed=42))
            self.assertEqual(checkpoint_status(output)["state"], "completed")
        self.assertIn("1/4 (25.0%)", stderr.getvalue().splitlines()[-1])
        self.assertIn("interrupted", stderr.getvalue().splitlines()[-1])
        self.assertNotIn("100.0%", stderr.getvalue())

    def test_checkpoint_rejects_overwrite_changed_settings_and_corrupt_records(self):
        with tempfile.TemporaryDirectory() as output:
            self.run_runner(output)
            saved = (Path(output) / "runs.jsonl").read_bytes()
            with self.assertRaisesRegex(ValueError, "already contains"):
                self.run_runner(output)
            with self.assertRaisesRegex(ValueError, "settings changed"):
                self.run_runner(output, resume=True, top_k=3)
            self.assertEqual((Path(output) / "runs.jsonl").read_bytes(), saved)
            with (Path(output) / "runs.jsonl").open("ab") as stream:
                stream.write(b'invalid JSON\n')
            with self.assertRaises(json.JSONDecodeError):
                self.run_runner(output, resume=True)

    def test_keyboard_interrupt_resume_preserves_variant_and_repeat_keys(self):
        count = 0
        def interrupt(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 4:
                raise KeyboardInterrupt()
            return _run_one(*args, **kwargs)
        options = dict(strategies=("proposed",), embedder=embed, repeats=2,
                       proposed_ablations=tuple(ABLATION_PRESETS)[:2], checkpoint_interval=2)
        with tempfile.TemporaryDirectory() as output:
            with patch("benchmark.runner._run_one", side_effect=interrupt):
                with self.assertRaises(KeyboardInterrupt):
                    self.run_runner(output, **options)
            self.assertEqual(checkpoint_status(output)["completed"], 3)
            with patch("benchmark.runner._run_one", wraps=_run_one) as remaining:
                result = self.run_runner(output, resume=True, **options)
            self.assertEqual(remaining.call_count, 1)
            self.assertEqual(result["n_runs"], 4)
            with patch("benchmark.runner._run_one", side_effect=AssertionError("already saved")):
                self.run_runner(output, resume=True, **options)

    def test_live_status_reads_before_interval_without_model_or_duplicate_writer(self):
        calls = 0
        def inspect_run(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                status = checkpoint_status(output)
                self.assertEqual(status["completed"], 1)
                self.assertTrue(status["runner_active"])
                self.assertEqual(status["by_strategy"]["sliding_window"]["task_success_rate"], 1)
                with self.assertRaisesRegex(ValueError, "another runner"):
                    self.run_runner(output, repeats=2, resume=True)
                stdout = io.StringIO()
                with patch.object(sys, "argv", ["runner", "--status", "--output", output]), redirect_stdout(stdout):
                    main()
                self.assertEqual(json.loads(stdout.getvalue())["completed"], 1)
            return _run_one(*args, **kwargs)
        with tempfile.TemporaryDirectory() as output, patch("benchmark.runner._run_one", side_effect=inspect_run):
            self.run_runner(output, repeats=2, checkpoint_interval=100)

    def test_run_rejects_llm_and_tokenizer_model_mismatch_before_agent_creation(self):
        count = Mock(side_effect=AssertionError("CountTokens must not be called"))
        counter = GeminiTokenCounter(
            model="other-model", client=SimpleNamespace(models=SimpleNamespace(count_tokens=count)),
        )
        with tempfile.TemporaryDirectory() as output:
            with self.assertRaisesRegex(ValueError, "LLM model and Gemini token counter model must match"):
                self.run_runner(output, token_counter=counter,
                                llm_config=LLMRunConfig(model=TARGET_GEMINI_MODEL))
            self.assertFalse((Path(output) / "runs.jsonl").exists())
        self.assertEqual(self.agents, [])
        count.assert_not_called()


    def test_experiment_configs_share_flash_lite_settings(self):
        smoke = json.loads(Path("experiments/configs/smoke.json").read_text())["model"]
        for name in ("main", "pilot", "ablation", "stress", "reference"):
            with self.subTest(config=name):
                config = json.loads(Path(f"experiments/configs/{name}.json").read_text())
                self.assertEqual(config["model"], smoke)
        self.assertEqual(smoke["name"], TARGET_GEMINI_MODEL)



    def test_runner_rejects_tokenizer_mode_mismatch(self):
        scenario = copy.deepcopy(
            self.scenario
        )

        scenario["token_count_mode"] = (
            f"gemini:{TARGET_GEMINI_MODEL}"
        )

        with tempfile.TemporaryDirectory() as output:
            with self.assertRaisesRegex(
                ValueError,
                "token counter mismatch",
            ):
                run_scenarios(
                    [scenario],
                    agent_factory=self.factory,
                    system_prompt=SYSTEM,
                    tool_definitions=TOOLS,
                    output_directory=output,
                    strategies=("sliding_window",),
                    bootstrap_iterations=100,
                    token_counter=provisional_token_count,
            )

    def test_legacy_gemini_fixture_requires_matching_legacy_counter(self):
        fixture_path = (
            Path(__file__).resolve().parents[1]
            / "benchmark" / "generated" / "smoke_gemini" / "cell_99a1ce542765.json"
        )
        scenario = json.loads(fixture_path.read_text(encoding="utf-8"))["scenarios"][0]
        scenario["context_budget"] = 1  # Validate model compatibility before any agent action.

        def mock_count(*, model, contents):
            return SimpleNamespace(total_tokens=len(contents.split()))

        client = SimpleNamespace(models=SimpleNamespace(count_tokens=mock_count))
        with patch.dict(os.environ, {}, clear=True):
            default_counter = GeminiTokenCounter(client=client)
        with tempfile.TemporaryDirectory() as output:
            with self.assertRaisesRegex(ValueError, "token counter mismatch"):
                run_scenarios(
                    [scenario], agent_factory=self.factory, system_prompt=SYSTEM,
                    tool_definitions=TOOLS, output_directory=output,
                    strategies=("sliding_window",), bootstrap_iterations=100,
                    token_counter=default_counter,
                )

        legacy_counter = GeminiTokenCounter(model="gemini-3.8-flash", client=client)
        with tempfile.TemporaryDirectory() as output:
            result = run_scenarios(
                [scenario], agent_factory=self.factory, system_prompt=SYSTEM,
                tool_definitions=TOOLS, output_directory=output,
                strategies=("sliding_window",), bootstrap_iterations=100,
                token_counter=legacy_counter,
            )
        self.assertEqual(result["n_runs"], 1)


if __name__ == "__main__":
    unittest.main()
