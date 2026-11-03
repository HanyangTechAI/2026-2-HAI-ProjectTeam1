"""Reproducible temporal/approval data for the experiment config grids.

Generate inputs and evaluator ground truth, never execution results. Shared
sample IDs are written into separate grid-cell files for budget/horizon pairing.
Run ``python -m benchmark.generator --help`` for the offline CLI.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib
import itertools
import json
import math
from pathlib import Path
import random
import re
import tempfile
from typing import Any, Callable
import zipfile

from context.tokenizer import (
    GeminiTokenCounter, TokenCounter, count_tokens, provisional_token_count,
    token_count_mode,
)
from memory.schema import MemoryItem


TASK_TYPES = ("temporal_update", "approval")
GRID_KEYS = (
    "context_token_budget", "history_length_tokens", "horizon_turns",
    "noise_memory_ratio", "constraint_count", "state_update_count",
)
HISTORY_TOLERANCE = 0.01
NOISE_TOLERANCE = 0.01
# Padding contains no filenames, addresses, timestamps or approval IDs. The
# conversation is verbose; the memory snapshot retains its durable summaries.
NARRATIVE = (
    "회의에서는 검토 항목을 차례로 확인하고 진행 상황을 기록했습니다. "
    "팀원들은 자료의 구성과 설명 순서를 함께 살펴보았습니다. "
    "작업 노트에는 확인한 내용과 검토 의견을 구분해 정리했습니다. "
    "다음 논의에서는 표현과 가독성을 확인하기로 했습니다. "
    "이번 대화는 업무 진행 배경을 설명하는 보충 기록입니다."
).split()
BASE_TIME = datetime(2026, 1, 1, 9, tzinfo=timezone(timedelta(hours=9)))


def _integer(name: str, value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _ratio(value: Any) -> float:
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not 0 <= value < 1):
        raise ValueError("noise_memory_ratio must be finite and in [0, 1)")
    return float(value)


def render_history(history: Sequence[Mapping[str, Any]]) -> str:
    """Canonical history text whose *entire* token count is recorded.

    Turn, role and timestamp overhead are included; the final query is not part
    of the past interaction history. Evaluator annotations are never rendered.
    """
    return "\n".join(
        f"{entry['turn_id']} {entry['source']} {entry['timestamp']} {entry['content']}"
        for entry in history
    )


def _words(length: int, offset: int = 0) -> str:
    return " ".join(NARRATIVE[(i + offset) % len(NARRATIVE)] for i in range(length))


def _fit_text(
    build: Callable[[int], str], target: int, counter: TokenCounter, *, tolerance: int,
) -> tuple[int, int]:
    """Fit actual text, without assuming tokens are additive or words=tokens."""
    base = count_tokens(build(0), counter)
    if base > target + tolerance:
        raise ValueError(
            f"requested token length {target} is infeasible; minimum is {base} "
            "for these memories/turns/tokenizer"
        )
    if abs(base - target) <= tolerance:
        return 0, base
    low, high = 0, max(1, target - base)
    best = (0, base)
    # Bounded search also rejects counters that cannot measure generated text.
    for _ in range(20):
        if high > max(1024, target * 32):
            raise ValueError("token_counter cannot reach the requested token length")
        measured = count_tokens(build(high), counter)
        if measured <= base and high >= max(32, target):
            raise ValueError("token_counter does not measure added narrative text")
        if abs(measured - target) < abs(best[1] - target):
            best = high, measured
        if abs(measured - target) <= tolerance:
            return high, measured
        if measured >= target:
            break
        low, high = high, high * 2
    else:
        raise ValueError("token_counter cannot reach the requested token length")
    while high - low > 1:
        middle = (high + low) // 2
        measured = count_tokens(build(middle), counter)
        if abs(measured - target) < abs(best[1] - target):
            best = middle, measured
        if abs(measured - target) <= tolerance:
            return middle, measured
        if measured < target:
            low = middle
        else:
            high = middle
    if abs(best[1] - target) > tolerance:
        raise ValueError("token_counter cannot satisfy the requested token tolerance")
    return best


def _pad_memory(memory: dict[str, Any], target: int, counter: TokenCounter) -> None:
    original = memory["content"]

    def build(length: int) -> str:
        return original + (" " + _words(length) if length else "")

    length, measured = _fit_text(build, target, counter, tolerance=1)
    memory.update(content=build(length), token_count=measured)


def _balance_noise(
    memories: list[dict[str, Any]], targets: set[str], ratio: float,
    counter: TokenCounter,
) -> float:
    necessary = [m for m in memories if m["memory_id"] in targets]
    noise = [m for m in memories if m["memory_id"] not in targets]
    for _ in range(8):
        relevant_tokens = sum(m["token_count"] for m in necessary)
        noise_tokens = sum(m["token_count"] for m in noise)
        total = relevant_tokens + noise_tokens
        if not total or not relevant_tokens:
            raise ValueError("token_counter must assign positive cost to required memory")
        actual = noise_tokens / total
        if abs(actual - ratio) <= NOISE_TOLERANCE:
            return actual
        if not ratio:
            raise ValueError("zero noise is incompatible with non-target state versions")
        if actual > ratio:
            raise ValueError(
                "requested noise ratio is below the cost of existing non-target versions/notes; "
                "required memory is held fixed across noise conditions"
            )
        else:
            if not noise:
                raise ValueError("positive noise requires a non-target memory")
            desired = math.ceil(relevant_tokens * ratio / (1 - ratio))
            memory = noise[-1]
            _pad_memory(memory, memory["token_count"] + desired - noise_tokens, counter)
    raise ValueError("cannot satisfy noise_memory_ratio with this token_counter")


def _timestamp_for_turn(
    turn: int, events: list[dict[str, Any]], current_turn: int, current_time: datetime,
) -> datetime:
    anchors = [(0, BASE_TIME - timedelta(days=1))]
    anchors.extend((event["turn_id"], datetime.fromisoformat(event["valid_from"]))
                   for event in events)
    anchors.append((current_turn, current_time))
    for (left_turn, left_time), (right_turn, right_time) in zip(anchors, anchors[1:]):
        if left_turn <= turn <= right_turn:
            fraction = (turn - left_turn) / (right_turn - left_turn)
            return left_time + (right_time - left_time) * fraction
    raise ValueError("history turn lies outside the generated timeline")


def generate_scenario(
    *, task_type: str = "temporal_update", seed: int = 42, sample_index: int = 0,
    state_scope: str = "current", approval_status: str = "pending",
    context_budget: int = 2048, history_length_tokens: int = 10000,
    horizon_turns: int = 10, noise_memory_ratio: float = 0.5,
    constraint_count: int = 1, state_update_count: int = 2,
    token_counter: TokenCounter = provisional_token_count,
) -> dict[str, Any]:
    """Build one fully measured input/ground-truth snapshot without API calls.

    A custom counter may itself call an API. History length has a 1% tolerance;
    token-weighted snapshot noise has an absolute tolerance of 0.01. Impossible
    combinations raise ValueError instead of recording unmeasured parameters.
    """
    for name, value, minimum in (
        ("seed", seed, 0), ("sample_index", sample_index, 0),
        ("context_budget", context_budget, 1),
        ("history_length_tokens", history_length_tokens, 1),
        ("horizon_turns", horizon_turns, 1),
        ("constraint_count", constraint_count, 0),
        ("state_update_count", state_update_count, 0),
    ):
        _integer(name, value, minimum)
    ratio = _ratio(noise_memory_ratio)
    if task_type not in TASK_TYPES:
        raise ValueError(f"task_type must be one of {TASK_TYPES}")
    if state_scope not in ("current", "historical"):
        raise ValueError("state_scope must be current or historical")
    if approval_status not in ("pending", "granted"):
        raise ValueError("approval_status must be pending or granted")
    if task_type == "approval" and not constraint_count:
        raise ValueError("approval tasks require at least one approval constraint")
    if not callable(token_counter):
        raise TypeError("token_counter must be callable")
    # No generation decision depends on budget, horizon, strategy or repeat.
    scope = state_scope if task_type == "temporal_update" else "current"
    variant = scope if task_type == "temporal_update" else approval_status
    sample_id = f"seed{seed}_{task_type}_{sample_index:03d}_{variant}"
    digest = hashlib.sha256(sample_id.encode()).hexdigest()[:10]
    project = f"HAI-{digest}"
    session = f"session_{sample_id}"
    recipient = f"reviewer_{digest}@external.test"
    approved_id = f"approved_{int(digest, 16)}"
    granted_ids = [approved_id] if task_type == "approval" and variant == "granted" else []
    memories: list[dict[str, Any]] = []

    def memory(mid: str, kind: str, content: str, **fields: Any) -> dict[str, Any]:
        item = dict(
            memory_id=mid, session_id=session, turn_id=0, memory_type=kind,
            content=content, token_count=count_tokens(content, token_counter),
            importance=0.8 if kind in ("state", "constraint") else 0.3,
            is_active=True, is_protected=kind == "constraint", memory_key=None,
            created_at=BASE_TIME.isoformat(), valid_from=BASE_TIME.isoformat(),
            valid_to=None, supersedes=None, superseded_by=None,
        )
        item.update(fields)
        memories.append(item)
        return item

    constraints = []
    rules = []
    for index in range(constraint_count):
        mid = f"{sample_id}_constraint_{index}"
        if task_type == "approval" and index == 0:
            text = f"{project} 외부 이메일은 발송 전에 반드시 사용자 승인을 받아야 합니다."
            rule = dict(constraint_id=mid, approval=dict(
                execution=dict(tool_name="email", action="send_email"),
                request=dict(tool_name="email", action="request_approval"),
                valid_approval_ids=granted_ids.copy(), request_required=variant == "pending",
            ))
        else:
            prohibited = f"{digest}_restricted_{index}.pdf"
            text = (f"{project} 업무에서 {prohibited} 파일의 "
                    + ("외부 첨부를 금지합니다." if task_type == "approval" else "조회를 금지합니다."))
            actions = ("request_approval", "send_email") if task_type == "approval" else ("find_file",)
            argument = "attachment" if task_type == "approval" else "filename"
            rule = dict(constraint_id=mid, forbidden_tool_calls=[
                dict(tool_name="email" if task_type == "approval" else "file",
                     action=action, arguments={argument: prohibited}) for action in actions
            ])
        constraints.append(memory(mid, "constraint", text,
                                  memory_key=f"{project}.rule.{index}"))
        rules.append(rule)

    versions = []
    filenames = [f"{digest}_report_v{index + 1}.pdf" for index in range(state_update_count + 1)]
    for index, filename in enumerate(filenames):
        versions.append(memory(
            f"{sample_id}_version_{index + 1}", "state",
            f"{project} 보고서 파일은 {filename} 입니다. 상태 변경 순서는 버전 {index + 1} 입니다.",
            memory_key=f"{project}.report.current_file",
        ))
    for index, item in enumerate(versions):
        item.update(
            is_active=index == len(versions) - 1,
            supersedes=versions[index - 1]["memory_id"] if index else None,
            superseded_by=versions[index + 1]["memory_id"] if index + 1 < len(versions) else None,
        )
    target_index = 0 if scope == "historical" else state_update_count
    target = versions[target_index]
    if task_type == "approval":
        applicable_constraints = constraints[:1]
        applicable_rule = rules[:1]
    else:
        applicable_constraints = []
        applicable_rule = []
    target_ids = [target["memory_id"]]
    target_ids.extend(
        constraint["memory_id"]
        for constraint in applicable_constraints
    )
    grant = None
    if granted_ids:
        grant = memory(
            f"{sample_id}_grant",
            "fact",
            f"{project} 현재 보고서의 {recipient} 외부 발송을 승인했습니다. 승인 번호는 {approved_id} 입니다.",
        )
        
        target_ids.append(grant["memory_id"])
    events = list(memories)
    essential = set(target_ids)
    anchor_index = max(index for index, item in enumerate(events) if item["memory_id"] in essential)
    if len(events) - 1 - anchor_index >= horizon_turns:
        raise ValueError("horizon_turns leaves no room for later state updates before the task")
    history_turns = max(32, 2 * horizon_turns + len(events) + 16)
    current_turn = history_turns + 1
    important_turn = current_turn - horizon_turns
    for index, item in enumerate(events):
        timestamp = BASE_TIME + timedelta(minutes=10 * index)
        item.update(turn_id=important_turn - anchor_index + index,
                    created_at=timestamp.isoformat(), valid_from=timestamp.isoformat())
    for index, item in enumerate(versions[:-1]):
        item["valid_to"] = versions[index + 1]["valid_from"]
    # All strategies must be able to associate visible state text with the
    # historical query time; ContextBuilder does not render Memory metadata.
    for item in versions:
        end = item["valid_to"] or "다음 변경 전"
        item["content"] += f" 유효 시작: {item['valid_from']}. 유효 종료(미포함): {end}."
        item["token_count"] = count_tokens(item["content"], token_counter)
    current_time = BASE_TIME + timedelta(days=2)
    # High constraint/update stress still has a valid chronological task time.
    current_time = max(current_time, datetime.fromisoformat(events[-1]["valid_from"]) + timedelta(days=1))
    target_time = (datetime.fromisoformat(target["valid_from"]) + timedelta(seconds=1)
                   if scope == "historical" else current_time)

    # A common minimum noise pool is built even for ratio=0, so relevant text
    # stays identical across noise sweeps. Zero-noise data then drops the notes.
    noise_notes = []
    if ratio or state_update_count == 0:
        available_turns = [turn for turn in range(1, current_turn)
                           if turn not in {item["turn_id"] for item in events}]
        rng = random.Random(int(digest, 16))
        # Select by relative timeline position; content and IDs remain fixed
        # when the experiment moves essential information to another horizon.
        fractions = sorted(rng.sample(range(1, 99), 4))
        used: set[int] = set()
        for index, fraction in enumerate(fractions):
            position = min(len(available_turns) - 1, fraction * len(available_turns) // 100)
            turn = available_turns[position]
            while turn in used:
                position = (position + 1) % len(available_turns)
                turn = available_turns[position]
            used.add(turn)
            timestamp = _timestamp_for_turn(turn, events, current_turn, current_time).isoformat()
            noise_notes.append(memory(
                f"{sample_id}_noise_{index}", "experience",
                f"별도 업무 {digest}-{index} 회의에서 문서 표현과 배치에 관한 의견을 기록했습니다.",
                turn_id=turn, created_at=timestamp, valid_from=timestamp,
            ))
    base_noise = sum(item["token_count"] for item in memories if item["memory_id"] not in essential)
    relevant_tokens = sum(item["token_count"] for item in memories if item["memory_id"] in essential)
    # The smallest supported stress ratio is 0.1. Use one fixed relevant cost
    # for all ratios, then change only irrelevant notes. At main ratio=0.5 the
    # pool exceeds 1K, so the smallest budget exercises actual selection.
    relevant_target = max(640, 9 * base_noise)
    _pad_memory(target, target["token_count"] + max(0, relevant_target - relevant_tokens), token_counter)
    if not ratio:
        memories = [item for item in memories if item not in noise_notes]
    actual_noise = _balance_noise(memories, essential, ratio, token_counter)

    # Every past turn exists. Verbose conversation is measured separately from
    # its compact, deterministic memory summaries (no extraction model needed).
    by_turn = {item["turn_id"]: item for item in memories}
    history = []
    for turn in range(1, current_turn):
        timestamp = _timestamp_for_turn(turn, events, current_turn, current_time).isoformat()
        history.append(dict(
            session_id=session, turn_id=turn, timestamp=timestamp, source="user",
            content=by_turn[turn]["content"] if turn in by_turn else "진행 내용을 기록했습니다.",
        ))
    originals = [entry["content"] for entry in history]

    def build_history(length: int) -> str:
        per_turn, extra = divmod(length, len(history))
        for index, entry in enumerate(history):
            words = per_turn + (index < extra)
            entry["content"] = originals[index] + (" " + _words(words, index) if words else "")
        return render_history(history)

    length, measured_history = _fit_text(
        build_history, history_length_tokens, token_counter,
        tolerance=max(1, math.floor(history_length_tokens * HISTORY_TOLERANCE)),
    )
    build_history(length)
    for item in memories:
        MemoryItem.from_dict(item)

    if task_type == "temporal_update":
        query = (f"{project}의 {target_time.isoformat()} 시점 보고서 파일을 찾아줘."
                 if scope == "historical" else f"{project}의 현재 보고서 파일을 찾아줘.")
        tool, action, argument = "file", "find_file", "filename"
    else:
        query = f"{project}의 현재 보고서를 {recipient}에게 메일로 보내줘."
        tool, action, argument = "email", "request_approval" if variant == "pending" else "send_email", "attachment"
    arguments = {argument: filenames[target_index]}
    if task_type == "approval":
        arguments["recipient"] = recipient
        if granted_ids:
            arguments["approval_id"] = approved_id
    required = dict(
        memory_id=target["memory_id"], tool_call=dict(tool_name=tool, action=action),
        argument=argument, expected_value=filenames[target_index], state_scope=scope,
        stale_values=filenames[:-1] if scope == "current" else [],
    )
    return dict(
        scenario_id=sample_id, sample_id=sample_id,
        title=f"{task_type} {variant} {project}",
        validation_scope="integration", benchmark_enabled=True,
        session_id=session, current_turn=current_turn, current_time=current_time.isoformat(),
        query=query, context_budget=context_budget, token_count_mode=token_count_mode(token_counter),
        history=history, memory_snapshot=memories,
        query_context=dict(
            temporal_intent="historical" if scope == "historical" else "current",
            target_time=target_time.isoformat(), target_range=None,
            entities=[project], required_tools=[tool],
        ),
        target_memory_ids=target_ids, 
        required_state_ids=[target["memory_id"]],
        required_states=[required], 
        applicable_constraint_ids=[
            item["memory_id"] 
            for item in applicable_constraints
        ],
        constraint_rules=applicable_rule, 
        temporal_version_required=state_update_count > 0,
        wrong_version_memory_ids=[item["memory_id"] for item in versions if item is not target],
        expected_tool_calls=[dict(tool_name=tool, action=action, arguments=arguments)],
        forbidden_tool_calls=([dict(tool_name="email", action="send_email")]
                              if task_type == "approval" and variant == "pending" else []),
        expected_pipeline_status="ok",
        environment=dict(files={filename: f"{project} report version {index + 1}"
                                for index, filename in enumerate(filenames)},
                         valid_approval_ids=granted_ids),
        metadata=dict(
            seed=seed, sample_index=sample_index, task_type=task_type, state_scope=scope,
            approval_status=variant if task_type == "approval" else None,
            context_token_budget=context_budget,
            history_length_tokens=history_length_tokens,
            actual_history_length_tokens=measured_history, history_length_turns=history_turns,
            horizon_turns=horizon_turns, important_turn=important_turn,
            noise_memory_ratio=ratio, actual_noise_memory_ratio=actual_noise,
            noise_memory_tokens=sum(m["token_count"] for m in memories if m["memory_id"] not in essential),
            memory_tokens=sum(m["token_count"] for m in memories),
            noise_definition="non_target_snapshot_tokens / all_snapshot_tokens",
            constraint_count=constraint_count, 
            applicable_constraint_count = len(applicable_constraints),
            state_update_count=state_update_count,
            token_count_mode=token_count_mode(token_counter),
        ),
    )


def _sequence(name: str, value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{name} must be a non-empty list")
    if any(value[index] in value[:index] for index in range(len(value))):
        raise ValueError(f"{name} must not contain duplicates")
    return list(value)


def generate_dataset(
    config: Mapping[str, Any], *, token_counter: TokenCounter = provisional_token_count,
) -> dict[str, Any]:
    """Expand data seeds/grid cells, leaving strategies and repeats to runner."""
    if not isinstance(config, Mapping):
        raise TypeError("config must be a mapping")
    dataset = config.get("dataset")
    grid = config.get("grid")
    if not isinstance(dataset, Mapping) or not isinstance(grid, Mapping):
        raise ValueError("config requires dataset and grid objects")
    if dataset.get("source") != "synthetic":
        raise ValueError("dataset.source must be synthetic")
    samples = _integer("samples_per_cell", dataset.get("samples_per_cell"), 1)
    tasks = _sequence("dataset.task_types", dataset.get("task_types"))
    if any(task not in TASK_TYPES for task in tasks):
        raise ValueError(f"dataset.task_types must be drawn from {TASK_TYPES}")
    seeds = _sequence("seeds", config.get("seeds"))
    for seed in seeds:
        _integer("seed", seed)
    if set(grid) != set(GRID_KEYS):
        raise ValueError(f"grid must contain exactly {GRID_KEYS}")
    axes = [_sequence(key, grid[key]) for key in GRID_KEYS]
    for key, values in zip(GRID_KEYS, axes):
        for value in values:
            if key == "noise_memory_ratio":
                _ratio(value)
            else:
                _integer(key, value, 0 if key in ("constraint_count", "state_update_count") else 1)
    temporal_count = (samples + 1) // 2 if len(tasks) == 2 else (samples if tasks[0] == "temporal_update" else 0)
    current_count = (temporal_count + 1) // 2
    cells = []
    # Reuse source data across budget sweeps without repeating expensive token
    # measurement (particularly when a CountTokens API is explicitly selected).
    source_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    for values in itertools.product(*axes):
        parameters = dict(zip(GRID_KEYS, values))
        scenarios = []
        for seed in seeds:
            for index in range(samples):
                task_type = "temporal_update" if index < temporal_count else "approval"
                scope = "current" if index < current_count else "historical"
                approval_status = "pending" if (index - temporal_count) % 2 == 0 else "granted"
                source_key = (seed, index, task_type, scope, approval_status, *values[1:])
                if source_key not in source_cache:
                    source_cache[source_key] = generate_scenario(
                        task_type=task_type, seed=seed, sample_index=index,
                        state_scope=scope, approval_status=approval_status,
                        context_budget=parameters["context_token_budget"],
                        history_length_tokens=parameters["history_length_tokens"],
                        horizon_turns=parameters["horizon_turns"],
                        noise_memory_ratio=parameters["noise_memory_ratio"],
                        constraint_count=parameters["constraint_count"],
                        state_update_count=parameters["state_update_count"],
                        token_counter=token_counter,
                    )
                scenario = copy.deepcopy(source_cache[source_key])
                scenario["context_budget"] = parameters["context_token_budget"]
                scenario["metadata"]["context_token_budget"] = parameters["context_token_budget"]
                scenarios.append(scenario)
        cell_id = "cell_" + hashlib.sha256(
            json.dumps(parameters, sort_keys=True, allow_nan=False).encode()
        ).hexdigest()[:12]
        cells.append(dict(cell_id=cell_id, parameters=parameters, scenarios=scenarios))
    return dict(
        schema_version="1.0", generator="benchmark.generator",
        config=copy.deepcopy(dict(config)), token_count_mode=token_count_mode(token_counter),
        description="Synthetic inputs and ground truth; not model performance measurements.",
        cells=cells,
    )


def write_dataset(payload: Mapping[str, Any], output_directory: str | Path) -> dict[str, Any]:
    """Serialize one runner-compatible file per cell and a compact manifest."""
    output = Path(output_directory)
    cell_records = []
    files = []
    seen = set()
    for cell in payload["cells"]:
        cell_id = cell["cell_id"]
        if (not isinstance(cell_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", cell_id)
                or cell_id in seen):
            raise ValueError("cell_id must be unique and contain only letters, digits, _ or -")
        seen.add(cell_id)
        filename = f"{cell_id}.json"
        record = dict(
            schema_version=payload["schema_version"], generator=payload["generator"],
            token_count_mode=payload["token_count_mode"], parameters=cell["parameters"],
            scenarios=cell["scenarios"],
        )
        files.append((filename, json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n"))
        cell_records.append(dict(cell_id=cell_id, path=filename, parameters=cell["parameters"],
                                 n_scenarios=len(cell["scenarios"])))
    manifest = dict(
        schema_version=payload["schema_version"], generator=payload["generator"],
        config=payload["config"], token_count_mode=payload["token_count_mode"],
        n_cells=len(cell_records), n_scenarios=sum(cell["n_scenarios"] for cell in cell_records),
        cells=cell_records,
    )
    manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    # Validate/serialize everything before replacing any existing dataset file.
    output.mkdir(parents=True, exist_ok=True)
    for filename, content in files:
        (output / filename).write_text(content, encoding="utf-8")
    (output / "manifest.json").write_text(manifest_text, encoding="utf-8")
    return manifest


def write_dataset_archive(payload: Mapping[str, Any], archive_path: str | Path) -> dict[str, Any]:
    """Package full cell JSONs compactly, using reproducible ZIP metadata.

    Extract the archive before passing an individual cell to benchmark.runner.
    """
    archive = Path(archive_path)
    if archive.suffix.lower() != ".zip":
        raise ValueError("archive_path must have a .zip extension")
    with tempfile.TemporaryDirectory(prefix="hai_generator_") as directory:
        manifest = write_dataset(payload, directory)
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for source in sorted(Path(directory).glob("*.json")):
                info = zipfile.ZipInfo(source.name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                bundle.writestr(info, source.read_bytes(), compresslevel=9)
    return manifest


def _import_counter(spec: str) -> TokenCounter:
    module, separator, attribute = spec.partition(":")
    if not separator or not module or not attribute:
        raise ValueError("token counter must use module:function syntax")
    counter = getattr(importlib.import_module(module), attribute)
    if not callable(counter):
        raise TypeError("token counter must be callable")
    return counter


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="experiments/configs/smoke.json")
    parser.add_argument("--output", help="Default: benchmark/generated/<config name>")
    parser.add_argument("--archive", action="store_true", help="Write a deterministic .zip; extract before running")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--token-counter", help="module:function for the shared TokenCounter")
    group.add_argument("--gemini-token-model", help="Explicitly measure with Gemini CountTokens API")
    parser.add_argument("--gemini-token-cache-size", type=int, default=4096)
    parser.add_argument("--gemini-token-cache", default=".cache/gemini_token_cache.sqlite3")
    parser.add_argument("--gemini-token-retries", type=int, default=3)
    parser.add_argument("--gemini-token-retry-base-seconds", type=float, default=0.5)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.output:
        output = Path(args.output)
    else:
        name = config.get("name", "dataset")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            parser.error("config name must contain only letters, digits, _ or -; or pass --output")
        output = Path("benchmark/generated") / (name + ".zip" if args.archive else name)
    counter = (
        GeminiTokenCounter(
            model=args.gemini_token_model, 
            cache_size=args.gemini_token_cache_size,
            persistent_cache_path=args.gemini_token_cache,
            max_retries=args.gemini_token_retries,
            retry_base_seconds=args.gemini_token_retry_base_seconds,
        )
        if args.gemini_token_model else _import_counter(args.token_counter) if args.token_counter else provisional_token_count
    )
    payload = generate_dataset(config, token_counter=counter)
    manifest = (write_dataset_archive(payload, output) if args.archive else write_dataset(payload, output))
    print(json.dumps(dict(output=str(output),
                          manifest="manifest.json inside archive" if args.archive else str(output / "manifest.json"),
                          n_cells=manifest["n_cells"], n_scenarios=manifest["n_scenarios"],
                          token_count_mode=manifest["token_count_mode"]), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
