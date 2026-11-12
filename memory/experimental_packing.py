from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import replace

from memory.selector import (
    MemoryCandidate,
    PipelineStatus,
    SelectionResult,
    calculate_memory_budget,
    select_mandatory,
)


def validate_alpha(alpha: float) -> float:
    if isinstance(alpha, bool) or not isinstance(alpha, (int, float)):
        raise TypeError("alpha must be a finite real number")
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("alpha must be finite and between 0 and 1")
    return float(alpha)


def packing_score(candidate: MemoryCandidate, alpha: float) -> float:
    """길이 패널티를 조절하는 점수: Utility / Tokens^alpha."""
    alpha = validate_alpha(alpha)

    if not isinstance(candidate, MemoryCandidate):
        raise TypeError("expected MemoryCandidate")

    utility = candidate.utility_score
    cost = candidate.memory.token_count

    if not math.isfinite(utility) or utility < 0:
        raise ValueError(
            "candidate utility must be finite and non-negative"
        )
    if type(cost) is not int or cost < 0:
        raise ValueError(
            "candidate token_count must be a non-negative integer"
        )

    return utility / (max(cost, 1) ** alpha)


def pack_flexible(
    candidates: Sequence[MemoryCandidate],
    budget: int,
    *,
    alpha: float,
) -> tuple[MemoryCandidate, ...]:
    """기존 Greedy 정렬 및 Hard Budget을 유지하고 길이 패널티만 변경."""
    validate_alpha(alpha)

    if type(budget) is not int or budget < 0:
        raise ValueError("budget must be a non-negative integer")

    if any(
        not isinstance(c, MemoryCandidate)
        for c in candidates
    ):
        raise TypeError(
            "candidates must be MemoryCandidate objects"
        )

    ids = [c.memory.memory_id for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate flexible memory IDs")

    # Frozen Selector와 동일한 Tie-breaking:
    # memory_id 오름차순으로 먼저 정렬한 뒤,
    # 점수/Utility/Relevance/Recency 내림차순의 안정 정렬.
    ranked = sorted(
        candidates,
        key=lambda c: c.memory.memory_id,
    )

    ranked.sort(
        key=lambda c: (
            packing_score(c, alpha),
            c.utility_score,
            c.relevance_score,
            c.recency_score,
        ),
        reverse=True,
    )

    remaining = budget
    picked = []

    for candidate in ranked:
        # 여러 alpha를 비교할 때 원래 후보 객체를 변경하지 않는다.
        item = replace(candidate)
        cost = item.memory.token_count

        if cost > remaining:
            item.selection_reason = "rejected_budget"
            continue

        item.selection_reason = "selected_flexible"

        picked.append(item)
        remaining -= cost

    return tuple(picked)


def repack_reference(
    reference: SelectionResult,
    scores: Mapping[str, Mapping[str, float]],
    *,
    total_budget: int,
    fixed_tokens: int,
    current_turn: int,
    alpha: float,
) -> SelectionResult:
    """충분한 예산으로 생성한 Frozen Reference를 작은 예산으로 재선택.

    전제:
    - reference.flexible에 모든 Flexible 후보가 들어 있다.
    - scores는 Frozen Retrieval과 동일한 후보 Utility를 기록한다.
    - Retrieval, Temporal, Mandatory State, Protected는 변경하지 않는다.

    실제 Frozen Selector와의 동등성은 Preflight에서 별도로 검증한다.
    """
    validate_alpha(alpha)

    if reference.status != PipelineStatus.OK:
        raise ValueError(
            "reference selection must have status OK"
        )

    if type(current_turn) is not int or current_turn < 0:
        raise ValueError(
            "current_turn must be a non-negative integer"
        )

    status, memory_budget = calculate_memory_budget(
        total_budget,
        fixed_tokens,
    )

    if status != PipelineStatus.OK:
        return SelectionResult(
            status=status,
            remaining_budget=0,
        )

    # Mandatory 선택은 Frozen 구현을 그대로 재사용.
    mandatory = select_mandatory(
        reference.protected,
        reference.states,
        memory_budget,
    )

    mandatory_ids = tuple(
        memory.memory_id
        for memory in mandatory.memories
    )
    mandatory_set = set(mandatory_ids)

    candidates = []
    seen = set()

    for memory in reference.flexible:
        mid = memory.memory_id

        if mid in seen or mid in mandatory_set:
            raise ValueError(
                f"duplicate or mandatory flexible ID: {mid}"
            )

        seen.add(mid)

        if mid not in scores:
            raise ValueError(
                f"missing retrieval score for {mid}"
            )

        info = scores[mid]
        utility = float(info["utility_score"])
        semantic = float(info["semantic_score"])

        if not math.isfinite(semantic):
            raise ValueError(
                f"non-finite semantic score for {mid}"
            )

        candidate = MemoryCandidate(
            memory=memory,
            semantic_score=semantic,
            relevance_score=max(semantic, 0.0),
            recency_score=(
                1.0 /
                (1.0 + max(current_turn - memory.turn_id, 0))
            ),
            utility_score=utility,
        )

        packing_score(candidate, alpha)
        candidates.append(candidate)

    # 필수 메모리가 예산을 넘으면 Flexible을 버리는 대신 중단.
    if mandatory.status != PipelineStatus.OK:
        return SelectionResult(
            status=PipelineStatus.INSUFFICIENT_CONTEXT_BUDGET,
            protected=reference.protected,
            states=reference.states,
            retrieved_memory_ids=reference.retrieved_memory_ids,
            selected_memory_ids=mandatory_ids,
            rejected_memory_ids=tuple(
                memory.memory_id
                for memory in reference.flexible
            ),
            memory_tokens=mandatory.used_tokens,
            remaining_budget=0,
        )

    picked = pack_flexible(
        candidates,
        mandatory.remaining_budget,
        alpha=alpha,
    )

    flexible = tuple(
        candidate.memory
        for candidate in picked
    )

    selected_ids = (
        mandatory_ids
        + tuple(memory.memory_id for memory in flexible)
    )

    selected_set = set(selected_ids)

    rejected = tuple(
        memory.memory_id
        for memory in reference.flexible
        if memory.memory_id not in selected_set
    )

    used = (
        mandatory.used_tokens
        + sum(memory.token_count for memory in flexible)
    )

    if used > memory_budget:
        raise AssertionError(
            "experimental packing exceeded hard memory budget"
        )

    return SelectionResult(
        status=PipelineStatus.OK,
        protected=reference.protected,
        states=reference.states,
        flexible=flexible,
        retrieved_memory_ids=reference.retrieved_memory_ids,
        selected_memory_ids=selected_ids,
        rejected_memory_ids=rejected,
        memory_tokens=used,
        remaining_budget=memory_budget - used,
    )
