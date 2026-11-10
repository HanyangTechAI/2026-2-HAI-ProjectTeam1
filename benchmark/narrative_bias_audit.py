from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from benchmark.experiment_runtime import (
    embedding_runtime_metadata,
    multilingual_embedder,
)
from memory.retrieval import semantic_retrieve
from memory.schema import MemoryItem, MemoryType
from memory.selector import PipelineStatus, select_memory
from memory.store import SnapshotMemoryStore


# =========================================================
# Constants
# =========================================================

MODES = (1, 2, 3)
POOLS = ("baseline", "proposed")

# 기존 NARRATIVE의 반복 시작 구절
REPETITION_MARKER = "회의에서는"

# 모드 간 변경되면 안 되는 시나리오 필드
FROZEN_SCENARIO_FIELDS = (
    "sample_id",
    "scenario_id",
    "session_id",
    "query",
    "current_turn",
    "current_time",
    "context_budget",
    "query_context",
    "target_memory_ids",
    "required_state_ids",
    "required_states",
    "constraint_rules",
    "applicable_constraint_ids",
    "wrong_version_memory_ids",
    "expected_tool_calls",
    "forbidden_tool_calls",
    "environment",
    "token_count_mode",
)

# NARRATIVE 변경에 영향을 받으면 안 되는 Memory 구조
FROZEN_MEMORY_FIELDS = (
    "memory_id",
    "session_id",
    "turn_id",
    "memory_type",
    "importance",
    "is_active",
    "is_protected",
    "memory_key",
    "created_at",
    "valid_from",
    "valid_to",
    "supersedes",
    "superseded_by",
)


# =========================================================
# Utilities
# =========================================================

def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()

    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)

    return hasher.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as stream:
        result = json.load(stream)

    if not isinstance(result, dict):
        raise ValueError(f"JSON root must be an object: {path}")

    return result


def mean_or_none(values) -> float | None:
    values = [value for value in values if value is not None]

    if not values:
        return None

    return float(statistics.mean(values))


def recall(found: set[str], targets: set[str]) -> float | None:
    if not targets:
        return None

    return len(found & targets) / len(targets)


def jaccard(left: list[str], right: list[str]) -> float:
    a = set(left)
    b = set(right)

    if not a and not b:
        return 1.0

    return len(a & b) / len(a | b)


def sample_type(scenario: dict[str, Any]) -> str:
    metadata = scenario.get("metadata", {})
    task = metadata.get("task_type", "unknown")

    if task == "temporal_update":
        variant = metadata.get("state_scope", "unknown")
    else:
        variant = metadata.get("approval_status", "unknown")

    return f"{task}/{variant}"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    fieldnames = list(dict.fromkeys(
        field
        for row in rows
        for field in row
    ))

    with path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            record = {}

            for key, value in row.items():
                if isinstance(value, (list, dict, tuple)):
                    record[key] = json.dumps(
                        value,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                else:
                    record[key] = value

            writer.writerow(record)


# =========================================================
# Dataset Loading
# =========================================================

def load_mode_dataset(
    location: Path,
    mode: int,
    budget: int | None,
    horizon: int | None,
) -> dict[str, Any]:
    """manifest에서 분석할 셀 하나를 선택한다."""

    manifest_path = (
        location / "manifest.json"
        if location.is_dir()
        else location
    )

    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)

    manifest = load_json(manifest_path)

    actual_mode = manifest.get("horizon_generation_mode", 1)

    if actual_mode != mode:
        raise ValueError(
            f"Expected mode {mode}, got {actual_mode}: "
            f"{manifest_path}"
        )

    cells = manifest.get("cells", [])

    matching = []

    for cell in cells:
        params = cell["parameters"]

        if (
            budget is not None
            and params["context_token_budget"] != budget
        ):
            continue

        if (
            horizon is not None
            and params["horizon_turns"] != horizon
        ):
            continue

        matching.append(cell)

    if len(matching) != 1:
        raise ValueError(
            f"Mode {mode}: expected exactly one matching cell, "
            f"found {len(matching)}. "
            "Specify --budget and --horizon to select one cell."
        )

    selected_cell = matching[0]
    cell_path = manifest_path.parent / selected_cell["path"]

    payload = load_json(cell_path)
    scenarios = payload.get("scenarios")

    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError(f"Invalid scenarios: {cell_path}")

    by_id = {}

    for scenario in scenarios:
        sid = scenario["scenario_id"]

        if sid in by_id:
            raise ValueError(f"Duplicate scenario_id: {sid}")

        if scenario.get("metadata", {}).get(
            "horizon_generation_mode", 1
        ) != mode:
            raise ValueError(
                f"Scenario mode mismatch: {sid}"
            )

        by_id[sid] = scenario

    manifest_counter = manifest.get("token_count_mode")
    cell_counter = payload.get("token_count_mode")

    if manifest_counter != cell_counter:
        raise ValueError(
            f"Token counter mismatch inside mode {mode}"
        )

    return {
        "mode": mode,
        "scenarios": by_id,
        "parameters": selected_cell["parameters"],
        "token_count_mode": cell_counter,
        "manifest_sha256": sha256_file(manifest_path),
        "cell_sha256": sha256_file(cell_path),
        "manifest_path": str(manifest_path),
        "cell_path": str(cell_path),
    }


# =========================================================
# Dataset Integrity Audit
# =========================================================

def memory_structure(memory: dict[str, Any]) -> dict:
    return {
        field: memory.get(field)
        for field in FROZEN_MEMORY_FIELDS
    }


def verify_scenario_pair(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    mode: int,
) -> int:
    """Ground Truth와 Memory 구조 동일성을 검증한다.

    반환값:
        내용이 달라진 Memory 개수
    """

    sid = baseline["scenario_id"]

    for field in FROZEN_SCENARIO_FIELDS:
        if baseline.get(field) != candidate.get(field):
            raise ValueError(
                f"[{sid}] Mode {mode}: "
                f"scenario field changed: {field}"
            )

    original = baseline["memory_snapshot"]
    changed = candidate["memory_snapshot"]

    if len(original) != len(changed):
        raise ValueError(
            f"[{sid}] Mode {mode}: memory count changed"
        )

    original_map = {
        memory["memory_id"]: memory
        for memory in original
    }

    changed_map = {
        memory["memory_id"]: memory
        for memory in changed
    }

    if set(original_map) != set(changed_map):
        raise ValueError(
            f"[{sid}] Mode {mode}: memory IDs changed"
        )

    changed_content_count = 0

    for mid in original_map:
        left = original_map[mid]
        right = changed_map[mid]

        if memory_structure(left) != memory_structure(right):
            raise ValueError(
                f"[{sid}] Mode {mode}: "
                f"memory metadata changed: {mid}"
            )

        if left["content"] != right["content"]:
            changed_content_count += 1

    # 정답 파일명 등이 실제 정답 State에 남아 있는지 검사
    for state in candidate["required_states"]:
        memory = changed_map[state["memory_id"]]
        expected = str(state["expected_value"])

        if expected not in memory["content"]:
            raise ValueError(
                f"[{sid}] Mode {mode}: "
                f"ground-truth value missing from memory: "
                f"{state['memory_id']}"
            )

    return changed_content_count


def verify_datasets(
    datasets: dict[int, dict[str, Any]],
) -> dict[str, Any]:

    reference = datasets[1]
    reference_ids = set(reference["scenarios"])

    changed_counts = {}

    for mode in (2, 3):
        candidate = datasets[mode]

        if candidate["parameters"] != reference["parameters"]:
            raise ValueError(
                f"Mode {mode}: experiment parameters differ"
            )

        if (
            candidate["token_count_mode"]
            != reference["token_count_mode"]
        ):
            raise ValueError(
                f"Mode {mode}: token counter differs"
            )

        if set(candidate["scenarios"]) != reference_ids:
            raise ValueError(
                f"Mode {mode}: scenario ID sets differ"
            )

        changed_counts[mode] = 0

        for sid in sorted(reference_ids):
            changed_counts[mode] += verify_scenario_pair(
                reference["scenarios"][sid],
                candidate["scenarios"][sid],
                mode,
            )

    for mode, dataset in datasets.items():
        for scenario in dataset["scenarios"].values():
            memories = scenario["memory_snapshot"]

            memory_ids = [m["memory_id"] for m in memories]

            if len(memory_ids) != len(set(memory_ids)):
                raise ValueError(
                    f"Duplicate memory ID in {scenario['scenario_id']}"
                )

            for memory in memories:
                if (
                    type(memory["token_count"]) is not int
                    or memory["token_count"] < 0
                ):
                    raise ValueError(
                        f"Invalid token_count: {memory['memory_id']}"
                    )

    return {
        "passed": True,
        "scenario_count": len(reference_ids),
        "changed_memory_contents": changed_counts,
        "token_count_mode": reference["token_count_mode"],
        "parameters": reference["parameters"],
    }


# =========================================================
# Retrieval Evaluation
# =========================================================

def available_memories(
    scenario: dict[str, Any],
    pool: str,
) -> list[MemoryItem]:
    """실제 Selector / Baseline의 초기 검색 후보 조건."""

    current_turn = scenario["current_turn"]
    session_id = scenario["session_id"]

    memories = [
        MemoryItem.from_dict(raw)
        for raw in scenario["memory_snapshot"]
    ]

    result = []

    for memory in memories:
        if memory.session_id != session_id:
            continue

        if memory.turn_id > current_turn:
            continue

        if pool == "proposed":
            if (
                memory.memory_type == MemoryType.CONSTRAINT
                and memory.is_protected
                and memory.is_active
            ):
                continue

        result.append(memory)

    return result


def evaluate_retrieval(
    scenario: dict[str, Any],
    mode: int,
    pool: str,
    top_k: int,
) -> dict[str, Any]:

    memories = available_memories(scenario, pool)

    # 전체 후보의 순위를 구하기 위해 K를 후보 수로 설정
    ranked = semantic_retrieve(
        query=scenario["query"],
        memories=memories,
        embedder=multilingual_embedder,
        top_k=max(1, len(memories)),
    )

    top_hits = ranked[:top_k]

    ranked_ids = [
        hit.memory.memory_id
        for hit in ranked
    ]

    top_ids = [
        hit.memory.memory_id
        for hit in top_hits
    ]

    ranks = {
        mid: index + 1
        for index, mid in enumerate(ranked_ids)
    }

    scores = {
        hit.memory.memory_id: hit.score
        for hit in ranked
    }

    # 행동에 필요한 State
    required_states = set(
        scenario["required_state_ids"]
    )

    # General Retrieval 대상
    # Protected Constraint는 별도 경로라 분모에서 제외
    general_targets = (
        set(scenario["target_memory_ids"])
        - set(scenario["applicable_constraint_ids"])
    )

    selected_ids = set(top_ids)

    state_ranks = [
        ranks[mid]
        for mid in required_states
        if mid in ranks
    ]

    target_scores = [
        scores[mid]
        for mid in required_states
        if mid in scores
    ]

    # 정답 Memory 길이와 과거 버전의 길이 차이
    target_memories = [
        memory
        for memory in memories
        if memory.memory_id in required_states
    ]

    target_keys = {
        memory.memory_key
        for memory in target_memories
        if memory.memory_key is not None
    }

    other_versions = [
        memory
        for memory in memories
        if (
            memory.memory_type == MemoryType.STATE
            and memory.memory_key in target_keys
            and memory.memory_id not in required_states
        )
    ]

    noise_memories = [
        memory
        for memory in memories
        if "_noise_" in memory.memory_id
    ]

    target_tokens = mean_or_none(
        memory.token_count
        for memory in target_memories
    )

    other_tokens = mean_or_none(
        memory.token_count
        for memory in other_versions
    )

    ratio = (
        target_tokens / other_tokens
        if target_tokens is not None
        and other_tokens is not None
        and other_tokens > 0
        else None
    )

    return {
        "sample_id": scenario["scenario_id"],
        "task_type": sample_type(scenario),
        "mode": mode,
        "pool": pool,
        "top_k": top_k,
        "candidate_count": len(memories),

        "top_k_ids": top_ids,

        "state_recall_at_k": recall(
            selected_ids, required_states
        ),
        "general_recall_at_k": recall(
            selected_ids, general_targets
        ),

        "target_rank": min(state_ranks)
        if state_ranks else None,

        "target_similarity": max(target_scores)
        if target_scores else None,

        "target_token_count": target_tokens,
        "other_version_mean_tokens": other_tokens,
        "target_to_other_token_ratio": ratio,

        "target_narrative_marker_count": sum(
            memory.content.count(REPETITION_MARKER)
            for memory in target_memories
        ),
        "noise_narrative_marker_count": sum(
            memory.content.count(REPETITION_MARKER)
            for memory in noise_memories
        ),

        "target_memory_ids": sorted(required_states),
        "general_target_ids": sorted(general_targets),

        "target_found_at_k": (
            bool(required_states & selected_ids)
        ),
    }


# =========================================================
# Optional Proposed Selection Audit
# =========================================================

def evaluate_selection(
    scenario: dict[str, Any],
    *,
    top_k: int,
    fixed_tokens: int,
    safety_margin: int,
) -> dict[str, Any]:
    """Proposed Selector의 Context Builder 이전 결과를 측정한다.

    fixed_tokens는 별도로 측정한 고정 입력 토큰 수를 사용해야 한다.
    여기서는 시나리오에 저장된 Memory token_count를 그대로 사용한다.

    따라서 실제 Prompt Budget Audit이나
    최종 Context Builder 검증을 대체하지 않는다.
    """

    memories = [
        MemoryItem.from_dict(raw)
        for raw in scenario["memory_snapshot"]
    ]

    store = SnapshotMemoryStore(tuple(memories))

    effective_budget = (
        scenario["context_budget"] - safety_margin
    )

    if effective_budget <= 0:
        raise ValueError("Effective budget must be positive")

    current_time = datetime.fromisoformat(
        scenario["current_time"].replace("Z", "+00:00")
    )

    result = select_memory(
        query=scenario["query"],
        session_id=scenario["session_id"],
        memory_store=store,
        total_budget=effective_budget,
        fixed_tokens=fixed_tokens,
        current_turn=scenario["current_turn"],
        current_time=current_time,
        embedder=multilingual_embedder,
        top_k=top_k,
        token_counter=None,
    )

    # 예산 실패로 중단된 실행에서는 LLM에 Context가 전달되지 않음
    succeeded = result.status == PipelineStatus.OK

    selected = (
        set(result.selected_memory_ids)
        if succeeded
        else set()
    )

    states = set(scenario["required_state_ids"])
    constraints = set(
        scenario["applicable_constraint_ids"]
    )

    return {
        "selection_status": result.status.value,
        "selection_state_recall": recall(
            selected, states
        ),
        "selection_constraint_recall": recall(
            selected, constraints
        ),
        "selected_memory_ids": sorted(selected),
        "selected_memory_count": len(selected),
        "selected_memory_tokens": (
            result.memory_tokens if succeeded else 0
        ),
        "effective_budget": effective_budget,
    }


# =========================================================
# Pairwise Comparison
# =========================================================

def compare_modes(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """같은 sample/pool에서 모드 간 변화를 비교한다."""

    by_key = defaultdict(dict)

    for row in rows:
        key = (
            row["sample_id"],
            row["pool"],
        )

        by_key[key][row["mode"]] = row

    results = []

    for (sid, pool), modes in sorted(by_key.items()):
        for left_mode, right_mode in (
            (1, 2),
            (1, 3),
            (2, 3),
        ):
            left = modes[left_mode]
            right = modes[right_mode]

            result = {
                "sample_id": sid,
                "task_type": left["task_type"],
                "pool": pool,
                "left_mode": left_mode,
                "right_mode": right_mode,

                "top_k_jaccard": jaccard(
                    left["top_k_ids"],
                    right["top_k_ids"],
                ),

                "top_k_changed": (
                    left["top_k_ids"] != right["top_k_ids"]
                ),

                "state_recall_delta": (
                    right["state_recall_at_k"]
                    - left["state_recall_at_k"]
                ),

                "general_recall_delta": (
                    right["general_recall_at_k"]
                    - left["general_recall_at_k"]
                ),

                "target_rank_delta": (
                    right["target_rank"]
                    - left["target_rank"]
                    if left["target_rank"] is not None
                    and right["target_rank"] is not None
                    else None
                ),

                "target_similarity_delta": (
                    right["target_similarity"]
                    - left["target_similarity"]
                    if left["target_similarity"] is not None
                    and right["target_similarity"] is not None
                    else None
                ),

                "target_token_delta": (
                    right["target_token_count"]
                    - left["target_token_count"]
                    if left["target_token_count"] is not None
                    and right["target_token_count"] is not None
                    else None
                ),

                "state_hit_improved": (
                    left["state_recall_at_k"] < 1.0
                    and right["state_recall_at_k"] == 1.0
                ),

                "state_hit_degraded": (
                    left["state_recall_at_k"] == 1.0
                    and right["state_recall_at_k"] < 1.0
                ),

                "left_top_k": left["top_k_ids"],
                "right_top_k": right["top_k_ids"],
            }

            if (
                "selection_state_recall" in left
                and "selection_state_recall" in right
            ):
                result["selection_state_recall_delta"] = (
                    right["selection_state_recall"]
                    - left["selection_state_recall"]
                )

            results.append(result)

    return results


# =========================================================
# Summary
# =========================================================

def summarize(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = defaultdict(list)

    for row in rows:
        groups[
            (row["mode"], row["pool"], "all")
        ].append(row)

        groups[
            (row["mode"], row["pool"], row["task_type"])
        ].append(row)

    summary = []

    for (mode, pool, subtype), group in sorted(groups.items()):
        item = {
            "mode": mode,
            "pool": pool,
            "task_type": subtype,
            "n": len(group),

            "state_recall_at_k": mean_or_none(
                row["state_recall_at_k"]
                for row in group
            ),

            "general_recall_at_k": mean_or_none(
                row["general_recall_at_k"]
                for row in group
            ),

            "mean_target_rank": mean_or_none(
                row["target_rank"]
                for row in group
            ),

            "mean_target_similarity": mean_or_none(
                row["target_similarity"]
                for row in group
            ),

            "mean_target_token_count": mean_or_none(
                row["target_token_count"]
                for row in group
            ),

            "mean_target_to_other_token_ratio": mean_or_none(
                row["target_to_other_token_ratio"]
                for row in group
            ),

            "mean_narrative_marker_count": mean_or_none(
                row["target_narrative_marker_count"]
                for row in group
            ),
        }

        if "selection_state_recall" in group[0]:
            item["selection_state_recall"] = mean_or_none(
                row["selection_state_recall"]
                for row in group
            )

            item["selection_constraint_recall"] = mean_or_none(
                row["selection_constraint_recall"]
                for row in group
            )

        summary.append(item)

    return summary


def summarize_pairs(
    pairs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    groups = defaultdict(list)

    for row in pairs:
        key = (
            row["pool"],
            row["left_mode"],
            row["right_mode"],
        )
        groups[key].append(row)

    summary = []

    for (pool, left, right), group in sorted(groups.items()):
        summary.append({
            "pool": pool,
            "left_mode": left,
            "right_mode": right,
            "n": len(group),

            "mean_top_k_jaccard": mean_or_none(
                row["top_k_jaccard"] for row in group
            ),

            "top_k_changed_count": sum(
                row["top_k_changed"] for row in group
            ),

            "mean_state_recall_delta": mean_or_none(
                row["state_recall_delta"] for row in group
            ),

            "mean_target_rank_delta": mean_or_none(
                row["target_rank_delta"] for row in group
            ),

            "state_hit_improved_count": sum(
                row["state_hit_improved"] for row in group
            ),

            "state_hit_degraded_count": sum(
                row["state_hit_degraded"] for row in group
            ),
        })

    return summary


# =========================================================
# Main
# =========================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NARRATIVE Retrieval Bias Offline Audit"
    )

    parser.add_argument("--mode1", type=Path, required=True)
    parser.add_argument("--mode2", type=Path, required=True)
    parser.add_argument("--mode3", type=Path, required=True)

    parser.add_argument("--budget", type=int, default=None)
    parser.add_argument("--horizon", type=int, default=None)

    parser.add_argument("--top-k", type=int, default=5)

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("experiments/results/narrative_bias_audit"),
    )

    parser.add_argument(
        "--selection-fixed-tokens",
        type=int,
        default=None,
        help=(
            "선택적 Proposed Selection Audit. "
            "실제 측정한 고정 입력 token 수를 지정한다."
        ),
    )

    parser.add_argument(
        "--safety-margin",
        type=int,
        default=32,
    )

    args = parser.parse_args()

    if args.top_k < 1:
        parser.error("--top-k must be positive")

    if args.safety_margin < 0:
        parser.error("--safety-margin must be non-negative")

    if (
        args.selection_fixed_tokens is not None
        and args.selection_fixed_tokens < 0
    ):
        parser.error(
            "--selection-fixed-tokens must be non-negative"
        )

    # -----------------------------------------------------
    # Load
    # -----------------------------------------------------

    paths = {
        1: args.mode1,
        2: args.mode2,
        3: args.mode3,
    }

    datasets = {}

    for mode, path in paths.items():
        print(f"[LOAD] Mode {mode}: {path}")

        datasets[mode] = load_mode_dataset(
            location=path,
            mode=mode,
            budget=args.budget,
            horizon=args.horizon,
        )

    # -----------------------------------------------------
    # Integrity Audit
    # -----------------------------------------------------

    print("[CHECK] Dataset integrity")

    integrity = verify_datasets(datasets)

    print(
        f"[PASS] {integrity['scenario_count']} paired scenarios"
    )

    # -----------------------------------------------------
    # Embedding
    # -----------------------------------------------------

    print("[MODEL] Loading frozen embedding model")

    # 처음 호출할 때 SentenceTransformer가 로드됨
    multilingual_embedder("NARRATIVE retrieval bias audit")

    try:
        embedding_metadata = embedding_runtime_metadata()
    except RuntimeError as exc:
        embedding_metadata = {
            "status": "revision_unverified",
            "reason": str(exc),
        }
        print(f"[WARNING] {exc}")

    # -----------------------------------------------------
    # Retrieval Audit
    # -----------------------------------------------------

    rows = []
    reference_ids = sorted(datasets[1]["scenarios"])

    for index, sid in enumerate(reference_ids, start=1):
        print(
            f"[AUDIT] {index}/{len(reference_ids)}: {sid}",
            flush=True,
        )

        for mode in MODES:
            scenario = datasets[mode]["scenarios"][sid]

            selection = None

            if args.selection_fixed_tokens is not None:
                selection = evaluate_selection(
                    scenario,
                    top_k=args.top_k,
                    fixed_tokens=args.selection_fixed_tokens,
                    safety_margin=args.safety_margin,
                )

            for pool in POOLS:
                row = evaluate_retrieval(
                    scenario=scenario,
                    mode=mode,
                    pool=pool,
                    top_k=args.top_k,
                )

                if selection is not None and pool == "proposed":
                    row.update(selection)

                rows.append(row)

    # -----------------------------------------------------
    # Aggregate
    # -----------------------------------------------------

    pairs = compare_modes(rows)

    summary = summarize(rows)
    pair_summary = summarize_pairs(pairs)

    # -----------------------------------------------------
    # Save
    # -----------------------------------------------------

    args.output.mkdir(parents=True, exist_ok=True)

    provenance = {
        "analysis": "narrative_bias_offline_v1",
        "source_modes": {
            mode: {
                key: value
                for key, value in dataset.items()
                if key != "scenarios"
            }
            for mode, dataset in datasets.items()
        },
        "integrity": integrity,
        "embedding": embedding_metadata,
        "top_k": args.top_k,
        "budget": args.budget,
        "horizon": args.horizon,
        "selection_fixed_tokens": args.selection_fixed_tokens,
        "safety_margin": args.safety_margin,
        "selection_scope": (
            "pre_context_builder"
            if args.selection_fixed_tokens is not None
            else "disabled"
        ),
    }

    write_csv(args.output / "retrieval_details.csv", rows)
    write_csv(args.output / "pairwise_details.csv", pairs)
    write_csv(args.output / "summary.csv", summary)
    write_csv(args.output / "pairwise_summary.csv", pair_summary)

    with (args.output / "audit.json").open(
        "w", encoding="utf-8"
    ) as stream:
        json.dump(
            {
                "provenance": provenance,
                "summary": summary,
                "pairwise_summary": pair_summary,
            },
            stream,
            ensure_ascii=False,
            indent=2,
        )

    # -----------------------------------------------------
    # Console Report
    # -----------------------------------------------------

    print("\n===== NARRATIVE BIAS AUDIT =====")

    for row in summary:
        if row["task_type"] != "all":
            continue

        print(
            f"Mode {row['mode']} | "
            f"{row['pool']:8s} | "
            f"State Recall@{args.top_k}: "
            f"{row['state_recall_at_k']:.3f} | "
            f"Mean Rank: {row['mean_target_rank']:.2f} | "
            f"Mean Similarity: "
            f"{row['mean_target_similarity']:.4f}"
        )

    print("\n===== PAIRWISE TOP-K =====")

    for row in pair_summary:
        print(
            f"{row['pool']:8s} | "
            f"{row['left_mode']}->{row['right_mode']} | "
            f"Jaccard={row['mean_top_k_jaccard']:.3f} | "
            f"Changed={row['top_k_changed_count']}/{row['n']} | "
            f"Improved={row['state_hit_improved_count']} | "
            f"Degraded={row['state_hit_degraded_count']}"
        )

    print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
