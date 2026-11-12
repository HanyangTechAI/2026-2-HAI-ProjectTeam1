from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from benchmark.critical_flexible_generator import (
    TASK_FAMILIES,
    _identity,
    make_scenario,
)
from context.tokenizer import (
    GeminiTokenCounter,
    provisional_token_count,
    token_count_mode,
)
from memory.schema import MemoryItem, MemoryType
from memory.store import SnapshotMemoryStore

DATASET_VERSION = "task_criticality_s235_v1"
GENERATOR = "benchmark.task_criticality_stress"
VARIANTS = ("S2", "S3_VALID", "S3_EMPTY", "S5")
DEFAULT_SEED_START = 200
DEFAULT_CASES_PER_FAMILY = 20


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _decoy(
    family: str, seed: int, variant: str, index: int
) -> str:
    return _digest(
        f"{DATASET_VERSION}:{family}:{seed}:{variant}:{index}"
    )[:14]


def _patch_memory(
    raw: dict[str, Any],
    counter: Callable[[str], int],
    *,
    content: str | None = None,
    importance: float | None = None,
) -> dict[str, Any]:
    memory = MemoryItem.from_dict(raw)

    if content is not None:
        cost = counter(content)
        if type(cost) is not int or cost < 0:
            raise ValueError(
                "counter must return a nonnegative integer"
            )

        memory = replace(
            memory,
            content=content,
            token_count=cost,
        )

    if importance is not None:
        memory = replace(memory, importance=importance)

    return memory.to_dict()


def _decoy_texts(
    project: str,
    family: str,
    seed: int,
    variant: str,
) -> tuple[str, ...]:
    tokens = [
        _decoy(family, seed, variant, i)
        for i in range(3)
    ]

    if family == "file_lookup":
        return (
            f"{project} 제출용 파일명 예시: "
            f"demo_{tokens[0]}.pdf. 실제 파일이 아닌 샘플입니다.",

            f"{project} 최종 파일명 취소: "
            f"retired_{tokens[1]}.pdf. 이 파일은 무효입니다.",

            f"HAI-OTHER{tokens[2][:5].upper()} 제출용 파일명: "
            f"cross_{tokens[2]}.pdf. 다른 프로젝트 정보입니다.",
        )

    return (
        f"{project} 발송 승인번호 예시: "
        f"approved_{tokens[0]}. 실제 승인이 아닙니다.",

        f"{project} 발송 승인번호 취소: "
        f"approved_{tokens[1]}. 이 승인은 무효입니다.",

        f"{project} 승인 요청 접수번호: "
        f"approval_{tokens[2]}. "
        "접수번호는 발송 허가가 아닙니다.",
    )


def _scenario(
    family: str,
    seed: int,
    variant: str,
    *,
    counter: Callable[[str], int],
    content_cache: dict,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant: {variant}")

    source_condition = "C" if variant == "S2" else "A"

    base = copy.deepcopy(
        make_scenario(
            family,
            seed,
            source_condition,
            counter=counter,
            content_cache=content_cache,
        )
    )

    identity = _identity(family, seed)
    project = identity["project"]

    case_id = (
        f"tcs235_{family}_{seed:03d}_{variant.lower()}"
    )
    session = f"session_{case_id}"

    original_critical = base["metadata"][
        "critical_flexible_memory_ids"
    ][0]

    original_noise = tuple(
        base["metadata"]["distractor_memory_ids"]
    )

    id_map = {
        item["memory_id"]: (
            f"{case_id}__"
            f"{item['memory_id'].split(source_condition + '_', 1)[-1]}"
        )
        for item in base["memory_snapshot"]
    }

    if len(set(id_map.values())) != len(id_map):
        raise RuntimeError(
            "memory ID remapping is not one-to-one"
        )

    critical_id = id_map[original_critical]
    noise_ids = [id_map[x] for x in original_noise]

    new_items: list[dict[str, Any]] = []

    for raw in base["memory_snapshot"]:
        m = MemoryItem.from_dict(raw)
        m = replace(
            m,
            memory_id=id_map[m.memory_id],
            session_id=session,
            supersedes=(
                id_map.get(m.supersedes)
                if m.supersedes else None
            ),
            superseded_by=(
                id_map.get(m.superseded_by)
                if m.superseded_by else None
            ),
        )
        new_items.append(m.to_dict())

    by_id = {
        m["memory_id"]: m
        for m in new_items
    }

    if variant == "S2":
        by_id[critical_id] = _patch_memory(
            by_id[critical_id],
            counter,
            importance=0.05,
        )

        for i, mid in enumerate(noise_ids):
            if family == "file_lookup":
                content = (
                    f"{project} 제출 자료 파일 검색과 검토 업무 "
                    f"관련 진행 기록 {i+1}. "
                    "현재 단계, 검토 순서, 담당자의 작업 내용을 "
                    "정리했습니다."
                )
            else:
                content = (
                    f"{project} 외부 메일 보고서 발송 및 승인 절차 "
                    f"관련 진행 기록 {i+1}. "
                    "현재 단계, 검토 순서, 담당자의 작업 내용을 "
                    "정리했습니다."
                )

            by_id[mid] = _patch_memory(
                by_id[mid],
                counter,
                content=content,
                importance=1.0,
            )

    elif variant in ("S3_VALID", "S3_EMPTY"):
        decoys = _decoy_texts(
            project, family, seed, variant
        )

        for mid, content in zip(
            noise_ids, decoys, strict=True
        ):
            by_id[mid] = _patch_memory(
                by_id[mid],
                counter,
                content=content,
                importance=0.95,
            )

        if variant == "S3_EMPTY":
            by_id[critical_id] = _patch_memory(
                by_id[critical_id],
                counter,
                content=(
                    f"{project} 프로젝트 일반 검토 기록. "
                    "실제 파일명 또는 발송 승인 완료 정보는 "
                    "기록되지 않았습니다."
                ),
                importance=0.30,
            )

    elif variant == "S5":
        alternate = _decoy(
            family, seed, variant, 0
        )

        if family == "file_lookup":
            content = (
                f"{project} 제출용 파일명: "
                f"alternate_{alternate}.pdf."
            )
        else:
            content = (
                f"{project} 발송 승인번호: "
                f"approved_{alternate}."
            )

        by_id[noise_ids[0]] = _patch_memory(
            by_id[noise_ids[0]],
            counter,
            content=content,
            importance=0.60,
        )

        by_id[critical_id] = _patch_memory(
            by_id[critical_id],
            counter,
            importance=0.60,
        )

    snapshot = [
        by_id[item["memory_id"]]
        for item in new_items
    ]

    slot = (
        "filename"
        if family == "file_lookup"
        else "approval_id"
    )

    positive = (
        (critical_id,)
        if variant in ("S2", "S3_VALID")
        else ()
    )

    conflicts = (
        (critical_id, noise_ids[0])
        if variant == "S5"
        else ()
    )

    invalid = (
        tuple(noise_ids)
        if variant.startswith("S3")
        else ()
    )

    expected_call = (
        base["expected_tool_calls"][0]
        if positive
        else None
    )

    scenario = {
        "scenario_id": case_id,
        "session_id": session,
        "query": base["query"],
        "current_turn": base["current_turn"],
        "current_time": base["current_time"],
        "context_budget": base["context_budget"],
        "token_count_mode": token_count_mode(counter),
        "benchmark_enabled": False,
        "validation_scope": "module",
        "memory_snapshot": snapshot,
        "metadata": {
            "dataset_version": DATASET_VERSION,
            "base_case_id": (
                f"tcs235_{family}_{seed:03d}"
            ),
            "seed": seed,
            "task_family": family,
            "stress_condition": (
                "S3"
                if variant.startswith("S3")
                else variant
            ),
            "variant": variant,
            "split": "development",
        },
    }

    label = {
        "scenario_id": case_id,
        "task_family": family,
        "variant": variant,
        "slot": slot,
        "expected_promoted_ids": list(positive),
        "invalid_evidence_ids": list(invalid),
        "competing_evidence_ids": list(conflicts),
        "expected_ambiguous_slots": (
            [slot] if variant == "S5" else []
        ),
        "evidence_available": bool(positive),
        "expected_tool_call": expected_call,
        "label_scope": "selection_only",
    }

    return scenario, label


def generate_dataset(
    *,
    counter: Callable[[str], int] = provisional_token_count,
    seed_start: int = DEFAULT_SEED_START,
    cases_per_family: int = DEFAULT_CASES_PER_FAMILY,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if type(seed_start) is not int or seed_start < 0:
        raise ValueError(
            "seed_start must be a nonnegative integer"
        )

    if (
        type(cases_per_family) is not int
        or cases_per_family < 1
    ):
        raise ValueError(
            "cases_per_family must be positive"
        )

    if not callable(counter):
        raise TypeError(
            "token counter must be callable"
        )

    scenarios = []
    labels = {}
    cache = {}

    for family in TASK_FAMILIES:
        for seed in range(
            seed_start,
            seed_start + cases_per_family
        ):
            for variant in VARIANTS:
                scenario, label = _scenario(
                    family,
                    seed,
                    variant,
                    counter=counter,
                    content_cache=cache,
                )

                scenarios.append(scenario)
                labels[scenario["scenario_id"]] = label

    dataset = {
        "schema_version": "1.0",
        "generator": GENERATOR,
        "dataset_version": DATASET_VERSION,
        "token_count_mode": token_count_mode(counter),
        "parameters": {
            "seed_start": seed_start,
            "cases_per_family": cases_per_family,
            "families": list(TASK_FAMILIES),
            "variants": list(VARIANTS),
            "split": "development",
        },
        "scenarios": scenarios,
    }

    oracle = {
        "dataset_version": DATASET_VERSION,
        "split": "development",
        "labels": labels,
    }

    validate_dataset(dataset, oracle, counter)

    return dataset, oracle


def validate_dataset(
    dataset: dict[str, Any],
    oracle: dict[str, Any],
    counter: Callable[[str], int],
) -> None:
    if (
        dataset.get("generator") != GENERATOR
        or dataset.get("dataset_version") != DATASET_VERSION
    ):
        raise ValueError("wrong dataset generator/version")

    if oracle.get("dataset_version") != DATASET_VERSION:
        raise ValueError("wrong label version")

    if (
        dataset.get("token_count_mode")
        != token_count_mode(counter)
    ):
        raise ValueError(
            "dataset/runtime token counter mismatch"
        )

    if (
        dataset.get("parameters", {}).get("split")
        != "development"
    ):
        raise ValueError(
            "only development data is supported"
        )

    scenarios = dataset.get("scenarios", [])
    labels = oracle.get("labels", {})

    n = (
        dataset["parameters"]["cases_per_family"]
        * len(TASK_FAMILIES)
        * len(VARIANTS)
    )

    ids = [s["scenario_id"] for s in scenarios]

    if (
        len(ids) != n
        or len(set(ids)) != n
        or set(ids) != set(labels)
    ):
        raise ValueError(
            "scenario/label cardinality mismatch"
        )

    counts = Counter(
        (
            s["metadata"]["task_family"],
            s["metadata"]["variant"],
        )
        for s in scenarios
    )

    if any(
        counts[(family, variant)]
        != dataset["parameters"]["cases_per_family"]
        for family in TASK_FAMILIES
        for variant in VARIANTS
    ):
        raise ValueError(
            "unbalanced family/variant counts"
        )

    for s in scenarios:
        sid = s["scenario_id"]
        label = labels[sid]
        meta = s["metadata"]
        variant = meta["variant"]
        family = meta["task_family"]

        if (
            label["scenario_id"] != sid
            or label["variant"] != variant
            or label["task_family"] != family
        ):
            raise ValueError(
                f"inconsistent label: {sid}"
            )

        if (
            s.get("benchmark_enabled") is not False
            or s.get("validation_scope") != "module"
        ):
            raise ValueError(
                f"selection-only data must disable Runner: {sid}"
            )

        if any(
            key in s
            for key in (
                "environment",
                "expected_tool_calls",
                "target_memory_ids",
                "critical_flexible_memory_ids",
                "constraint_rules",
                "required_states",
            )
        ):
            raise ValueError(
                f"oracle leakage into scenario: {sid}"
            )

        items = [
            MemoryItem.from_dict(x)
            for x in s["memory_snapshot"]
        ]

        SnapshotMemoryStore(tuple(items))

        by_id = {
            m.memory_id: m
            for m in items
        }

        if (
            len(by_id) != len(items)
            or any(
                m.session_id != s["session_id"]
                for m in items
            )
        ):
            raise ValueError(
                f"duplicate IDs or mismatched session: {sid}"
            )

        if len(items) != (
            4 if family == "file_lookup" else 6
        ):
            raise ValueError(
                f"unexpected memory count: {sid}"
            )

        for m in items:
            if m.token_count != counter(m.content):
                raise ValueError(
                    f"token count drift: {sid} {m.memory_id}"
                )

        positives = set(
            label["expected_promoted_ids"]
        )
        invalid = set(
            label["invalid_evidence_ids"]
        )
        conflicts = set(
            label["competing_evidence_ids"]
        )

        if not positives.isdisjoint(
            invalid | conflicts
        ):
            raise ValueError(
                f"overlapping truth labels: {sid}"
            )

        if not (
            positives | invalid | conflicts
        ).issubset(by_id):
            raise ValueError(
                f"label ID absent from memory: {sid}"
            )

        if variant in ("S2", "S3_VALID"):
            if (
                len(positives) != 1
                or label["expected_tool_call"] is None
            ):
                raise ValueError(
                    f"positive case must have one gold fact: {sid}"
                )

        elif variant == "S3_EMPTY":
            if (
                positives
                or conflicts
                or label["expected_tool_call"] is not None
            ):
                raise ValueError(
                    f"negative-only case malformed: {sid}"
                )

        elif variant == "S5":
            if (
                positives
                or len(conflicts) != 2
                or label["expected_ambiguous_slots"]
                != [label["slot"]]
            ):
                raise ValueError(
                    f"conflict case malformed: {sid}"
                )

        if variant == "S2":
            critical = by_id[
                next(iter(positives))
            ]

            noises = [
                m for m in items
                if (
                    m.memory_type == MemoryType.FACT
                    and m.memory_id not in positives
                )
            ]

            if (
                critical.importance != 0.05
                or len(noises) != 3
                or any(
                    m.importance != 1.0
                    for m in noises
                )
            ):
                raise ValueError(
                    f"S2 importance manipulation invalid: {sid}"
                )

            if any(
                critical.token_count <= m.token_count
                for m in noises
            ):
                raise ValueError(
                    f"S2 critical must be longer: {sid}"
                )


def _json_bytes(data: dict) -> bytes:
    return (
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        ) + "\n"
    ).encode("utf-8")


def save_dataset(
    dataset: dict,
    oracle: dict,
    output: str | Path,
) -> dict:
    path = Path(output)

    labels_path = path.with_name(
        path.stem + "_labels.json"
    )

    manifest_path = path.with_name(
        path.stem + "_manifest.json"
    )

    if any(
        p.exists()
        for p in (
            path,
            labels_path,
            manifest_path,
        )
    ):
        raise FileExistsError(
            "dataset, labels, or manifest already exists"
        )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    dataset_bytes = _json_bytes(dataset)
    label_bytes = _json_bytes(oracle)

    manifest = {
        "dataset_version": DATASET_VERSION,
        "token_count_mode": dataset["token_count_mode"],
        "dataset_sha256": hashlib.sha256(
            dataset_bytes
        ).hexdigest(),
        "labels_sha256": hashlib.sha256(
            label_bytes
        ).hexdigest(),
        "scenario_count": len(dataset["scenarios"]),
        "condition_counts": dict(sorted(
            Counter(
                s["metadata"]["stress_condition"]
                for s in dataset["scenarios"]
            ).items()
        )),
        "variant_counts": dict(sorted(
            Counter(
                s["metadata"]["variant"]
                for s in dataset["scenarios"]
            ).items()
        )),
        "label_file": labels_path.name,
    }

    path.write_bytes(dataset_bytes)
    labels_path.write_bytes(label_bytes)
    manifest_path.write_bytes(
        _json_bytes(manifest)
    )

    return manifest


def load_and_validate(
    source: str | Path,
    counter: Callable,
) -> tuple[dict, dict, dict]:
    path = Path(source)

    labels_path = path.with_name(
        path.stem + "_labels.json"
    )

    manifest_path = path.with_name(
        path.stem + "_manifest.json"
    )

    raw = path.read_bytes()
    labels_raw = labels_path.read_bytes()

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    if (
        manifest.get("dataset_sha256")
        != hashlib.sha256(raw).hexdigest()
        or manifest.get("labels_sha256")
        != hashlib.sha256(labels_raw).hexdigest()
        or manifest.get("label_file") != labels_path.name
    ):
        raise ValueError(
            "dataset or label checksum mismatch"
        )

    dataset = json.loads(raw)
    oracle = json.loads(labels_raw)

    validate_dataset(
        dataset, oracle, counter
    )

    return dataset, oracle, manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    sub = parser.add_subparsers(
        dest="command",
        required=True,
    )

    gen = sub.add_parser("generate")

    gen.add_argument(
        "--counter",
        choices=("gemini", "provisional"),
        default="gemini",
    )

    gen.add_argument(
        "--seed-start",
        type=int,
        default=DEFAULT_SEED_START,
    )

    gen.add_argument(
        "--cases-per-family",
        type=int,
        default=DEFAULT_CASES_PER_FAMILY,
    )

    gen.add_argument(
        "--output",
        default=(
            "benchmark/generated/"
            "task_criticality_s235_v1/dataset.json"
        ),
    )

    check = sub.add_parser("validate")

    check.add_argument(
        "--counter",
        choices=("gemini", "provisional"),
        default="gemini",
    )

    check.add_argument(
        "--dataset",
        required=True,
    )

    args = parser.parse_args()

    counter = (
        GeminiTokenCounter(
            model="gemini-3.1-flash-lite",
            persistent_cache_path=(
                ".cache/gemini_token_cache.sqlite3"
            ),
        )
        if args.counter == "gemini"
        else provisional_token_count
    )

    if args.command == "generate":
        dataset, oracle = generate_dataset(
            counter=counter,
            seed_start=args.seed_start,
            cases_per_family=args.cases_per_family,
        )

        result = save_dataset(
            dataset, oracle, args.output
        )

        print(json.dumps(
            result,
            ensure_ascii=False,
            indent=2,
        ))

        print(
            "[PASS] Generated",
            args.output,
        )

    else:
        _, _, result = load_and_validate(
            args.dataset, counter
        )

        print(
            "[PASS] Validated",
            result["scenario_count"],
            "scenarios",
        )

    if args.counter == "provisional":
        print(
            "[NOTICE] Provisional token counts are "
            "not valid for live LLM experiments"
        )


if __name__ == "__main__":
    main()
