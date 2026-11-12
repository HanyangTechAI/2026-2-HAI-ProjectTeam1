
"""Provider 실패와 정상 모델 실패를 구분하고 checkpoint/resume을 검증한다."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.agent import Agent
from agent.llm_planner import LLMPlanner, LLMProviderError
from benchmark.generator import generate_scenario
from benchmark.runner import run_scenarios
from context.tokenizer import provisional_token_count


class FakeProviderError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def valid_response():
    return SimpleNamespace(
        parsed={
            "tool_name": "",
            "action": "",
            "arguments": {},
            "text_response": "완료",
        },
        text=None,
        usage_metadata=None,
    )


def malformed_response():
    return SimpleNamespace(
        parsed=None,
        text=None,
        usage_metadata=None,
    )


class FakeModels:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def generate_content(self, **kwargs):
        self.calls += 1
        result = self.responses.pop(0)

        if isinstance(result, Exception):
            raise result

        return result


def planner_with_models(models, *, retries=0, sleeper=None):
    return LLMPlanner(
        client=SimpleNamespace(models=models),
        max_retries=retries,
        retry_base_seconds=0.5,
        sleeper=sleeper or (lambda seconds: None),
    )


class LLMProviderFailureTests(unittest.TestCase):

    def test_transient_failure_recovers_with_retry(self):
        models = FakeModels([
            FakeProviderError(503),
            FakeProviderError(429),
            valid_response(),
        ])
        delays = []

        planner = planner_with_models(
            models,
            retries=2,
            sleeper=delays.append,
        )

        action = planner.plan("test context")

        self.assertEqual(action.text_response, "완료")
        self.assertEqual(models.calls, 3)
        self.assertEqual(delays, [0.5, 1.0])
        self.assertEqual(planner.telemetry().api_calls, 3)

    def test_exhausted_retries_raise_provider_error(self):
        models = FakeModels([
            FakeProviderError(503),
            FakeProviderError(503),
            FakeProviderError(503),
        ])

        planner = planner_with_models(models, retries=2)

        with self.assertRaises(LLMProviderError) as raised:
            planner.plan("test context")

        error = raised.exception

        self.assertEqual(error.attempts, 3)
        self.assertEqual(error.status_code, 503)
        self.assertIsInstance(
            error.__cause__,
            FakeProviderError,
        )
        self.assertEqual(models.calls, 3)

    def test_non_retryable_error_raises_immediately(self):
        models = FakeModels([
            FakeProviderError(400),
        ])
        delays = []

        planner = planner_with_models(
            models,
            retries=2,
            sleeper=delays.append,
        )

        with self.assertRaises(LLMProviderError) as raised:
            planner.plan("test context")

        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(models.calls, 1)
        self.assertEqual(delays, [])

    def test_malformed_model_response_is_evaluated(self):
        models = FakeModels([
            malformed_response(),
        ])

        planner = planner_with_models(models, retries=2)

        action = planner.plan("test context")

        self.assertIsNone(action.tool_name)
        self.assertIsNone(action.action)
        self.assertEqual(
            action.text_response,
            "모델 응답을 행동으로 읽지 못했습니다.",
        )
        self.assertEqual(models.calls, 1)

    def test_runner_interrupts_and_resumes_failed_run(self):
        scenarios = [
            generate_scenario(
                seed=101,
                sample_index=index,
                context_budget=8192,
                token_counter=provisional_token_count,
            )
            for index in range(2)
        ]

        # 첫 실행 성공, 두 번째 실패,
        # resume한 두 번째 실행 성공.
        models = FakeModels([
            valid_response(),
            FakeProviderError(503),
            valid_response(),
        ])

        def make_agent():
            return Agent(
                planner=planner_with_models(
                    models,
                    retries=0,
                )
            )

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results"

            options = {
                "agent_factory": make_agent,
                "system_prompt": "테스트 시스템 프롬프트",
                "tool_definitions": "file.find_file",
                "output_directory": output,
                "strategies": ("sliding_window",),
                "token_counter": provisional_token_count,
                "max_steps": 1,
                "bootstrap_iterations": 100,
                "show_progress": False,
            }

            # 1차 실행: 두 번째 scenario에서 중단
            with self.assertRaises(LLMProviderError):
                run_scenarios(scenarios, **options)

            progress = json.loads(
                (output / "progress.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                progress["state"],
                "interrupted",
            )
            self.assertEqual(progress["completed"], 1)

            saved_rows = (
                output / "runs.jsonl"
            ).read_text(encoding="utf-8").splitlines()

            self.assertEqual(len(saved_rows), 1)

            # 2차 실행: 첫 번째 결과를 재실행하지 않음
            result = run_scenarios(
                scenarios,
                resume=True,
                **options,
            )

            self.assertEqual(result["n_runs"], 2)
            self.assertEqual(models.calls, 3)

            rows = [
                json.loads(line)
                for line in (
                    output / "runs.jsonl"
                ).read_text(encoding="utf-8").splitlines()
            ]

            self.assertEqual(len(rows), 2)

            keys = [
                (
                    row["evaluation"]["sample_id"],
                    row["strategy"],
                    row["repeat_id"],
                )
                for row in rows
            ]

            self.assertEqual(len(set(keys)), 2)

            final_progress = json.loads(
                (output / "progress.json").read_text(
                    encoding="utf-8"
                )
            )

            self.assertEqual(
                final_progress["state"],
                "completed",
            )
            self.assertEqual(
                final_progress["completed"],
                2,
            )


if __name__ == "__main__":
    unittest.main()
