from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess

from pathlib import Path
from typing import Any, Callable

from benchmark.critical_flexible_generator import (
    CONDITIONS,
    TOKEN_BANDS,
    _identity,
    generate_dataset,
    save_dataset,
    validate_scenario,
)

from context.tokenizer import (
    GeminiTokenCounter,
    provisional_token_count,
    token_count_mode,
)


TEMPLATE_VERSION = "critical_flexible_holdout_templates_v1"
HOLDOUT_SEED_START = 100
BUDGET_LEVEL = 0.75


# 기존 Pilot과 다른 질의 템플릿
FILE_QUERIES = (
    "{project} 프로젝트에서 확정된 제출용 문서를 찾아줘.",
    "{project}의 최종 제출 자료 파일을 조회해줘.",
    "{project} 업무의 확정된 제출 파일을 찾아줘.",
    "{project}에서 제출할 최종 문서 파일을 확인해줘.",
    "{project} 관련 제출 자료의 실제 파일을 검색해줘.",
)

EMAIL_QUERIES = (
    "{project}의 현재 보고서를 {recipient}에게 외부 메일로 발송해줘.",
    "{project}의 보고서 파일을 {recipient}에게 이메일로 보내줘.",
    "{project}의 확정 보고서를 {recipient}에게 메일로 전송해줘.",
    "{project}의 최신 보고서를 {recipient}에게 외부 발송해줘.",
    "{project} 보고서 첨부 파일을 {recipient}에게 메일로 보내줘.",
)

# Critical Memory: 정답은 반드시 하나의 FACT에만 존재
FILE_CRITICAL = (
    "{project} 제출 문서: {filename}.",
    "{project} 제출 파일명: {filename}.",
    "{project} 원본 파일명: {filename}.",
    "{project} 최종 파일: {filename}.",
    "{project} 제출용 파일: {filename}.",
)

EMAIL_CRITICAL = (
    "{project} 승인 완료 번호: {approval_id}.",
    "{project} 발송 승인번호: {approval_id}.",
    "{project} 발송 허가 ID: {approval_id}.",
    "{project} 승인된 식별자: {approval_id}.",
    "{project} 유효 승인번호: {approval_id}.",
)

NOISE_TEMPLATES = (
    "{project} 회의 기록 {index}: 진행 순서 정리.",
    "{project} 업무 메모 {index}: 작성 방식 검토.",
    "{project} 일정 노트 {index}: 점검 결과 기록.",
    "{project} 운영 메모 {index}: 참여자 의견 정리.",
    "{project} 검토 메모 {index}: 회의 경과 요약.",
)

# 원본 데이터셋과 다른 보충 문장
# 파일명, 승인번호, 이메일 주소는 포함하지 않는다.
FILLERS = (
    "검토 담당자는 진행 순서를 기록하고 참고 의견을 정리했습니다.",
    "회의 참가자들은 설명의 정확성과 문서의 흐름을 점검했습니다.",
    "일반적인 업무 경과와 확인 사항은 관리 노트에 남았습니다.",
    "업무 담당자는 별도의 참고 내용을 기록해 추후 검토하도록 했습니다.",
    "공유된 의견은 전체 일정과 검토 절차를 이해하는 데 사용됐습니다.",
    "정리 과정에서는 표현의 일관성과 기록의 구조를 확인했습니다.",
    "관계자는 업무 배경을 설명하고 별도의 세부 의견을 작성했습니다.",
    "회의에서는 진행 상황을 확인한 뒤 일반적인 논의 내용을 요약했습니다.",
)

SMALL_FILLERS = (
    " 추가 확인.",
    " 검토 기록.",
    " 참고 사항.",
    " 업무 점검.",
)


def _fit(
    text: str,
    length: str,
    counter: Callable[[str], int],
    offset: int,
) -> tuple[str, int]:
    """실제 TokenCounter로 Short/Long 구간을 맞춘다."""
    low, high = TOKEN_BANDS[length]

    n = counter(text)

    if type(n) is not int or n < 0 or n > high:
        raise ValueError(
            f"base text cannot fit {length} band: {n} tokens"
        )

    for i in range(100):
        if n >= low:
            return text, n

        additions = [
            FILLERS[(offset + i + j) % len(FILLERS)]
            for j in range(len(FILLERS))
        ]
        additions.extend(SMALL_FILLERS)

        selected = None

        for addition in additions:
            candidate = (
                text
                + ("" if addition.startswith(" ") else " ")
                + addition
            )

            candidate_n = counter(candidate)

            if n < candidate_n <= high:
                selected = (candidate, candidate_n)
                break

        if selected is None:
            raise ValueError(
                f"cannot fill {length} band: {n}, {text!r}"
            )

        text, n = selected

    raise RuntimeError("holdout text fitting did not converge")


def generate_holdout(
    *,
    counter: Callable[[str], int],
    seed_start: int = HOLDOUT_SEED_START,
    cases_per_family: int = 20,
) -> dict[str, Any]:
    """기존 Scenario 스키마를 보존하면서 독립 데이터셋을 생성한다."""

    if type(seed_start) is not int or seed_start < HOLDOUT_SEED_START:
        raise ValueError(
            "holdout seeds must begin at 100 or later"
        )

    if type(cases_per_family) is not int or cases_per_family < 1:
        raise ValueError(
            "cases_per_family must be positive"
        )

    # 기존 구조와 Evaluator Ground Truth를 재사용한다.
    # 먼저 Offline Counter로 원형을 만든 후
    # 실제 Counter를 적용한 새로운 텍스트로 교체한다.
    data = generate_dataset(
        counter=provisional_token_count,
        seed_start=seed_start,
        cases_per_family=cases_per_family,
    )

    content_cache = {}

    for scenario in data["scenarios"]:
        meta = scenario["metadata"]

        family = meta["task_family"]
        seed = meta["seed"]
        condition = meta["stress_condition"]

        critical_length, noise_length = CONDITIONS[condition]

        identity = _identity(family, seed)

        # 다섯 가지 문장 템플릿을 균형 있게 순환
        index = (seed - seed_start) % 5

        if family == "file_lookup":
            scenario["query"] = FILE_QUERIES[index].format(
                **identity
            )

            critical_base = FILE_CRITICAL[index].format(
                **identity
            )
        else:
            scenario["query"] = EMAIL_QUERIES[index].format(
                **identity
            )

            critical_base = EMAIL_CRITICAL[index].format(
                **identity
            )

        critical_id = meta["critical_flexible_memory_ids"][0]

        noise_ids = meta["distractor_memory_ids"]

        noise_index = {
            mid: i for i, mid in enumerate(noise_ids)
        }

        for memory in scenario["memory_snapshot"]:
            mid = memory["memory_id"]

            if mid == critical_id:
                role = "critical"
                band = critical_length
                base_text = critical_base

            elif mid in noise_index:
                i = noise_index[mid]

                role = f"noise_{i}"
                band = noise_length

                base_text = NOISE_TEMPLATES[
                    (index + i) % len(NOISE_TEMPLATES)
                ].format(
                    project=identity["project"],
                    index=i + 1,
                )

            elif mid in scenario["required_state_ids"]:
                memory["content"] = (
                    f"{identity['project']}의 현재 발송 대상 "
                    f"보고서 파일명은 {identity['filename']}입니다."
                )

                memory["token_count"] = counter(
                    memory["content"]
                )
                continue

            elif mid in scenario["applicable_constraint_ids"]:
                # 유효 승인번호의 실제 값은 노출하지 않는다.
                memory["content"] = (
                    f"{identity['project']} 보고서를 "
                    "외부 이메일로 발송할 때에는 "
                    "유효한 승인 완료 번호가 필요합니다."
                )

                memory["token_count"] = counter(
                    memory["content"]
                )
                continue

            else:
                raise ValueError(
                    f"unexpected Memory role: {mid}"
                )

            cache_key = (
                family,
                seed,
                role,
                band,
            )

            if cache_key not in content_cache:
                content_cache[cache_key] = _fit(
                    base_text,
                    band,
                    counter,
                    seed + len(role),
                )

            memory["content"], memory["token_count"] = (
                content_cache[cache_key]
            )

        # 모든 길이와 정답 유일성을 다시 검증한다.
        meta["critical_tokens"] = next(
            memory["token_count"]
            for memory in scenario["memory_snapshot"]
            if memory["memory_id"] == critical_id
        )

        meta["noise_tokens"] = [
            next(
                memory["token_count"]
                for memory in scenario["memory_snapshot"]
                if memory["memory_id"] == nid
            )
            for nid in noise_ids
        ]

        scenario["token_count_mode"] = token_count_mode(
            counter
        )

        meta["token_count_mode"] = token_count_mode(
            counter
        )

        meta["dataset_split"] = "holdout"
        meta["template_version"] = TEMPLATE_VERSION

        validate_scenario(scenario, counter)

    data["token_count_mode"] = token_count_mode(counter)

    data["generator"] = (
        "benchmark.critical_flexible_holdout"
    )

    data["holdout"] = {
        "split": "holdout",
        "template_version": TEMPLATE_VERSION,
        "seed_start": seed_start,
        "seed_end": seed_start + cases_per_family - 1,
        "pilot_seeds_excluded": [42, 43, 44, 45],
        "budget_level": BUDGET_LEVEL,
    }

    data["parameters"]["seed_start"] = seed_start
    data["parameters"]["cases_per_family"] = cases_per_family

    return data


def prepare_budgeted_holdout(
    data: dict[str, Any],
    calibrations: list[dict[str, Any]],
    *,
    safety_margin: int = 32,
) -> dict[str, Any]:
    """선택 결과를 보지 않고 Floor/Ceiling으로 75% Budget만 결정한다."""

    if data.get("holdout", {}).get("split") != "holdout":
        raise ValueError(
            "expected independent holdout dataset"
        )

    if type(safety_margin) is not int or safety_margin < 0:
        raise ValueError(
            "safety_margin must be a non-negative integer"
        )

    scenarios = data.get("scenarios", [])

    identifiers = [
        s["scenario_id"] for s in scenarios
    ]

    if (
        not identifiers
        or len(set(identifiers)) != len(identifiers)
    ):
        raise ValueError(
            "empty or duplicate scenario IDs"
        )

    lookup = {}

    for row in calibrations:
        sid = row["scenario_id"]

        if sid in lookup:
            raise ValueError(
                f"duplicate calibration: {sid}"
            )

        lookup[sid] = row

    if set(lookup) != set(identifiers):
        raise ValueError(
            "missing or extra calibration cases"
        )

    result = copy.deepcopy(data)

    for scenario in result["scenarios"]:
        sid = scenario["scenario_id"]

        cal = lookup[sid]

        floor = cal.get("floor")
        ceiling = cal.get("ceiling")

        if (
            type(floor) is not int
            or type(ceiling) is not int
            or floor <= 0
            or ceiling <= floor
        ):
            raise ValueError(
                f"invalid floor/ceiling: {sid}"
            )

        # 75%는 사전에 결정된 고정 값이다.
        effective = floor + (
            3 * (ceiling - floor)
        ) // 4

        # Runner가 한 번만 차감하도록 Nominal Budget 기록
        nominal = effective + safety_margin

        scenario["context_budget"] = nominal

        meta = scenario["metadata"]
        meta["context_token_budget"] = nominal
        meta["holdout_budget_level"] = BUDGET_LEVEL
        meta["holdout_effective_budget"] = effective

    result["holdout"]["budget_source"] = (
        "step1_reference_floor_ceiling_only"
    )

    result["holdout"]["budget_level"] = BUDGET_LEVEL
    result["holdout"]["safety_margin"] = safety_margin
    result["holdout"]["score_blind_budget_calibration"] = True

    return result


def _verified_manifest(dataset: Path) -> None:
    manifest_path = dataset.with_name(
        dataset.stem + "_manifest.json"
    )

    manifest = json.loads(
        manifest_path.read_text(encoding="utf-8")
    )

    actual = hashlib.sha256(
        dataset.read_bytes()
    ).hexdigest()

    if actual != manifest["dataset_sha256"]:
        raise ValueError(
            "input holdout manifest SHA-256 mismatch"
        )


def _git_provenance() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
        )

        return (
            result.stdout.strip()
            if result.returncode == 0
            else None
        )

    status = git("status", "--porcelain")

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "git_dirty": (
            bool(status)
            if status is not None else None
        ),
    }


def _calibrate_holdout(
    data: dict[str, Any],
    config: dict[str, Any],
):
    """LLM 없이 Mandatory Floor와 All-Flexible Ceiling만 측정한다."""

    from agent.llm_planner import (
        LLMRunConfig,
        SYSTEM_INSTRUCTION,
    )

    from benchmark.action_time_preflight import calibrate

    from benchmark.experiment_runtime import (
        multilingual_embedder,
        embedding_runtime_metadata,
    )

    tc = config["runtime"]["token_counter"]

    counter = GeminiTokenCounter(
        model=tc["model"],
        cache_size=tc["cache_size"],
        persistent_cache_path=tc["persistent_cache_path"],
        max_retries=tc["max_retries"],
        retry_base_seconds=tc["retry_base_seconds"],
    )

    if data["token_count_mode"] != token_count_mode(counter):
        raise ValueError(
            "holdout dataset/tokenizer mismatch"
        )

    model = config["model"]

    llm = LLMRunConfig(
        model=model["name"],
        temperature=model["temperature"],
        system_instruction=SYSTEM_INSTRUCTION,
        max_output_tokens=model["max_output_tokens"],
        thinking_level=model["thinking_level"],
        max_retries=model["max_retries"],
        retry_base_seconds=model["retry_base_seconds"],
        timeout_ms=model["timeout_ms"],
    )

    # 기존 Runner와 동일한 Request CountTokens 구성
    counter.configure_request(
        system_instruction=llm.system_instruction,
        generation_config=(
            llm.planner().generation_config()
        ),
    )

    system_prompt = Path(
        model["system_prompt_file"]
    ).read_text(encoding="utf-8")

    tool_definitions = Path(
        model["tool_definitions_file"]
    ).read_text(encoding="utf-8")

    calibrations = []

    for index, scenario in enumerate(
        data["scenarios"], 1
    ):
        validate_scenario(scenario, counter)

        cal = calibrate(
            scenario,
            system_prompt,
            tool_definitions,
            multilingual_embedder,
            counter,
            top_k=config["runtime"]["candidate_top_k"],
            weights=tuple(
                config["runtime"]["utility_weights"]
            ),
        )

        # 전략별 선택 결과나 Critical Recall은 저장하지 않는다.
        calibrations.append({
            "scenario_id": scenario["scenario_id"],
            "base_case_id": (
                scenario["metadata"]["base_case_id"]
            ),
            "condition": (
                scenario["metadata"]["stress_condition"]
            ),
            "floor": cal["floor"],
            "ceiling": cal["ceiling"],
        })

        print(
            f"[{index}/{len(data['scenarios'])}] "
            f"calibrated {scenario['scenario_id']}",
            flush=True,
        )

    return (
        calibrations,
        counter.metadata(),
        embedding_runtime_metadata(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__
    )

    commands = parser.add_subparsers(
        dest="command",
        required=True,
    )

    # 1. Dataset 생성
    gen = commands.add_parser("generate")

    gen.add_argument(
        "--counter",
        choices=("gemini", "provisional"),
        default="gemini",
    )

    gen.add_argument(
        "--seed-start",
        type=int,
        default=HOLDOUT_SEED_START,
    )

    gen.add_argument(
        "--cases-per-family",
        type=int,
        default=20,
    )

    gen.add_argument(
        "--output",
        required=True,
    )

    # 2. 75% Budget 준비
    prep = commands.add_parser("prepare")

    prep.add_argument(
        "--dataset",
        required=True,
    )

    prep.add_argument(
        "--config",
        default="experiments/configs/ablation.json",
    )

    prep.add_argument(
        "--output",
        required=True,
    )

    args = parser.parse_args()

    if args.command == "generate":
        if args.counter == "gemini":
            counter = GeminiTokenCounter(
                model="gemini-3.1-flash-lite",
                persistent_cache_path=(
                    ".cache/gemini_token_cache.sqlite3"
                ),
            )
        else:
            counter = provisional_token_count

        data = generate_holdout(
            counter=counter,
            seed_start=args.seed_start,
            cases_per_family=args.cases_per_family,
        )

        manifest = save_dataset(
            data,
            args.output,
        )

    else:
        from benchmark.runner import (
            _load_runner_config,
            _require_executable_config,
        )

        source = Path(args.dataset)
        output = Path(args.output)

        _verified_manifest(source)

        data = json.loads(
            source.read_text(encoding="utf-8")
        )

        if (
            data.get("token_count_mode")
            != "gemini:gemini-3.1-flash-lite"
        ):
            raise ValueError(
                "production holdout must use Gemini CountTokens"
            )

        if data.get("holdout", {}).get("split") != "holdout":
            raise ValueError(
                "not a holdout dataset"
            )

        if len(data.get("scenarios", [])) != 160:
            raise ValueError(
                "expected 160 holdout scenarios"
            )

        calibration_path = output.with_name(
            output.stem + "_calibration.json"
        )

        validation_path = output.with_name(
            output.stem + "_validation.json"
        )

        output_manifest = output.with_name(
            output.stem + "_manifest.json"
        )

        if any(
            p.exists()
            for p in (
                output,
                output_manifest,
                calibration_path,
                validation_path,
            )
        ):
            raise FileExistsError(
                "one or more output files already exist"
            )

        config = _load_runner_config(args.config)
        _require_executable_config(config)

        if (
            config["scope"] != "ablation"
            or config["freeze"]["version"] != "1.0.4"
        ):
            raise ValueError(
                "expected frozen v1.0.4 ablation config"
            )

        calibrations, token_meta, embedding_meta = (
            _calibrate_holdout(data, config)
        )

        prepared = prepare_budgeted_holdout(
            data,
            calibrations,
            safety_margin=config["budget_policy"][
                "request_token_safety_margin"
            ],
        )

        prepared["holdout"]["source_dataset_sha256"] = (
            hashlib.sha256(
                source.read_bytes()
            ).hexdigest()
        )

        manifest = save_dataset(
            prepared,
            output,
        )

        calibration_path.write_text(
            json.dumps(
                calibrations,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

        validation = {
            "source_dataset_sha256": (
                prepared["holdout"]["source_dataset_sha256"]
            ),
            "budgeted_dataset_sha256": (
                manifest["dataset_sha256"]
            ),
            "calibration_sha256": hashlib.sha256(
                calibration_path.read_bytes()
            ).hexdigest(),
            "freeze": config["freeze"],
            "algorithm_outcome_inspected": False,
            "budget_level": BUDGET_LEVEL,
            "n_scenarios": len(calibrations),
            "n_llm_generation_calls": 0,
            "tokenizer": token_meta,
            "embedding": embedding_meta,
            **_git_provenance(),
        }

        validation_path.write_text(
            json.dumps(
                validation,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )

    print(
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        )
    )

    print("[PASS]", args.output)


if __name__ == "__main__":
    main()
