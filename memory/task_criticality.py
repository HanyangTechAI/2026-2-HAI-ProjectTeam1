from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from itertools import combinations
import re
from typing import Sequence

from memory.experimental_packing import (
    pack_flexible,
    validate_alpha,
)
from memory.schema import MemoryItem
from memory.selector import MemoryCandidate


_PROJECT = re.compile(
    r"(?<![A-Za-z0-9_-])"
    r"[A-Za-z][A-Za-z0-9]*-[A-Za-z0-9]{3,}"
    r"(?![A-Za-z0-9_-])"
)

_FILE = re.compile(
    r"(?<![A-Za-z0-9_.-])"
    r"[A-Za-z0-9_.-]+"
    r"\.(?:pdf|docx?|xlsx?|pptx?|hwp|txt|csv|zip)"
    r"(?![A-Za-z0-9_])",
    re.I,
)

_GRANT = re.compile(
    r"\bapproved_[A-Za-z0-9_-]+\b",
    re.I,
)

_RECEIPT = re.compile(
    r"\bapproval_[A-Za-z0-9_-]+\b",
    re.I,
)

_EMAIL = re.compile(
    r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    re.I,
)

_FILE_CUE = re.compile(
    r"파일명|제출|최종|확정|현재|첨부|"
    r"보고서\s*파일|file\s*name",
    re.I,
)

_GRANT_CUE = re.compile(
    r"승인|허가|grant|authorized",
    re.I,
)

_INVALID_CUE = re.compile(
    r"미승인|승인\s*대기|승인\s*요청|접수번호|"
    r"취소|만료|무효|예시|샘플|가짜|"
    r"철회|회수|폐기|실제\s*파일이\s*아닌|"
    r"not\s+approved|not\s+valid|"
    r"expired|revoked|sample|example|dummy",
    re.I,
)


class EvidenceStatus(str, Enum):
    VALID_CANDIDATE = "valid_candidate"
    INVALID = "invalid"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class SlotEvidence:
    memory_id: str
    slot: str
    value: str
    confidence: float = 1.0


@dataclass(frozen=True)
class CriticalPackingResult:
    selected: tuple[MemoryCandidate, ...]
    promoted_ids: tuple[str, ...]

    required_slots: tuple[str, ...]
    grounded_slots: tuple[str, ...]
    ambiguous_slots: tuple[str, ...]
    unresolved_slots: tuple[str, ...]

    action: str | None

    excluded_invalid_ids: tuple[str, ...] = ()

    exclusion_reasons: tuple[
        tuple[str, str], ...
    ] = ()

    slot_statuses: tuple[
        tuple[str, str], ...
    ] = ()


def _intent(query: str) -> str | None:
    text = query.casefold()

    send = bool(
        re.search(
            r"보내|발송|전송|\bsend\b",
            text,
        )
    )

    email = bool(
        re.search(
            r"메일|이메일|\bemail\b",
            text,
        )
    )

    find = bool(
        re.search(
            r"찾|검색|조회|\bfind\b|\bsearch\b",
            text,
        )
    )

    file_word = bool(
        re.search(
            r"파일|문서|보고서|자료|"
            r"\bfile\b|\bdocument\b",
            text,
        )
    )

    if send and email and find:
        return None

    if send and email and _EMAIL.search(query):
        return "email.send_email"

    if find and file_word and not send:
        return "file.find_file"

    return None


def _target_project(query: str) -> str | None:
    projects = {
        value.casefold()
        for value in _PROJECT.findall(query)
    }

    if len(projects) != 1:
        return None

    return next(iter(projects))


def _evidence_windows(
    text: str,
    slot: str,
):
    """값과 주변 문맥을 함께 추출한다."""

    if slot in ("filename", "attachment"):
        expression = _FILE
        cue = _FILE_CUE

    elif slot == "approval_id":
        expression = _GRANT
        cue = _GRANT_CUE

    else:
        return

    for match in expression.finditer(text):
        local = text[
            max(0, match.start() - 65):
            min(len(text), match.end() + 75)
        ]

        if cue.search(local):
            yield match.group(), local


def _slot_has_raw_evidence(
    content: str,
    slot: str,
) -> bool:
    if slot == "approval_id":
        return bool(
            _GRANT.search(content)
            or _RECEIPT.search(content)
        )

    if slot in ("filename", "attachment"):
        return bool(
            _FILE.search(content)
        )

    return False


def _assess_memory(
    memory: MemoryItem,
    *,
    query: str,
    slots: Sequence[str],
) -> tuple[
    tuple[SlotEvidence, ...],
    str | None,
]:
    """하나의 Memory를 검사하고 유효 후보 또는 제외 사유 반환.

    판정 가능한 무효 Evidence가 존재한다면 해당 Memory 전체를
    제외한다. MemoryItem은 현재 부분 삭제가 불가능하기 때문이다.
    """

    target = _target_project(query)

    if target is None:
        return (), None

    content = memory.content

    projects = {
        value.casefold()
        for value in _PROJECT.findall(content)
    }

    evidence: list[SlotEvidence] = []
    invalid_reason: str | None = None

    for slot in slots:
        for value, local in _evidence_windows(
            content,
            slot,
        ):
            if not projects or projects != {target}:
                invalid_reason = "wrong_or_mixed_project"

            elif _INVALID_CUE.search(local):
                invalid_reason = "negated_or_example_evidence"

            else:
                evidence.append(
                    SlotEvidence(
                        memory_id=memory.memory_id,
                        slot=slot,
                        value=value,
                    )
                )

    if "approval_id" in slots:
        for match in _RECEIPT.finditer(content):
            local = content[
                max(0, match.start() - 65):
                min(len(content), match.end() + 75)
            ]

            if (
                _GRANT_CUE.search(local)
                or _INVALID_CUE.search(local)
            ):
                invalid_reason = (
                    invalid_reason
                    or "approval_receipt_not_grant"
                )

    if invalid_reason is not None:
        return (), invalid_reason

    return tuple(evidence), None


def _reserve(
    candidates: Sequence[MemoryCandidate],
    coverage: dict[str, set[str]],
    budget: int,
) -> tuple[MemoryCandidate, ...]:
    """필요한 Slot Coverage 우선, 이후 토큰 비용 최소화."""

    if not candidates:
        return ()

    if len(candidates) <= 12:
        best_key = None
        best: tuple[MemoryCandidate, ...] = ()

        for n in range(len(candidates) + 1):
            for subset in combinations(
                candidates,
                n,
            ):
                cost = sum(
                    c.memory.token_count
                    for c in subset
                )

                if cost > budget:
                    continue

                covered = (
                    set().union(*(
                        coverage[c.memory.memory_id]
                        for c in subset
                    ))
                    if subset
                    else set()
                )

                key = (
                    -len(covered),
                    cost,
                    -sum(
                        c.utility_score
                        for c in subset
                    ),
                    tuple(sorted(
                        c.memory.memory_id
                        for c in subset
                    )),
                )

                if best_key is None or key < best_key:
                    best_key = key
                    best = tuple(subset)

        return best

    remaining = budget

    uncovered = set().union(*(
        coverage[c.memory.memory_id]
        for c in candidates
    ))

    chosen: list[MemoryCandidate] = []
    pending = list(candidates)

    while uncovered:
        feasible = [
            c
            for c in pending
            if (
                c.memory.token_count <= remaining
                and coverage[c.memory.memory_id] & uncovered
            )
        ]

        if not feasible:
            break

        picked = min(
            feasible,
            key=lambda c: (
                -len(
                    coverage[c.memory.memory_id]
                    & uncovered
                ),
                c.memory.token_count,
                -c.utility_score,
                c.memory.memory_id,
            ),
        )

        chosen.append(picked)
        pending.remove(picked)

        remaining -= picked.memory.token_count

        uncovered.difference_update(
            coverage[picked.memory.memory_id]
        )

    return tuple(chosen)


def pack_task_criticality(
    candidates: Sequence[MemoryCandidate],
    *,
    query: str,
    mandatory: Sequence[MemoryItem] = (),
    budget: int,
    fallback_alpha: float = 1.0,
) -> CriticalPackingResult:
    """무효 Evidence 필터링 → Slot 보존 → Fallback Packing."""

    if not isinstance(query, str) or not query.strip():
        raise ValueError(
            "query must be nonempty"
        )

    if type(budget) is not int or budget < 0:
        raise ValueError(
            "budget must be a nonnegative integer"
        )

    validate_alpha(fallback_alpha)

    if any(
        not isinstance(c, MemoryCandidate)
        for c in candidates
    ):
        raise TypeError(
            "candidates must contain MemoryCandidate objects"
        )

    if any(
        not isinstance(m, MemoryItem)
        for m in mandatory
    ):
        raise TypeError(
            "mandatory must contain MemoryItem objects"
        )

    ids = [
        c.memory.memory_id
        for c in candidates
    ]

    if len(ids) != len(set(ids)):
        raise ValueError(
            "duplicate flexible candidate IDs"
        )

    
    action = _intent(query)

    required: tuple[str, ...] = (
        ("filename",)
        if action == "file.find_file"
        else ("attachment", "approval_id")
        if action == "email.send_email"
        else ()
    )

    grounded: set[str] = set()

    if (
        action == "file.find_file"
        and _FILE.search(query)
    ):
        grounded.add("filename")

    if (
        action == "email.send_email"
        and _FILE.search(query)
    ):
        grounded.add("attachment")

    for memory in mandatory:
        evidences, reason = _assess_memory(
            memory,
            query=query,
            slots=required,
        )

        if reason is None:
            grounded.update(
                e.slot for e in evidences
            )

    
    safe: list[MemoryCandidate] = []
    invalid: dict[str, str] = {}
    evidences: list[SlotEvidence] = []

    for candidate in candidates:
        found, reason = _assess_memory(
            candidate.memory,
            query=query,
            slots=required,
        )

        if reason is not None:
            invalid[
                candidate.memory.memory_id
            ] = reason
        else:
            safe.append(candidate)
            evidences.extend(found)

    
    missing = tuple(
        slot for slot in required
        if slot not in grounded
    )

    values_by_slot: dict[
        str, set[str]
    ] = {
        slot: set()
        for slot in missing
    }

    for evidence in evidences:
        if evidence.slot in values_by_slot:
            values_by_slot[
                evidence.slot
            ].add(
                evidence.value.casefold()
            )

    ambiguous = tuple(sorted(
        slot
        for slot, values in values_by_slot.items()
        if len(values) > 1
    ))

    eligible = set(missing) - set(ambiguous)

    
    coverage: dict[str, set[str]] = {}

    for evidence in evidences:
        if evidence.slot in eligible:
            coverage.setdefault(
                evidence.memory_id,
                set(),
            ).add(evidence.slot)

    applicable = [
        c for c in safe
        if coverage.get(c.memory.memory_id)
    ]

    
    reserved = _reserve(
        applicable,
        coverage,
        budget,
    )

    reserved_ids = {
        c.memory.memory_id
        for c in reserved
    }

    used = sum(
        c.memory.token_count
        for c in reserved
    )

    if used > budget:
        raise AssertionError(
            "reserved memory budget overflow"
        )

    
    filler_candidates = [
        c for c in safe
        if c.memory.memory_id not in reserved_ids
    ]

    fillers = pack_flexible(
        filler_candidates,
        budget - used,
        alpha=fallback_alpha,
    )

    promoted = tuple(
        replace(
            c,
            selection_reason="selected_task_slot",
        )
        for c in sorted(
            reserved,
            key=lambda item: item.memory.memory_id,
        )
    )

    selected = promoted + fillers

    if sum(
        c.memory.token_count
        for c in selected
    ) > budget:
        raise AssertionError(
            "flexible budget overflow"
        )

    
    covered = (
        set().union(*(
            coverage[c.memory.memory_id]
            for c in reserved
        ))
        if reserved
        else set()
    )

    unresolved = tuple(sorted(
        set(missing) - covered
    ))

    slot_statuses = []

    for slot in required:
        if slot in ambiguous:
            value = EvidenceStatus.AMBIGUOUS

        elif slot in grounded or slot in covered:
            value = EvidenceStatus.VALID_CANDIDATE

        elif (
            slot in values_by_slot
            and values_by_slot[slot]
        ):
            value = EvidenceStatus.UNRESOLVED

        elif (
            slot in missing
            and any(
                _slot_has_raw_evidence(
                    c.memory.content,
                    slot,
                )
                for c in candidates
                if c.memory.memory_id in invalid
            )
        ):
            value = EvidenceStatus.INVALID

        else:
            value = EvidenceStatus.UNRESOLVED

        slot_statuses.append(
            (slot, value.value)
        )

    excluded_ids = tuple(sorted(invalid))

    selected_ids = {
        c.memory.memory_id
        for c in selected
    }

    if set(excluded_ids) & selected_ids:
        raise AssertionError(
            "invalid evidence leaked into selected memories"
        )

    return CriticalPackingResult(
        selected=selected,
        promoted_ids=tuple(
            c.memory.memory_id
            for c in promoted
        ),
        required_slots=required,
        grounded_slots=tuple(sorted(grounded)),
        ambiguous_slots=ambiguous,
        unresolved_slots=unresolved,
        action=action,
        excluded_invalid_ids=excluded_ids,
        exclusion_reasons=tuple(sorted(
            invalid.items()
        )),
        slot_statuses=tuple(slot_statuses),
    )
