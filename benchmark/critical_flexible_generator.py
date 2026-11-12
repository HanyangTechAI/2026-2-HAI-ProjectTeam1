from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from context.tokenizer import (
    GeminiTokenCounter,
    provisional_token_count,
    token_count_mode,
)
from memory.schema import MemoryItem, MemoryType
from memory.store import SnapshotMemoryStore


DATASET_VERSION = "critical_flexible_v1"
TASK_FAMILIES = ("file_lookup", "approved_email")

CONDITIONS = {
    "A": ("short", "short"),
    "B": ("short", "long"),
    "C": ("long", "short"),
    "D": ("long", "long"),
}

TOKEN_BANDS = {
    "short": (24, 40),
    "long": (130, 170),
}

TIME = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
CURRENT_TURN = 100
CONTEXT_BUDGET = 2048

# 정답 파일명, 승인번호, 이메일 주소를 포함하지 않는 중립적인 확장 문장.
FILLER = (
    "이 항목은 진행 배경을 이해하기 위한 일반적인 참고 기록입니다.",
    "참여자들은 기록 내용을 정리하고 검토 순서를 함께 확인했습니다.",
    "관련 논의는 진행 상황에 따라 순서대로 정리되었습니다.",
    "과정에서 확인한 보충 설명은 별도 의견으로 기록했습니다.",
    "이후 검토에서는 표현의 명확성과 정리 방식을 확인했습니다.",
    "작성 과정에서 자료의 구조와 설명 흐름을 점검했습니다.",
    "공유된 기록은 일반적인 업무 진행 경위를 서술합니다.",
    "검토 과정의 참고 사항을 담당자들이 차례로 살펴보았습니다.",
)


def _identity(family: str, seed: int) -> dict[str, str]:
    """기본 과제의 식별자와 정답을 결정적으로 생성한다."""
    key = f"{DATASET_VERSION}:{family}:{seed}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()

    return {
        "base_case_id": f"cf_v1_{family}_{seed:03d}",
        "project": f"HAI-{h[:6].upper()}",
        "filename": f"{h[6:18]}_final.pdf",
        "approval_id": f"approved_{h[18:34]}",
        "recipient": f"reviewer_{h[2:12]}@external.test",
    }


def _fit_content(
    base: str,
    length_class: str,
    counter: Callable[[str], int],
    offset: int,
) -> tuple[str, int]:
    """실제 토크나이저로 지정된 Short/Long 길이를 맞춘다."""
    lo, hi = TOKEN_BANDS[length_class]

    text = base
    count = counter(text)

    if type(count) is not int or count < 0:
        raise ValueError(
            "token counter must return a non-negative integer"
        )

    if count > hi:
        raise ValueError(
            f"base text exceeds {length_class} limit: "
            f"{count}>{hi}: {base}"
        )

    cursor = offset

    for _ in range(100):
        if lo <= count <= hi:
            return text, count

        # 긴 Memory의 토큰 측정 호출을 줄이기 위해
        # 4문장 → 2문장 → 1문장 순서로 시도한다.
        for batch in (4, 2, 1):
            addition = " ".join(
                FILLER[(cursor + j) % len(FILLER)]
                for j in range(batch)
            )

            candidate = text + " " + addition
            n = counter(candidate)

            if type(n) is not int or n < 0:
                raise ValueError(
                    "token counter must return a non-negative integer"
                )

            if count < n <= hi:
                text = candidate
                count = n
                cursor += batch
                break

        else:
            # Short 구간 끝에서 문장 하나가 너무 길면
            # 작은 단위로 채운다.
            for tail in (" 참고.", " 기록.", " 확인."):
                candidate = text + tail
                n = counter(candidate)

                if count < n <= hi:
                    text = candidate
                    count = n
                    break
            else:
                raise ValueError(
                    f"cannot fit content into {length_class} token band"
                )

    raise RuntimeError("content padding did not converge")


def _memory(
    memory_id: str,
    session_id: str,
    kind: MemoryType,
    text: str,
    measured_tokens: int,
    *,
    turn: int = 80,
    importance: float = 0.3,
    protected: bool = False,
    key: str | None = None,
) -> dict[str, Any]:
    """기존 MemoryItem Schema를 사용하는 Snapshot 항목 생성."""
    created = (
        TIME
        - timedelta(days=30)
        + timedelta(minutes=turn)
    )

    item = MemoryItem(
        memory_id=memory_id,
        session_id=session_id,
        turn_id=turn,
        memory_type=kind,
        content=text,
        token_count=measured_tokens,
        created_at=created,
        valid_from=created,
        importance=importance,
        is_active=True,
        is_protected=protected,
        memory_key=key,
    )

    return item.to_dict()


def make_scenario(
    family: str,
    seed: int,
    condition: str,
    *,
    counter: Callable[[str], int] = provisional_token_count,
    content_cache: dict[
        tuple[str, int, str, str],
        tuple[str, int],
    ] | None = None,
) -> dict[str, Any]:
    """기본 과제 하나의 특정 길이 조건을 생성한다."""

    if family not in TASK_FAMILIES:
        raise ValueError(f"invalid task family: {family}")

    if type(seed) is not int or seed < 0:
        raise ValueError(
            "seed must be a non-negative integer"
        )

    if condition not in CONDITIONS:
        raise ValueError(
            f"unknown stress condition: {condition}"
        )

    if not callable(counter):
        raise TypeError("counter must be callable")

    identity = _identity(family, seed)

    base = identity["base_case_id"]
    project = identity["project"]
    session = f"session_{base}"

    critical_length, noise_length = CONDITIONS[condition]

    cache = (
        content_cache
        if content_cache is not None
        else {}
    )

    # =========================================================
    # 1. Task 및 Critical Ground Truth
    # =========================================================

    if family == "file_lookup":
        critical_base = (
            f"{project} 제출용 파일명: "
            f"{identity['filename']}."
        )

        critical_value = identity["filename"]

        query = (
            f"{project} 프로젝트의 "
            "제출 자료 파일을 찾아줘."
        )

        action = {
            "tool_name": "file",
            "action": "find_file",
        }

        arguments = {
            "filename": critical_value,
        }

    else:
        critical_base = (
            f"{project} 발송 승인번호: "
            f"{identity['approval_id']}."
        )

        critical_value = identity["approval_id"]

        query = (
            f"{project} 프로젝트의 현재 보고서를 "
            f"{identity['recipient']}에게 "
            "외부 메일로 보내줘."
        )

        action = {
            "tool_name": "email",
            "action": "send_email",
        }

        arguments = {
            "recipient": identity["recipient"],
            "attachment": identity["filename"],
            "approval_id": identity["approval_id"],
        }

    def sized(
        role: str,
        base_text: str,
        length: str,
        offset: int,
    ):
        cache_key = (family, seed, role, length)

        if cache_key not in cache:
            cache[cache_key] = _fit_content(
                base_text,
                length,
                counter,
                offset,
            )

        return cache[cache_key]

    # =========================================================
    # 2. Critical Flexible FACT
    # =========================================================

    critical_text, critical_tokens = sized(
        "critical",
        critical_base,
        critical_length,
        seed,
    )

    critical_id = f"{base}_{condition}_critical"

    memories = [
        _memory(
            critical_id,
            session,
            MemoryType.FACT,
            critical_text,
            critical_tokens,
            importance=0.9,
        )
    ]

    # =========================================================
    # 3. Distractor FACT × 3
    # =========================================================

    distractor_ids = []
    noise_tokens = []

    for i in range(3):
        noise_id = f"{base}_{condition}_noise_{i}"

        noise_base = (
            f"{project} 검토 메모 {i + 1}: "
            "일반 진행 기록."
        )

        text, n = sized(
            f"noise_{i}",
            noise_base,
            noise_length,
            seed + i + 1,
        )

        distractor_ids.append(noise_id)
        noise_tokens.append(n)

        memories.append(
            _memory(
                noise_id,
                session,
                MemoryType.FACT,
                text,
                n,
            )
        )

    # =========================================================
    # 4. Mandatory / Protected
    # =========================================================

    required_state_ids = []
    required_states = []

    applicable_constraint_ids = []
    constraint_rules = []

    target_ids = [critical_id]

    if family == "approved_email":

        # 승인번호는 여기에 절대 넣지 않는다.
        state_id = f"{base}_{condition}_state"

        state_text = (
            f"{project} 현재 보고서 파일은 "
            f"{identity['filename']} 입니다."
        )

        memories.append(
            _memory(
                state_id,
                session,
                MemoryType.STATE,
                state_text,
                counter(state_text),
                turn=90,
                importance=0.8,
                key=f"{project}.report.current_file",
            )
        )

        required_state_ids.append(state_id)

        required_states.append({
            "memory_id": state_id,
            "tool_call": action,
            "argument": "attachment",
            "expected_value": identity["filename"],
            "state_scope": "current",
            "stale_values": [],
        })

        # 승인번호의 실제 값은 넣지 않고 규칙만 보존한다.
        constraint_id = f"{base}_{condition}_constraint"

        rule_text = (
            f"{project} 외부 메일 발송은 "
            "승인 완료 번호가 있는 경우에만 허용됩니다."
        )

        memories.append(
            _memory(
                constraint_id,
                session,
                MemoryType.CONSTRAINT,
                rule_text,
                counter(rule_text),
                turn=91,
                importance=0.8,
                protected=True,
                key=f"{project}.email.approval_rule",
            )
        )

        applicable_constraint_ids.append(constraint_id)

        constraint_rules.append({
            "constraint_id": constraint_id,
            "approval": {
                "execution": action,
                "request": {
                    "tool_name": "email",
                    "action": "request_approval",
                },
                "valid_approval_ids": [
                    identity["approval_id"]
                ],
                "request_required": False,
            },
        })

        target_ids.extend([
            state_id,
            constraint_id,
        ])

    # =========================================================
    # 5. Runner / Evaluator Scenario
    # =========================================================

    scenario = {
        "scenario_id": f"{base}_{condition}",
        "sample_id": f"{base}_{condition}",
        "title": (
            f"Critical Flexible {family} "
            f"{condition} seed={seed}"
        ),
        "benchmark_enabled": True,
        "validation_scope": "integration",

        "session_id": session,
        "current_turn": CURRENT_TURN,
        "current_time": TIME.isoformat(),

        "query": query,
        "context_budget": CONTEXT_BUDGET,
        "token_count_mode": token_count_mode(counter),

        "history": "",
        "memory_snapshot": memories,

        "target_memory_ids": target_ids,
        "required_state_ids": required_state_ids,
        "required_states": required_states,

        "applicable_constraint_ids": applicable_constraint_ids,
        "constraint_rules": constraint_rules,

        "temporal_version_required": False,
        "wrong_version_memory_ids": [],

        "expected_tool_calls": [{
            **action,
            "arguments": arguments,
        }],

        "forbidden_tool_calls": [],
        "expected_pipeline_status": "ok",

        # Mock Tool Executor에만 전달한다.
        # Planner Context에는 정답으로 제공하지 않는다.
        "environment": {
            "files": {
                identity["filename"]: (
                    f"{project} report content"
                ),
            },
            "valid_approval_ids": (
                [identity["approval_id"]]
                if family == "approved_email"
                else []
            ),
        },

        "metadata": {
            "dataset_version": DATASET_VERSION,
            "base_case_id": base,
            "seed": seed,
            "task_family": family,
            "stress_condition": condition,

            "critical_length": critical_length,
            "noise_length": noise_length,

            "critical_flexible_memory_ids": [
                critical_id
            ],
            "distractor_memory_ids": distractor_ids,

            "critical_tokens": critical_tokens,
            "noise_tokens": noise_tokens,

            "context_token_budget": CONTEXT_BUDGET,
            "token_count_mode": token_count_mode(counter),
        },
    }

    validate_scenario(scenario, counter)
    return scenario


def validate_scenario(
    scenario: dict[str, Any],
    counter: Callable[[str], int],
) -> None:
    """길이, 정답 누출, Store, 평가 Schema를 검증한다."""

    meta = scenario["metadata"]
    condition = meta["stress_condition"]

    critical_length, noise_length = CONDITIONS[condition]

    items = [
        MemoryItem.from_dict(x)
        for x in scenario["memory_snapshot"]
    ]

    # 실제 Store 일관성 검사
    SnapshotMemoryStore(tuple(items))

    ids = [m.memory_id for m in items]

    if len(ids) != len(set(ids)):
        raise ValueError("duplicate memory ID")

    critical_ids = meta["critical_flexible_memory_ids"]
    noise_ids = meta["distractor_memory_ids"]

    if len(critical_ids) != 1 or len(noise_ids) != 3:
        raise ValueError(
            "expected exactly one critical and three distractors"
        )

    critical_id = critical_ids[0]
    by_id = {m.memory_id: m for m in items}

    critical = by_id[critical_id]
    noise = [by_id[i] for i in noise_ids]

    competing = [critical, *noise]

    if any(
        m.memory_type != MemoryType.FACT or m.is_protected
        for m in competing
    ):
        raise ValueError(
            "all competing memories must be unprotected FACTs"
        )

    if len({m.turn_id for m in competing}) != 1:
        raise ValueError(
            "competing memory recency is not controlled"
        )

    if critical.importance != 0.9 or any(
        m.importance != 0.3 for m in noise
    ):
        raise ValueError(
            "competing memory importance not controlled"
        )

    # 실제 토큰 길이를 다시 측정해 검증한다.
    checks = [
        (critical, critical_length),
        *[(m, noise_length) for m in noise],
    ]

    for m, klass in checks:
        lo, hi = TOKEN_BANDS[klass]
        actual = counter(m.content)

        if m.token_count != actual or not lo <= actual <= hi:
            raise ValueError(
                f"incorrect {klass} token count "
                f"for {m.memory_id}: {actual}"
            )

    # =========================================================
    # Ground Truth 유일성
    # =========================================================

    family = meta["task_family"]

    args = scenario["expected_tool_calls"][0]["arguments"]

    answer = (
        args["filename"]
        if family == "file_lookup"
        else args["approval_id"]
    )

    if answer in scenario["query"]:
        raise ValueError(
            "critical answer leaked into the query"
        )

    if critical.content.count(answer) != 1:
        raise ValueError(
            "critical answer must occur exactly once in its FACT"
        )

    if any(
        answer in m.content
        for m in items
        if m.memory_id != critical_id
    ):
        raise ValueError(
            "critical answer leaked into another memory"
        )

    if not set(critical_ids).issubset(
        scenario["target_memory_ids"]
    ):
        raise ValueError(
            "critical labels absent from target IDs"
        )

    if len(scenario["expected_tool_calls"]) != 1:
        raise ValueError(
            "expected exactly one tool call"
        )

    # =========================================================
    # Task Family별 필수 Schema
    # =========================================================

    if family == "file_lookup":
        if (
            scenario["required_states"]
            or scenario["constraint_rules"]
            or len(items) != 4
        ):
            raise ValueError(
                "file lookup must contain four competing FACTs"
            )

        if scenario["expected_tool_calls"][0]["action"] != "find_file":
            raise ValueError(
                "file lookup has wrong tool"
            )

    else:
        if (
            len(items) != 6
            or len(scenario["required_states"]) != 1
            or len(scenario["constraint_rules"]) != 1
        ):
            raise ValueError(
                "approved email requires one State and one Constraint"
            )

        if scenario["expected_tool_calls"][0]["action"] != "send_email":
            raise ValueError(
                "approved email has wrong tool"
            )

        if scenario["environment"]["valid_approval_ids"] != [answer]:
            raise ValueError(
                "mock tool approval IDs differ from the answer"
            )


def generate_dataset(
    *,
    counter: Callable[[str], int] = provisional_token_count,
    seed_start: int = 42,
    cases_per_family: int = 20,
) -> dict[str, Any]:
    """40개 기본 과제와 4개 길이 조건을 생성한다."""

    if type(seed_start) is not int or seed_start < 0:
        raise ValueError(
            "seed_start must be a non-negative integer"
        )

    if type(cases_per_family) is not int or cases_per_family < 1:
        raise ValueError(
            "cases_per_family must be a positive integer"
        )

    content_cache = {}

    scenarios = [
        make_scenario(
            family,
            seed,
            condition,
            counter=counter,
            content_cache=content_cache,
        )
        for family in TASK_FAMILIES
        for seed in range(
            seed_start,
            seed_start + cases_per_family,
        )
        for condition in CONDITIONS
    ]

    if len({
        s["scenario_id"] for s in scenarios
    }) != len(scenarios):
        raise RuntimeError("duplicate scenario IDs")

    # 같은 기본 과제의 네 가지 조건에서
    # Query와 정답 Tool 호출이 변하면 안 된다.
    for family in TASK_FAMILIES:
        for seed in range(
            seed_start,
            seed_start + cases_per_family,
        ):
            base = f"cf_v1_{family}_{seed:03d}"

            variants = [
                s for s in scenarios
                if s["metadata"]["base_case_id"] == base
            ]

            if len(variants) != len(CONDITIONS):
                raise RuntimeError(
                    "not all length conditions present"
                )

            if len({
                s["query"] for s in variants
            }) != 1:
                raise RuntimeError(
                    "paired queries differ"
                )

            if len({
                json.dumps(
                    s["expected_tool_calls"],
                    sort_keys=True,
                )
                for s in variants
            }) != 1:
                raise RuntimeError(
                    "paired ground truths differ"
                )

    return {
        "schema_version": "1.0",
        "generator": "benchmark.critical_flexible_generator",
        "dataset_version": DATASET_VERSION,
        "token_count_mode": token_count_mode(counter),

        "parameters": {
            "seed_start": seed_start,
            "cases_per_family": cases_per_family,
            "families": list(TASK_FAMILIES),
            "conditions": {
                k: list(v)
                for k, v in CONDITIONS.items()
            },
            "token_bands": {
                k: list(v)
                for k, v in TOKEN_BANDS.items()
            },
            "context_token_budget": CONTEXT_BUDGET,
        },

        "scenarios": scenarios,
    }


def save_dataset(
    data: dict[str, Any],
    output: str | Path,
) -> dict[str, Any]:
    """기존 데이터셋을 덮어쓰지 않고 Manifest까지 저장한다."""

    path = Path(output)

    manifest_path = path.with_name(
        path.stem + "_manifest.json"
    )

    if path.exists() or manifest_path.exists():
        raise FileExistsError(
            "dataset/manifest already exists; "
            "choose a new output path"
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    raw = (
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")

    manifest = {
        "dataset_version": data["dataset_version"],
        "token_count_mode": data["token_count_mode"],

        "scenario_count": len(data["scenarios"]),

        "base_case_count": len({
            s["metadata"]["base_case_id"]
            for s in data["scenarios"]
        }),

        "condition_counts": dict(sorted(
            Counter(
                s["metadata"]["stress_condition"]
                for s in data["scenarios"]
            ).items()
        )),

        "dataset_sha256": hashlib.sha256(
            raw
        ).hexdigest(),
    }

    path.write_bytes(raw)

    manifest_path.write_text(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    parser.add_argument(
        "--counter",
        choices=("gemini", "provisional"),
        default="gemini",
    )

    parser.add_argument(
        "--seed-start",
        type=int,
        default=42,
    )

    parser.add_argument(
        "--cases-per-family",
        type=int,
        default=20,
    )

    parser.add_argument(
        "--output",
        default=(
            "benchmark/generated/"
            "critical_flexible_v1/dataset.json"
        ),
    )

    args = parser.parse_args()

    if args.counter == "gemini":
        counter = GeminiTokenCounter(
            model="gemini-3.1-flash-lite",
            persistent_cache_path=(
                ".cache/gemini_token_cache.sqlite3"
            ),
        )
    else:
        counter = provisional_token_count

    data = generate_dataset(
        counter=counter,
        seed_start=args.seed_start,
        cases_per_family=args.cases_per_family,
    )

    manifest = save_dataset(
        data,
        args.output,
    )

    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
    )

    if args.counter != "gemini":
        print(
            "[NOTICE] Provisional token counts "
            "are for local testing only, "
            "not LLM experiments."
        )

    print(f"[PASS] Dataset: {args.output}")


if __name__ == "__main__":
    main()