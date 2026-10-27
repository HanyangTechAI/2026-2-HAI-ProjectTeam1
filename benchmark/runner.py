"""스냅샷 시나리오를 baseline, Context Builder, 평가기로 연결해 실행한다.

사용법: python -m benchmark.runner --help
시나리오 정답과 고정된 검색·토큰 fixture는 전략이나 Agent에 전달하지 않는다.
이 모듈은 합성 대화 이력 생성기가 아니다.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import Enum
import importlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol

from agent.agent import AgentRunResult
from agent.evaluation import scenario_with_granted_approvals
from agent.tools.types import ToolResult
from context.builder import build_context, fixed_context_tokens
from context.tokenizer import TokenCounter, count_tokens, provisional_token_count
from memory.baselines import Embedder, sliding_window, utility_per_token, vector_top_k
from memory.schema import MemoryItem
from memory.selector import PipelineStatus, SelectionResult

from .evaluator import aggregate_results, evaluate


STRATEGIES = ("sliding_window", "vector_top_k", "utility_per_token")


class AgentAdapter(Protocol):
    def __call__(
        self, context: str, *, session_id: str,
    ) -> AgentRunResult | Mapping[str, Any]:
        """조립된 Context로 실행하고 실제 행동과 결과를 반환한다."""
        ...


def _json_record(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _json_record(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_record(item) for item in value]
    return value


def _select(
    strategy: str, scenario: Mapping[str, Any], memories: Sequence[MemoryItem],
    fixed_tokens: int, embedder: Embedder | None, top_k: int,
    utility_weights: tuple[float, float, float],
) -> SelectionResult:
    options = dict(
        session_id=scenario["session_id"],
        total_budget=scenario["context_budget"],
        fixed_tokens=fixed_tokens,
        current_turn=scenario["current_turn"],
    )
    if strategy == "sliding_window":
        return sliding_window(memories, **options)
    if embedder is None:
        raise ValueError(f"{strategy} requires an embedding function")
    if strategy == "vector_top_k":
        return vector_top_k(
            scenario["query"], memories, embedder=embedder, top_k=top_k, **options,
        )
    return utility_per_token(
        scenario["query"], memories, embedder=embedder, top_k=top_k,
        weights=utility_weights, **options,
    )


def _run_one(
    scenario: Mapping[str, Any], strategy: str, repeat_id: int, *,
    agent_factory: Callable[[], AgentAdapter], embedder: Embedder | None,
    system_prompt: str, tool_definitions: str, token_counter: TokenCounter,
    token_count_mode: str, top_k: int,
    utility_weights: tuple[float, float, float],
) -> dict[str, Any]:
    start = perf_counter()
    # Store에 삽입하면 버전 관계가 다시 생성되므로 스냅샷을 직접 복원한다.
    # fixture의 비용은 이번 실행에 사용하는 TokenCounter의 측정값으로 대체한다.
    memories = [MemoryItem.from_dict(item) for item in scenario["memory_snapshot"]]
    memories = [replace(memory, token_count=count_tokens(memory.content, token_counter))
                for memory in memories
                if memory.session_id == scenario["session_id"]
                and memory.turn_id <= scenario["current_turn"]]
    fixed_tokens = fixed_context_tokens(
        system_prompt, tool_definitions, scenario["query"], token_counter,
    )
    selection = _select(
        strategy, scenario, memories, fixed_tokens, embedder, top_k, utility_weights,
    )
    if not isinstance(selection, SelectionResult):
        raise TypeError("strategy must return memory.selector.SelectionResult")
    context = build_context(
        system_prompt, tool_definitions, scenario["query"], selection,
        scenario["context_budget"], token_counter,
    )
    run: dict[str, Any] = dict(
        status=context.status.value,
        selection_result=_json_record(selection),
        context_result=_json_record(context),
        token_count_mode=token_count_mode,
        strategy_options=dict(top_k=top_k, utility_weights=list(utility_weights)),
        retrieved_memories=[memory.to_dict() for mid in selection.retrieved_memory_ids
                            for memory in memories if memory.memory_id == mid],
        response=None, goal_completed=False, tool_calls=[], tool_results=[],
        usage=dict(fixed_input_tokens=fixed_tokens, input_tokens=None, output_tokens=None),
        planning_latency_ms=None, total_llm_cost=None,
        agent_executed=False, stopped_reason=context.status.value,
    )
    final_ids = set(context.selected_memory_ids) if context.status == PipelineStatus.OK else set()
    run["context_result"]["memory_tokens"] = sum(
        memory.token_count for memory in selection.flexible if memory.memory_id in final_ids
    )
    # 이 baseline들은 버전·적용 여부에 따른 후보 확장을 하지 않으므로
    # 예산 적용 전의 전체 후보를 알 수 있다. Sliding의 제외 목록에는
    # 연속 구간을 유지하는 예산 정책에 따라 제외한 나머지 항목도 포함된다.
    if selection.status == PipelineStatus.OK:
        run["selection_trace"] = dict(
            candidate_memory_ids=list(selection.selected_memory_ids + selection.rejected_memory_ids),
            budget_rejected_memory_ids=list(selection.rejected_memory_ids),
        )
    if context.status == PipelineStatus.OK:
        # 실행마다 새 adapter를 만들어 전략·반복 사이에 planner와 Tool 상태가 섞이지 않게 한다.
        adapter = agent_factory()
        if not callable(adapter):
            raise TypeError("agent_factory must return a callable agent adapter")
        agent_start = perf_counter()
        outcome = _json_record(adapter(context.context, session_id=scenario["session_id"]))
        agent_ms = (perf_counter() - agent_start) * 1000
        if not isinstance(outcome, dict):
            raise TypeError("agent adapter must return AgentRunResult or a mapping")
        if "goal_completed" not in outcome and scenario.get("expected_answers") is None:
            raise ValueError("action-task adapter must explicitly report goal_completed")
        if "goal_completed" in outcome and type(outcome["goal_completed"]) is not bool:
            raise TypeError("goal_completed must be a boolean")
        run.update(
            response=outcome.get("response"), goal_completed=outcome.get("goal_completed"),
            tool_calls=outcome.get("tool_calls", []),
            tool_results=outcome.get("tool_results", []),
            stopped_reason=outcome.get("stopped_reason", "completed"),
            planning_latency_ms=outcome.get("planning_latency_ms"),
            agent_latency_ms=agent_ms,
            total_llm_cost=outcome.get("total_llm_cost"), agent_executed=True,
        )
        usage = outcome.get("usage") or {}
        # API 토큰 수에는 조립된 문자열에 없는 모델별 형식 비용이 포함될 수 있다.
        # 누락된 출력 토큰 수나 비용을 추정값으로 채우지 않는다.
        run["usage"].update(
            input_tokens=usage.get("input_tokens", context.total_input_tokens),
            output_tokens=usage.get("output_tokens"),
        )
        if "total_input_tokens" in usage:
            run["usage"]["total_input_tokens"] = usage["total_input_tokens"]
    elif selection.status == PipelineStatus.OK:
        # Agent를 호출하지 않았더라도 예산을 초과한 측정값을 기록한다.
        run["usage"]["input_tokens"] = context.total_input_tokens
    run["total_latency_ms"] = (perf_counter() - start) * 1000

    # 대기 중인 승인 요청은 발송 권한을 부여하지 않는다. 기존 adapter 보조 함수를
    # 재사용하고, 실제 평가에 사용한 스냅샷을 JSONL에 보존한다.
    tool_results = [ToolResult(**item) for item in run["tool_results"]]
    evaluation_scenario = scenario_with_granted_approvals(scenario, tool_results)
    evaluation = evaluate(
        evaluation_scenario, run, strategy=strategy, repeat_id=repeat_id, k=top_k,
    )
    return dict(
        scenario=evaluation_scenario, strategy=strategy, repeat_id=repeat_id,
        run_result=run, evaluation=evaluation.to_dict(),
    )


def run_scenarios(
    scenarios: Sequence[Mapping[str, Any]], *,
    agent_factory: Callable[[], AgentAdapter],
    system_prompt: str,
    tool_definitions: str,
    output_directory: str | Path,
    strategies: Sequence[str] = STRATEGIES,
    embedder: Embedder | None = None,
    token_counter: TokenCounter = provisional_token_count,
    top_k: int = 20,
    repeats: int = 1,
    utility_weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    bootstrap_iterations: int = 2000,
    seed: int = 42,
) -> dict[str, Any]:
    """활성 integration 시나리오를 전략·반복별로 실행한다.

    오프라인 평가기 입력과 개별 평가를 runs.jsonl에 저장하고, 기존 평가기의
    집계 목록을 summary.json에 저장한다. 모든 실행이 정상 종료된 뒤 같은 이름의
    출력 파일을 덮어쓴다. 오류를 성공 결과로 바꾸지 않고 예외로 전달한다.
    미지원 시나리오와 모듈 전용 시나리오는 TSR 표본에 포함하지 않는다.
    기본 공백 기준 TokenCounter는 임시 측정이며 실제 모델 토큰 사용량이 아니다.
    """
    strategies = tuple(strategies)
    if not strategies or len(set(strategies)) != len(strategies) or any(
        strategy not in STRATEGIES for strategy in strategies
    ):
        raise ValueError(f"strategies must be distinct names from {STRATEGIES}")
    if any(strategy != "sliding_window" for strategy in strategies) and embedder is None:
        raise ValueError("vector_top_k and utility_per_token require embedder")
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if type(repeats) is not int or repeats <= 0:
        raise ValueError("repeats must be a positive integer")
    if type(bootstrap_iterations) is not int or bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be an integer >= 100")
    if not callable(agent_factory) or not callable(token_counter):
        raise TypeError("agent_factory and token_counter must be callable")
    enabled = [scenario for scenario in scenarios
               if scenario.get("benchmark_enabled", True)
               and scenario.get("validation_scope", "integration") == "integration"]
    if not enabled:
        raise ValueError("no enabled integration scenarios selected")
    ids = [scenario.get("scenario_id", scenario.get("sample_id")) for scenario in enabled]
    if any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise ValueError("scenarios require unique non-empty scenario_id/sample_id")
    mode = "provisional" if token_counter is provisional_token_count else "supplied_token_counter"
    rows = [
        _run_one(
            scenario, strategy, repeat_id, agent_factory=agent_factory,
            embedder=embedder, system_prompt=system_prompt, tool_definitions=tool_definitions,
            token_counter=token_counter, token_count_mode=mode, top_k=top_k,
            utility_weights=utility_weights,
        )
        for scenario in enabled for strategy in strategies for repeat_id in range(repeats)
    ]
    summary = aggregate_results(
        [row["evaluation"] for row in rows],
        bootstrap_iterations=bootstrap_iterations, seed=seed,
    )
    # 기존 결과 파일을 덮어쓰기 전에 모든 결과의 직렬화를 완료한다.
    runs_text = "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                        for row in rows)
    summary_text = json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    (output / "runs.jsonl").write_text(runs_text, encoding="utf-8")
    (output / "summary.json").write_text(summary_text, encoding="utf-8")
    return dict(n_runs=len(rows), n_scenarios=len(enabled), summary=summary,
                runs_path=str(output / "runs.jsonl"), summary_path=str(output / "summary.json"))


def _import_callable(spec: str) -> Callable:
    module, separator, attribute = spec.partition(":")
    if not separator or not module or not attribute:
        raise ValueError("callable must use module:function syntax")
    value = getattr(importlib.import_module(module), attribute)
    if not callable(value):
        raise TypeError(f"{spec} is not callable")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", default="benchmark/scenarios.json")
    parser.add_argument("--scenario-ids", nargs="+")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=STRATEGIES)
    parser.add_argument("--agent-factory", required=True, help="module:function returning a fresh callable adapter")
    parser.add_argument("--embedder", help="module:function taking text and returning a numeric vector")
    parser.add_argument("--token-counter", help="module:function taking text and returning token count")
    parser.add_argument("--system-prompt-file", required=True)
    parser.add_argument("--tool-definitions-file", required=True)
    parser.add_argument("--output", default="experiments/results/baselines")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--utility-weights", type=float, nargs=3, default=(0.6, 0.3, 0.1))
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    payload = json.loads(Path(args.scenarios).read_text(encoding="utf-8"))
    scenarios = payload["scenarios"] if isinstance(payload, dict) else payload
    if args.scenario_ids:
        requested = set(args.scenario_ids)
        known = {scenario.get("scenario_id", scenario.get("sample_id")) for scenario in scenarios}
        if requested - known:
            parser.error(f"unknown scenario IDs: {sorted(requested - known)}")
        scenarios = [scenario for scenario in scenarios
                     if scenario.get("scenario_id", scenario.get("sample_id")) in requested]
        if any(not scenario.get("benchmark_enabled", True)
               or scenario.get("validation_scope", "integration") != "integration"
               for scenario in scenarios):
            parser.error("requested IDs must be enabled integration scenarios")
    result = run_scenarios(
        scenarios, agent_factory=_import_callable(args.agent_factory),
        system_prompt=Path(args.system_prompt_file).read_text(encoding="utf-8"),
        tool_definitions=Path(args.tool_definitions_file).read_text(encoding="utf-8"),
        output_directory=args.output, strategies=args.strategies,
        embedder=_import_callable(args.embedder) if args.embedder else None,
        token_counter=_import_callable(args.token_counter) if args.token_counter else provisional_token_count,
        top_k=args.top_k, repeats=args.repeats, utility_weights=tuple(args.utility_weights),
        bootstrap_iterations=args.bootstrap_iterations, seed=args.seed,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
