"""스냅샷 시나리오를 PipelineAdapter로 실행한 뒤 평가하고 저장한다.

사용법: python -m benchmark.runner --help
시나리오 정답과 고정된 검색·토큰 fixture는 전략이나 Agent에 전달하지 않는다.
이 모듈은 합성 대화 이력 생성기가 아니다.
"""

from __future__ import annotations

import argparse
import hashlib
import os
if os.name == "nt":
    import msvcrt
else:
    import fcntl
from contextlib import nullcontext
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timedelta
from enum import Enum
import importlib
import importlib.metadata as importlib_metadata
import platform
import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Any

from agent.agent import Agent
from agent.evaluation import scenario_with_granted_approvals
from agent.llm_planner import ACTION_SCHEMA, LLMRunConfig, SYSTEM_INSTRUCTION

from context.tokenizer import (
    GeminiTokenCounter, TARGET_GEMINI_MODEL, TokenCounter, count_tokens,
    provisional_token_count, token_count_mode,
)

from memory.retrieval import Embedder
from memory.schema import MemoryItem
from memory.selector import PipelineStatus
from memory.ablation import ABLATION_PRESETS, FULL_ABLATION, SelectorAblationConfig, get_ablation_config

from pipeline.orchestrator import GoalChecker, PipelineRunResult
from pipeline.pipeline_adapter import AgentFactory, make_pipeline_adapter

from .evaluator import aggregate_results, evaluate
from .experiment_settings import EXPERIMENT_EMBEDDER_SPEC, EXPERIMENT_EMBEDDING_MODEL


STRATEGIES = ("sliding_window", "vector_top_k", "utility_per_token", "proposed")
EnvironmentAgentFactory = Callable[[dict[str, Any]], Agent]

FREEZE_VERSION = "1.0.5"
FROZEN_MODEL = "gemini-3.1-flash-lite"
FROZEN_SYSTEM_PROMPT_FILE = "experiments/prompts/system_v1.txt"
FROZEN_TOOL_DEFINITIONS_FILE = "experiments/prompts/tools_v1.txt"
FROZEN_ALGORITHM_SPEC_FILE = "docs/algorithm.md"
FROZEN_EMBEDDER = EXPERIMENT_EMBEDDER_SPEC
FROZEN_EMBEDDING_MODEL = EXPERIMENT_EMBEDDING_MODEL
FROZEN_ALGORITHM_SHA256 = "4a7dbd4b3664def90015540eff1faf6487a87bc9047adf8f60ce8bbce6fc6870"
FROZEN_PROMPT_SHA256 = {
    FROZEN_SYSTEM_PROMPT_FILE: (
        "9ad2e2d481997048ba770648ef1d07050a098a77674f5187c9dd645f75b6fac5"
    ),
    FROZEN_TOOL_DEFINITIONS_FILE: (
        "cc701a57b9aa951060ccfd28d69e74955e37af1e709ca932e04b96d725e61c3e"
    ),
}
FROZEN_PLANNER_BEHAVIOR_SHA256 = {
    "SYSTEM_INSTRUCTION": "aff1c564d3d2c2d9aa28e1af91cf722c00588db88bad54d276bb4c0cdab6307f",
    "ACTION_SCHEMA": "634277e47b8b38427623d5d9116289d90d04868a43f4753ed7ab779a750370b9",
}
EXECUTABLE_STATUS = "executable"
NON_EXECUTABLE_STATUSES = frozenset({"generation_only", "deferred"})
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _open_lock_file(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    
    stream = path.open("a+b")
    
    stream.seek(0, os.SEEK_END)
    
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
        os.fsync(stream.fileno())
        
    stream.seek(0)
    
    return stream


def _try_lock(stream) -> bool:
    stream.seek(0)
    
    if os.name == "nt":
        try:
            msvcrt.locking(
                stream.fileno(),
                msvcrt.LK_NBLCK,
                1,
            )
            return True
        except OSError:
            return False
        
    try:
        fcntl.flock(
            stream.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,
        )
        return True
    except BlockingIOError:
        return False
    
    
def _unlock(stream) -> None:
    if stream.closed:
        return
    
    stream.seek(0)
    
    if os.name == "nt":
        try:
            msvcrt.locking(
                stream.fileno(),
                msvcrt.LK_UNLCK,
                1,
            )
        except OSError:
            pass
        return
    
    try:
        fcntl.flock(
            stream.fileno(),
            fcntl.LOCK_UN,
        )
    except OSError:
        pass


def _algorithm_spec_sha256() -> str:
    path = PROJECT_ROOT / FROZEN_ALGORITHM_SPEC_FILE
    if not path.is_file():
        raise ValueError(
            f"frozen algorithm specification does not exist: {FROZEN_ALGORITHM_SPEC_FILE}"
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _planner_behavior_sha256() -> dict[str, str]:
    action_schema = json.dumps(
        ACTION_SCHEMA,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "SYSTEM_INSTRUCTION": hashlib.sha256(
            SYSTEM_INSTRUCTION.encode("utf-8")
        ).hexdigest(),
        "ACTION_SCHEMA": hashlib.sha256(action_schema).hexdigest(),
    }


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


def _git_revision() -> dict[str, Any]:
    """Resolve this checkout, independent of the caller's working directory."""
    root = Path(__file__).resolve().parent.parent
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root,
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "diff-index", "--quiet", "HEAD", "--"], cwd=root,
            capture_output=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None}
    return {"git_commit": commit,
            "git_dirty": bool(dirty.returncode) if dirty.returncode in (0, 1) else None}


def _distribution_version(distribution_name: str) -> str | None:
    try:
        return importlib_metadata.version(distribution_name)
    except importlib_metadata.PackageNotFoundError:
        return None
    
    
def _embedding_runtime_metadata(embedder: Embedder | None) -> dict[str, Any] | None:
    if embedder is None:
        return None
    
    module_name = getattr(
        embedder,
        "__module__",
        None,
    )
    
    qualname = getattr(
        embedder,
        "__qualname__",
        getattr(embedder, "__name__", None),
    )
    
    metadata: dict[str, Any] = {
        "callable": f"{module_name}:{qualname}" if module_name and qualname else repr(embedder),
    }
    
    if not isinstance(module_name, str):
        return metadata
    
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return metadata
    
    getter = getattr(
        module,
        "embedding_runtime_metadata",
        None,
    )
    
    if getter is None:
        return metadata
    
    if not callable(getter):
        raise TypeError("embedding_runtime_metadata must be callable")
    
    details = getter()
    
    if not isinstance(details, Mapping):
        raise TypeError("embedding_runtime_metadata() must return a mapping")
    
    metadata.update(_json_record(details))
    
    return metadata


def _runtime_environment_metadata(embedder: Embedder | None) -> dict[str, Any]:
    return {
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        
        "packages": {
            "google-genai": _distribution_version("google-genai"),
            "sentence-transformers": _distribution_version("sentence-transformers"),
            
            "transformers": _distribution_version("transformers"),
            "huggingface-hub": _distribution_version("huggingface-hub"),
            "torch": _distribution_version("torch"),
        },
        
        "embedding": _embedding_runtime_metadata(embedder),
    }


def _prompt_checksums(system_prompt: str, tool_definitions: str,
                      llm_config: LLMRunConfig | None) -> dict[str, str | None]:
    """SHA-256 of the exact UTF-8 strings supplied to the runtime."""
    prompts = {"system_prompt": system_prompt, "tool_definitions": tool_definitions,
               "system_instruction": None if llm_config is None else llm_config.system_instruction}
    return {name: None if value is None else hashlib.sha256(value.encode("utf-8")).hexdigest()
            for name, value in prompts.items()}



def _report_progress(completed: int, total: int, started: float, message: str, *, initial_completed: int = 0) -> None:
    """진행 표시는 stderr로 보내 결과 JSON stdout과 분리한다."""
    elapsed = max(0.0, perf_counter() - started)
    eta = (str(timedelta(seconds=int(elapsed * (total - completed) / (completed - initial_completed))))
           if completed > initial_completed else "--:--:--")
    filled = 20 * completed // total
    bar = "#" * filled + "-" * (20 - filled)
    line = (f"[{bar}] {completed}/{total} ({100 * completed / total:.1f}%) "
            f"elapsed={timedelta(seconds=int(elapsed))} ETA~{eta} | {message}")
    terminal = sys.stderr.isatty()
    print(("\r" if terminal else "") + line + ("\033[K" if terminal else ""),
          file=sys.stderr, end="" if terminal else "\n", flush=True)



def _atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def _read_saved_rows(path: Path) -> tuple[list[dict], int]:
    """Only newline-terminated records are committed; a torn last write is ignored."""
    rows = []
    committed_bytes = 0
    if path.exists():
        with path.open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    break
                rows.append(json.loads(line))  # Corrupt committed records must fail loudly.
                committed_bytes += len(line)
    return rows, committed_bytes


def _row_key(row: Mapping[str, Any]) -> tuple[str, str, int]:
    evaluation = row["evaluation"]
    return evaluation["sample_id"], row["strategy"], row["repeat_id"]


def _partial_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    groups = {}
    for row in rows:
        evaluation = row["evaluation"]
        group = groups.setdefault(row["strategy"], dict(completed=0, task_successes=0,
                                                       constraint_violating_runs=0,
                                                       constraint_evaluated=0,
                                                       final_parse_fallbacks=0, failure_counts={}, metrics={}))
        for failure in evaluation.get("failure_types", []):
            group["failure_counts"][failure] = group["failure_counts"].get(failure, 0) + 1
        for name in (
            "required_state_accuracy", 
            "temporal_version_correct",
            "constraint_violation_rate", 
            "input_tokens", 
            "output_tokens",
            "planning_latency_ms",
            "total_llm_cost",
        ):
            value = evaluation.get(name)
            if isinstance(value, (int, float)):
                metric = group["metrics"].setdefault(name, {"sum": 0, "n": 0})
                metric["sum"] += value
                metric["n"] += 1
        group["completed"] += 1
        group["task_successes"] += int(evaluation["task_success"])
        violation = evaluation.get("constraint_violation_rate")
        if violation is not None:
            group["constraint_evaluated"] += 1
            group["constraint_violating_runs"] += int(bool(violation))
        group["final_parse_fallbacks"] += int(
            row["run_result"].get("response") == "모델 응답을 행동으로 읽지 못했습니다.")
    for group in groups.values():
        for metric in group["metrics"].values():
            metric["mean"] = metric.pop("sum") / metric["n"]
        group["task_success_rate"] = group["task_successes"] / group["completed"]
    return groups


def checkpoint_status(directory: str | Path) -> dict[str, Any]:
    """Read committed results without constructing a model, tokenizer or embedder."""
    output = Path(directory)
    progress = json.loads((output / "progress.json").read_text(encoding="utf-8"))
    rows, _ = _read_saved_rows(output / "runs.jsonl")
    lock = _open_lock_file(output / ".runner.lock")
    
    try:
        acquired = _try_lock(lock)
        
        if acquired:
            active = False
            _unlock(lock)
        else:
            active = True
            
    finally:
        lock.close()
    
    if progress["state"] == "running" and not active:
        progress["state"] = "interrupted"
    progress.update(runner_active=active, completed=len(rows),
                    percent=100 * len(rows) / progress["total"],
                    last_result_at=datetime.fromtimestamp((output / "runs.jsonl").stat().st_mtime).astimezone().isoformat(),
                    by_strategy=_partial_metrics(rows),
                    provisional=progress["state"] != "completed",
                    note="Completed samples only; runner_active checks the writer lock, not API responsiveness.")
    return progress


class _RunCheckpoint:
    def __init__(self, output: Path, metadata: dict, identity: dict,
                 expected: set[tuple], resume: bool, interval: int):
        self.output, self.metadata = output, metadata
        self.identity, self.expected = identity, expected
        self.resume, self.interval = resume, interval
        self.rows = []
        self.current = None
        self.state = "running"

    def __enter__(self):
        self.output.mkdir(parents=True, exist_ok=True)
        self.lock = _open_lock_file(self.output / ".runner.lock")
        try:
            if not _try_lock(self.lock):
                raise ValueError("another runner is using this output directory")
            metadata_path = self.output / "experiment.json"
            runs_path = self.output / "runs.jsonl"
            if self.resume:
                saved = json.loads(metadata_path.read_text(encoding="utf-8"))
                if saved.get("resume_identity") != self.identity:
                    raise ValueError("resume rejected: dataset, code or runtime settings changed")
                self.rows, committed_bytes = _read_saved_rows(runs_path)
                keys = [_row_key(row) for row in self.rows]
                if len(set(keys)) != len(keys) or not set(keys) <= self.expected:
                    raise ValueError("invalid or duplicate saved run keys")
                runs_path.touch(exist_ok=True)
                with runs_path.open("r+b") as stream:
                    stream.truncate(committed_bytes)
                # A previous final summary must not look current during resumed execution.
                (self.output / "summary.json").unlink(missing_ok=True)
            else:
                if any((self.output / name).exists() for name in
                       ("runs.jsonl", "experiment.json", "summary.json", "progress.json")):
                    raise ValueError("output already contains results; use --resume or a new directory")
                self.metadata["resume_identity"] = self.identity
                _atomic_json(metadata_path, self.metadata)
                runs_path.touch()
            self.stream = runs_path.open("a", encoding="utf-8")
            self.completed = {_row_key(row) for row in self.rows}
            self.publish()
            return self
        except BaseException:
            if hasattr(self, "stream"):
                self.stream.close()
                
            _unlock(self.lock)
            self.lock.close()
            
            raise

    def publish(self):
        _atomic_json(self.output / "progress.json", dict(
            state=self.state, completed=len(self.rows), total=len(self.expected),
            updated_at=datetime.now().astimezone().isoformat(), current_at_checkpoint=self.current,
            provisional=self.state != "completed", by_strategy=_partial_metrics(self.rows),
            diagnostics_note="final_parse_fallbacks is a text-match diagnostic, not a typed planner error metric",
        ))

    def append(self, row):
        record = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        self.stream.write(record)
        self.stream.flush()
        os.fsync(self.stream.fileno())
        self.rows.append(row)
        self.completed.add(_row_key(row))
        if len(self.rows) % self.interval == 0:
            self.publish()

    def __exit__(self, kind, error, traceback):
        try:
            if kind is not None:
                self.state = "interrupted"
            self.publish()
        finally:
            self.stream.close()
            
            _unlock(self.lock)
            self.lock.close()


def _callable_identity(function: Any) -> str | None:
    if function is None:
        return None
    return f"{getattr(function, '__module__', type(function).__module__)}:{getattr(function, '__qualname__', type(function).__qualname__)}"


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


def _prompt_token_audit(
    *,
    executed_steps,
    actual_prompt_tokens,
    nominal_context_budget: int,
    effective_context_budget: int,
) -> dict[str, Any]:
    if type(nominal_context_budget) is not int or nominal_context_budget < 0:
        raise ValueError("nominal_context_budget must be a non-negative integer")
    
    if type(effective_context_budget) is not int or effective_context_budget < 0:
        raise ValueError("effective_context_budget must be a non-negative integer")
    
    if effective_context_budget > nominal_context_budget:
        raise ValueError("effective_context_budget cannot exceed nominal_context_budget")
    
    counted = [
        step.context_result.total_input_tokens
        for step in executed_steps
    ]
    
    raw_actual = [] if actual_prompt_tokens is None else list(actual_prompt_tokens)
    
    actual: list[int | None] = []
    
    for value in raw_actual:
        if value is None:
            actual.append(None)
        elif type(value) is int and value >= 0:
            actual.append(value)
        else:
            raise ValueError("actual prompt tokens must contain non-negative integers or None")
        
    call_count_match = len(counted) == len(actual)
    
    complete = call_count_match and all(
        value is not None
        for value in actual
    )
    
    pair_count = min(
        len(counted),
        len(actual),
    )
    
    deltas: list[int | None] = []
    
    for index in range(pair_count):
        actual_value = actual[index]
        
        if actual_value is None:
            deltas.append(None)
        else:
            deltas.append(actual_value - counted[index])
            
    valid_deltas = [
        value
        for value in deltas
        if value is not None
    ]
    
    max_positive_delta = max(0, max(valid_deltas)) if valid_deltas else None
    
    max_absolute_delta = max(abs(value) for value in valid_deltas) if valid_deltas else None
    
    max_actual = (
        max(
            value
            for value in actual
            if value is not None
        )
        if any(
            value is not None
            for value in actual
        )
        else None
    )
    
    max_counted = max(counted) if counted else None
    
    counted_budget_exceeded = any(
        value > effective_context_budget
        for value in counted
    )
    
    observed_overflow = any(
        value > nominal_context_budget
        for value in actual
        if value is not None
    )
    
    if observed_overflow:
        actual_budget_exceeded = True
    elif complete:
        actual_budget_exceeded = False
    else:
        actual_budget_exceeded = None
        
    if not counted and not actual:
        verification_status = "not_applicable"
    elif actual_budget_exceeded is True:
        verification_status = "exceeded"
    elif complete:
        verification_status = "verified"
    else:
        verification_status = "unverified"
    
    return {
        "nominal_context_budget": nominal_context_budget,
        
        "effective_context_budget": effective_context_budget,
        
        "request_token_safety_margin": nominal_context_budget - effective_context_budget,
        
        "counted_prompt_tokens_per_call": counted,
        "actual_prompt_tokens_per_call": actual,
        
        "delta_actual_minus_counted_per_call": deltas,
        
        "call_count_match": call_count_match,
        "complete": complete,
        
        "max_counted_prompt_tokens": max_counted,
        "max_actual_prompt_tokens": max_actual,
        
        "max_positive_delta": max_positive_delta,
        "max_absolute_delta": max_absolute_delta,
        
        "counted_budget_exceeded": counted_budget_exceeded,
        "actual_budget_exceeded": actual_budget_exceeded,
        
        "verification_status": verification_status,
    }


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
    metric_k: int,
    utility_weights: tuple[float, float, float],
    max_steps: int,
    goal_checker: GoalChecker | None,
    proposed_ablation: SelectorAblationConfig | None,
    llm_config: LLMRunConfig | None,
    request_token_safety_margin: int,
) -> dict[str, Any]:
    nominal_context_budget = scenario["context_budget"]
    
    effective_context_budget = nominal_context_budget - request_token_safety_margin
    
    if effective_context_budget < 0:
        raise ValueError("request_token_safety_margin exceeds scenario context budget")
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
        total_budget=effective_context_budget,
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
    
    llm_usage = outcome.llm_usage or {}
    
    prompt_token_audit = _prompt_token_audit(
        executed_steps=executed_steps,
        actual_prompt_tokens=llm_usage.get("prompt_tokens_per_call"),
        nominal_context_budget=nominal_context_budget,
        effective_context_budget=effective_context_budget,
    )
    
    run.update(
        prompt_token_audit=prompt_token_audit,
        selection_result=_json_record(selection),
        context_result=_json_record(context),
        token_count_mode=token_count_mode,
        token_counter_metadata=({key: value for key, value in token_counter.metadata().items()
                                 if key != "stats"}
                                if isinstance(token_counter, GeminiTokenCounter) else None),
        strategy_options=dict(
            top_k=top_k,
            candidate_top_k=top_k,
            metric_k=metric_k,
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
            
            output_tokens=llm_usage.get("output_tokens"),
            llm_prompt_tokens=llm_usage.get("prompt_tokens"),
            llm_prompt_tokens_per_call=llm_usage.get("prompt_tokens_per_call"),
            llm_max_prompt_tokens=llm_usage.get("max_prompt_tokens"),
            llm_cached_prompt_tokens=llm_usage.get("cached_prompt_tokens"),
            llm_tool_use_prompt_tokens=llm_usage.get("tool_use_prompt_tokens"),
            llm_candidate_tokens=llm_usage.get("candidate_tokens"),
            llm_thoughts_tokens=llm_usage.get("thoughts_tokens"),
            llm_total_tokens=llm_usage.get("total_tokens"),
            llm_api_calls=llm_usage.get("api_calls"),
            
            llm_successful_response_calls=llm_usage.get("successful_response_calls"),
            llm_usage_metadata_calls=llm_usage.get("usage_metadata_calls"),
            llm_cost_usage_complete_calls=llm_usage.get("cost_usage_complete_calls"),
            llm_billing_uncertain_attempts=llm_usage.get("billing_uncertain_attempts"),
            llm_prompt_usage_complete=llm_usage.get("prompt_usage_complete"),
            llm_cost_estimate_complete=llm_usage.get("cost_estimate_complete"),
            llm_accounting_status=llm_usage.get("accounting_status"),
        ),
        
        planning_latency_ms=outcome.planning_latency_ms,
        total_llm_cost=outcome.total_llm_cost,
        
        llm_usage=llm_usage or None,
        
        agent_executed=outcome.agent_executed,
        mandatory_memory_overflow=_mandatory_memory_overflow(outcome),
        
        nominal_context_budget=nominal_context_budget,
        effective_context_budget=effective_context_budget,
        request_token_safety_margin=request_token_safety_margin,
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
        evaluation_scenario, run, strategy=strategy_label, repeat_id=repeat_id, k=metric_k,
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
    top_k: int = 5,
    metric_k: int | None = None,
    repeats: int = 1,
    utility_weights: tuple[float, float, float] = (0.6, 0.3, 0.1),
    bootstrap_iterations: int = 2000,
    seed: int = 42,
    max_steps: int = 8,
    goal_checker: GoalChecker | None = None,
    proposed_ablations: Sequence[str] = ("full",),
    llm_config: LLMRunConfig | None = None,
    config_metadata: Mapping[str, Any] | None = None,
    cli_overrides: Mapping[str, Any] | None = None,
    show_progress: bool = False,
    resume: bool = False,
    checkpoint_interval: int = 100,
    request_token_safety_margin: int = 0,
) -> dict[str, Any]:
    """활성 integration 시나리오를 전략·반복별로 실행한다.

    오프라인 평가기 입력과 개별 평가를 runs.jsonl에 저장하고, 기존 평가기의
    집계 목록을 summary.json에 저장한다. 개별 결과는 즉시 저장하며 resume으로
    완료된 실행을 건너뛴다. 기존 출력은 명시적인 resume 없이 덮어쓰지 않는다. 오류를 성공 결과로 바꾸지 않고 예외로 전달한다.
    미지원 시나리오와 모듈 전용 시나리오는 TSR 표본에 포함하지 않는다.
    기본 공백 기준 TokenCounter는 임시 측정이며 실제 모델 토큰 사용량이 아니다.
    agent_factory 또는 environment_agent_factory 중 하나만 지정한다. 후자는
    scenario.environment의 files와 valid_approval_ids 사본만 받아 실행마다
    새 Agent와 mock Tool을 초기화한다. 정답이나 query는 factory에 넘기지 않는다.
    다단계 선택·Context 조립·
    행동 실행은 Orchestrator에 맡기고 단계별 기록과 누적 입력량을 결과에 보존한다.
    """
    if type(resume) is not bool:
        raise TypeError("resume must be a boolean")
    if type(checkpoint_interval) is not int or checkpoint_interval < 1:
        raise ValueError("checkpoint_interval must be a positive integer")
    factory_identity = dict(agent=_callable_identity(agent_factory),
                            environment=_callable_identity(environment_agent_factory),
                            embedder=_callable_identity(embedder),
                            token_counter=_callable_identity(token_counter),
                            goal_checker=_callable_identity(goal_checker))
    if type(show_progress) is not bool:
        raise TypeError("show_progress must be a boolean")
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
    if metric_k is None:
        metric_k = top_k
    if type(metric_k) is not int or metric_k <= 0:
        raise ValueError("metric_k must be a positive integer")
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
    if llm_config is not None and isinstance(token_counter, GeminiTokenCounter):
        if llm_config.model != token_counter.model:
            raise ValueError("LLM model and Gemini token counter model must match")
        configure_request = getattr(token_counter, "configure_request", None)
        generation_config = getattr(llm_config, "generation_config", None)
        if callable(configure_request):
            request_config = (generation_config() if callable(generation_config)
                              else llm_config.planner().generation_config())
            configure_request(
                system_instruction=llm_config.system_instruction,
                generation_config=request_config,
            )
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
        raise ValueError(
            "scenario/runtime token counter mismatch: "
            f"scenario={sorted(scenario_modes)}, runtime={mode}; "
            "regenerate scenarios with the runtime model instead of relabeling metadata"
        )
        
    if type(request_token_safety_margin) is not int or request_token_safety_margin < 0:
        raise ValueError("request_token_safety_margin must be a non-negative integer")

    smallest_budget = min(
        scenario["context_budget"]
        for scenario in enabled
    )
    
    if request_token_safety_margin > 0 and request_token_safety_margin >= smallest_budget:
        raise ValueError("request_token_safety_margin must be smaller than every context budget")

    if isinstance(token_counter, GeminiTokenCounter):
        tokenizer_metadata = token_counter.metadata()
    else:
        tokenizer_metadata = {
            "type": token_count_mode(token_counter)
        }

    runtime_environment = _runtime_environment_metadata(embedder)

    provenance = dict(
        **_git_revision(),
        runtime_environment=runtime_environment,
        prompt_checksums=_prompt_checksums(system_prompt, tool_definitions, llm_config),
        bootstrap_seed=seed,
        dataset_seeds=sorted({scenario.get("metadata", {}).get("seed") for scenario in enabled
                              if scenario.get("metadata", {}).get("seed") is not None}),
    )
    planner_behavior_hashes = _planner_behavior_sha256()
    experiment_metadata = {
        **provenance,
        "strategies": list(strategies),
        "proposed_ablations": list(proposed_ablations),
        "n_scenarios": len(enabled),
        "repeats": repeats,
        "top_k": top_k,
        "candidate_top_k": top_k,
        "metric_k": metric_k,
        "utility_weights": list(utility_weights),
        "max_steps": max_steps,
        "bootstrap_iterations": bootstrap_iterations,
        "token_count_mode": mode,
        "tokenizer": tokenizer_metadata,
        "token_cache_policy": "isolated_per_run" if isinstance(token_counter, GeminiTokenCounter) else None,
        "scenario_ids": ids,
        "system_prompt": system_prompt,
        "tool_definitions": tool_definitions,
        "algorithm_hash": _algorithm_spec_sha256(),
        "planner_rules_hash": planner_behavior_hashes["SYSTEM_INSTRUCTION"],
        "action_schema_hash": planner_behavior_hashes["ACTION_SCHEMA"],
        "llm": None if llm_config is None else llm_config.to_dict(),
        "config": None if config_metadata is None else _json_record(config_metadata),
        "cli_overrides": {} if cli_overrides is None else _json_record(cli_overrides),
        "request_token_safety_margin": request_token_safety_margin,
    }

    output = Path(output_directory)
    identity_settings = dict(experiment_metadata, factories=factory_identity)
    identity_settings["tokenizer"] = {key: value for key, value in tokenizer_metadata.items()
                                      if key != "stats"}  # Preserve configuration, exclude counters.
    identity_settings.pop("cli_overrides", None)
    identity_settings.pop("config", None)
    code_root = Path(__file__).resolve().parent.parent
    code_hash = hashlib.sha256()
    for package in ("agent", "benchmark", "context", "memory", "pipeline"):
        for source in sorted((code_root / package).rglob("*.py")):
            code_hash.update(str(source.relative_to(code_root)).encode())
            code_hash.update(source.read_bytes())
    identity = dict(
        settings=hashlib.sha256(json.dumps(identity_settings, ensure_ascii=False, sort_keys=True,
                                          allow_nan=False).encode()).hexdigest(),
        scenarios=hashlib.sha256(json.dumps(_json_record(enabled), ensure_ascii=False,
                                           sort_keys=True, allow_nan=False).encode()).hexdigest(),
        code=code_hash.hexdigest(),
    )
    expected = {(sid, strategy if strategy != "proposed" or ablation == "full"
                 else f"proposed/{ablation}", repeat)
                for sid in ids for strategy in strategies
                for ablation in (proposed_ablations if strategy == "proposed" else (None,))
                for repeat in range(repeats)}
    with _RunCheckpoint(output, experiment_metadata, identity, expected,
                        resume, checkpoint_interval) as checkpoint:
        rows = checkpoint.rows
        total_runs = len(enabled) * repeats * sum(
            len(proposed_ablations) if strategy == "proposed" else 1 for strategy in strategies
        )
        initial_completed = len(rows)
        progress_started = perf_counter()

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
                        label = strategy if ablation is None or ablation.name == "full" else f"proposed/{ablation.name}"
                        if (scenario.get("scenario_id", scenario.get("sample_id")), label, repeat_id) in checkpoint.completed:
                            continue
                        current = f"{scenario.get('scenario_id', scenario.get('sample_id'))} | {label} | repeat {repeat_id + 1}/{repeats}"
                        checkpoint.current = current
                        if show_progress:
                            _report_progress(len(rows), total_runs, progress_started, "running " + current, initial_completed=initial_completed)
                        try:
                            # 영속 캐시도 실행 간 공유하지 않는다. 원본 DB는 보존한다.
                            cache_scope = (token_counter.isolated_cache()
                                           if isinstance(token_counter, GeminiTokenCounter) else nullcontext())
                            with cache_scope:
                                row = _run_one(
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
                                    metric_k=metric_k,
                                    utility_weights=utility_weights,
                                    max_steps=max_steps,
                                    goal_checker=goal_checker,
                                    proposed_ablation=ablation,
                                    llm_config=llm_config,
                                    request_token_safety_margin=request_token_safety_margin,
                                )
                                row.update(provenance)
                                row["dataset_seed"] = scenario.get("metadata", {}).get("seed")
                                checkpoint.append(row)
                        except (Exception, KeyboardInterrupt):
                            if show_progress:
                                _report_progress(len(rows), total_runs, progress_started, "interrupted " + current, initial_completed=initial_completed)
                                if sys.stderr.isatty():
                                    print(file=sys.stderr, flush=True)
                            raise
                        if show_progress:
                            _report_progress(len(rows), total_runs, progress_started, "finished " + current, initial_completed=initial_completed)

        if show_progress:
            _report_progress(len(rows), total_runs, progress_started, "aggregating and saving", initial_completed=initial_completed)
        summary = aggregate_results(
            [row["evaluation"] for row in rows],
            bootstrap_iterations=bootstrap_iterations, seed=seed,
        )
        _atomic_json(output / "summary.json", summary)
        experiment_metadata["resume_identity"] = identity
        if isinstance(token_counter, GeminiTokenCounter):
            experiment_metadata["tokenizer"] = token_counter.metadata()
        experiment_metadata["tokenizer_stats_scope"] = "current_invocation"
        _atomic_json(output / "experiment.json", experiment_metadata)
        checkpoint.state = "completed"
        checkpoint.current = None
        if show_progress:
            _report_progress(len(rows), total_runs, progress_started, f"saved {output}", initial_completed=initial_completed)
            if sys.stderr.isatty():
                print(file=sys.stderr, flush=True)
        return dict(
            **provenance,
            n_runs=len(rows),
            n_scenarios=len(enabled),
            summary=summary,
            runs_path=str(output / "runs.jsonl"),
            summary_path=str(output / "summary.json"),
            experiment_path=str(output / "experiment.json"),
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


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"config {name} must be an object")
    return value


def _load_runner_config(path: str | Path) -> dict[str, Any]:
    """Load and validate the versioned, executable parameter freeze."""
    loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError("config must be a JSON object")

    freeze = _mapping(loaded.get("freeze"), "freeze")
    if freeze.get("kind") != "architecture_parameter_freeze":
        raise ValueError("config freeze.kind must be 'architecture_parameter_freeze'")
    if freeze.get("version") != FREEZE_VERSION:
        raise ValueError(f"config freeze.version must be {FREEZE_VERSION!r}")
    scope = loaded.get("scope")
    if not isinstance(scope, str) or not scope.strip():
        raise ValueError("config scope must be a non-empty string")
    if loaded.get("name") != scope:
        raise ValueError("config scope must match config name")
    status = loaded.get("status")
    if status not in {EXECUTABLE_STATUS, *NON_EXECUTABLE_STATUSES}:
        raise ValueError("config status must be executable, generation_only, or deferred")
    expected_executable = status == EXECUTABLE_STATUS
    if loaded.get("executable") is not expected_executable:
        raise ValueError("config executable flag does not match status")
    if not expected_executable:
        reason = loaded.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("non-executable config must declare a reason")

    dataset = _mapping(loaded.get("dataset"), "dataset")
    if dataset.get("scenario_profile") != "balanced_v2":
        raise ValueError("config dataset.scenario_profile must be frozen to 'balanced_v2'")

    model = _mapping(loaded.get("model"), "model")
    runtime = _mapping(loaded.get("runtime"), "runtime")
    token_counter = _mapping(runtime.get("token_counter"), "runtime.token_counter")
    evaluation = _mapping(loaded.get("evaluation"), "evaluation")

    expected_model = {
        "provider": "google",
        "name": FROZEN_MODEL,
        "temperature": 0,
        "max_output_tokens": 2048,
        "thinking_level": "LOW",
        "system_prompt_file": FROZEN_SYSTEM_PROMPT_FILE,
        "tool_definitions_file": FROZEN_TOOL_DEFINITIONS_FILE,
        "max_retries": 2,
        "retry_base_seconds": 0.5,
        "timeout_ms": 60000,
    }
    for key, expected in expected_model.items():
        if model.get(key) != expected:
            raise ValueError(f"config model.{key} must be frozen to {expected!r}")
    if model["provider"] != "google":
        raise ValueError("config model.provider must be google")
    if model["thinking_level"] not in {"LOW", "MEDIUM", "HIGH"}:
        raise ValueError("config model.thinking_level must be LOW, MEDIUM, or HIGH")

    expected_runtime = {
        "candidate_top_k": 5,
        "max_steps": 8,
        "utility_weights": [0.6, 0.3, 0.1],
        "embedder": FROZEN_EMBEDDER,
        "embedding_model": FROZEN_EMBEDDING_MODEL,
    }
    for key, expected in expected_runtime.items():
        if runtime.get(key) != expected:
            raise ValueError(f"config runtime.{key} must be frozen to {expected!r}")
    expected_counter = {
        "provider": "google",
        "model": FROZEN_MODEL,
        "cache_size": 4096,
        "persistent_cache_path": ".cache/gemini_token_cache.sqlite3",
        "max_retries": 3,
        "retry_base_seconds": 0.5,
    }
    for key, expected in expected_counter.items():
        if token_counter.get(key) != expected:
            raise ValueError(
                f"config runtime.token_counter.{key} must be frozen to {expected!r}"
            )
    if model["name"] != token_counter["model"]:
        raise ValueError("config model and token counter models must match")
    if type(evaluation.get("retrieval_k")) is not int or evaluation["retrieval_k"] < 1:
        raise ValueError("config evaluation.retrieval_k must be a positive integer")
    if expected_executable:
        _config_strategy_plan(loaded.get("strategies"))
    for prompt_path, expected_hash in FROZEN_PROMPT_SHA256.items():
        path = Path(prompt_path)
        if not path.is_file():
            raise ValueError(f"frozen prompt file does not exist: {prompt_path}")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual_hash != expected_hash:
            raise ValueError(
                f"frozen prompt changed: {prompt_path}; bump freeze.version before execution"
            )
    actual_algorithm_hash = _algorithm_spec_sha256()
    if actual_algorithm_hash != FROZEN_ALGORITHM_SHA256:
        raise ValueError(
            "frozen algorithm specification changed: "
            f"{FROZEN_ALGORITHM_SPEC_FILE}; bump freeze.version before execution"
        )
    planner_behavior_hashes = _planner_behavior_sha256()
    for name, expected_hash in FROZEN_PLANNER_BEHAVIOR_SHA256.items():
        actual_hash = planner_behavior_hashes[name]
        if actual_hash != expected_hash:
            raise ValueError(
                "frozen planner behavior changed: "
                f"agent.llm_planner:{name}; bump freeze.version before execution"
            )
    return loaded


def _require_executable_config(config: Mapping[str, Any]) -> None:
    status = config.get("status")
    if status != EXECUTABLE_STATUS or config.get("executable") is not True:
        reason = config.get("reason", "no execution reason declared")
        raise ValueError(f"config status {status!r} is not executable: {reason}")


def _validate_configured_scenario_profile(
    payload: Any, scenarios: Sequence[Mapping[str, Any]], expected_profile: str,
) -> None:
    """Keep frozen balanced runs from silently consuming old or mixed inputs."""
    if (isinstance(payload, Mapping) and "scenario_profile" in payload
            and payload["scenario_profile"] != expected_profile):
        raise ValueError(
            f"input scenario_profile must match configured {expected_profile!r}; "
            f"got {payload['scenario_profile']!r}"
        )
    for scenario in scenarios:
        if not isinstance(scenario, Mapping):
            raise ValueError("each input scenario must be an object")
        if (not scenario.get("benchmark_enabled", True)
                or scenario.get("validation_scope", "integration") != "integration"):
            continue
        metadata = scenario.get("metadata")
        actual = metadata.get("scenario_profile") if isinstance(metadata, Mapping) else None
        if actual != expected_profile:
            sample_id = scenario.get("scenario_id", scenario.get("sample_id"))
            raise ValueError(
                f"scenario {sample_id!r} metadata.scenario_profile must match configured "
                f"{expected_profile!r}; got {actual!r}. Regenerate the input or omit "
                "--config for an ad-hoc legacy run."
            )


def _config_strategy_plan(labels: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Map serialized ablation labels onto the runner's base strategy API."""
    if not isinstance(labels, list) or not labels or any(
        not isinstance(label, str) or not label for label in labels
    ):
        raise ValueError("config strategies must be a non-empty list of strings")
    if len(set(labels)) != len(labels):
        raise ValueError("config strategies must be unique")

    strategies: list[str] = []
    ablations: list[str] = []
    for label in labels:
        if label in STRATEGIES:
            if label not in strategies:
                strategies.append(label)
            if label == "proposed" and "full" not in ablations:
                ablations.append("full")
            continue
        prefix = "proposed/"
        if label.startswith(prefix):
            ablation = label[len(prefix):]
            if ablation not in ABLATION_PRESETS:
                raise ValueError(f"unsupported config strategy: {label}")
            if "proposed" not in strategies:
                strategies.append("proposed")
            if ablation not in ablations:
                ablations.append(ablation)
            continue
        raise ValueError(f"unsupported config strategy: {label}")
    return tuple(strategies), tuple(ablations or ["full"])


def _resolved_value(
    key: str,
    cli_value: Any,
    configured_value: Any,
    fallback: Any,
    *,
    has_config: bool,
    overrides: dict[str, Any],
) -> Any:
    if cli_value is not None:
        if has_config:
            overrides[key] = {
                "configured": _json_record(configured_value),
                "effective": _json_record(cli_value),
            }
        return cli_value
    return configured_value if has_config else fallback


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenarios", default="benchmark/scenarios.json")
    parser.add_argument("--scenario-ids", nargs="+")
    parser.add_argument("--resume", action="store_true", help="Resume matching saved runs")
    parser.add_argument("--status", action="store_true", help="Read saved progress only; requires --output")
    parser.add_argument("--checkpoint-interval", type=int, default=100, help="Refresh progress.json every N completed runs (results saved individually)")
    parser.add_argument("--no-progress", action="store_true", help="Disable progress on stderr")
    parser.add_argument("--strategies", nargs="+", choices=STRATEGIES)
    factory_group = parser.add_mutually_exclusive_group()
    factory_group.add_argument("--agent-factory", help="module:function returning a fresh Agent instance")
    factory_group.add_argument(
        "--environment-agent-factory",
        help="module:function receiving only initial files/grants and returning a fresh Agent",
    )
    parser.add_argument("--embedder", help="module:function taking text and returning a numeric vector")
    tokenizer_group = parser.add_mutually_exclusive_group()
    tokenizer_group.add_argument("--token-counter", help="Custom module:function taking text and returning token count")
    tokenizer_group.add_argument(
        "--gemini-token-model", "--gemini-model",
        help=f"Use Gemini CountTokens API with this model. Project target: {TARGET_GEMINI_MODEL}",
    )
    parser.add_argument("--gemini-token-cache-size", type=int)
    parser.add_argument("--config", help="Canonical versioned experiment parameter freeze JSON")
    parser.add_argument("--llm-model", help="Override the shared model name and enable the LLM planner.")
    parser.add_argument("--temperature", type=float, help="Override the shared temperature. Every strategy uses this value.")
    parser.add_argument("--max-output-tokens", type=int)
    parser.add_argument("--thinking-level", choices=("LOW", "MEDIUM", "HIGH"))
    parser.add_argument("--llm-max-retries", type=int)
    parser.add_argument("--llm-retry-base-seconds", type=float)
    parser.add_argument("--llm-timeout-ms", type=int)
    parser.add_argument("--system-prompt-file")
    parser.add_argument("--tool-definitions-file")
    parser.add_argument("--output")
    parser.add_argument("--top-k", type=int, help="Candidate retrieval K")
    parser.add_argument("--metric-k", type=int, help="Evaluation Recall@K/MRR@K cutoff")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--utility-weights", type=float, nargs=3)
    parser.add_argument("--bootstrap-iterations", type=int)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--gemini-token-cache")
    parser.add_argument("--gemini-token-retries", type=int)
    parser.add_argument("--gemini-token-retry-base-seconds", type=float)
    parser.add_argument("--proposed-ablations", nargs="+", choices=tuple(ABLATION_PRESETS.keys()))
    parser.add_argument("--request-token-safety-margin", type=int)
    args = parser.parse_args()
    if args.status:
        if not args.output:
            parser.error("--status requires --output")
        print(json.dumps(checkpoint_status(args.output), ensure_ascii=False, indent=2))
        return

    config: dict[str, Any] | None = None
    if args.config:
        try:
            config = _load_runner_config(args.config)
            _require_executable_config(config)
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            parser.error(str(exc))

    if not args.agent_factory and not args.environment_agent_factory:
        parser.error("one of --agent-factory or --environment-agent-factory is required")

    has_config = config is not None
    model_config = _mapping(config["model"], "model") if config else {}
    runtime_config = _mapping(config["runtime"], "runtime") if config else {}
    token_config = (_mapping(runtime_config["token_counter"], "runtime.token_counter")
                    if config else {})
    evaluation_config = _mapping(config["evaluation"], "evaluation") if config else {}
    budget_policy_config = _mapping(
        config["budget_policy"],
        "budget_policy",
    ) if config else {}
    overrides: dict[str, Any] = {}

    if (config and args.embedder is not None
            and args.embedder != runtime_config["embedder"]):
        parser.error(
            "--embedder cannot override the frozen experiment embedder; "
            "omit --config for an ad-hoc run"
        )

    if config:
        configured_strategies, configured_ablations = _config_strategy_plan(config["strategies"])
    else:
        configured_strategies, configured_ablations = STRATEGIES, ("full",)
    strategies = tuple(_resolved_value(
        "strategies", args.strategies, configured_strategies, STRATEGIES,
        has_config=has_config, overrides=overrides,
    ))
    proposed_ablations = tuple(_resolved_value(
        "proposed_ablations", args.proposed_ablations, configured_ablations, ("full",),
        has_config=has_config, overrides=overrides,
    ))
    output_directory = _resolved_value(
        "output_directory", args.output, config.get("output_directory") if config else None,
        "experiments/results/baselines", has_config=has_config, overrides=overrides,
    )
    top_k = _resolved_value(
        "candidate_top_k", args.top_k, runtime_config.get("candidate_top_k"), 5,
        has_config=has_config, overrides=overrides,
    )
    metric_k = _resolved_value(
        "metric_k", args.metric_k, evaluation_config.get("retrieval_k"), top_k,
        has_config=has_config, overrides=overrides,
    )
    repeats = _resolved_value(
        "repeats", args.repeats, config.get("repeats") if config else None, 1,
        has_config=has_config, overrides=overrides,
    )
    max_steps = _resolved_value(
        "max_steps", args.max_steps, runtime_config.get("max_steps"), 8,
        has_config=has_config, overrides=overrides,
    )
    utility_weights = tuple(_resolved_value(
        "utility_weights", args.utility_weights, runtime_config.get("utility_weights"),
        (0.6, 0.3, 0.1), has_config=has_config, overrides=overrides,
    ))
    bootstrap_iterations = _resolved_value(
        "bootstrap_iterations", args.bootstrap_iterations,
        evaluation_config.get("bootstrap_iterations"), 2000,
        has_config=has_config, overrides=overrides,
    )
    seed = _resolved_value(
        "bootstrap_seed", args.seed, evaluation_config.get("bootstrap_seed"), 42,
        has_config=has_config, overrides=overrides,
    )
    system_prompt_file = _resolved_value(
        "system_prompt_file", args.system_prompt_file, model_config.get("system_prompt_file"),
        None, has_config=has_config, overrides=overrides,
    )
    tool_definitions_file = _resolved_value(
        "tool_definitions_file", args.tool_definitions_file,
        model_config.get("tool_definitions_file"), None,
        has_config=has_config, overrides=overrides,
    )
    embedder_spec = _resolved_value(
        "embedder", args.embedder, runtime_config.get("embedder"), None,
        has_config=has_config, overrides=overrides,
    )
    if not system_prompt_file or not tool_definitions_file:
        parser.error(
            "--system-prompt-file and --tool-definitions-file are required without config declarations"
        )

    model_name = _resolved_value(
        "model.name", args.llm_model, model_config.get("name"), None,
        has_config=has_config, overrides=overrides,
    )
    temperature = _resolved_value(
        "model.temperature", args.temperature, model_config.get("temperature"), 0,
        has_config=has_config, overrides=overrides,
    )
    max_output_tokens = _resolved_value(
        "model.max_output_tokens", args.max_output_tokens,
        model_config.get("max_output_tokens"), 2048,
        has_config=has_config, overrides=overrides,
    )
    thinking_level = _resolved_value(
        "model.thinking_level", args.thinking_level, model_config.get("thinking_level"), "LOW",
        has_config=has_config, overrides=overrides,
    )
    llm_max_retries = _resolved_value(
        "model.max_retries", args.llm_max_retries, model_config.get("max_retries"), 2,
        has_config=has_config, overrides=overrides,
    )
    llm_retry_base_seconds = _resolved_value(
        "model.retry_base_seconds", args.llm_retry_base_seconds,
        model_config.get("retry_base_seconds"), 0.5,
        has_config=has_config, overrides=overrides,
    )
    llm_timeout_ms = _resolved_value(
        "model.timeout_ms", args.llm_timeout_ms, model_config.get("timeout_ms"), 60000,
        has_config=has_config, overrides=overrides,
    )

    token_model = _resolved_value(
        "token_counter.model", args.gemini_token_model, token_config.get("model"), None,
        has_config=has_config, overrides=overrides,
    )
    token_cache_size = _resolved_value(
        "token_counter.cache_size", args.gemini_token_cache_size,
        token_config.get("cache_size"), 4096,
        has_config=has_config, overrides=overrides,
    )
    token_cache_path = _resolved_value(
        "token_counter.persistent_cache_path", args.gemini_token_cache,
        token_config.get("persistent_cache_path"), ".cache/gemini_token_cache.sqlite3",
        has_config=has_config, overrides=overrides,
    )
    token_retries = _resolved_value(
        "token_counter.max_retries", args.gemini_token_retries,
        token_config.get("max_retries"), 3,
        has_config=has_config, overrides=overrides,
    )
    token_retry_base_seconds = _resolved_value(
        "token_counter.retry_base_seconds", args.gemini_token_retry_base_seconds,
        token_config.get("retry_base_seconds"), 0.5,
        has_config=has_config, overrides=overrides,
    )
    request_token_safety_margin = _resolved_value(
        "request_token_safety_margin",
        args.request_token_safety_margin,
        budget_policy_config.get("request_token_safety_margin"),
        0,
        has_config=has_config,
        overrides=overrides,
    )
    if args.token_counter and config:
        overrides["token_counter"] = {
            "configured": _json_record(token_config),
            "effective": args.token_counter,
        }

    effective_token_model = token_model or TARGET_GEMINI_MODEL
    if model_name and not args.token_counter and str(model_name) != effective_token_model:
        parser.error("LLM model and Gemini token counter model must match")

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
    if config:
        try:
            _validate_configured_scenario_profile(
                payload, scenarios, config["dataset"]["scenario_profile"],
            )
        except (TypeError, ValueError) as exc:
            parser.error(str(exc))
    if args.token_counter:
        token_counter = _import_callable(args.token_counter)
    else:
        token_counter = GeminiTokenCounter(
            model=token_model,
            cache_size=token_cache_size,
            persistent_cache_path=token_cache_path,
            max_retries=token_retries,
            retry_base_seconds=token_retry_base_seconds,
        )
    system_prompt = Path(system_prompt_file).read_text(encoding="utf-8")
    llm_config = None
    if model_name:
        llm_config = LLMRunConfig(
            model=str(model_name),
            temperature=0 if temperature is None else temperature,
            system_instruction=SYSTEM_INSTRUCTION,
            max_output_tokens=max_output_tokens,
            thinking_level=thinking_level,
            max_retries=llm_max_retries,
            retry_base_seconds=llm_retry_base_seconds,
            timeout_ms=llm_timeout_ms,
        )
    config_metadata = None if config is None else {
        "path": str(Path(args.config)),
        "freeze": config["freeze"],
        "scope": config["scope"],
        "status": config["status"],
        "embedder": {
            "callable": embedder_spec,
            "model": runtime_config["embedding_model"],
        },
    }
    result = run_scenarios(
        scenarios,
        agent_factory=_import_callable(args.agent_factory) if args.agent_factory else None,
        environment_agent_factory=(_import_callable(args.environment_agent_factory)
                                   if args.environment_agent_factory else None),
        system_prompt=system_prompt,
        tool_definitions=Path(tool_definitions_file).read_text(encoding="utf-8"),
        output_directory=output_directory, strategies=strategies,
        embedder=_import_callable(embedder_spec) if embedder_spec else None,
        token_counter=token_counter,
        top_k=top_k, metric_k=metric_k, repeats=repeats, utility_weights=utility_weights,
        bootstrap_iterations=bootstrap_iterations, seed=seed, max_steps=max_steps,
        proposed_ablations=proposed_ablations,
        llm_config=llm_config,
        config_metadata=config_metadata,
        cli_overrides=overrides,
        show_progress=not args.no_progress,
        resume=args.resume, checkpoint_interval=args.checkpoint_interval,
        request_token_safety_margin=request_token_safety_margin,
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
