from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

from benchmark.experiment_runtime import embedding_runtime_metadata, multilingual_embedder
from memory.retrieval import semantic_retrieve
from memory.schema import MemoryItem, MemoryType


# 0: Mode 1 padding, 1: Mode 2 padding
CONDITIONS = {
    "A_T1_N1": (0, 0),
    "B_T2_N1": (1, 0),
    "C_T1_N2": (0, 1),
    "D_T2_N2": (1, 1),
}

IMMUTABLE_SCENARIO_FIELDS = (
    "scenario_id", "sample_id", "query", "session_id", "current_turn",
    "current_time", "context_budget", "query_context", "target_memory_ids",
    "required_state_ids", "required_states", "constraint_rules",
    "applicable_constraint_ids", "expected_tool_calls", "forbidden_tool_calls",
    "environment", "wrong_version_memory_ids",
)

IMMUTABLE_MEMORY_FIELDS = (
    "memory_id", "session_id", "turn_id", "memory_type", "importance",
    "is_active", "is_protected", "memory_key", "created_at", "valid_from",
    "valid_to", "supersedes", "superseded_by",
)


def load_json(path: Path) -> dict[str, Any]:
    obj = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(obj, dict):
        raise ValueError(f"Expected a JSON object: {path}")

    return obj


def sha256(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(chunk)

    return h.hexdigest()


def load_dataset(
    root: Path,
    expected_mode: int,
    budget: int,
    horizon: int,
) -> dict[str, Any]:
    manifest_path = root / "manifest.json" if root.is_dir() else root
    manifest = load_json(manifest_path)

    if manifest.get("horizon_generation_mode", 1) != expected_mode:
        raise ValueError(f"Wrong dataset mode: {manifest_path}")

    matches = [
        cell
        for cell in manifest["cells"]
        if cell["parameters"]["context_token_budget"] == budget
        and cell["parameters"]["horizon_turns"] == horizon
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected exactly 1 H{horizon}/B{budget} cell "
            f"in {root}; got {len(matches)}"
        )

    cell = matches[0]
    cell_path = manifest_path.parent / cell["path"]
    payload = load_json(cell_path)

    if payload.get("token_count_mode") != manifest.get("token_count_mode"):
        raise ValueError(f"Token counter mismatch within {root}")

    scenarios: dict[str, Any] = {}

    for sc in payload["scenarios"]:
        sid = sc["scenario_id"]

        if sid in scenarios:
            raise ValueError(f"Duplicate scenario: {sid}")

        if sc.get("metadata", {}).get(
            "horizon_generation_mode", 1
        ) != expected_mode:
            raise ValueError(f"Wrong scenario mode: {sid}")

        scenarios[sid] = sc

    return {
        "parameters": cell["parameters"],
        "counter": payload.get("token_count_mode"),
        "scenarios": scenarios,
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "cell_path": str(cell_path),
        "cell_sha256": sha256(cell_path),
    }


def validate_pair(
    s1: dict[str, Any],
    s2: dict[str, Any],
) -> tuple[str, set[str], set[str]]:
    """두 모드의 정답·제약·버전 구조 일치 여부를 검증한다."""

    sid = s1["scenario_id"]

    for field in IMMUTABLE_SCENARIO_FIELDS:
        if s1.get(field) != s2.get(field):
            raise ValueError(
                f"{sid}: scenario field changed: {field}"
            )

    targets = set(s1["required_state_ids"])

    if len(targets) != 1:
        raise ValueError(
            f"{sid}: expected one required State (found {targets})"
        )

    m1 = {
        m["memory_id"]: m
        for m in s1["memory_snapshot"]
    }

    m2 = {
        m["memory_id"]: m
        for m in s2["memory_snapshot"]
    }

    if (
        len(m1) != len(s1["memory_snapshot"])
        or len(m2) != len(s2["memory_snapshot"])
    ):
        raise ValueError(f"{sid}: duplicate memory IDs")

    if set(m1) != set(m2):
        raise ValueError(f"{sid}: memory IDs changed")

    noises = {
        mid for mid in m1
        if "_noise_" in mid
    }

    if not targets <= set(m1) or not noises or targets & noises:
        raise ValueError(
            f"{sid}: invalid target/noise identity"
        )

    changed = set()

    for mid in m1:
        a, b = m1[mid], m2[mid]

        for field in IMMUTABLE_MEMORY_FIELDS:
            if a.get(field) != b.get(field):
                raise ValueError(
                    f"{sid}: {mid} changed immutable field {field}"
                )

        if (
            a["content"] != b["content"]
            or a["token_count"] != b["token_count"]
        ):
            changed.add(mid)

        MemoryItem.from_dict(a)
        MemoryItem.from_dict(b)

    # Target과 Noise 이외의 기억이 변경되면 교차 실험을 중단
    unexpected = changed - (targets | noises)

    if unexpected:
        raise ValueError(
            f"{sid}: non-target/non-noise memory changed: "
            f"{unexpected}"
        )

    # 패딩 변경으로 정답 파일명이 훼손되지 않았는지 검증
    for state in s1["required_states"]:
        for source in (m1, m2):
            if (
                state["expected_value"]
                not in source[state["memory_id"]]["content"]
            ):
                raise ValueError(
                    f"{sid}: expected State content missing"
                )

    return sid, targets, noises


def hybrid_memories(
    s1: dict[str, Any],
    s2: dict[str, Any],
    targets: set[str],
    noises: set[str],
    target_mode2: int,
    noise_mode2: int,
) -> list[MemoryItem]:
    """원본 Mode 1을 복사하고 지정된 요인의 content/token_count만 교체."""

    src2 = {
        m["memory_id"]: m
        for m in s2["memory_snapshot"]
    }

    result = []

    for original in s1["memory_snapshot"]:
        mid = original["memory_id"]

        swap = (
            (mid in targets and target_mode2)
            or (mid in noises and noise_mode2)
        )

        new = dict(original)

        if swap:
            new["content"] = src2[mid]["content"]
            new["token_count"] = src2[mid]["token_count"]

        result.append(MemoryItem.from_dict(new))

    return result


def evaluate(
    scenario: dict[str, Any],
    memories: list[MemoryItem],
    target_id: str,
    noises: set[str],
    top_k: int,
    pool: str,
    condition: str,
) -> dict[str, Any]:
    """기존 semantic_retrieve로 정답 순위와 Noise 경쟁을 측정."""

    candidate_memories = [
        m for m in memories
        if m.session_id == scenario["session_id"]
        and m.turn_id <= scenario["current_turn"]
        and (
            pool != "proposed"
            or not (
                m.memory_type == MemoryType.CONSTRAINT
                and m.is_active
                and m.is_protected
            )
        )
    ]

    # Top-k보다 넓게 검색해 정답의 실제 전체 순위 측정
    hits = semantic_retrieve(
        query=scenario["query"],
        memories=candidate_memories,
        embedder=multilingual_embedder,
        top_k=max(1, len(candidate_memories)),
    )

    if not hits:
        raise ValueError(
            f"{scenario['scenario_id']}: no available memories"
        )

    ranks = {
        hit.memory.memory_id: i + 1
        for i, hit in enumerate(hits)
    }

    scores = {
        hit.memory.memory_id: hit.score
        for hit in hits
    }

    if target_id not in ranks:
        raise ValueError(
            f"{scenario['scenario_id']}: "
            "target excluded from search"
        )

    top = hits[:top_k]

    top_ids = [
        hit.memory.memory_id
        for hit in top
    ]

    # 정답을 제외한 경쟁 후보들 중 K번째 점수
    # 양수이면 정답이 Top-k에 포함될 가능성이 높음
    competitors = [
        hit.score
        for hit in hits
        if hit.memory.memory_id != target_id
    ]

    kth_competitor = (
        competitors[min(top_k, len(competitors)) - 1]
        if competitors else None
    )

    m_by_id = {
        m.memory_id: m
        for m in memories
    }

    essential = set(scenario["target_memory_ids"])

    target_tokens = m_by_id[target_id].token_count

    noise_tokens = sum(
        m_by_id[mid].token_count
        for mid in noises
    )

    relevant_tokens = sum(
        m_by_id[mid].token_count
        for mid in essential
    )

    actual_noise_ratio = (
        noise_tokens / (noise_tokens + relevant_tokens)
    )

    metadata = scenario.get("metadata", {})

    subtype = (
        metadata.get("state_scope")
        if metadata.get("task_type") == "temporal_update"
        else metadata.get("approval_status")
    )

    return {
        "sample_id": scenario["scenario_id"],
        "task_type": f"{metadata.get('task_type')}/{subtype}",
        "pool": pool,
        "condition": condition,

        "hit_at_k": int(target_id in top_ids),
        "target_rank": ranks[target_id],
        "target_similarity": scores[target_id],

        "k_margin": (
            None if kth_competitor is None
            else scores[target_id] - kth_competitor
        ),

        "noise_in_top_k": sum(
            mid in noises
            for mid in top_ids
        ),

        "best_noise_rank": min(
            (ranks[mid] for mid in noises if mid in ranks),
            default=None,
        ),

        "top_k_ids": top_ids,
        "target_tokens": target_tokens,
        "noise_tokens": noise_tokens,
        "actual_noise_ratio": actual_noise_ratio,
    }


def percentile_ci(
    values: list[float],
    *,
    seed: int = 2026,
    iterations: int = 4000,
) -> tuple[float, float]:
    """기본 과제 단위 paired bootstrap CI."""

    rng = random.Random(seed)
    n = len(values)

    estimates = sorted(
        sum(
            values[rng.randrange(n)]
            for _ in range(n)
        ) / n
        for _ in range(iterations)
    )

    return (
        estimates[int(iterations * 0.025)],
        estimates[int(iterations * 0.975)],
    )


def effects(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """요인별 효과 및 Target×Noise 상호작용 계산."""

    indexed = defaultdict(dict)

    for row in rows:
        indexed[
            (row["sample_id"], row["pool"])
        ][row["condition"]] = row

    per_sample = []

    for (sid, pool), conditions in sorted(indexed.items()):
        if set(conditions) != set(CONDITIONS):
            raise ValueError(
                f"Missing conditions: {sid}/{pool}"
            )

        A, B, C, D = (
            conditions[c]
            for c in CONDITIONS
        )

        per_sample.append({
            "sample_id": sid,
            "task_type": A["task_type"],
            "pool": pool,

            "A_hit": A["hit_at_k"],
            "B_hit": B["hit_at_k"],
            "C_hit": C["hit_at_k"],
            "D_hit": D["hit_at_k"],

            # 정답 패딩 효과 (Noise를 Mode 1에 고정)
            "target_effect_noise1":
                B["hit_at_k"] - A["hit_at_k"],

            # 정답 패딩 효과 (Noise를 Mode 2에 고정)
            "target_effect_noise2":
                D["hit_at_k"] - C["hit_at_k"],

            # Noise 패딩 효과 (정답을 Mode 1에 고정)
            "noise_effect_target1":
                C["hit_at_k"] - A["hit_at_k"],

            # Noise 패딩 효과 (정답을 Mode 2에 고정)
            "noise_effect_target2":
                D["hit_at_k"] - B["hit_at_k"],

            # 상호작용 = D - B - C + A
            "interaction":
                D["hit_at_k"]
                - B["hit_at_k"]
                - C["hit_at_k"]
                + A["hit_at_k"],

            "target_rank_change_noise1":
                B["target_rank"] - A["target_rank"],

            "noise_rank_change_target1":
                C["target_rank"] - A["target_rank"],

            "full_rank_change":
                D["target_rank"] - A["target_rank"],
        })

    fields = [
        "target_effect_noise1",
        "target_effect_noise2",
        "noise_effect_target1",
        "noise_effect_target2",
        "interaction",
    ]

    summary = []

    for pool in ("baseline", "proposed"):
        subtypes = sorted({
            r["task_type"]
            for r in per_sample
        })

        for subtype in ("all", *subtypes):
            subset = [
                r for r in per_sample
                if r["pool"] == pool
                and (
                    subtype == "all"
                    or r["task_type"] == subtype
                )
            ]

            if not subset:
                continue

            item = {
                "pool": pool,
                "task_type": subtype,
                "n": len(subset),
            }

            for condition in "ABCD":
                item[f"{condition}_recall_at_k"] = (
                    sum(
                        r[f"{condition}_hit"]
                        for r in subset
                    ) / len(subset)
                )

            for field in fields:
                values = [
                    r[field]
                    for r in subset
                ]

                lo, hi = percentile_ci(values)

                item[field] = sum(values) / len(values)
                item[field + "_ci_low"] = lo
                item[field + "_ci_high"] = hi

                item[field + "_improved"] = sum(
                    x > 0 for x in values
                )

                item[field + "_degraded"] = sum(
                    x < 0 for x in values
                )

            summary.append(item)

    return per_sample, summary


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return

    cols = list(rows[0])

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=cols,
        )

        writer.writeheader()

        for row in rows:
            writer.writerow({
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, list)
                    else value
                )
                for key, value in row.items()
            })


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
        "--budget", type=int, default=2048
    )
    parser.add_argument(
        "--horizon", type=int, default=100
    )
    parser.add_argument(
        "--top-k", type=int, default=5
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(
            "experiments/results/narrative_factorial_h100_b2048"
        ),
    )

    args = parser.parse_args()

    if (
        args.budget <= 0
        or args.horizon <= 0
        or args.top_k <= 0
    ):
        parser.error(
            "--budget, --horizon and --top-k must be positive"
        )

    # -----------------------------------------------------
    # 1. 데이터 로딩
    # -----------------------------------------------------

    datasets = {
        1: load_dataset(
            args.mode1, 1, args.budget, args.horizon
        ),
        2: load_dataset(
            args.mode2, 2, args.budget, args.horizon
        ),
    }

    s1 = datasets[1]["scenarios"]
    s2 = datasets[2]["scenarios"]

    if datasets[1]["parameters"] != datasets[2]["parameters"]:
        raise ValueError(
            "Mode 1 and 2 grid parameters differ"
        )

    if datasets[1]["counter"] != datasets[2]["counter"]:
        raise ValueError(
            "Mode 1 and 2 token counters differ"
        )

    if set(s1) != set(s2):
        raise ValueError(
            "Mode 1 and 2 sample IDs differ"
        )

    print(
        "[MODEL] Load frozen embedding model",
        flush=True,
    )

    # 리비전을 확인할 수 없으면 연구용 결과 생성 중단
    embedding = embedding_runtime_metadata()

    # -----------------------------------------------------
    # 2. 2x2 교차 Retrieval
    # -----------------------------------------------------

    details = []

    for idx, sid in enumerate(sorted(s1), start=1):
        print(
            f"[AUDIT] {idx}/{len(s1)} {sid}",
            flush=True,
        )

        _, targets, noises = validate_pair(
            s1[sid], s2[sid]
        )

        target_id = next(iter(targets))

        for condition, (t, n) in CONDITIONS.items():
            memories = hybrid_memories(
                s1[sid],
                s2[sid],
                targets,
                noises,
                t,
                n,
            )

            for pool in ("baseline", "proposed"):
                details.append(
                    evaluate(
                        scenario=s1[sid],
                        memories=memories,
                        target_id=target_id,
                        noises=noises,
                        top_k=args.top_k,
                        pool=pool,
                        condition=condition,
                    )
                )

    # -----------------------------------------------------
    # 3. Paired Factorial Effect
    # -----------------------------------------------------

    cases, summary = effects(details)

    # -----------------------------------------------------
    # 4. 결과 저장
    # -----------------------------------------------------

    args.output.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_csv(
        args.output / "factorial_details.csv",
        details,
    )

    write_csv(
        args.output / "factorial_effects.csv",
        cases,
    )

    write_csv(
        args.output / "factorial_summary.csv",
        summary,
    )

    audit = {
        "scope": "offline_retrieval_only",
        "factors": {
            "target": "required State padding",
            "noise": "synthetic noise padding",
        },
        "conditions": CONDITIONS,
        "top_k": args.top_k,
        "n_unique_samples": len(s1),
        "embedding": embedding,
        "mode1": {
            k: v
            for k, v in datasets[1].items()
            if k != "scenarios"
        },
        "mode2": {
            k: v
            for k, v in datasets[2].items()
            if k != "scenarios"
        },
        "summary": summary,
    }

    (
        args.output / "audit.json"
    ).write_text(
        json.dumps(
            audit,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # -----------------------------------------------------
    # 5. 콘솔 요약
    # -----------------------------------------------------

    print("\n=== 2x2 factorial Recall@K ===")

    for row in summary:
        if row["task_type"] != "all":
            continue

        print(
            f"\n{row['pool']}  n={row['n']}"
        )

        print(
            "  " + " | ".join(
                f"{c}={row[f'{c}_recall_at_k']:.1%}"
                for c in "ABCD"
            )
        )

        for metric in (
            "target_effect_noise1",
            "noise_effect_target1",
            "interaction",
        ):
            print(
                f"  {metric}: {row[metric]:+.1%} "
                f"(bootstrap 95% CI "
                f"{row[metric + '_ci_low']:+.1%} to "
                f"{row[metric + '_ci_high']:+.1%})"
            )

    print(f"\nSaved to: {args.output}")


if __name__ == "__main__":
    main()
