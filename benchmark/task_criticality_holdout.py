from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from benchmark.critical_flexible_generator import (
    TASK_FAMILIES,
    _identity,
)
from benchmark.task_criticality_stress import (
    DATASET_VERSION,
    VARIANTS,
    _decoy,
    generate_dataset,
    load_and_validate,
    save_dataset,
    validate_dataset,
)
from context.tokenizer import (
    GeminiTokenCounter,
    provisional_token_count,
)
from memory.schema import MemoryItem, MemoryType
from memory.task_criticality import (
    _assess_memory,
    _intent,
)

TEMPLATE_VERSION = "task_criticality_independent_holdout_v1"
MIN_HOLDOUT_SEED = 1000

FILE_QUERIES = (
    "{project} 자료 제출에 사용할 보고서 파일을 찾아줘.",
    "{project}의 최종 제출 문서를 조회해 줘.",
    "{project} 담당 건의 보고서 파일을 검색해 줘.",
)

EMAIL_QUERIES = (
    "{project}의 보고서를 {recipient}에게 이메일로 전송해 줘.",
    "{project} 프로젝트 자료를 {recipient}에게 메일로 보내줘.",
    "{project} 건의 보고서를 {recipient} 앞으로 이메일 발송해 줘.",
)

FILE_EVIDENCE = (
    "{project} 검토 끝에 확정한 제출 파일명은 {value}.",
    "{project} 기록에 남은 최종 보고서 파일명: {value}.",
    "{project} 제출 자료로 지정한 파일은 {value}.",
)

EMAIL_EVIDENCE = (
    "{project} 담당자가 외부 발송을 허가한 승인번호는 {value}.",
    "{project} 건에 사용 가능한 이메일 발송 승인 값: {value}.",
    "{project} 파일 발송 허가가 완료된 번호는 {value}.",
)

NEUTRAL = (
    "{project} 실무 회의에서 업무 분담과 문서 정리 순서를 확인했습니다.",
    "{project} 담당자들이 검토 일정과 진행 사항을 대조했습니다.",
    "{project} 공유 회의에서 자료 설명 순서와 기록 방식을 논의했습니다.",
)

FILLER_REPLACEMENTS = {
    "이 항목은 진행 배경을 이해하기 위한 일반적인 참고 기록입니다.":
        "이 기록은 업무 과정을 파악하는 보조 설명입니다.",
    "참여자들은 기록 내용을 정리하고 검토 순서를 함께 확인했습니다.":
        "담당자들은 검토 순서와 작성한 기록을 대조했습니다.",
    "작성 과정에서 자료의 구조와 설명 흐름을 점검했습니다.":
        "작성자는 문서 구성과 설명의 흐름을 다시 살펴봤습니다.",
}


def _replace_filler(text: str) -> str:
    for old, new in FILLER_REPLACEMENTS.items():
        text = text.replace(old, new)
    return text


def _update(
    raw: dict[str, Any],
    content: str,
    counter: Callable[[str], int],
) -> dict[str, Any]:
    memory = MemoryItem.from_dict(raw)
    cost = counter(content)

    if type(cost) is not int or cost < 0:
        raise ValueError("invalid token count")

    return replace(
        memory,
        content=content,
        token_count=cost,
    ).to_dict()


def _rewrite_scenario(
    scenario: dict[str, Any],
    label: dict[str, Any],
    counter: Callable[[str], int],
) -> None:
    meta = scenario["metadata"]
    family = meta["task_family"]
    variant = meta["variant"]
    seed = meta["seed"]

    identity = _identity(family, seed)
    project = identity["project"]

    if family == "file_lookup":
        templates = FILE_QUERIES
        scenario["query"] = templates[
            seed % len(templates)
        ].format(project=project)

        original_head = (
            f"{project} 제출용 파일명: "
            f"{identity['filename']}."
        )
        evidence_templates = FILE_EVIDENCE
        gold_value = identity["filename"]

    else:
        templates = EMAIL_QUERIES
        scenario["query"] = templates[
            seed % len(templates)
        ].format(
            project=project,
            recipient=identity["recipient"],
        )

        original_head = (
            f"{project} 발송 승인번호: "
            f"{identity['approval_id']}."
        )
        evidence_templates = EMAIL_EVIDENCE
        gold_value = identity["approval_id"]

    critical_id = next(
        raw["memory_id"]
        for raw in scenario["memory_snapshot"]
        if raw["memory_id"].endswith("__critical")
    )

    invalid_ids = list(label["invalid_evidence_ids"])
    conflict_ids = set(label["competing_evidence_ids"])

    rewritten = []

    for raw in scenario["memory_snapshot"]:
        memory = MemoryItem.from_dict(raw)
        mid = memory.memory_id

        if memory.memory_type == MemoryType.STATE:
            content = (
                f"{project}에서 현재 첨부할 보고서 파일명은 "
                f"{identity['filename']}."
            )

        elif memory.memory_type == MemoryType.CONSTRAINT:
            content = (
                f"{project} 외부 이메일은 사전 승인 완료 번호가 "
                "확인된 뒤에만 발송할 수 있습니다."
            )

        elif mid == critical_id:
            if variant == "S3_EMPTY":
                if family == "file_lookup":
                    content = (
                        f"{project} 제출 자료의 파일 식별 정보는 "
                        "현재 기록에 남아 있지 않습니다."
                    )
                else:
                    content = (
                        f"{project} 발송 허가 처리 여부를 확인할 "
                        "근거가 기록에 없습니다."
                    )

            else:
                if not memory.content.startswith(original_head):
                    raise ValueError(
                        f"critical template drift: {mid}"
                    )

                suffix = memory.content[len(original_head):]

                template = evidence_templates[
                    seed % len(evidence_templates)
                ]

                content = template.format(
                    project=project,
                    value=gold_value,
                ) + _replace_filler(suffix)

        elif variant.startswith("S3") and mid in invalid_ids:
            index = invalid_ids.index(mid)
            token = _decoy(family, seed, variant, index)

            if family == "file_lookup":
                if index == 0:
                    content = (
                        f"{project} 제출 양식의 샘플 파일명은 "
                        f"demo_{token}.pdf 입니다."
                    )
                elif index == 1:
                    content = (
                        f"{project}에서 폐기 처리한 파일명 "
                        f"retired_{token}.pdf는 다시 사용하지 않습니다."
                    )
                else:
                    other = f"HAI-OTHER{token[:5].upper()}"
                    content = (
                        f"{other} 제출 파일명은 cross_{token}.pdf. "
                        f"{project}와 다른 프로젝트 기록입니다."
                    )

            else:
                if index == 0:
                    content = (
                        f"{project} 발송 승인번호 샘플: "
                        f"approved_{token}. 실제 허가가 아닙니다."
                    )
                elif index == 1:
                    content = f"{project} 발송 승인번호: approved_{token}. 해당 승인은 취소되어 현재 사용할 수 없습니다."
                else:
                    content = f"{project} 승인 요청 접수번호: approval_{token}. 이 번호는 요청 접수만 의미하며 실제 발송 허가가 아닙니다."

        elif variant == "S5" and mid in conflict_ids:
            token = _decoy(family, seed, variant, 0)

            if family == "file_lookup":
                content = (
                    f"{project} 제출 자료의 최종 파일명으로 "
                    f"alternate_{token}.pdf를 기록했습니다."
                )
            else:
                content = (
                    f"{project} 담당 건의 발송 승인번호: "
                    f"approved_{token}."
                )

        else:
            content = NEUTRAL[
                (seed + len(mid)) % len(NEUTRAL)
            ].format(project=project)

        rewritten.append(_update(raw, content, counter))

    scenario["memory_snapshot"] = rewritten
    meta["split"] = "holdout"
    meta["template_version"] = TEMPLATE_VERSION

    label["split"] = "holdout"


def _verify_evidence(
    scenario: dict[str, Any],
    label: dict[str, Any],
) -> None:
    family = label["task_family"]

    expected_intent = (
        "file.find_file"
        if family == "file_lookup"
        else "email.send_email"
    )

    if _intent(scenario["query"]) != expected_intent:
        raise ValueError(
            f"intent drift: {scenario['scenario_id']}"
        )

    by_id = {
        raw["memory_id"]: MemoryItem.from_dict(raw)
        for raw in scenario["memory_snapshot"]
    }

    slot = label["slot"]

    for mid in label["expected_promoted_ids"]:
        evidence, reason = _assess_memory(
            by_id[mid],
            query=scenario["query"],
            slots=(slot,),
        )

        if reason is not None or not any(
            item.slot == slot for item in evidence
        ):
            raise ValueError(
                f"positive evidence lost: {mid}"
            )

    for mid in label["invalid_evidence_ids"]:
        _, reason = _assess_memory(
            by_id[mid],
            query=scenario["query"],
            slots=(slot,),
        )

        if reason is None:
            raise ValueError(
                f"invalid evidence became valid: {mid}"
            )

    values = set()

    for mid in label["competing_evidence_ids"]:
        evidence, reason = _assess_memory(
            by_id[mid],
            query=scenario["query"],
            slots=(slot,),
        )

        if reason is not None or not evidence:
            raise ValueError(
                f"conflicting evidence lost: {mid}"
            )

        values.update(
            item.value.casefold()
            for item in evidence
            if item.slot == slot
        )

    if label["variant"] == "S5" and len(values) != 2:
        raise ValueError(
            f"conflict collapsed: {scenario['scenario_id']}"
        )


def generate_holdout(
    *,
    counter: Callable[[str], int] = provisional_token_count,
    seed_start: int = 1000,
    cases_per_family: int = 20,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        type(seed_start) is not int
        or seed_start < MIN_HOLDOUT_SEED
    ):
        raise ValueError("holdout seed must be >= 1000")

    dataset, oracle = generate_dataset(
        counter=counter,
        seed_start=seed_start,
        cases_per_family=cases_per_family,
    )

    dataset["parameters"]["split"] = "holdout"
    dataset["parameters"]["template_version"] = TEMPLATE_VERSION
    oracle["split"] = "holdout"

    for scenario in dataset["scenarios"]:
        label = oracle["labels"][scenario["scenario_id"]]

        _rewrite_scenario(scenario, label, counter)
        _verify_evidence(scenario, label)

    validate_dataset(dataset, oracle, counter)

    return dataset, oracle


def main() -> None:
    parser = argparse.ArgumentParser()
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
    gen.add_argument("--seed-start", type=int, default=1000)
    gen.add_argument(
        "--cases-per-family",
        type=int,
        default=20,
    )
    gen.add_argument(
        "--output",
        default=(
            "benchmark/generated/"
            "task_criticality_holdout_v1/dataset.json"
        ),
    )

    check = sub.add_parser("validate")
    check.add_argument("--dataset", required=True)
    check.add_argument(
        "--counter",
        choices=("gemini", "provisional"),
        default="gemini",
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
        dataset, oracle = generate_holdout(
            counter=counter,
            seed_start=args.seed_start,
            cases_per_family=args.cases_per_family,
        )

        manifest = save_dataset(
            dataset,
            oracle,
            args.output,
        )

        print(
            f"[PASS] Holdout generated: {len(dataset['scenarios'])}"
        )
        print(
            f"Dataset SHA-256: {manifest['dataset_sha256']}"
        )
        print(
            f"Labels SHA-256: {manifest['labels_sha256']}"
        )

    else:
        dataset, oracle, manifest = load_and_validate(
            args.dataset,
            counter,
        )

        if (
            dataset["parameters"].get("split") != "holdout"
            or dataset["parameters"].get("template_version")
            != TEMPLATE_VERSION
        ):
            raise ValueError("not an independent holdout")

        for scenario in dataset["scenarios"]:
            _verify_evidence(
                scenario,
                oracle["labels"][scenario["scenario_id"]],
            )

        print(
            f"[PASS] Holdout verified: {len(dataset['scenarios'])}"
        )
        print(
            f"Dataset SHA-256: {manifest['dataset_sha256']}"
        )


if __name__ == "__main__":
    main()