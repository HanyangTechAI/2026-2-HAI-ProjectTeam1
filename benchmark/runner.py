"""스냅샷 시나리오를 PipelineAdapter로 실행한 뒤 평가하고 저장한다.

사용법: python -m benchmark.runner --help
시나리오 정답과 고정된 검색·토큰 fixture는 전략이나 Agent에 전달하지 않는다.
이 모듈은 합성 대화 이력 생성기가 아니다.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime
from enum import Enum
import importlib
import json
from pathlib import Path
from time import perf_counter
from typing import Any

from agent.agent import Agent
from agent.evaluation import scenario_with_granted_approvals
from agent.llm_planner import LLMRunConfig, shared_system_instruction

from context.tokenizer import TokenCounter, count_tokens, provisional_token_count, GeminiTokenCounter, token_count_mode

from memory.retrieval import Embedder
from memory.schema import MemoryItem
from memory.selector import PipelineStatus
from memory.ablation import ABLATION_PRESETS, FULL_ABLATION, SelectorAblationConfig, get_ablation_config

from pipeline.orchestrator import GoalChecker, PipelineRunResult
from pipeline.pipeline_adapter import AgentFactory, make_pipeline_adapter

from .evaluator import aggregate_results, evaluate


STRATEGIES = ("sliding_window", "vector_top_k", "utility_per_token", "proposed")
EnvironmentAgentFactory = Callable[[dict[str, Any]], Agent]


def _environment_snapshot(scenario: Mapping[str, Any]) -> dict[str, Any]:
    """Copy supported mock initial state, excluding scenario/evaluation fields.

    File catalogs and previously granted approvals seed tools, not the planner.
    A fresh copy is made for each strategy/repeat so factory mutations cannot
    alter another run or the original scenario.
    """
    environment = scenario.get("environment", {})
    if not isinstance(environment, Mapping):
        raise ValueError("scenario.environment must be an object")
    snapshot = {}
    if "files" in environment:
        files = environment["files"]
        if not isinstance(files, Mapping) or any(
            not isinstance(name, str) or not name.strip() or not isinstance(content, str)
            for name, content in files.items()
        ):
            raise ValueError("environment.files must map non-empty filenames to text")
        snapshot["files"] = dict(files)
    if "valid_approval_ids" in environment:
        grants = environment["valid_approval_ids"]
        if not isinstance(grants, (list, tuple)) or any(
            not isinstance(grant, str) or not grant.strip() for grant in grants
        ):
            raise ValueError("environment.valid_approval_ids must contain non-empty strings")
        snapshot["valid_approval_ids"] = list(grants)
    return snapshot


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


def _mandatory_memory_overflow(outcome: PipelineRunResult) -> bool | None:
    """선택/Context 실행 기록으로 필수 기억 초과를 판정한다. 추가 토큰 호출은 없다.

    고정 입력만으로 중단되어 선택을 한 번도 수행하지 못했으면 미측정(None).
    Flexible 제거 후 필수 기억의 formatting 비용 때문에 중단된 경우도 포함한다.
    """
    evaluated = [step for step in outcome.steps
                 if step.selection_result.status != PipelineStatus.INVALID_BUDGET_CONFIGURATION]
    if not evaluated:
        return None
    return any(
        bool(step.selection_result.protected or step.selection_result.states)
        and (step.selection_result.status == PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET
             or step.context_result.status == PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET)
        for step in evaluated
    )


def _run_one(
    scenario: Mapping[str, Any], 
    strategy: str, 
    repeat_id: int, 
    *,
    agent_factory: AgentFactory | None,
    environment_agent_factory: EnvironmentAgentFactory | None,
    embedder: Embedder | None,
    system_prompt: str, 
    tool_definitions: str, 
    token_counter: TokenCounter,
    token_count_mode: str, 
    top_k: int,
    utility_weights: tuple[float, float, float],
    max_steps: int, 
    goal_checker: GoalChecker | None,
    proposed_ablation: SelectorAblationConfig | None,
    llm_config: LLMRunConfig | None,
) -> dict[str, Any]:
    start = perf_counter()
    # Store에 재삽입하지 않고 기존 버전 관계를 유지한 채 스냅샷을 복원한다.
    memories = tuple(MemoryItem.from_dict(item) for item in scenario["memory_snapshot"])
    memories = tuple(memory for memory in memories
                     if memory.session_id == scenario["session_id"]
                     and memory.turn_id <= scenario["current_turn"])
    if environment_agent_factory is not None:
        def runtime_agent_factory() -> Agent:
            return environment_agent_factory(_environment_snapshot(scenario))
    else:
        runtime_agent_factory = agent_factory
        
    if strategy == "proposed":
        if embedder is None:
            raise ValueError("proposed strategy requires embedder")
        
        ablation = proposed_ablation or FULL_ABLATION
        
        adapter = make_pipeline_adapter(
            strategy,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            agent_factory=runtime_agent_factory,
            embedder=embedder,
            top_k=top_k,
            utility_weights=utility_weights,
            ablation=ablation,
            token_counter=token_counter,
            max_steps=max_steps,
            goal_checker=goal_checker,
        )
        
        strategy_label = "proposed" if ablation.name == "full" else f"proposed/{ablation.name}"
        
    else:
        adapter = make_pipeline_adapter(
            strategy,
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            agent_factory=runtime_agent_factory,
            embedder=embedder,
            top_k=top_k,
            utility_weights=utility_weights,
            token_counter=token_counter,
            max_steps=max_steps,
            goal_checker=goal_checker,
        )
        
        strategy_label = strategy
        
    # policy는 adapter 생성 시 지정하고, 나머지 실행 입력은 Orchestrator.run으로 전달한다.
    outcome = adapter(
        query=scenario["query"],
        session_id=scenario["session_id"],
        memories=memories,
        total_budget=scenario["context_budget"],
        current_turn=scenario["current_turn"],
        current_time=_scenario_current_time(scenario),
    )
    
    if not isinstance(outcome, PipelineRunResult):
        raise TypeError("pipeline adapter must return PipelineRunResult")
    if not outcome.steps:
        raise ValueError("pipeline result must contain at least one step")
    final_step = outcome.steps[-1]
    selection = final_step.selection_result
    context = final_step.context_result
    # 누적 입력량은 단일 Context 예산과 비교하지 않는다. 정상 실행의 input_tokens에는
    # 실제 Agent에 전달된 단계별 입력량의 최댓값을 기록하고 누적량은 별도로 보존한다.
    executed_steps = [
        step
        for step in outcome.steps
        if step.action_result is not None
    ]
    if outcome.status == PipelineStatus.OK:
        if executed_steps:
            peak_step = max(
                executed_steps,
                key=lambda step: step.context_result.total_input_tokens,
            )
            
            input_tokens = peak_step.context_result.total_input_tokens
            
            fixed_input_tokens = peak_step.fixed_tokens
        else:
            input_tokens = 0
            fixed_input_tokens = final_step.fixed_tokens
            
    elif selection.status != PipelineStatus.OK:
        # 선택 중단 시 조립되지 않은 Context의 입력량은 미측정이다.
        input_tokens = None
        fixed_input_tokens = final_step.fixed_tokens
        
    else:
        input_tokens = context.total_input_tokens
        fixed_input_tokens = final_step.fixed_tokens
        
    run = _json_record(outcome)
    run.update(
        selection_result=_json_record(selection),
        context_result=_json_record(context),
        token_count_mode=token_count_mode,
        token_counter_metadata=({key: value for key, value in token_counter.metadata().items()
                                 if key != "stats"}
                                if isinstance(token_counter, GeminiTokenCounter) else None),
        strategy_options=dict(
            top_k=top_k,
            utility_weights=list(utility_weights),
            max_steps=max_steps,
            **({} if llm_config is None else {
                "model": llm_config.model,
                "temperature": llm_config.temperature,
            }),
        ),
        retrieved_memories=[
            replace(memory, token_count=count_tokens(memory.content, token_counter)).to_dict()
            for mid in selection.retrieved_memory_ids
            for memory in memories if memory.memory_id == mid
        ],
        usage=dict(
            fixed_input_tokens=fixed_input_tokens,
            input_tokens=input_tokens,
            total_input_tokens=outcome.total_input_tokens,
            output_tokens=None,
        ),
        planning_latency_ms=None, total_llm_cost=None,
        agent_executed=outcome.agent_executed,
        mandatory_memory_overflow=_mandatory_memory_overflow(outcome),
    )
    final_ids = set(context.selected_memory_ids) if outcome.status == PipelineStatus.OK else set()
    run["context_result"]["memory_tokens"] = sum(
        memory.token_count
        for memory in selection.protected + selection.states + selection.flexible
        if memory.memory_id in final_ids
    )
    # 평가기에는 최종 선택을 전달하고, 중간 선택·Context·행동은 steps에 모두 보존한다.
    if selection.status == PipelineStatus.OK:
        run["selection_trace"] = dict(
            candidate_memory_ids=list(selection.selected_memory_ids + selection.rejected_memory_ids),
            budget_rejected_memory_ids=list(selection.rejected_memory_ids),
        )
    run["total_latency_ms"] = (perf_counter() - start) * 1000
    # 대기 중인 승인 요청을 유효한 승인으로 평가하지 않도록 기존 보조 함수를 사용한다.
    evaluation_scenario = scenario_with_granted_approvals(scenario, outcome.tool_results)
    evaluation = evaluate(
        evaluation_scenario, run, strategy=strategy_label, repeat_id=repeat_id, k=top_k,
    )
    return dict(
        scenario=evaluation_scenario, strategy=strategy_label, repeat_id=repeat_id,
        ablation=proposed_ablation.to_dict() if proposed_ablation is not None else None,
        run_result=run, evaluation=evaluation.to_dict(),
    )


def run_scenarios(
    scenarios: Sequence[Mapping[str, Any]], *,
    agent_factory: AgentFactory | None = None,
    environment_agent_factory: EnvironmentAgentFactory | None = None,
    system_prompt: str,
    tool_definitions: str,
    output_directory: str | Path,
    strategies: Sequence[str] = STRATEGIES,
    embedder: Embedder | None = None,
    token_counter: TokenCounter | None = provisional_token_count,
    top_k: int = 20,
    repeats: int = 1,
    utility_weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    bootstrap_iterations: int = 2000,
    seed: int = 42,
    max_steps: int = 8,
    goal_checker: GoalChecker | None = None,
    proposed_ablations: Sequence[str] = ("full",),
    llm_config: LLMRunConfig | None = None,
) -> dict[str, Any]:
    """활성 integration 시나리오를 전략·반복별로 실행한다.

    오프라인 평가기 입력과 개별 평가를 runs.jsonl에 저장하고, 기존 평가기의
    집계 목록을 summary.json에 저장한다. 모든 실행이 정상 종료된 뒤 같은 이름의
    출력 파일을 덮어쓴다. 오류를 성공 결과로 바꾸지 않고 예외로 전달한다.
    미지원 시나리오와 모듈 전용 시나리오는 TSR 표본에 포함하지 않는다.
    기본 공백 기준 TokenCounter는 임시 측정이며 실제 모델 토큰 사용량이 아니다.
    agent_factory 또는 environment_agent_factory 중 하나만 지정한다. 후자는
    scenario.environment의 files와 valid_approval_ids 사본만 받아 실행마다
    새 Agent와 mock Tool을 초기화한다. 정답이나 query는 factory에 넘기지 않는다.
    다단계 선택·Context 조립·
    행동 실행은 Orchestrator에 맡기고 단계별 기록과 누적 입력량을 결과에 보존한다.
    """
    if type(max_steps) is not int or max_steps < 1:
        raise ValueError("max_steps must be a positive integer")
    if goal_checker is not None and not callable(goal_checker):
        raise TypeError("goal_checker must be callable")
    strategies = tuple(strategies)
    if not strategies or len(set(strategies)) != len(strategies) or any(
        strategy not in STRATEGIES for strategy in strategies
    ):
        raise ValueError(f"strategies must be distinct names from {STRATEGIES}")
    
    embedding_strategies = {"vector_top_k", "utility_per_token", "proposed"}
    
    if any(strategy in embedding_strategies for strategy in strategies) and embedder is None:
        raise ValueError("vector_top_k, utility_per_token, and proposed require embedder")
    
    enabled = [
        scenario 
        for scenario in scenarios
        if scenario.get("benchmark_enabled", True) and scenario.get("validation_scope", "integration") == "integration"
    ]
    
    if not enabled:
        raise ValueError("no enabled integration scenarios selected")
    
    if "proposed" in strategies:
        for scenario in enabled:
            if scenario.get("current_time") is None:
                raise ValueError("proposed strategy requires scenario.current_time")
    
    if type(top_k) is not int or top_k <= 0:
        raise ValueError("top_k must be a positive integer")
    if type(repeats) is not int or repeats <= 0:
        raise ValueError("repeats must be a positive integer")
    if type(bootstrap_iterations) is not int or bootstrap_iterations < 100:
        raise ValueError("bootstrap_iterations must be an integer >= 100")
    if (agent_factory is None) == (environment_agent_factory is None):
        raise ValueError("specify exactly one of agent_factory or environment_agent_factory")
    if llm_config is not None and not isinstance(llm_config, LLMRunConfig):
        raise TypeError("llm_config must be an LLMRunConfig")
    if llm_config is not None:
        # 전략 루프 전에 한 번만 묶어서 모델, temperature, 시스템 지시문이 갈라지지 않게 한다.
        if agent_factory is not None:
            agent_factory = _with_shared_planner(agent_factory, llm_config)
        else:
            environment_agent_factory = _with_shared_planner(environment_agent_factory, llm_config)
    factory = agent_factory if agent_factory is not None else environment_agent_factory
    if token_counter is None:
        token_counter = GeminiTokenCounter()
    if not callable(factory) or not callable(token_counter):
        raise TypeError("agent factory and token_counter must be callable")
    
    ids = [scenario.get("scenario_id", scenario.get("sample_id")) for scenario in enabled]
    if any(not isinstance(sid, str) or not sid for sid in ids) or len(set(ids)) != len(ids):
        raise ValueError("scenarios require unique non-empty scenario_id/sample_id")
    
    proposed_ablations = tuple(proposed_ablations)
    
    if not proposed_ablations or len(set(proposed_ablations)) != len(proposed_ablations):
        raise ValueError("proposed_ablations must be non-empty and unique")
    
    unknown_ablations = set(proposed_ablations) - set(ABLATION_PRESETS)
    
    if unknown_ablations:
        raise ValueError(f"unknown proposed ablations: {sorted(unknown_ablations)}")
    
    mode = token_count_mode(token_counter)
    
    scenario_modes = {
        scenario.get("token_count_mode")
        for scenario in enabled
    }
    
    if None in scenario_modes:
        raise ValueError("all scenarios must declare token_count_mode")
    
    if scenario_modes != {mode}:
        raise ValueError(f"scenario/runtime token counter mismatch: scenario={sorted(scenario_modes)}, runtime={mode}")
    
    rows = []
    
    for scenario in enabled:
        for strategy in strategies:
            if strategy == "proposed":
                variants = tuple(
                    get_ablation_config(name)
                    for name in proposed_ablations
                )
            else:
                variants = (None,)
                
            for ablation in variants:
                for repeat_id in range(repeats):
                    # 영속 캐시도 실행 간 공유하지 않는다. 원본 DB는 보존한다.
                    cache_scope = (token_counter.isolated_cache()
                                   if isinstance(token_counter, GeminiTokenCounter) else nullcontext())
                    with cache_scope:
                        rows.append(
                            _run_one(
                                scenario,
                                strategy,
                                repeat_id,
                                agent_factory=agent_factory,
                                environment_agent_factory=environment_agent_factory,
                                embedder=embedder,
                                system_prompt=system_prompt,
                                tool_definitions=tool_definitions,
                                token_count_mode=mode,
                                token_counter=token_counter,
                                top_k=top_k,
                                utility_weights=utility_weights,
                                max_steps=max_steps,
                                goal_checker=goal_checker,
                                proposed_ablation=ablation,
                                llm_config=llm_config,
                            )
                        )
    
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
    
    if isinstance(token_counter, GeminiTokenCounter):
        tokenizer_metadata = token_counter.metadata()
    else:
        tokenizer_metadata = {
            "type": token_count_mode(token_counter)
        }
        
    experiment_metadata = {
        "strategies": list(strategies),
        "n_scenarios": len(enabled),
        "repeats": repeats,
        "top_k": top_k,
        "utility_weights": list(utility_weights),
        "max_steps": max_steps,
        "token_count_mode": mode,
        "tokenizer": tokenizer_metadata,
        "token_cache_policy": "isolated_per_run" if isinstance(token_counter, GeminiTokenCounter) else None,
        "scenario_ids": ids,
        "system_prompt": system_prompt,
        "llm": None if llm_config is None else llm_config.to_dict(),
    }
    
    experiment_text = json.dumps(
        experiment_metadata,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    
    experiment_path = output / "experiment.json"
    
    experiment_path.write_text(
        experiment_text,
        encoding="utf-8",
    )
    
    return dict(
        n_runs=len(rows), 
        n_scenarios=len(enabled), 
        summary=summary,
        runs_path=str(output / "runs.jsonl"), 
        summary_path=str(output / "summary.json"),
        experiment_path=str(experiment_path),
        tokenizer=tokenizer_metadata,
    )


def _with_shared_planner(factory: Callable, config: LLMRunConfig) -> Callable:
    """모든 전략이 같은 LLMRunConfig로 Planner를 다시 달게 한다."""
    def wrapped(*args: Any, **kwargs: Any) -> Agent:
        agent = factory(*args, **kwargs)
        if not isinstance(agent, Agent):
            raise TypeError("agent factory must return an Agent instance")
        agent.planner = config.planner()
        return agent
    return wrapped


def _import_callable(spec: str) -> Callable:
    module, separator, attribute = spec.partition(":")
    if not separator or not module or not attribute:
        raise ValueError("callable must use module:function syntax")
    value = getattr(importlib.import_module(module), attribute)
    if not callable(value):
        raise TypeError(f"{spec} is not callable")
    return value


def _scenario_current_time(scenario: Mapping[str, Any]) -> datetime | None:
    raw = scenario.get("current_time")
    
    if raw is None:
        return None
    
    if isinstance(raw, datetime):
        return raw
    
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("scenario.current_time must be a valid ISO 8601 timestamp") from exc
        
    raise TypeError("scenario.current_time must be datetime, ISO string, or None")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", default="benchmark/scenarios.json")
    parser.add_argument("--scenario-ids", nargs="+")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=STRATEGIES)
    factory_group = parser.add_mutually_exclusive_group(required=True)
    factory_group.add_argument("--agent-factory", help="module:function returning a fresh Agent instance")
    factory_group.add_argument(
        "--environment-agent-factory",
        help="module:function receiving only initial files/grants and returning a fresh Agent",
    )
    parser.add_argument("--embedder", help="module:function taking text and returning a numeric vector")
    tokenizer_group = parser.add_mutually_exclusive_group()
    tokenizer_group.add_argument("--token-counter", help="Custom module:function taking text and returning token count")
    tokenizer_group.add_argument("--gemini-token-model", "--gemini-model", help="Use Gemini CountTokens API with this model. Example: gemini-3.8-flash")
    parser.add_argument("--gemini-token-cache-size", type=int, default=4096)
    parser.add_argument("--config", help="Experiment config JSON. Its model block is shared by every strategy.")
    parser.add_argument("--llm-model", help="Override the shared model name and enable the LLM planner.")
    parser.add_argument("--temperature", type=float, help="Override the shared temperature. Every strategy uses this value.")
    parser.add_argument("--system-prompt-file", required=True)
    parser.add_argument("--tool-definitions-file", required=True)
    parser.add_argument("--output", default="experiments/results/baselines")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--utility-weights", type=float, nargs=3, default=(0.6, 0.3, 0.1))
    parser.add_argument("--bootstrap-iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--gemini-token-cache", default=(".cache/gemini_token_cache.sqlite3"))
    parser.add_argument("--gemini-token-retries", type=int, default=3)
    parser.add_argument("--gemini-token-retry-base-seconds", type=float, default=0.5)
    parser.add_argument("--proposed-ablations", nargs="+", choices=tuple(ABLATION_PRESETS.keys()), default=["full"])
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
    if args.gemini_token_model or not args.token_counter:
        token_counter = GeminiTokenCounter(
            model=args.gemini_token_model,
            cache_size=args.gemini_token_cache_size,
            persistent_cache_path=args.gemini_token_cache,
            max_retries=args.gemini_token_retries,
            retry_base_seconds=args.gemini_token_retry_base_seconds,
        )
    elif args.token_counter:
        token_counter = _import_callable(args.token_counter)
    else:
        token_counter = provisional_token_count
    system_prompt = Path(args.system_prompt_file).read_text(encoding="utf-8")
    config_model: dict[str, Any] = {}
    if args.config:
        loaded = json.loads(Path(args.config).read_text(encoding="utf-8"))
        raw_model = loaded.get("model") or {}
        if not isinstance(raw_model, dict):
            parser.error("config model must be an object")
        config_model = raw_model
        prompt_file = config_model.get("system_prompt_file")
        if isinstance(prompt_file, str) and prompt_file:
            declared = Path(prompt_file).read_text(encoding="utf-8")
            if declared != system_prompt:
                parser.error("system prompt file does not match config model.system_prompt_file")
    model_name = args.llm_model or config_model.get("name")
    temperature = config_model.get("temperature", 0) if args.temperature is None else args.temperature
    max_output_tokens = config_model.get("max_output_tokens", 1024)
    llm_config = None
    if model_name:
        llm_config = LLMRunConfig(
            model=str(model_name),
            temperature=0 if temperature is None else temperature,
            system_instruction=shared_system_instruction(system_prompt),
            max_output_tokens=max_output_tokens,
        )
    result = run_scenarios(
        scenarios,
        agent_factory=_import_callable(args.agent_factory) if args.agent_factory else None,
        environment_agent_factory=(_import_callable(args.environment_agent_factory)
                                   if args.environment_agent_factory else None),
        system_prompt=system_prompt,
        tool_definitions=Path(args.tool_definitions_file).read_text(encoding="utf-8"),
        output_directory=args.output, strategies=args.strategies,
        embedder=_import_callable(args.embedder) if args.embedder else None,
        token_counter=token_counter,
        top_k=args.top_k, repeats=args.repeats, utility_weights=tuple(args.utility_weights),
        bootstrap_iterations=args.bootstrap_iterations, seed=args.seed, max_steps=args.max_steps,
        proposed_ablations=args.proposed_ablations,
        llm_config=llm_config,
    )
    if isinstance(token_counter, GeminiTokenCounter):
        result["tokenizer"] = {
            "type": "gemini",
            "model": token_counter.model,
            "cache": {
                "hits": token_counter.cache_info().hits,
                "misses": token_counter.cache_info().misses,
                "currsize": token_counter.cache_info().currsize,
            },
        }
    else:
        result["tokenizer"] = {
            "type": token_count_mode(token_counter)
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
