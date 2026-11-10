from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os

from datetime import datetime
from pathlib import Path
from typing import Any

from agent.llm_planner import SYSTEM_INSTRUCTION

from benchmark.experiment_runtime import (
    embedding_runtime_metadata,
    multilingual_embedder,
)

from context.builder import (
    build_context,
    fixed_context_tokens,
)
from context.tokenizer import GeminiTokenCounter

from memory.query_analyzer import analyze_query
from memory.schema import MemoryItem

from memory.selector import (
    PipelineStatus,
    retrieve_candidates,
    expand_version_chains,
    resolve_temporal_versions,
    resolve_required_states,
    retrieve_protected,
    select_memory,
)

from memory.store import SnapshotMemoryStore


# =========================================================
# 1. 파일 로딩 및 데이터 검증
# =========================================================

def read_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError(f"JSON 객체가 아님: {path}")

    return data


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_cell(
    root: Path,
    mode: int,
    budget: int,
    horizon: int,
) -> dict[str, Any]:

    manifest_file = (
        root / "manifest.json"
        if root.is_dir() else root
    )

    manifest = read_json(manifest_file)

    if manifest.get("horizon_generation_mode", 1) != mode:
        raise ValueError(
            f"Mode {mode} 불일치: {manifest_file}"
        )

    candidates = [
        cell
        for cell in manifest["cells"]
        if (
            cell["parameters"]["context_token_budget"] == budget
            and cell["parameters"]["horizon_turns"] == horizon
        )
    ]

    if len(candidates) != 1:
        raise ValueError(
            f"Mode {mode}: H{horizon}, B{budget} "
            f"셀 {len(candidates)}개 발견. 정확히 1개 필요"
        )

    cell_file = manifest_file.parent / candidates[0]["path"]
    cell = read_json(cell_file)

    if (
        cell.get("token_count_mode")
        != manifest.get("token_count_mode")
    ):
        raise ValueError("Token Counter 불일치")

    scenarios = {}

    for scenario in cell["scenarios"]:
        sid = scenario["scenario_id"]

        if sid in scenarios:
            raise ValueError(f"중복 Scenario: {sid}")

        actual_mode = scenario.get(
            "metadata", {}
        ).get("horizon_generation_mode", 1)

        if actual_mode != mode:
            raise ValueError(f"Scenario Mode 불일치: {sid}")

        scenarios[sid] = scenario

    return {
        "scenarios": scenarios,
        "parameters": candidates[0]["parameters"],
        "token_count_mode": cell["token_count_mode"],
        "manifest_sha256": sha256(manifest_file),
        "cell_sha256": sha256(cell_file),
        "cell_path": str(cell_file),
    }


def validate_pair(
    a: dict[str, Any],
    b: dict[str, Any],
) -> None:
    """Mode 1/2에서 Ground Truth가 동일한지 확인."""

    fields = (
        "scenario_id",
        "query",
        "session_id",
        "current_time",
        "current_turn",
        "context_budget",
        "target_memory_ids",
        "required_state_ids",
        "required_states",
        "constraint_rules",
        "applicable_constraint_ids",
        "expected_tool_calls",
        "forbidden_tool_calls",
        "wrong_version_memory_ids",
        "environment",
        "query_context",
    )

    for field in fields:
        if a.get(field) != b.get(field):
            raise ValueError(
                f"{a['scenario_id']}: {field} 변경됨"
            )

    ma = {
        m["memory_id"]: m
        for m in a["memory_snapshot"]
    }

    mb = {
        m["memory_id"]: m
        for m in b["memory_snapshot"]
    }

    if (
        len(ma) != len(a["memory_snapshot"])
        or len(mb) != len(b["memory_snapshot"])
    ):
        raise ValueError("Memory ID 중복")

    if ma.keys() != mb.keys():
        raise ValueError("Memory ID 집합이 다름")

    targets = set(a["required_state_ids"])

    noises = {
        mid
        for mid in ma
        if "_noise_" in mid
    }

    mutable = {"content", "token_count"}

    for mid in ma:
        metadata_a = {
            k: v
            for k, v in ma[mid].items()
            if k not in mutable
        }

        metadata_b = {
            k: v
            for k, v in mb[mid].items()
            if k not in mutable
        }

        if metadata_a != metadata_b:
            raise ValueError(
                f"{a['scenario_id']}: {mid} 메타데이터 변경"
            )

        content_changed = (
            ma[mid]["content"] != mb[mid]["content"]
            or ma[mid]["token_count"] != mb[mid]["token_count"]
        )

        if content_changed and mid not in (targets | noises):
            raise ValueError(
                f"{a['scenario_id']}: "
                f"Target/Noise 이외의 Memory 변경: {mid}"
            )

    if not targets or not targets <= ma.keys():
        raise ValueError("Required State ID 오류")

    for state in a["required_states"]:
        for memory_map in (ma, mb):
            memory = memory_map[state["memory_id"]]

            if state["expected_value"] not in memory["content"]:
                raise ValueError(
                    "정답 State의 파일명이 사라짐"
                )


# =========================================================
# 2. 공통 지표
# =========================================================

def memory_ids(candidates) -> list[str]:
    return [
        candidate.memory.memory_id
        for candidate in candidates
    ]


def recall(
    found: set[str],
    expected: set[str],
) -> float | None:

    if not expected:
        return None

    return len(found & expected) / len(expected)


def mean_metric(
    rows: list[dict[str, Any]],
    key: str,
) -> float | None:

    values = [
        row[key]
        for row in rows
        if row.get(key) is not None
    ]

    if not values:
        return None

    return sum(values) / len(values)


# =========================================================
# 3. Proposed 단계별 Audit
# =========================================================

def audit_one(
    scenario: dict[str, Any],
    mode: int,
    config: dict[str, Any],
    counter: GeminiTokenCounter,
    system_prompt: str,
    tool_definitions: str,
) -> dict[str, Any]:

    now = datetime.fromisoformat(
        scenario["current_time"].replace("Z", "+00:00")
    )

    nominal_budget = scenario["context_budget"]

    margin = config["budget_policy"][
        "request_token_safety_margin"
    ]

    effective_budget = nominal_budget - margin

    if effective_budget <= 0:
        raise ValueError("Effective Budget이 0 이하")

    memories = tuple(
        MemoryItem.from_dict(raw)
        for raw in scenario["memory_snapshot"]
        if (
            raw["session_id"] == scenario["session_id"]
            and raw["turn_id"] <= scenario["current_turn"]
        )
    )

    store = SnapshotMemoryStore(memories)

    required_states = set(
        scenario["required_state_ids"]
    )

    applicable_constraints = set(
        scenario.get("applicable_constraint_ids", [])
    )

    # -----------------------------------------------------
    # Stage 1. Query Analysis
    # -----------------------------------------------------

    query_context = analyze_query(
        query=scenario["query"],
        current_turn=scenario["current_turn"],
        current_time=now,
    )

    # -----------------------------------------------------
    # Stage 2. Initial Retrieval
    # -----------------------------------------------------

    retrieved = retrieve_candidates(
        query_context=query_context,
        memory_store=store,
        session_id=scenario["session_id"],
        embedder=multilingual_embedder,
        top_k=config["runtime"]["candidate_top_k"],
        exclude_active_protected=True,
    )

    initial_ids = set(memory_ids(retrieved))

    # 같은 Version Chain 중 하나라도 검색됐는지 확인
    target_keys = {
        memory.memory_key
        for memory in memories
        if memory.memory_id in required_states
    }

    chain_entry = any(
        candidate.memory.memory_key in target_keys
        and candidate.memory.memory_key is not None
        for candidate in retrieved
    )

    # -----------------------------------------------------
    # Stage 3. Version Chain Expansion
    # -----------------------------------------------------

    expanded = expand_version_chains(
        candidates=retrieved,
        memory_store=store,
    )

    expanded_ids = set(memory_ids(expanded))

    # -----------------------------------------------------
    # Stage 4. Temporal Resolution
    # -----------------------------------------------------

    resolved = resolve_temporal_versions(
        candidates=expanded,
        query_context=query_context,
    )

    resolved_ids = set(memory_ids(resolved))

    # -----------------------------------------------------
    # Stage 5. Required State
    # -----------------------------------------------------

    required = resolve_required_states(
        candidates=resolved,
        query_context=query_context,
    )

    required_ids = {
        memory.memory_id
        for memory in required
    }

    # -----------------------------------------------------
    # Stage 6. Protected Constraints
    # -----------------------------------------------------

    protected = retrieve_protected(
        query_context=query_context,
        memory_store=store,
        session_id=scenario["session_id"],
    )

    protected_ids = {
        memory.memory_id
        for memory in protected
    }

    # -----------------------------------------------------
    # Stage 7. Fixed Input Tokens
    # -----------------------------------------------------

    fixed_tokens = fixed_context_tokens(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=scenario["query"],
        token_counter=counter,
    )

    # -----------------------------------------------------
    # Stage 8. Actual Proposed Selector
    # -----------------------------------------------------

    selection = select_memory(
        query=scenario["query"],
        session_id=scenario["session_id"],
        memory_store=store,
        total_budget=effective_budget,
        fixed_tokens=fixed_tokens,
        current_turn=scenario["current_turn"],
        current_time=now,
        embedder=multilingual_embedder,
        top_k=config["runtime"]["candidate_top_k"],
        utility_weights=tuple(
            config["runtime"]["utility_weights"]
        ),
        token_counter=counter,
    )

    # 진단용 단계별 계산과 실제 Selector가 일치하는지 검사
    if (
        tuple(memory_ids(retrieved))
        != selection.retrieved_memory_ids
    ):
        raise AssertionError(
            "Initial Retrieval과 Selector 결과 불일치"
        )

    if (
        {m.memory_id for m in selection.states}
        != required_ids
    ):
        raise AssertionError(
            "Required State Trace 불일치"
        )

    if (
        {m.memory_id for m in selection.protected}
        != protected_ids
    ):
        raise AssertionError(
            "Protected Trace 불일치"
        )

    # -----------------------------------------------------
    # Stage 9. Final Context Builder
    # -----------------------------------------------------

    context = build_context(
        system_prompt=system_prompt,
        tool_definitions=tool_definitions,
        query=scenario["query"],
        selection=selection,
        total_budget=effective_budget,
        token_counter=counter,
    )

    context_ok = (
        context.status == PipelineStatus.OK
        and context.context is not None
    )

    # 실패한 Context는 최종 선택 성공으로 인정하지 않음
    final_ids = (
        set(context.selected_memory_ids)
        if context_ok
        else set()
    )

    if (
        context_ok
        and context.total_input_tokens > effective_budget
    ):
        raise AssertionError(
            "최종 Context가 실효 Budget 초과"
        )

    if (
        context_ok
        and not final_ids <= set(selection.selected_memory_ids)
    ):
        raise AssertionError(
            "Selector가 선택하지 않은 Memory가 Context에 포함"
        )

    if context_ok:
        for memory in memories:
            if memory.memory_id not in final_ids:
                continue

            if memory.content.strip() not in context.context:
                raise AssertionError(
                    f"Context에서 Memory 본문 누락: "
                    f"{memory.memory_id}"
                )

    # -----------------------------------------------------
    # Stage 10. Metrics
    # -----------------------------------------------------

    stages = {
        "initial": initial_ids,
        "expanded": expanded_ids,
        "resolved": resolved_ids,
        "required": required_ids,
        "selection": set(selection.selected_memory_ids),
        "final": final_ids,
    }

    metadata = scenario.get("metadata", {})

    task_type = metadata.get("task_type")

    task_variant = (
        metadata.get("state_scope")
        if task_type == "temporal_update"
        else metadata.get("approval_status")
    )

    row = {
        "sample_id": scenario["scenario_id"],
        "mode": mode,
        "task_type": task_type,
        "task_variant": task_variant,

        "nominal_budget": nominal_budget,
        "effective_budget": effective_budget,
        "fixed_tokens": fixed_tokens,

        "selection_status": selection.status.value,
        "context_status": context.status.value,
        "context_built": int(context_ok),

        "final_context_tokens": (
            context.total_input_tokens
            if context_ok else None
        ),

        "unused_budget": (
            context.unused_budget
            if context_ok else None
        ),

        "selected_memory_tokens": selection.memory_tokens,

        "context_dropped_flexible_count": len(
            context.dropped_flexible_ids
        ),

        "required_state_ids": sorted(required_states),
        "applicable_constraint_ids": sorted(
            applicable_constraints
        ),

        "chain_entry": int(chain_entry),

        "recovered_from_initial_miss": int(
            not required_states <= initial_ids
            and required_states <= final_ids
        ),

        "wrong_version_in_final": int(
            bool(
                final_ids
                & set(
                    scenario.get(
                        "wrong_version_memory_ids", []
                    )
                )
            )
        ),
    }

    for stage, ids in stages.items():
        row[f"{stage}_ids"] = sorted(ids)

        row[f"{stage}_state_recall"] = recall(
            ids, required_states
        )

        row[f"{stage}_state_complete"] = int(
            required_states <= ids
        )

    row["protected_ids"] = sorted(protected_ids)

    row["required_constraints_recall"] = recall(
        protected_ids,
        applicable_constraints,
    )

    row["final_constraints_recall"] = recall(
        final_ids,
        applicable_constraints,
    )

    row["final_constraints_complete"] = (
        int(applicable_constraints <= final_ids)
        if applicable_constraints else None
    )

    row["dropped_flexible_ids"] = list(
        context.dropped_flexible_ids
    )

    return row


# =========================================================
# 4. CSV 저장
# =========================================================

def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:

    if not rows:
        return

    columns = list(rows[0])

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as stream:

        writer = csv.DictWriter(
            stream,
            fieldnames=columns,
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (list, dict))
                    else value
                )
                for key, value in row.items()
            })


# =========================================================
# 5. Main
# =========================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--mode1", type=Path, required=True
    )

    parser.add_argument(
        "--mode2", type=Path, required=True
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path("experiments/configs/main.json"),
    )

    parser.add_argument(
        "--budget", type=int, default=2048
    )

    parser.add_argument(
        "--horizon", type=int, default=100
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/results/"
            "narrative_final_context_h100_b2048"
        ),
    )

    parser.add_argument(
        "--resume",
        action="store_true",
    )

    args = parser.parse_args()

    # -----------------------------------------------------
    # 1. Freeze 검증
    # -----------------------------------------------------

    config = read_json(args.config)

    if config.get("freeze", {}).get("version") != "1.0.3":
        raise ValueError(
            "Main Freeze v1.0.3이 필요함"
        )

    if (
        config["model"]["name"] != "gemini-3.1-flash-lite"
        or config["runtime"]["candidate_top_k"] != 5
        or config["budget_policy"][
            "request_token_safety_margin"
        ] != 32
    ):
        raise ValueError(
            "Main 설정과 불일치"
        )

    if (
        tuple(config["runtime"]["utility_weights"])
        != (0.6, 0.3, 0.1)
    ):
        raise ValueError(
            "Utility Weights 불일치"
        )

    # -----------------------------------------------------
    # 2. Dataset 로딩
    # -----------------------------------------------------

    sources = {
        1: read_cell(
            args.mode1, 1, args.budget, args.horizon
        ),
        2: read_cell(
            args.mode2, 2, args.budget, args.horizon
        ),
    }

    s1 = sources[1]["scenarios"]
    s2 = sources[2]["scenarios"]

    if (
        sources[1]["parameters"]
        != sources[2]["parameters"]
        or s1.keys() != s2.keys()
    ):
        raise ValueError(
            "Mode 1/2 Grid 또는 Scenario ID 불일치"
        )

    expected_counter = (
        f"gemini:{config['model']['name']}"
    )

    for mode in (1, 2):
        if (
            sources[mode]["token_count_mode"]
            != expected_counter
        ):
            raise ValueError(
                f"Mode {mode}: Tokenizer 모델 불일치"
            )

    for sid in s1:
        validate_pair(s1[sid], s2[sid])

    print(
        f"[PASS] {len(s1)}개 Paired Scenario 검증"
    )

    # -----------------------------------------------------
    # 3. Prompt 및 모델 검증
    # -----------------------------------------------------

    model_config = config["model"]

    system_prompt = Path(
        model_config["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model_config["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    print("[MODEL] Embedding 모델 로딩")

    embedding = embedding_runtime_metadata()

    # -----------------------------------------------------
    # 4. 결과 및 Resume 설정
    # -----------------------------------------------------

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    provenance = {
        "audit_version": "narrative_final_context_v1",
        "freeze": config["freeze"],
        "scope": "single_step_pre_llm",
        "config_sha256": sha256(args.config),

        "system_prompt_sha256": hashlib.sha256(
            system_prompt.encode()
        ).hexdigest(),

        "tool_definitions_sha256": hashlib.sha256(
            tool_definitions.encode()
        ).hexdigest(),

        "system_instruction_sha256": hashlib.sha256(
            SYSTEM_INSTRUCTION.encode()
        ).hexdigest(),

        "source": {
            str(mode): {
                key: value
                for key, value in data.items()
                if key != "scenarios"
            }
            for mode, data in sources.items()
        },

        "top_k": 5,
        "nominal_budget": args.budget,
        "safety_margin": 32,
        "embedder": embedding,
    }

    fingerprint = hashlib.sha256(
        json.dumps(
            provenance,
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

    spec_file = args.output / "run_spec.json"
    log_file = args.output / "rows.jsonl"

    if args.resume:
        if (
            not spec_file.is_file()
            or not log_file.is_file()
        ):
            raise ValueError(
                "Resume용 Checkpoint가 없음"
            )

        previous = read_json(spec_file)

        if previous.get("fingerprint") != fingerprint:
            raise ValueError(
                "Checkpoint 설정 또는 입력 데이터가 다름"
            )

    else:
        if spec_file.exists() or log_file.exists():
            raise ValueError(
                "이미 실행 기록이 있음. "
                "--resume 또는 다른 --output 사용"
            )

        spec_file.write_text(
            json.dumps(
                {
                    "fingerprint": fingerprint,
                    **provenance,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    results = {}

    if args.resume:
        with log_file.open(
            "r", encoding="utf-8"
        ) as stream:

            for line in stream:
                row = json.loads(line)

                key = (
                    row["sample_id"],
                    row["mode"],
                )

                if key in results:
                    raise ValueError(
                        f"중복 Checkpoint: {key}"
                    )

                results[key] = row

    # -----------------------------------------------------
    # 5. Gemini CountTokens 초기화
    # -----------------------------------------------------

    if not os.getenv("GEMINI_API_KEY"):
        raise ValueError(
            "GEMINI_API_KEY가 등록되지 않음"
        )

    tokenizer_config = config[
        "runtime"
    ]["token_counter"]

    counter = GeminiTokenCounter(
        model=tokenizer_config["model"],
        cache_size=tokenizer_config["cache_size"],

        persistent_cache_path=tokenizer_config[
            "persistent_cache_path"
        ],

        max_retries=tokenizer_config["max_retries"],

        retry_base_seconds=tokenizer_config[
            "retry_base_seconds"
        ],
    )

    # Main Runner와 동일한 요청 단위 토큰 계산
    counter.configure_request(
        system_instruction=SYSTEM_INSTRUCTION,
    )

    # -----------------------------------------------------
    # 6. 실행
    # -----------------------------------------------------

    total = len(s1)

    print(
        f"[START] {total}개 Paired Scenario | "
        f"H={args.horizon} | B={args.budget}"
    )

    with log_file.open(
        "a", encoding="utf-8"
    ) as log:

        for index, sid in enumerate(sorted(s1), 1):

            for mode in (1, 2):
                if (sid, mode) in results:
                    continue

                row = audit_one(
                    scenario=sources[mode][
                        "scenarios"
                    ][sid],
                    mode=mode,
                    config=config,
                    counter=counter,
                    system_prompt=system_prompt,
                    tool_definitions=tool_definitions,
                )

                log.write(
                    json.dumps(
                        row,
                        ensure_ascii=False,
                    ) + "\n"
                )

                log.flush()

                results[(sid, mode)] = row

            print(
                f"[AUDIT] {index}/{total}: {sid}",
                flush=True,
            )

    # -----------------------------------------------------
    # 7. 전체 결과 병합
    # -----------------------------------------------------

    details = [
        results[(sid, mode)]
        for sid in sorted(s1)
        for mode in (1, 2)
    ]

    paired = []

    for sid in sorted(s1):
        a = results[(sid, 1)]
        b = results[(sid, 2)]

        paired.append({
            "sample_id": sid,
            "task_type": a["task_type"],
            "task_variant": a["task_variant"],

            "initial_mode1": a[
                "initial_state_complete"
            ],
            "initial_mode2": b[
                "initial_state_complete"
            ],

            "expanded_mode1": a[
                "expanded_state_complete"
            ],
            "expanded_mode2": b[
                "expanded_state_complete"
            ],

            "final_mode1": a[
                "final_state_complete"
            ],
            "final_mode2": b[
                "final_state_complete"
            ],

            "final_state_delta": (
                b["final_state_complete"]
                - a["final_state_complete"]
            ),

            "final_context_built_mode1": a[
                "context_built"
            ],
            "final_context_built_mode2": b[
                "context_built"
            ],

            "selection_status_mode1": a[
                "selection_status"
            ],
            "selection_status_mode2": b[
                "selection_status"
            ],

            "final_context_tokens_mode1": a[
                "final_context_tokens"
            ],
            "final_context_tokens_mode2": b[
                "final_context_tokens"
            ],
        })

    # -----------------------------------------------------
    # 8. Aggregate Metrics
    # -----------------------------------------------------

    metrics = (
        "initial_state_complete",
        "expanded_state_complete",
        "resolved_state_complete",
        "required_state_complete",
        "selection_state_complete",
        "final_state_complete",
        "context_built",
        "chain_entry",
        "final_constraints_complete",
        "wrong_version_in_final",
        "recovered_from_initial_miss",
        "final_context_tokens",
        "fixed_tokens",
        "context_dropped_flexible_count",
    )

    summary = {}

    for mode in (1, 2):
        subset = [
            row
            for row in details
            if row["mode"] == mode
        ]

        mode_summary = {
            "n": len(subset),
        }

        for metric in metrics:
            mode_summary[metric] = mean_metric(
                subset, metric
            )

        mode_summary["selection_status_counts"] = {
            status: sum(
                row["selection_status"] == status
                for row in subset
            )
            for status in sorted({
                row["selection_status"]
                for row in subset
            })
        }

        mode_summary["context_status_counts"] = {
            status: sum(
                row["context_status"] == status
                for row in subset
            )
            for status in sorted({
                row["context_status"]
                for row in subset
            })
        }

        mode_summary["initial_miss_count"] = sum(
            not row["initial_state_complete"]
            for row in subset
        )

        mode_summary[
            "initial_miss_final_recovered_count"
        ] = sum(
            row["recovered_from_initial_miss"]
            for row in subset
        )

        mode_summary[
            "accepted_context_overflow_count"
        ] = sum(
            row["context_built"]
            and (
                row["final_context_tokens"]
                > row["effective_budget"]
            )
            for row in subset
        )

        summary[f"mode{mode}"] = mode_summary

    summary["paired_final_improved"] = sum(
        row["final_state_delta"] > 0
        for row in paired
    )

    summary["paired_final_degraded"] = sum(
        row["final_state_delta"] < 0
        for row in paired
    )

    # -----------------------------------------------------
    # 9. 저장
    # -----------------------------------------------------

    write_csv(
        args.output / "details.csv",
        details,
    )

    write_csv(
        args.output / "paired.csv",
        paired,
    )

    (args.output / "summary.json").write_text(
        json.dumps(
            {
                "provenance_fingerprint": fingerprint,
                "summary": summary,
                "token_counter_metadata": counter.metadata(),

                "note": (
                    "Single-step pre-LLM selection and Context. "
                    "Not final task success or provider prompt usage."
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # -----------------------------------------------------
    # 10. 출력
    # -----------------------------------------------------

    print("\n===== FINAL CONTEXT AUDIT =====")

    for mode in (1, 2):
        row = summary[f"mode{mode}"]

        print(
            f"Mode {mode} | "
            f"Initial={row['initial_state_complete']:.1%} | "
            f"Expanded={row['expanded_state_complete']:.1%} | "
            f"Required={row['required_state_complete']:.1%} | "
            f"Final={row['final_state_complete']:.1%} | "
            f"Context OK={row['context_built']:.1%}"
        )

        print(
            "  Initial misses recovered: "
            f"{row['initial_miss_final_recovered_count']}"
            f"/{row['initial_miss_count']}"
        )

        print(
            "  Selection statuses: "
            f"{row['selection_status_counts']}"
        )

    print(
        f"\nPaired Final Improved: "
        f"{summary['paired_final_improved']}"
    )

    print(
        f"Paired Final Degraded: "
        f"{summary['paired_final_degraded']}"
    )

    print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
